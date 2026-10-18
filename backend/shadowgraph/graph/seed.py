"""Builds each tenant's live graph from the seed modules, and creates its memory graph.

Re-seeds automatically when the seed version, the embedding backend or the demo PR numbers change.
"""
from __future__ import annotations

import hashlib
import json
import logging
from types import ModuleType
from typing import TYPE_CHECKING

from ..db import PREFIX, live_graph, memory_graph
from . import queries as Q
from . import seed_acme, seed_globex
from .schema import LIVE_INDEXES, MEMORY_INDEXES, ensure_indexes

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger("shadowgraph.seed")
SEED_VERSION = "2026-10-18.2"
TENANT_MODULES: dict[str, ModuleType] = {"acme": seed_acme, "globex": seed_globex}
CHAT_BASE = "https://chat.{tenant}.example/c"  # .example is reserved: these links are clearly fake


def _demo_numbers(rt: "Runtime") -> tuple[int, int, int]:
    s = rt.settings
    return (s.pr_token_verify, s.pr_session_refresh, s.pr_jwks)


def _signature(rt: "Runtime", tenant: str) -> str:
    raw = json.dumps([SEED_VERSION, rt.embedder.name, _demo_numbers(rt), tenant])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


async def _batch(rt: "Runtime", graph: str, cypher: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), 200):
        await rt.db.query(graph, cypher, {"rows": rows[i:i + 200]}, emit=False)


async def seed_live(rt: "Runtime", tenant: str) -> None:
    mod = TENANT_MODULES[tenant]
    graph = live_graph(tenant)
    await rt.db.delete(graph)
    chat_base = CHAT_BASE.format(tenant=tenant)

    await _batch(rt, graph, "UNWIND $rows AS r CREATE (:Team {id: r.id, name: r.name, oncall_channel: r.oncall})",
                 [{"id": t[0], "name": t[1], "oncall": t[2]} for t in mod.TEAMS])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        CREATE (s:Service {id: r.id, name: r.id, tier: r.tier, status: 'active', description: r.description})
        WITH s, r MATCH (t:Team {id: r.team}) CREATE (t)-[:OWNS]->(s)""",
                 [{"id": s[0], "tier": s[1], "team": s[2], "description": s[3]} for s in mod.SERVICES])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (s:Service {id: r.service})
        CREATE (s)-[:EXPOSES]->(:Endpoint {id: r.id, route: r.route, service: r.service, version: r.version,
                                           deprecated: r.deprecated, method: 'POST', protocol: 'http'})""",
                 [{"id": mod.ep(e[0], e[1]), "service": e[0], "route": e[1], "version": e[2], "deprecated": e[3]}
                  for e in mod.ENDPOINTS])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (a:Service {id: r.caller}), (e:Endpoint {id: r.endpoint}), (b:Service {id: r.provider})
        CREATE (a)-[:DEPENDS_ON]->(e)
        CREATE (a)-[:CALLS {via: r.endpoint, p99_ms: r.p99, rpm: r.rpm, critical: r.critical}]->(b)""",
                 [{"caller": d[0], "provider": d[1], "endpoint": mod.ep(d[1], d[2]), "p99": d[3], "rpm": d[4],
                   "critical": d[3] >= 100} for d in mod.DEPENDS_ON])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        CREATE (g:Engineer {id: r.handle, handle: r.handle, name: r.name, timezone: r.tz, role: 'engineer'})
        WITH g, r MATCH (t:Team {id: r.team}) CREATE (g)-[:MEMBER_OF]->(t)""",
                 [{"handle": e[0], "name": e[1], "tz": e[2], "team": e[3]} for e in mod.ENGINEERS])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (g:Engineer {handle: r.handle}), (s:Service {id: r.service})
        CREATE (g)-[:CONTRIBUTED_TO {commits: r.commits}]->(s)""",
                 [{"handle": c[0], "service": c[1], "commits": c[2]} for c in mod.CONTRIBUTED_TO])
    # FalkorDB's PageRank collapses parallel edges (verified in tests/test_smoke.py), so we store ONE edge
    # per pair with a count. PageRank then measures how many distinct people defer to someone.
    pairs: dict[tuple[str, str], dict] = {}
    for src, dst, kind in mod.DEFERRED_TO:
        entry = pairs.setdefault((src, dst), {"src": src, "dst": dst, "count": 0, "kinds": []})
        entry["count"] += 1
        if kind not in entry["kinds"]:
            entry["kinds"].append(kind)
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (a:Engineer {handle: r.src}), (b:Engineer {handle: r.dst})
        CREATE (a)-[:DEFERRED_TO {count: r.count, kinds: r.kinds}]->(b)""", list(pairs.values()))
    await _batch(rt, graph, """
        UNWIND $rows AS r
        CREATE (:ChatThread {id: r.id, channel: r.channel, summary: r.summary, timestamp: r.ts, url: r.url})""",
                 [{"id": t[0], "channel": "#" + t[1], "summary": t[2], "ts": t[3],
                   "url": f"{chat_base}/{t[1]}/{t[0].lower()}"} for t in mod.THREADS])

    texts = [f"{d[1]}. {d[2]}" for d in mod.DECISIONS]
    vectors = await rt.embedder.embed(texts)
    superseded = {s[0] for s in mod.SUPERSEDED}
    await _batch(rt, graph, """
        UNWIND $rows AS r
        CREATE (d:DecisionRecord {id: r.id, title: r.title, summary: r.summary, status: r.status,
                                  valid_from: r.valid_from, valid_until: r.valid_until, recorded_at: r.valid_from,
                                  blocks_removal: r.blocks, source_url: r.url, embedding: vecf32(r.embedding)})
        WITH d, r
        MATCH (g:Engineer {handle: r.author}), (c:ChatThread {id: r.thread})
        CREATE (g)-[:AUTHORED]->(d)
        CREATE (d)-[:DISCUSSED_IN]->(c)""",
                 [{"id": d[0], "title": d[1], "summary": d[2], "valid_from": d[3], "author": d[4], "thread": d[5],
                   "blocks": d[7], "status": "superseded" if d[0] in superseded else "accepted",
                   "valid_until": next((s[2] for s in mod.SUPERSEDED if s[0] == d[0]), None),
                   "url": f"{chat_base}/decisions/{d[0].lower()}", "embedding": vec}
                  for d, vec in zip(mod.DECISIONS, vectors)])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (d:DecisionRecord {id: r.adr}), (t)
        WHERE (t:Endpoint OR t:Service) AND t.id = r.target
        CREATE (d)-[:GOVERNS]->(t)""",
                 [{"adr": d[0], "target": t} for d in mod.DECISIONS for t in d[6]])
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (a:DecisionRecord {id: r.old}), (b:DecisionRecord {id: r.new})
        CREATE (a)-[:SUPERSEDED_BY {superseded_at: r.at, reason: r.reason}]->(b)""",
                 [{"old": s[0], "new": s[1], "at": s[2], "reason": s[3]} for s in mod.SUPERSEDED])

    prs = [{"number": p[0], "title": p[1], "author": p[2], "target": p[3], "change": p[4], "cites": p[5],
            "created_at": p[6], "status": "merged", "description": p[1]} for p in mod.HISTORICAL_PRS]
    prs += [dict(p, status="open") for p in mod.demo_prs(_demo_numbers(rt))]
    await _batch(rt, graph, """
        UNWIND $rows AS r
        CREATE (pr:PullRequest {id: 'PR-' + toString(r.number), number: r.number, title: r.title,
                                description: r.description, status: r.status, created_at: r.created_at,
                                diff_summary: r.title})
        WITH pr, r
        MATCH (g:Engineer {handle: r.author})
        CREATE (g)-[:AUTHORED]->(pr)
        WITH pr, r
        MATCH (t) WHERE (t:Endpoint OR t:Service) AND t.id = r.target
        CREATE (pr)-[:MODIFIES {change: r.change}]->(t)""", prs)
    await _batch(rt, graph, """
        UNWIND $rows AS r
        MATCH (pr:PullRequest {number: r.number}), (d:DecisionRecord {id: r.adr})
        CREATE (pr)-[:CITES]->(d)""",
                 [{"number": p["number"], "adr": a} for p in prs for a in p["cites"]])

    await ensure_indexes(rt.db, graph, LIVE_INDEXES)
    await rt.db.query(graph, """
        CREATE (:Meta {id: 'meta', tenant: $tenant, company: $company, seed_version: $v, embed_backend: $eb,
                       signature: $sig, seeded_at: timestamp()})""",
                      {"tenant": tenant, "company": mod.COMPANY, "v": SEED_VERSION, "eb": rt.embedder.name,
                       "sig": _signature(rt, tenant)}, emit=False)
    log.info("seeded %s", graph)


async def ensure_memory(rt: "Runtime", tenant: str, wipe: bool = False) -> None:
    graph = memory_graph(tenant)
    if wipe:
        await rt.db.delete(graph)
    rows = []
    if await rt.db.exists(graph):
        rows = await rt.db.query(graph, Q.META, emit=False)
        if rows and rows[0].get("embed_backend") != rt.embedder.name:
            log.warning("memory for %s was embedded with %s; starting fresh", tenant, rows[0].get("embed_backend"))
            await rt.db.delete(graph)
            rows = []
    if not rows:
        await rt.db.query(graph, "MERGE (m:Meta {id: 'meta'}) SET m.embed_backend = $eb, m.tenant = $t",
                          {"eb": rt.embedder.name, "t": tenant}, emit=False)
    await ensure_indexes(rt.db, graph, MEMORY_INDEXES)


async def tenant_is_current(rt: "Runtime", tenant: str) -> bool:
    graph = live_graph(tenant)
    if not await rt.db.exists(graph):
        return False
    rows = await rt.db.query(graph, Q.META, emit=False)
    return bool(rows) and rows[0].get("signature") == _signature(rt, tenant)


async def drop_leftover_shadows(rt: "Runtime") -> int:
    dropped = 0
    for name in await rt.db.list_graphs():
        if name.startswith(PREFIX) and "_sim_" in name:
            dropped += int(await rt.db.delete(name))
    return dropped


async def ensure_seeded(rt: "Runtime", drop_shadows: bool = False) -> None:
    if drop_shadows:  # API server startup only, so no process can drop a shadow graph that is in use
        await drop_leftover_shadows(rt)
    for tenant in rt.settings.tenants:
        if tenant not in TENANT_MODULES:
            log.warning("no seed data for tenant %r; skipping", tenant)
            continue
        if not await tenant_is_current(rt, tenant):
            await seed_live(rt, tenant)
        await ensure_memory(rt, tenant)


async def reset_tenant(rt: "Runtime", tenant: str) -> None:
    """Demo retakes: rebuild the live graph and clear memory."""
    await seed_live(rt, tenant)
    await ensure_memory(rt, tenant, wipe=True)
