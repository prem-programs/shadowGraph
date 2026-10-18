"""Impact of one change on everyone who imports Home Assistant core: who breaks, where, when, and why.

Inputs: a ChangeRef plus the base and head file trees (see sources.py). The graph supplies the consumers.
Every claim in the report is traceable: symbol state comes from static analysis of both trees, consumers
from the snapshot's USES edges (file, line, context), release dates from HA's monthly schedule.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from ..db import live_graph
from .importer import release_date, short_version
from .sources import ChangeRef, Tree
from .surface import EXISTS, MISSING, UNKNOWN, Export, Resolver, Surface, module_name, module_surface, signature_change
from .usage import scan_source

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger("shadowgraph.ha.impact")
TENANT = "ha"
SEVERITY = {"load": 5, "runtime": 4, "conditional": 3, "shimmed": 2, "type": 1}
LEVEL_RANK = {"breaks": 4, "may_break": 3, "warns": 2, "review": 2, "safe": 1}


class TreeSurfaces:
    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.mods = tree.modules()
        self.cache: dict[str, Surface | None] = {}
        self.resolver = Resolver(self.get, self.is_package)

    def get(self, mod: str) -> Surface | None:
        if mod not in self.cache:
            path = self.mods.get(mod)
            src = self.tree.read(path) if path else None
            self.cache[mod] = module_surface(src, mod) if src is not None else None
        return self.cache[mod]

    def is_package(self, mod: str) -> bool:
        return self.mods.get(mod, "").endswith("__init__.py")


# ---------------------------------------------------------------------------------------------- effects
def entry_text(entries: list[str], path: str) -> str:
    if "setup" in entries:
        return "the integration fails to set up"
    major = [e for e in entries if e.startswith(("platform:", "config_flow"))]
    parts = []
    for e in major or entries:
        kind, _, name = e.partition(":")
        if kind == "platform":
            parts.append(f"its {name} platform")
        elif kind == "config_flow":
            parts.append("its config flow")
        else:
            parts.append(f"{name.rsplit('/', 1)[-1]}.py")
    if not parts:
        return f"{path.rsplit('/', 1)[-1]} fails to import"
    names = list(dict.fromkeys(parts))
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    if major:
        return f"{joined} fail{'s' if len(names) == 1 else ''} to load"
    return f"{joined} fail{'s' if len(names) == 1 else ''} to import when used"


def removal_effect(context: str, path: str, entries: list[str]) -> tuple[str, str]:
    if context == "load":
        return "breaks", entry_text(entries, path)
    if context == "runtime":
        return "breaks", "raises an error when that code runs"
    if context == "conditional":
        return "may_break", "fails if that branch runs (inside a module-level `if`)"
    if context == "shimmed":
        return "safe", "has a fallback (try/except), keeps working"
    return "safe", "type hint only, no runtime effect (annotations are lazy in Python 3.14)"


def site_url(it: dict[str, Any], path: str, line: int) -> str:
    repo, sha = it.get("repo") or "", it.get("sha") or "dev"
    return f"https://github.com/{repo}/blob/{sha}/{path}#L{line}"


# ---------------------------------------------------------------------------------------------- queries
CANDIDATES = """
MATCH (s:Symbol)
WHERE s.module IN $mods OR any(m IN coalesce(s.via, []) WHERE m IN $mods)
RETURN s.id AS id, s.module AS module, s.name AS name, s.users_core AS users_core,
       s.users_external AS users_external"""

CONSUMERS = """
MATCH (i:Integration)-[u:USES]->(s:Symbol)
WHERE s.id IN $ids
RETURN s.id AS symbol, i.id AS id, i.kind AS kind, i.name AS name, i.domain AS domain, i.repo AS repo,
       i.url AS url, i.sha AS sha, i.last_commit AS last_commit, i.min_ha AS min_ha, i.files_json AS files_json,
       u.context AS context, u.n AS n, u.sites AS sites"""

CASCADE = """
MATCH p = (a:Integration)-[:DEPENDS_ON*1..6]->(b:Integration)
WHERE b.id IN $failed AND NOT a.id IN $failed AND all(r IN relationships(p) WHERE r.after = false)
WITH a, b, min(length(p)) AS hops
RETURN a.id AS id, a.name AS name, a.kind AS kind, a.repo AS repo, collect(DISTINCT b.id) AS because,
       min(hops) AS hops
ORDER BY hops, id LIMIT 200"""

TOUCHED = """
MATCH (b:Integration {kind: 'core'})
WHERE b.domain IN $domains
OPTIONAL MATCH (a:Integration)-[d:DEPENDS_ON]->(b)
OPTIONAL MATCH (p:Person)-[:CODEOWNER]->(b)
RETURN b.domain AS domain, b.name AS name, b.integration_type AS integration_type,
       count(DISTINCT CASE WHEN d.after = false AND a.kind = 'core' THEN a END) AS dependents_core,
       count(DISTINCT CASE WHEN d.after = false AND a.kind = 'external' THEN a END) AS dependents_external,
       collect(DISTINCT p.id) AS owners"""

REACH = """
MATCH (i:Integration)-[:USES]->(s:Symbol)
WHERE s.module IN $mods
RETURN i.kind AS kind, count(DISTINCT i) AS integrations"""

POSTS = """
MATCH (p:Post)-[:MENTIONS]->(s:Symbol)
WHERE s.id IN $ids
RETURN p.id AS id, p.title AS title, p.date AS date, p.url AS url, p.author AS author,
       p.author_handle AS author_handle, p.summary AS summary, collect(s.id) AS symbols
ORDER BY date DESC LIMIT 6"""

MODULE_OWNERS = """
MATCH (p:Person)-[:CODEOWNER]->(m:Module) WHERE m.id IN $mods
RETURN DISTINCT p.id AS id"""

SAME_MODULE_HISTORY = """
MATCH (s:Symbol) WHERE s.module IN $mods AND s.removed_in <> ''
RETURN s.id AS id, s.removed_in AS removed_in, s.users_external AS users_external
ORDER BY removed_in DESC LIMIT 8"""

NEXT_RELEASE = "MATCH (r:Release {is_next: true}) RETURN r.id AS version, r.date AS date"

SAVE_CHANGE = """
MERGE (c:Change {id: $id})
SET c.number = $number, c.title = $title, c.author = $author, c.url = $url, c.state = $state,
    c.base_sha = $base, c.head_sha = $head, c.level = $level, c.headline = $headline,
    c.analyzed_at = timestamp(), c.source = $source, c.files = $files, c.breaks = $breaks,
    c.warns = $warns, c.report_json = $report
WITH c
OPTIONAL MATCH (c)-[old:CHANGES]->()
DELETE old
WITH DISTINCT c
UNWIND $links AS l
MATCH (s:Symbol {id: l.id})
CREATE (c)-[:CHANGES {how: l.how}]->(s)"""


# ---------------------------------------------------------------------------------------------- diffing
def _api_paths(change: ChangeRef) -> list[tuple[str | None, str | None]]:
    """(base path, head path) for every changed Python file under homeassistant/ (not tests)."""
    out = []
    for f in change.files:
        p = f.path
        if not (p.startswith("homeassistant/") and p.endswith(".py")):
            continue
        if f.status == "added":
            out.append((None, p))
        elif f.status == "removed":
            out.append((p, None))
        elif f.status == "renamed":
            out.append((f.previous, p))
        else:
            out.append((p, p))
    return out


def _diff_symbols(base: TreeSurfaces, head: TreeSurfaces, candidates: list[dict[str, Any]],
                  pairs: list[tuple[str | None, str | None]]) -> dict[str, Any]:
    removed, deprecated, sig, restored, unknown = [], [], [], [], 0
    for c in candidates:
        mod, name = c["module"], c["name"]
        b = base.resolver.state(mod, name)
        h = head.resolver.state(mod, name)
        if UNKNOWN in (b, h):
            unknown += 1
            continue
        if b == EXISTS and h == MISSING:
            be = base.resolver.export(mod, name)
            removed.append({**c, "kind": be.kind if be else "", "reexport": bool(be and be.kind == "reexport"),
                            "in_all": bool(be and be.in_all), "private": name.split(".")[0].startswith("_"),
                            "origin": be.origin if be else None})
        elif b == MISSING and h == EXISTS:
            restored.append(c)
        elif b == EXISTS and h == EXISTS:
            be, he = base.resolver.export(mod, name), head.resolver.export(mod, name)
            if he is not None and he.deprecated and not (be is not None and be.deprecated):
                deprecated.append({**c, **{f"dep_{k}": v for k, v in he.deprecated.items()}})
            elif be is not None and he is not None and be.params and he.params:
                ch = signature_change(be.params, he.params)
                if ch:
                    sig.append({**c, "changes": ch})

    # `Class.member` uses of a removed class are consequences, not separate removals
    gone = {r["id"] for r in removed}
    for r in removed:
        parent = r["id"].rsplit(".", 1)[0]
        if "." in r["name"] and parent in gone:
            r["consequence_of"] = parent
    removed = [r for r in removed if not r.get("consequence_of")] + [r for r in removed if r.get("consequence_of")]

    # every public name removed from a changed module, used or not (the "nobody imports it" part)
    used = {c["id"] for c in candidates}
    removed_unused: list[str] = []
    new_deprecations: list[dict[str, Any]] = []
    added = 0
    for bpath, hpath in pairs:
        bmod = module_name(bpath) if bpath else None
        hmod = module_name(hpath) if hpath else None
        bs = base.get(bmod) if bmod else None
        hs = head.get(hmod) if hmod else None
        bex = bs.exports if bs else {}
        hex_ = hs.exports if hs else {}
        mod = hmod or bmod
        for n, e in bex.items():
            if n not in hex_ and not e.private and f"{bmod}.{n}" not in used and "." not in n:
                if e.kind != "reexport" or e.in_all:
                    removed_unused.append(f"{bmod}.{n}")
        for n, e in hex_.items():
            if n not in bex and not e.private and "." not in n and e.kind != "reexport":
                added += 1
            if e.deprecated and not (n in bex and bex[n].deprecated):
                key = f"{mod}.{n}"
                if key not in used:
                    new_deprecations.append({"id": key, "module": mod, "name": n, "users_core": 0, "users_external": 0,
                                             **{f"dep_{k}": v for k, v in e.deprecated.items()}})
    return {"removed": removed, "deprecated": deprecated + new_deprecations, "signature": sig,
            "restored": restored, "unknown": unknown, "removed_unused": sorted(removed_unused), "added": added}


def _still_uses(head: Tree, path: str, symbol: str, known: set[str]) -> bool | None:
    src = head.read(path)
    if src is None:
        return False  # the file was deleted or moved in this change
    scan = scan_source(src, known, path.split("/")[3:-1] if path.startswith("homeassistant/components/") else [])
    if not scan.parsed:
        return None
    return any(u.key() == symbol for u in scan.uses)


# ---------------------------------------------------------------------------------------------- main
async def analyze(rt: "Runtime", change: ChangeRef, base_tree: Tree, head_tree: Tree, *,
                  run: str | None = None, agent: str | None = None, save: bool = True) -> dict[str, Any]:
    t0 = time.perf_counter()
    graph = live_graph(TENANT)
    kw = {"run_id": run, "agent": agent, "read_only": True}
    pairs = _api_paths(change)
    mods = sorted({module_name(p) for pair in pairs for p in pair if p})
    domains = sorted({f.path.split("/")[2] for f in change.files
                      if f.path.startswith("homeassistant/components/") and f.path.count("/") >= 3})
    tests = sum(1 for f in change.files if f.path.startswith("tests/"))
    rt.hub.emit(run, "change_opened", agent=agent, summary=f"{len(change.files)} files, {len(mods)} core modules, "
                f"{len(domains)} integrations touched")

    candidates = await rt.db.query(graph, CANDIDATES, {"mods": mods}, label="names somebody imports from "
                                   "the changed modules", tool="impact_candidates", **kw) if mods else []
    base, head = TreeSurfaces(base_tree), TreeSurfaces(head_tree)
    diff = await asyncio.to_thread(_diff_symbols, base, head, candidates, pairs)
    rt.hub.emit(run, "api_diff", agent=agent,
                summary=f"{len(diff['removed'])} used names removed, {len(diff['deprecated'])} deprecated, "
                        f"{len(diff['signature'])} signatures changed ({len(candidates)} used names checked)")

    affected = {s["id"]: ("removed", s) for s in diff["removed"]}
    affected |= {s["id"]: ("deprecated", s) for s in diff["deprecated"]}
    affected |= {s["id"]: ("signature", s) for s in diff["signature"]}
    rows = await rt.db.query(graph, CONSUMERS, {"ids": list(affected)}, label="who uses the changed names",
                             tool="impact_consumers", **kw) if affected else []

    known = set(base.mods) | set(head.mods) | {"homeassistant"}
    touched_paths = {f.path for f in change.files}
    nxt = (await rt.db.query(graph, NEXT_RELEASE, **kw) or [{"version": "", "date": ""}])[0]
    consumers: dict[str, dict[str, Any]] = {}
    for r in rows:
        how, sym = affected[r["symbol"]]
        sites = json.loads(r["sites"] or "[]")
        files = json.loads(r["files_json"] or "{}")
        c = consumers.setdefault(r["id"], {
            "id": r["id"], "kind": r["kind"], "name": r["name"], "domain": r["domain"], "repo": r["repo"],
            "url": r["url"], "last_commit": r["last_commit"], "min_ha": r["min_ha"], "uses": [],
            "level": "safe", "effect": "", "fixed_in_pr": None})
        it = {"repo": r["repo"], "sha": r["sha"]}
        for path, line, ctx in sites:
            if how == "removed":
                level, text = removal_effect(ctx, path, files.get(path, []))
            elif how == "deprecated":
                brk = sym.get("dep_breaks_in")
                level = "warns"
                text = (f"logs a deprecation warning from {nxt['version'] or 'the next release'}"
                        + (f"; stops working in {short_version(brk)} ({_nice_date(release_date(brk))})" if brk else ""))
                if ctx == "type":
                    level, text = "safe", "type hint only, no warning"
            else:
                level, text = "review", "calls may need updating: " + "; ".join(sym.get("changes", []))
            c["uses"].append({"symbol": r["symbol"], "how": how, "file": path, "line": line, "context": ctx,
                              "level": level, "effect": text, "url": site_url(it, path, line)})
        if r["kind"] == "core" and how == "removed":
            paths = {s[0] for s in sites}
            if paths & touched_paths:
                still = [await asyncio.to_thread(_still_uses, head_tree, p, r["symbol"], known) for p in paths]
                c["fixed_in_pr"] = not any(still)
            else:
                c["fixed_in_pr"] = False
    for c in consumers.values():
        c["uses"].sort(key=lambda u: (-LEVEL_RANK[u["level"]], -SEVERITY.get(u["context"], 0), u["file"], u["line"]))
        if c["kind"] == "core" and c["fixed_in_pr"]:
            for u in c["uses"]:
                if u["how"] == "removed":
                    u["level"], u["effect"] = "safe", "updated in this change"
            c["uses"].sort(key=lambda u: -LEVEL_RANK[u["level"]])
        top = c["uses"][0]
        c["level"], c["effect"] = top["level"], top["effect"]
        c["symbols"] = sorted({u["symbol"] for u in c["uses"]})
    ordered = sorted(consumers.values(), key=lambda c: (-LEVEL_RANK[c["level"]], c["kind"] != "external",
                                                        -(int((c.get("last_commit") or "0").replace("-", "") or 0)),
                                                        c["name"].lower()))

    failed = [c["id"] for c in ordered if c["level"] == "breaks" and c["effect"].startswith("the integration fails")]
    cascade = await rt.db.query(graph, CASCADE, {"failed": failed}, label="who depends on the integrations "
                                "that would fail", tool="impact_cascade", **kw) if failed else []
    touched = await rt.db.query(graph, TOUCHED, {"domains": domains}, label="touched integrations and "
                                "their dependents", tool="impact_touched", **kw) if domains else []
    reach_rows = await rt.db.query(graph, REACH, {"mods": mods}, label="how many integrations import the "
                                   "changed modules", tool="impact_reach", **kw) if mods else []
    reach = {r["kind"]: r["integrations"] for r in reach_rows}
    ids = list(affected)
    posts = await rt.db.query(graph, POSTS, {"ids": ids}, label="announcements that mention these names",
                              tool="impact_posts", **kw) if ids else []
    owners_mod = [r["id"] for r in await rt.db.query(graph, MODULE_OWNERS, {"mods": mods}, **kw)] if mods else []
    history = await rt.db.query(graph, SAME_MODULE_HISTORY, {"mods": mods}, **kw) if mods else []

    report = {
        "change": {k: v for k, v in change.public().items() if k != "files"},
        "files": {"total": len(change.files), "core_python": len(pairs), "tests": tests,
                  "list": [f.path for f in change.files][:300]},
        "modules": mods, "domains": domains,
        "api": {k: diff[k] for k in ("removed", "deprecated", "signature", "restored", "unknown", "added")}
               | {"removed_unused": diff["removed_unused"][:50], "removed_unused_count": len(diff["removed_unused"]),
                  "checked": len(candidates)},
        "consumers": ordered, "cascade": cascade, "touched": touched,
        "reach": {"core": reach.get("core", 0), "external": reach.get("external", 0)},
        "posts": posts, "owners": sorted({o for t in touched for o in t["owners"]} | set(owners_mod)),
        "history": history, "next_release": nxt,
    }
    report["verdict"] = verdict(report)
    report["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    rt.hub.emit(run, "verdict", agent=agent, summary=report["verdict"]["headline"],
                level=report["verdict"]["level"])
    if save:
        await save_change(rt, report)
    for t in (base_tree, head_tree):
        close = getattr(t, "close", None)
        if close:
            close()
    return report


def _nice_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"  # portable (no %-d on Windows)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def verdict(r: dict[str, Any]) -> dict[str, Any]:
    ext = [c for c in r["consumers"] if c["kind"] == "external"]
    core = [c for c in r["consumers"] if c["kind"] == "core"]
    ext_break = [c for c in ext if c["level"] == "breaks"]
    ext_maybe = [c for c in ext if c["level"] == "may_break"]
    core_break = [c for c in core if c["level"] == "breaks"]
    warns = [c for c in r["consumers"] if c["level"] == "warns"]
    api = r["api"]
    nxt = r["next_release"].get("version") or "the next release"
    reasons: list[str] = []
    if ext_break or core_break:
        level = "breaks"
        who = ext_break[0] if ext_break else core_break[0]
        u = who["uses"][0]
        sym = u["symbol"].rsplit(".", 1)[-1] if "." in u["symbol"] else u["symbol"]
        n = len(ext_break)
        headline = (f"Breaks {_plural(n, 'custom integration')} on {nxt}: {who['name']} imports {sym} "
                    f"({u['file'].rsplit('/', 1)[-1]}:{u['line']})" if ext_break else
                    f"Core integration {who['name']} still uses {sym}: core CI should fail")
        if len(ext_break) > 1:
            headline += f" and {_plural(len(ext_break) - 1, 'other')}"
        top = [x for x in api["removed"] if not x.get("consequence_of")]
        reasons.append(f"{_plural(len(top), 'name')} that other code imports {'is' if len(top) == 1 else 'are'} removed")
    elif api["deprecated"]:
        level = "retires"
        users = len({c["id"] for c in warns})
        names = sorted({d["name"] for d in api["deprecated"]})
        prefix = os.path.commonprefix(names) if len(names) > 1 else ""
        cut = prefix.rfind("_")
        label = (prefix[: cut + 1] + "*" if cut > 0 else ", ".join(names[:3]) + ("…" if len(names) > 3 else "")) \
            if len(names) > 1 else names[0]
        brk = sorted({short_version(d.get("dep_breaks_in")) for d in api["deprecated"] if d.get("dep_breaks_in")})
        headline = (f"Starts retiring {label}"
                    + (f" ({_plural(len(names), 'name')})" if len(names) > 1 else "")
                    + f": {_plural(users, 'integration')} still use{'s' if users == 1 else ''} "
                    + ("them" if len(names) > 1 else "it")
                    + (f"; removal planned for {', '.join(brk)}" if brk else ""))
    elif ext_maybe or api["signature"]:
        level = "review"
        headline = (f"Signature change on {_plural(len(api['signature']), 'used function')}"
                    if api["signature"] else f"{_plural(len(ext_maybe), 'custom integration')} may break")
    elif api["removed"]:
        level = "clear"
        headline = (f"Removes {_plural(len(api['removed']), 'used name')}, but every user has a fallback, "
                    f"was updated in this change, or only uses it as a type hint")
    elif r["modules"]:
        level = "clear"
        reach = r["reach"]["core"] + r["reach"]["external"]
        headline = (f"No public API change in {_plural(len(r['modules']), 'core module')}"
                    + (f" ({_plural(reach, 'integration')} import{'s' if reach == 1 else ''} from "
                       f"{'it' if len(r['modules']) == 1 else 'them'})" if reach else ""))
    else:
        level = "clear"
        headline = "No Python change under homeassistant/" if not r["files"]["total"] else \
            "Touches no Home Assistant Python code"
    if ext_maybe:
        reasons.append(f"{_plural(len(ext_maybe), 'custom integration')} use{'s' if len(ext_maybe) == 1 else ''} it inside a conditional")
    if r["cascade"]:
        reasons.append(f"{_plural(len(r['cascade']), 'more integration')} depend on one that would fail to set up")
    if api["removed_unused_count"]:
        reasons.append(f"{_plural(api['removed_unused_count'], 'other name')} removed that nobody we scanned imports")
    if r["touched"]:
        big = [t for t in r["touched"] if (t["dependents_core"] + t["dependents_external"]) >= 5]
        for t in big[:2]:
            reasons.append(f"{t['name']} is a dependency of {t['dependents_core'] + t['dependents_external']} integrations")
    return {"level": level, "headline": headline, "reasons": reasons,
            "counts": {"external_breaks": len(ext_break), "external_may_break": len(ext_maybe),
                       "core_breaks": len(core_break), "warns": len(warns), "consumers": len(r["consumers"])}}


def change_id(change: dict[str, Any]) -> str:
    if change.get("number"):
        return f"PR-{change['number']}"
    raw = f"{change.get('base_sha')}..{change.get('head_sha')}..{change.get('title')}"
    return "local-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


async def save_change(rt: "Runtime", report: dict[str, Any]) -> str:
    ch = report["change"]
    cid = change_id(ch)
    links = [{"id": s["id"], "how": how} for how in ("removed", "deprecated", "signature")
             for s in report["api"][how]]
    v = report["verdict"]
    await rt.db.query(live_graph(TENANT), SAVE_CHANGE, {
        "id": cid, "number": ch.get("number") or 0, "title": ch.get("title") or "", "author": ch.get("author") or "",
        "url": ch.get("url") or "", "state": ch.get("state") or "", "base": ch.get("base_sha") or "",
        "head": ch.get("head_sha") or "", "level": v["level"], "headline": v["headline"],
        "source": ch.get("source") or "", "files": report["files"]["total"],
        "breaks": v["counts"]["external_breaks"], "warns": v["counts"]["warns"],
        "report": json.dumps(report, default=str), "links": links}, emit=False)
    return cid
