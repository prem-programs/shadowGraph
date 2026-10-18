"""MCP tools for the Home Assistant workspace.

Two groups:
  pre-flight tools for any coding agent (Claude Code, Cursor, ...): ha_who_uses, ha_can_remove, ha_check_pr,
      ha_check_edits, ha_retirements, ha_draft_migration, ha_search. Read-only; nothing is ever posted.
  run tools used by ShadowGraph's own agents during a check: ha_open_change, ha_sim_apply, ha_sim_measure,
      ha_find_announcements, ha_find_reviewers, ha_post_report (plus the generic sim_fork/sim_drop and
      memory_* tools with tenant="ha").
"""
from __future__ import annotations

import time
from typing import Any

from ..core import memory
from ..core.sim import SimError, _check_shadow
from ..db import live_graph, memory_graph
from ..graph import queries as Q
from ..mcp_server import _call, server
from ..runtime import Runtime
from . import service
from .impact import change_id, save_change

TENANT = "ha"


def _runs(rt: Runtime) -> dict[str, dict[str, Any]]:
    runs = getattr(rt, "_ha_runs", None)
    if runs is None:
        runs = {}
        rt._ha_runs = runs  # type: ignore[attr-defined]
    return runs


def _run(rt: Runtime, run_id: str) -> dict[str, Any]:
    run = _runs(rt).get(run_id)
    if run is None:
        raise LookupError(f"no open change for run '{run_id}'; call ha_open_change first")
    return run


def _wrap(name: str, run_id: str, agent: str, args: dict[str, Any], fn):
    async def guarded(rt: Runtime, er: str):
        try:
            return await fn(rt, er)
        except service.NotAvailable as exc:
            raise ValueError(str(exc)) from exc
    return _call(name, TENANT, run_id, agent, args, guarded)


def _compact_consumer(c: dict[str, Any]) -> dict[str, Any]:
    u = c["uses"][0]
    return {"integration": c["name"], "id": c["id"], "kind": c["kind"], "level": c["level"], "effect": c["effect"],
            "file": u["file"], "line": u["line"], "url": u["url"], "symbol": u["symbol"],
            "last_commit": c.get("last_commit")}


def compact_report(r: dict[str, Any], limit: int = 15) -> dict[str, Any]:
    api = r["api"]
    return {
        "verdict": r["verdict"], "change": {k: r["change"].get(k) for k in ("number", "title", "author", "url", "state")},
        "next_release": r["next_release"],
        "removed": [{"name": s["id"], "users_external": s["users_external"], "users_core": s["users_core"],
                     "reexport": s.get("reexport")} for s in api["removed"] if not s.get("consequence_of")],
        "deprecated": [{"name": s["id"], "breaks_in": s.get("dep_breaks_in"), "replacement": s.get("dep_replacement")}
                       for s in api["deprecated"]],
        "signature": [{"name": s["id"], "changes": s["changes"]} for s in api["signature"]],
        "consumers": [_compact_consumer(c) for c in r["consumers"][:limit]],
        "consumers_total": len(r["consumers"]), "cascade": r["cascade"][:10],
        "announcements": [{"title": p["title"], "url": p["url"], "date": p["date"]} for p in r["posts"]],
        "reviewers": r["owners"][:6],
    }


# ============================================================================ pre-flight tools (any agent)

@server.tool(description="Home Assistant: who imports a core name, and what happens to each user if it is removed. "
                         "symbol is fully qualified, e.g. 'homeassistant.components.sensor.EntityCategory' or "
                         "'homeassistant.helpers.event.async_track_state_change'. Covers core integrations and "
                         "every HACS custom integration in the snapshot.")
async def ha_who_uses(symbol: str, limit: int = 25, run_id: str = "", agent: str = "") -> dict:
    async def fn(rt: Runtime, er: str) -> dict:
        d = await service.symbol_detail(rt, symbol)
        if d is None:
            hits = await service.search_symbols(rt, symbol.rsplit(".", 1)[-1], 8)
            raise LookupError(f"{symbol} is not a name anyone imports. Close matches: "
                              + ", ".join(h["id"] for h in hits) if hits else f"{symbol} not found")
        return {"symbol": d["symbol"]["id"], "state": d["symbol"].get("state"), "summary": d["summary"],
                "deprecated": bool(d["symbol"].get("deprecated")), "breaks_in": d["symbol"].get("breaks_in") or None,
                "users": [{"integration": u["name"], "id": u["id"], "kind": u["kind"], "if_removed": u["if_removed"],
                           "effect": u["effect"], "file": u["uses"][0]["file"] if u["uses"] else None,
                           "line": u["uses"][0]["line"] if u["uses"] else None,
                           "url": u["uses"][0]["url"] if u["uses"] else None} for u in d["users"][:limit]],
                "users_total": len(d["users"]), "alternatives": [a["id"] for a in d["alternatives"]],
                "announcements": [{"title": p["title"], "url": p["url"]} for p in d["posts"]]}
    return await _wrap("ha_who_uses", run_id, agent, {"symbol": symbol}, fn)


@server.tool(description="Home Assistant pre-flight before deleting or renaming a core name: can it be removed "
                         "today without breaking anyone? Returns yes/no, who would break (with file:line), the "
                         "deprecation status and where the name also lives.")
async def ha_can_remove(symbol: str, run_id: str = "", agent: str = "") -> dict:
    async def fn(rt: Runtime, er: str) -> dict:
        d = await service.symbol_detail(rt, symbol)
        if d is None:
            return {"symbol": symbol, "can_remove": True, "reason": "no scanned integration imports this name"}
        s, sm = d["symbol"], d["summary"]
        breaking = [u for u in d["users"] if u["if_removed"] == "breaks"]
        advice = ("Safe to remove: every user has a fallback or uses it only as a type hint." if not breaking else
                  f"Do not remove yet: {sm['would_break']} integrations would break "
                  f"({sm['would_break_external']} custom). "
                  + ("Deprecate it first with a removal version, then wait for users to migrate."
                     if not s.get("deprecated") else
                     f"It is deprecated until {s.get('breaks_in')}; {len(breaking)} users still need to migrate."))
        return {"symbol": s["id"], "can_remove": not breaking, "advice": advice, "summary": sm,
                "deprecated": bool(s.get("deprecated")), "breaks_in": s.get("breaks_in") or None,
                "would_break": [{"integration": u["name"], "kind": u["kind"], "effect": u["effect"],
                                 "file": u["uses"][0]["file"], "line": u["uses"][0]["line"],
                                 "url": u["uses"][0]["url"]} for u in breaking[:20]],
                "also_exported_by": [a["module"] for a in d["alternatives"]],
                "announcements": [{"title": p["title"], "url": p["url"]} for p in d["posts"]]}
    return await _wrap("ha_can_remove", run_id, agent, {"symbol": symbol}, fn)


@server.tool(description="Home Assistant: impact report for a pull request on home-assistant/core (open PRs are "
                         "read through GitHub's test-merge ref; merged ones are replayed from their commit). "
                         "Read-only: nothing is posted to GitHub.")
async def ha_check_pr(pr_number: int, run_id: str = "", agent: str = "") -> dict:
    async def fn(rt: Runtime, er: str) -> dict:
        r = await service.analyze_pr(rt, pr_number, run=er, agent=agent or "external")
        return compact_report(r) | {"change_id": change_id(r["change"])}
    return await _wrap("ha_check_pr", run_id, agent, {"pr_number": pr_number}, fn)


@server.tool(description="Home Assistant pre-flight for a change you are about to make: give the full new content "
                         "of each file you will edit (content=null deletes it), paths relative to the core repo "
                         "(e.g. 'homeassistant/helpers/event.py'). Returns who would break, before you commit.")
async def ha_check_edits(files: list[dict], title: str = "Planned change", run_id: str = "", agent: str = "") -> dict:
    async def fn(rt: Runtime, er: str) -> dict:
        edits = {}
        for f in files:
            path = (f.get("path") or "").lstrip("/")
            if not path:
                raise ValueError("every file needs a path")
            edits[path] = f.get("content")
        r = await service.analyze_edits(rt, edits, title, run=er, agent=agent or "external")
        return compact_report(r)
    return await _wrap("ha_check_edits", run_id, agent, {"files": [f.get("path") for f in files], "title": title}, fn)


@server.tool(description="Home Assistant retirement board: every deprecated core name with its removal release, "
                         "days left and how many integrations still use it. Optional filter by name or module.")
async def ha_retirements(filter: str = "", run_id: str = "", agent: str = "") -> dict:
    async def fn(rt: Runtime, er: str) -> dict:
        board = await service.retirements(rt)
        cards = [c for c in board["cards"] if not filter or filter.lower() in (c["family"] + c["module"]).lower()]
        return {"next_release": board["next_release"], "totals": board["totals"],
                "retirements": [{k: c[k] for k in ("family", "module", "breaks_in", "release_date", "days_left",
                                                   "users_core", "users_external", "blocking", "ready",
                                                   "replacements")} for c in cards[:40]]}
    return await _wrap("ha_retirements", run_id, agent, {"filter": filter}, fn)


@server.tool(description="Home Assistant: draft a migration patch for one integration that uses a removed or "
                         "deprecated name. Returns unified diffs to review; it never opens a PR or posts anything.")
async def ha_draft_migration(symbol: str, integration_id: str, run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_draft_migration", run_id, agent, {"symbol": symbol, "integration_id": integration_id},
                       lambda rt, er: service.draft_migration(rt, symbol, integration_id))


@server.tool(description="Home Assistant: find core names by partial name (e.g. 'EntityCategory', "
                         "'async_track'). Sorted by how many integrations use them.")
async def ha_search(query: str, run_id: str = "", agent: str = "") -> dict:
    async def fn(rt: Runtime, er: str) -> dict:
        return {"results": await service.search_symbols(rt, query, 20)}
    return await _wrap("ha_search", run_id, agent, {"query": query}, fn)


# ============================================================================ run tools (ShadowGraph's agents)

async def _open_change(rt: Runtime, er: str, number: int, sha: str, agent: str) -> dict:
    if sha:
        report = await service.analyze_commit(rt, sha, number=number or None, run=er, agent=agent, save=False)
    else:
        report = await service.analyze_pr(rt, number, run=er, agent=agent, save=False)
    api = report["api"]
    top = [s for s in api["removed"] if not s.get("consequence_of")]
    names = ", ".join(s["name"] for s in (top + api["deprecated"])[:6])
    text = (f"{report['change']['title']}. Core modules: {', '.join(report['modules'][:6]) or 'none'}. "
            f"Removed: {', '.join(s['name'] for s in top[:8]) or 'nothing'}. "
            f"Deprecated: {', '.join(s['name'] for s in api['deprecated'][:8]) or 'nothing'}.")
    episode_id = memory.new_id("ep")
    vec = await rt.embedder.embed_one(text)
    await rt.db.query(memory_graph(TENANT), Q.OPEN_EPISODE,
                      {"id": episode_id, "pr": int(report["change"].get("number") or 0),
                       "title": report["change"]["title"], "text": text, "embedding": vec, "run_id": er},
                      run_id=er, agent=agent, tool="ha_open_change", label="open episode")
    _runs(rt)[er] = {"report": report, "episode_id": episode_id, "results": {}, "opened": time.time()}
    rt.hub.emit(er, "episode_opened", agent=agent, tool="ha_open_change",
                summary=f"Episode {episode_id}: {report['change']['title']}")
    module = report["modules"][0] if report["modules"] else (report["domains"][0] if report["domains"] else "core")
    return {"episode_id": episode_id, "symptom_text": text,
            "target": {"target": f"{module}:{names or report['change']['title'][:60]}", "provider": module,
                       "route": names or report["change"]["title"][:60]},
            "preview": {"level": report["verdict"]["level"], "headline": report["verdict"]["headline"],
                        "removed": len(top), "deprecated": len(api["deprecated"]), "signature": len(api["signature"]),
                        "consumers": len(report["consumers"]), "modules": report["modules"][:10],
                        "domains": report["domains"][:10]}}


@server.tool(description="(ShadowGraph agents) Open a Home Assistant change for this run: fetch it read-only, diff "
                         "the API of both trees, find consumers, and open a memory episode. number = PR number; "
                         "sha = a merged commit to replay.")
async def ha_open_change(number: int = 0, sha: str = "", run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_open_change", run_id, agent, {"number": number, "sha": sha},
                       lambda rt, er: _open_change(rt, er, number, sha, agent))


async def _sim_apply(rt: Runtime, er: str, shadow: str, agent: str) -> dict:
    _check_shadow(TENANT, shadow)
    run = _run(rt, er)
    r = run["report"]
    kw = {"run_id": er, "agent": agent, "tool": "ha_sim_apply"}
    removed = [s["id"] for s in r["api"]["removed"]]
    deprecated = [{"id": s["id"], "breaks": s.get("dep_breaks_in") or ""} for s in r["api"]["deprecated"]]
    fixed = [c["id"] for c in r["consumers"] if c["kind"] == "core" and c.get("fixed_in_pr")]
    rows = await rt.db.query(shadow, """
        MATCH (s:Symbol) WHERE s.id IN $ids SET s.state = 'removed_by_change' RETURN count(s) AS n""",
                             {"ids": removed}, label="mark removed names", **kw)
    await rt.db.query(shadow, """
        UNWIND $rows AS d MATCH (s:Symbol {id: d.id})
        SET s.deprecated = true, s.deprecated_by_change = true, s.breaks_in = d.breaks""",
                      {"rows": deprecated}, label="mark deprecated names", **kw)
    if fixed:
        await rt.db.query(shadow, "MATCH (i:Integration) WHERE i.id IN $ids SET i.updated_in_change = true",
                          {"ids": fixed}, label="core integrations updated by the change itself", **kw)
    rt.hub.emit(er, "change_applied", agent=agent, tool="ha_sim_apply", graph=shadow,
                summary=f"In {shadow}: {rows[0]['n'] if rows else 0} names removed, {len(deprecated)} deprecated")
    return {"shadow": shadow, "removed": rows[0]["n"] if rows else 0, "deprecated": len(deprecated)}


@server.tool(description="(ShadowGraph agents) Apply the open change to a Home Assistant shadow graph: removed "
                         "names disappear, deprecated names get their removal version.")
async def ha_sim_apply(shadow: str, run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_sim_apply", run_id, agent, {"shadow": shadow},
                       lambda rt, er: _sim_apply(rt, er, shadow, agent))


async def _sim_measure(rt: Runtime, er: str, shadow: str, check: str, agent: str) -> dict:
    _check_shadow(TENANT, shadow)
    run = _run(rt, er)
    kw = {"run_id": er, "agent": agent, "tool": "ha_sim_measure"}
    if check == "consumers":
        rows = await rt.db.query(shadow, """
            MATCH (i:Integration)-[u:USES]->(s:Symbol {state: 'removed_by_change'})
            WHERE u.context IN ['load', 'runtime'] AND coalesce(i.updated_in_change, false) = false
            SET i.status = 'fails'
            RETURN i.kind AS kind, count(DISTINCT i) AS n""", label="integrations that lose a name they import",
                                 **kw)
        out = {r["kind"]: r["n"] for r in rows}
        soft = await rt.db.query(shadow, """
            MATCH (i:Integration)-[u:USES]->(s:Symbol {state: 'removed_by_change'})
            WHERE u.context IN ['shimmed', 'type', 'conditional']
            RETURN count(DISTINCT i) AS n""", label="users with a fallback or type-hint use", **kw)
        return {"check": check, "failing_external": out.get("external", 0), "failing_core": out.get("core", 0),
                "soft_users": soft[0]["n"] if soft else 0}
    if check == "cascade":
        r = run["report"]
        setup_fail = [c["id"] for c in r["consumers"]
                      if c["level"] == "breaks" and c["effect"].startswith("the integration fails")]
        await rt.db.query(shadow, "MATCH (i:Integration) WHERE i.id IN $ids SET i.status = 'fails_setup'",
                          {"ids": setup_fail}, label="integrations that fail to set up", **kw)
        total, rounds = 0, 0
        while rounds < 8:
            rows = await rt.db.query(shadow, """
                MATCH (a:Integration)-[d:DEPENDS_ON]->(b:Integration)
                WHERE d.after = false AND b.status IN ['fails_setup', 'fails_dependency'] AND a.status IS NULL
                SET a.status = 'fails_dependency'
                RETURN count(DISTINCT a) AS n""", label=f"propagate failures to dependents (round {rounds + 1})",
                                     **kw)
            n = rows[0]["n"] if rows else 0
            rounds += 1
            if not n:
                break
            total += n
        chains = await rt.db.query(shadow, """
            MATCH (a:Integration {status: 'fails_dependency'})-[:DEPENDS_ON]->(b:Integration)
            WHERE b.status IN ['fails_setup', 'fails_dependency']
            RETURN a.id AS id, a.name AS name, collect(b.id) AS because LIMIT 25""", label="dependency chains",
                                   read_only=True, **kw)
        return {"check": check, "fail_setup": len(setup_fail), "dependents_failing": total, "rounds": rounds,
                "chains": chains}
    if check == "deadline":
        rows = await rt.db.query(shadow, """
            MATCH (s:Symbol {deprecated_by_change: true})
            OPTIONAL MATCH (i:Integration)-[u:USES]->(s)
            WHERE u.context <> 'type'
            RETURN s.id AS id, s.breaks_in AS breaks_in, count(DISTINCT i) AS users,
                   count(DISTINCT CASE WHEN i.kind = 'external' THEN i END) AS external""",
                                 label="users of newly deprecated names (type hints excluded)", **kw)
        total = await rt.db.query(shadow, """
            MATCH (i:Integration)-[u:USES]->(s:Symbol {deprecated_by_change: true})
            WHERE u.context <> 'type'
            RETURN count(DISTINCT i) AS n""", label="distinct integrations to migrate", **kw)
        return {"check": check, "deprecated": rows, "users": total[0]["n"] if total else 0}
    raise SimError(f"unknown check '{check}' (use consumers, cascade or deadline)")


@server.tool(description="(ShadowGraph agents) Measure a Home Assistant shadow graph for one check: 'consumers' "
                         "(integrations that lose an import), 'cascade' (failures spread through manifest "
                         "dependencies) or 'deadline' (users of newly deprecated names).")
async def ha_sim_measure(shadow: str, check: str, run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_sim_measure", run_id, agent, {"shadow": shadow, "check": check},
                       lambda rt, er: _sim_measure(rt, er, shadow, check, agent))


ADR_SEARCH = """
CALL db.idx.vector.queryNodes('Decision', 'embedding', 3, vecf32($q)) YIELD node, score
RETURN node.id AS id, node.title AS title, node.status AS status, node.url AS url, score ORDER BY score"""
POST_SEARCH = """
CALL db.idx.vector.queryNodes('Post', 'embedding', 4, vecf32($q)) YIELD node, score
RETURN node.id AS id, node.title AS title, node.date AS date, node.url AS url, score ORDER BY score"""


async def _announcements(rt: Runtime, er: str, agent: str) -> dict:
    run = _run(rt, er)
    r = run["report"]
    kw = {"run_id": er, "agent": agent, "tool": "ha_find_announcements", "read_only": True}
    q = await rt.embedder.embed_one(r["change"]["title"] + ". " + " ".join(r["modules"][:5]))
    g = live_graph(TENANT)
    near_posts = await rt.db.query(g, POST_SEARCH, {"q": q}, label="similar developer blog posts", **kw)
    near_adrs = await rt.db.query(g, ADR_SEARCH, {"q": q}, label="similar architecture decisions", **kw)
    return {"linked_posts": [{k: p[k] for k in ("title", "url", "date")} for p in r["posts"]],
            "similar_posts": [p for p in near_posts if float(p["score"]) < 0.55][:3],
            "decisions": [a for a in near_adrs if float(a["score"]) < 0.5][:2]}


@server.tool(description="(ShadowGraph agents) Developer-blog posts and architecture decisions about the open "
                         "change: posts that name the affected symbols, plus the closest ones by meaning.")
async def ha_find_announcements(run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_find_announcements", run_id, agent, {}, lambda rt, er: _announcements(rt, er, agent))


async def _reviewers(rt: Runtime, er: str, agent: str) -> dict:
    r = _run(rt, er)["report"]
    authors = sorted({p.get("author_handle") or p.get("author") for p in r["posts"] if p.get("author_handle")})
    return {"codeowners": r["owners"], "announced_by": authors,
            "touched": [{"domain": t["domain"], "owners": t["owners"]} for t in r["touched"]][:10]}


@server.tool(description="(ShadowGraph agents) Who should review the open change: code owners of the touched "
                         "modules and integrations, and the authors of the announcements.")
async def ha_find_reviewers(run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_find_reviewers", run_id, agent, {}, lambda rt, er: _reviewers(rt, er, agent))


async def _post_report(rt: Runtime, er: str, results: dict[str, Any], stats: dict[str, Any], agent: str) -> dict:
    run = _run(rt, er)
    report = run["report"]
    report["checks"] = results
    report["agent_stats"] = stats
    cons = results.get("consumers", {}).get("measure") or {}
    if cons:
        engine = report["verdict"]["counts"]["external_breaks"]
        shadow = cons.get("failing_external", 0)
        report["cross_check"] = {"engine_external_breaks": engine, "shadow_external_breaks": shadow,
                                 "agree": engine == shadow}
    cid = await save_change(rt, report)
    v = report["verdict"]
    await memory.close_episode(rt, TENANT, run["episode_id"], v["level"], v["headline"], stats.get("steps", 0),
                               stats.get("ms", 0.0), agent=agent, emit_run=er)
    rt.hub.emit(er, "report_saved", agent=agent, tool="ha_post_report",
                summary=f"Saved {cid} ({v['level']}). Nothing was posted to GitHub: read-only by design.")
    return {"change_id": cid, "level": v["level"], "headline": v["headline"]}


@server.tool(description="(ShadowGraph agents) Save the run's impact report to the graph and close the episode. "
                         "Never posts to GitHub: the Home Assistant workspace is read-only.")
async def ha_post_report(results: dict, stats: dict, run_id: str = "", agent: str = "") -> dict:
    return await _wrap("ha_post_report", run_id, agent, {"checks": sorted(results)},
                       lambda rt, er: _post_report(rt, er, results, stats, agent))


def forget_run(rt: Runtime, run_id: str) -> dict[str, Any] | None:
    return _runs(rt).pop(run_id, None)


def report_of(rt: Runtime, run_id: str) -> dict[str, Any] | None:
    run = _runs(rt).get(run_id)
    return run["report"] if run else None

