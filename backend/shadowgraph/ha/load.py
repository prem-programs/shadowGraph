"""Put a Home Assistant snapshot into FalkorDB (graph `{prefix}ha_live`).

Nodes:  Integration, Module, Symbol, Person, Release, Post, Decision, Meta
Edges:  (Integration)-[:USES {sites, context, n}]->(Symbol)       one edge per integration and name
        (Module)-[:EXPORTS]->(Symbol)    (Integration)-[:PROVIDES]->(Module)
        (Integration)-[:DEPENDS_ON {after}]->(Integration)          from manifest.json
        (Person)-[:CODEOWNER]->(Integration|Module)
        (Symbol)-[:REMOVED_IN|DEPRECATED_IN|BREAKS_IN]->(Release)
        (Post)-[:MENTIONS]->(Symbol)   (Post)-[:WRITTEN_BY]->(Person)   (Decision)-[:SUPERSEDED_BY]->(Decision)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..db import live_graph
from ..embed import DIM
from ..graph.schema import ensure_indexes

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger("shadowgraph.ha.load")
LOADER_VERSION = "2026-10-18.3"
TENANT = "ha"
SEVERITY = {"load": 5, "runtime": 4, "conditional": 3, "shimmed": 2, "type": 1}

HA_INDEXES = [
    "CREATE INDEX FOR (i:Integration) ON (i.id)",
    "CREATE INDEX FOR (i:Integration) ON (i.domain)",
    "CREATE INDEX FOR (s:Symbol) ON (s.id)",
    "CREATE INDEX FOR (s:Symbol) ON (s.name)",
    "CREATE INDEX FOR (s:Symbol) ON (s.module)",
    "CREATE INDEX FOR (m:Module) ON (m.id)",
    "CREATE INDEX FOR (p:Person) ON (p.id)",
    "CREATE INDEX FOR (r:Release) ON (r.id)",
    "CREATE INDEX FOR (p:Post) ON (p.id)",
    "CREATE INDEX FOR (d:Decision) ON (d.id)",
    "CREATE INDEX FOR (c:Change) ON (c.id)",
    f"CREATE VECTOR INDEX FOR (p:Post) ON (p.embedding) OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}",
    f"CREATE VECTOR INDEX FOR (d:Decision) ON (d.embedding) OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}",
    "CALL db.idx.fulltext.createNodeIndex('Symbol', 'name')",
]


def default_snapshot_path() -> str:
    env = os.environ.get("HA_SNAPSHOT", "").strip()
    if env:
        return env
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[2], Path.cwd()):  # repo root, backend/, cwd
        p = root / "data" / "ha_snapshot.json.gz"
        if p.is_file():
            return str(p)
    return str(here.parents[3] / "data" / "ha_snapshot.json.gz")


def snapshot_signature(snap: dict[str, Any], embedder_name: str) -> str:
    m = snap["meta"]
    raw = json.dumps([LOADER_VERSION, m.get("core_sha"), m.get("generated_at"), m.get("uses"), embedder_name])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _handle(url: str) -> str:
    m = re.match(r"https?://github\.com/([A-Za-z0-9-]+)/?$", url or "")
    return m.group(1) if m else ""


def _distinctive(name: str) -> bool:
    """A bare name in prose identifies a symbol only if it cannot be an ordinary word or attribute."""
    if re.search(r"[a-z][A-Z]", name) and name[:1].isupper():   # CapWords: SensorEntity
        return True
    if name.isupper() and "_" in name:                           # CONSTANT_NAMES
        return True
    return name.startswith("async_") and name.count("_") >= 2    # async_track_state_change


def link_posts(posts: list[dict[str, Any]], symbols: dict[str, dict[str, Any]]) -> None:
    """Link developer-blog posts to the symbols they name in backticks (fully qualified, a dotted suffix,
    or a distinctive bare name that exactly one module defines)."""
    defined: dict[str, list[str]] = defaultdict(list)
    removed: dict[str, list[str]] = defaultdict(list)  # gone now, so their kind is unknown
    for key, s in symbols.items():
        if s["name"].startswith("_"):
            continue
        if s.get("kind") not in (None, "reexport"):
            defined[s["name"]].append(key)
        elif s.get("state") == "missing":
            removed[s["name"]].append(key)
    for name, keys in removed.items():
        if name not in defined and len(keys) == 1:  # e.g. async_track_state_change after its removal
            defined[name] = keys
    for post in posts:
        idents = post.get("idents")
        if idents is None:
            continue  # an older snapshot: keep its links
        linked: set[str] = set()
        for ident in idents:
            if ident in symbols:
                linked.add(ident)
            elif "." in ident:
                head = ident.split(".")[0]
                if _distinctive(head):
                    tail = [k for k, s in symbols.items() if k.endswith("." + ident) and s.get("kind") != "reexport"]
                    if 0 < len(tail) <= 2:
                        linked.update(tail)
            elif _distinctive(ident) and len(defined.get(ident, [])) == 1:
                linked.update(defined[ident])
        post["symbols"] = sorted(linked)


def integration_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for it in snap["integrations"]:
        rows.append({k: it.get(k, "") for k in ("id", "kind", "domain", "name", "repo", "url", "sha", "root",
                                                  "integration_type", "iot_class", "quality_scale", "last_commit",
                                                  "min_ha", "version")}
                    | {"n_files": len(it.get("files") or {}), "files_json": json.dumps(it.get("files") or {})})
    return rows


def use_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate uses per (integration, symbol); keep every site as [file, line, context]."""
    agg: dict[tuple[int, str], list[list[Any]]] = defaultdict(list)
    for idx, key, path, line, ctx in snap["uses"]:
        agg[(idx, key)].append([path, line, ctx])
    its = snap["integrations"]
    rows = []
    for (idx, key), sites in agg.items():
        sites.sort(key=lambda s: (-SEVERITY.get(s[2], 0), s[0], s[1]))
        rows.append({"it": its[idx]["id"], "sym": key, "context": sites[0][2], "n": len(sites),
                     "sites": json.dumps(sites[:20])})
    return rows


def symbol_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    users = None
    if "uses" in snap and snap["uses"]:
        users = defaultdict(lambda: {"core": set(), "external": set()})
        its = snap["integrations"]
        for idx, key, *_ in snap["uses"]:
            users[key][its[idx]["kind"]].add(idx)
    rows = []
    for key, s in snap["symbols"].items():
        d = s.get("deprecated") or {}
        u_core = len(users[key]["core"]) if users is not None else s.get("users_core", 0)
        u_ext = len(users[key]["external"]) if users is not None else s.get("users_external", 0)
        rows.append({
            "id": key, "module": s["module"], "name": s["name"], "kind": s.get("kind", ""),
            "state": s.get("state", "exists" if s.get("exists") else "missing"), "line": s.get("line", 0),
            "path": s.get("path") or "", "in_all": bool(s.get("in_all")), "private": s["name"].split(".")[0].startswith("_"),
            "reexport": s.get("kind") == "reexport", "origin": s.get("origin") or "",
            "deprecated": bool(d), "breaks_in": d.get("breaks_in") or "", "replacement": d.get("replacement") or "",
            "dep_how": d.get("how") or "", "release_date": d.get("release_date") or "",
            "removed_in": s.get("removed_in") or "", "last_seen_in": s.get("last_seen_in") or "",
            "deprecated_in": s.get("deprecated_in") or "", "added_in": s.get("added_in") or "",
            "never_existed": bool(s.get("never_existed")),
            "params_json": json.dumps(s.get("params")) if s.get("params") else "",
            "users_core": u_core, "users_external": u_ext,
            "via": s.get("via") or [],
        })
    return rows


async def _batch(rt: "Runtime", graph: str, cypher: str, rows: list[dict], size: int = 500) -> None:
    for i in range(0, len(rows), size):
        await rt.db.query(graph, cypher, {"rows": rows[i:i + size]}, emit=False)


KEEP_CHANGES = """
MATCH (c:Change)
OPTIONAL MATCH (c)-[r:CHANGES]->(s:Symbol)
RETURN c AS c, collect({id: s.id, how: r.how}) AS links"""


async def load_snapshot(rt: "Runtime", snap: dict[str, Any], tenant: str = TENANT) -> dict[str, int]:
    graph = live_graph(tenant)
    kept: list[dict[str, Any]] = []
    if await rt.db.exists(graph):  # past checks survive a snapshot refresh
        try:
            kept = await rt.db.query(graph, KEEP_CHANGES, emit=False, read_only=True)
        except Exception:
            kept = []
    await rt.db.delete(graph)
    await ensure_indexes(rt.db, graph, HA_INDEXES)

    releases = [{"id": r["version"], "date": r["date"], "tag": r.get("tag") or "", "expected": bool(r.get("expected")),
                 "is_next": bool(r.get("is_next")), "sha": r.get("sha") or ""} for r in snap["releases"]]
    await _batch(rt, graph, """UNWIND $rows AS r CREATE (:Release {id: r.id, date: r.date, tag: r.tag,
                 expected: r.expected, is_next: r.is_next, sha: r.sha})""", releases)

    its = integration_rows(snap)
    await _batch(rt, graph, """UNWIND $rows AS r CREATE (:Integration {id: r.id, kind: r.kind, domain: r.domain,
                 name: r.name, repo: r.repo, url: r.url, sha: r.sha, root: r.root, integration_type: r.integration_type,
                 iot_class: r.iot_class, quality_scale: r.quality_scale, last_commit: r.last_commit, min_ha: r.min_ha,
                 version: r.version, n_files: r.n_files, files_json: r.files_json})""", its)

    syms = symbol_rows(snap)
    await _batch(rt, graph, """UNWIND $rows AS r CREATE (:Symbol {id: r.id, module: r.module, name: r.name,
                 kind: r.kind, state: r.state, line: r.line, path: r.path, in_all: r.in_all, private: r.private,
                 reexport: r.reexport, origin: r.origin, deprecated: r.deprecated, breaks_in: r.breaks_in,
                 replacement: r.replacement, dep_how: r.dep_how, release_date: r.release_date,
                 removed_in: r.removed_in, last_seen_in: r.last_seen_in, deprecated_in: r.deprecated_in,
                 added_in: r.added_in, never_existed: r.never_existed, params_json: r.params_json,
                 users_core: r.users_core, users_external: r.users_external, via: r.via})""", syms)

    mods = [{"id": m, "path": meta.get("path") or "", "integration": meta.get("integration") or "",
             "owners": meta.get("owners") or []} for m, meta in snap["modules"].items()]
    await _batch(rt, graph, """UNWIND $rows AS r CREATE (m:Module {id: r.id, path: r.path, integration: r.integration})
                 WITH m, r MATCH (i:Integration {id: r.integration}) CREATE (i)-[:PROVIDES]->(m)""",
                 [m for m in mods if m["integration"]])
    await _batch(rt, graph, "UNWIND $rows AS r CREATE (:Module {id: r.id, path: r.path, integration: ''})",
                 [m for m in mods if not m["integration"]])
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (m:Module {id: r.module}), (s:Symbol {id: r.id})
                 CREATE (m)-[:EXPORTS]->(s)""", [{"module": s["module"], "id": s["id"]} for s in syms])

    # USES edges: stream from ha_uses.jsonl.gz if available, or fall back to in-memory snap["uses"]
    uses_streamed = False
    uses_candidates = [
        Path(__file__).resolve().parents[3] / "data" / "ha_uses.jsonl.gz",
        Path.cwd() / "data" / "ha_uses.jsonl.gz",
        Path("/app/data/ha_uses.jsonl.gz"),
    ]
    uses_file = next((str(p) for p in uses_candidates if p.is_file()), None)
    if uses_file and not snap.get("uses"):
        import gzip as _gz
        cypher_uses = """UNWIND $rows AS r MATCH (i:Integration {id: r.it}), (s:Symbol {id: r.sym})
                         CREATE (i)-[:USES {context: r.context, n: r.n, sites: r.sites}]->(s)"""
        batch = []
        with _gz.open(uses_file, "rt", encoding="utf-8") as f:
            for line in f:
                batch.append(json.loads(line))
                if len(batch) >= 800:
                    await rt.db.query(graph, cypher_uses, {"rows": batch}, emit=False)
                    batch.clear()
        if batch:
            await rt.db.query(graph, cypher_uses, {"rows": batch}, emit=False)
            batch.clear()
        uses_streamed = True
    elif snap.get("uses"):
        await _batch(rt, graph, """UNWIND $rows AS r MATCH (i:Integration {id: r.it}), (s:Symbol {id: r.sym})
                     CREATE (i)-[:USES {context: r.context, n: r.n, sites: r.sites}]->(s)""", use_rows(snap), 400)

    # manifest dependencies resolve by domain, preferring the core integration of that name
    core_by_domain = {it["domain"]: it["id"] for it in snap["integrations"] if it["kind"] == "core"}
    ext_by_domain = {it["domain"]: it["id"] for it in snap["integrations"] if it["kind"] == "external"}
    deps = []
    for it in snap["integrations"]:
        for after, names in ((False, it.get("deps") or []), (True, it.get("after_deps") or [])):
            for d in names:
                target = core_by_domain.get(d) or ext_by_domain.get(d)
                if target and target != it["id"]:
                    deps.append({"src": it["id"], "dst": target, "after": after})
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (a:Integration {id: r.src}), (b:Integration {id: r.dst})
                 CREATE (a)-[:DEPENDS_ON {after: r.after}]->(b)""", deps)

    people = sorted({o for it in snap["integrations"] for o in it.get("owners") or []}
                    | {o for m in mods for o in m["owners"]}
                    | {_handle(p.get("author_url", "")) for p in snap["posts"]} - {""})
    await _batch(rt, graph, "UNWIND $rows AS r CREATE (:Person {id: r.id, team: r.team})",
                 [{"id": p, "team": "/" in p} for p in people])
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (p:Person {id: r.p}), (i:Integration {id: r.i})
                 CREATE (p)-[:CODEOWNER]->(i)""",
                 [{"p": o, "i": it["id"]} for it in snap["integrations"] for o in it.get("owners") or []])
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (p:Person {id: r.p}), (m:Module {id: r.m})
                 CREATE (p)-[:CODEOWNER]->(m)""", [{"p": o, "m": m["id"]} for m in mods for o in m["owners"]])

    rel_ids = {r["id"] for r in releases}
    links = []
    for s in syms:
        for rel, field in (("REMOVED_IN", "removed_in"), ("DEPRECATED_IN", "deprecated_in")):
            if s[field] and s[field] in rel_ids:
                links.append({"s": s["id"], "r": s[field], "t": rel})
        short = ".".join(s["breaks_in"].split(".")[:2]) if s["breaks_in"] else ""
        if short in rel_ids:
            links.append({"s": s["id"], "r": short, "t": "BREAKS_IN"})
    for t in ("REMOVED_IN", "DEPRECATED_IN", "BREAKS_IN"):
        await _batch(rt, graph, f"""UNWIND $rows AS r MATCH (s:Symbol {{id: r.s}}), (rel:Release {{id: r.r}})
                     CREATE (s)-[:{t}]->(rel)""", [x for x in links if x["t"] == t])

    posts = snap["posts"]
    link_posts(posts, snap["symbols"])
    vecs = await rt.embedder.embed([f"{p['title']}. {p['summary']}" for p in posts]) if posts else []
    await _batch(rt, graph, """UNWIND $rows AS r CREATE (:Post {id: r.id, title: r.title, author: r.author,
                 author_handle: r.handle, date: r.date, url: r.url, summary: r.summary, versions: r.versions,
                 embedding: vecf32(r.embedding)})""",
                 [{"id": p["id"], "title": p["title"], "author": p["author"], "handle": _handle(p.get("author_url", "")),
                   "date": p["date"], "url": p["url"], "summary": p["summary"], "versions": p.get("versions", []),
                   "embedding": v} for p, v in zip(posts, vecs)], 200)
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (p:Post {id: r.p}), (s:Symbol {id: r.s})
                 CREATE (p)-[:MENTIONS]->(s)""", [{"p": p["id"], "s": s} for p in posts for s in p.get("symbols", [])])
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (p:Post {id: r.p}), (a:Person {id: r.a})
                 CREATE (p)-[:WRITTEN_BY]->(a)""",
                 [{"p": p["id"], "a": _handle(p.get("author_url", ""))} for p in posts if _handle(p.get("author_url", ""))])

    adrs = snap["adrs"]
    avecs = await rt.embedder.embed([f"{a['title']}. {a['summary']}" for a in adrs]) if adrs else []
    await _batch(rt, graph, """UNWIND $rows AS r CREATE (:Decision {id: r.id, title: r.title, status: r.status,
                 url: r.url, summary: r.summary, embedding: vecf32(r.embedding)})""",
                 [dict(a, embedding=v) for a, v in zip(({k: a[k] for k in ("id", "title", "status", "url", "summary")}
                                                         for a in adrs), avecs)])
    await _batch(rt, graph, """UNWIND $rows AS r MATCH (a:Decision {id: r.a}), (b:Decision {id: r.b})
                 CREATE (a)-[:SUPERSEDED_BY]->(b)""", [{"a": a["id"], "b": b} for a in adrs for b in a["superseded_by"]])

    for row in kept:
        props = {k: v for k, v in row["c"].items() if not k.startswith("_")}
        links = [l for l in row["links"] if l.get("id")]
        await rt.db.query(graph, """CREATE (c:Change) SET c = $props WITH c
                          UNWIND $links AS l MATCH (s:Symbol {id: l.id}) CREATE (c)-[:CHANGES {how: l.how}]->(s)""",
                          {"props": props, "links": links}, emit=False)

    m = snap["meta"]
    await rt.db.query(graph, """CREATE (:Meta {id: 'meta', tenant: $tenant, company: 'Home Assistant',
                      core_repo: $repo, core_sha: $sha, core_date: $date, generated_at: $gen, next_release: $next,
                      core_integrations: $nc, external_integrations: $ne, external_repos: $nr, uses: $uses,
                      signature: $sig, embed_backend: $eb, seeded_at: timestamp()})""",
                      {"tenant": tenant, "repo": m["core_repo"], "sha": m["core_sha"], "date": m["core_date"],
                       "gen": m["generated_at"], "next": m.get("next_release") or "", "nc": m["core_integrations"],
                       "ne": m["external_integrations"], "nr": m.get("external_repos", 0), "uses": m["uses"],
                       "sig": snapshot_signature(snap, rt.embedder.name), "eb": rt.embedder.name}, emit=False)
    counts = {"integrations": len(its), "symbols": len(syms), "dependencies": len(deps), "posts": len(posts),
              "decisions": len(adrs), "people": len(people)}
    log.info("loaded %s: %s", graph, counts)
    return counts
