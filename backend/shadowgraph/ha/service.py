"""Home Assistant workspace operations shared by the API, the MCP server and the agents."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..db import live_graph
from . import impact
from .importer import load_snapshot as read_snapshot_file
from .load import TENANT, default_snapshot_path, load_snapshot, snapshot_signature
from .sources import ChangedFile, ChangeRef, GitCommitTree, GitHubSource, GitSource, RawTree
from .surface import module_name

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger("shadowgraph.ha")


class NotAvailable(RuntimeError):
    """The HA workspace cannot do this here (no snapshot, no source configured, ...)."""


class HAState:
    def __init__(self) -> None:
        self.snapshot_path = ""
        self.meta: dict[str, Any] = {}
        self.core_files: list[str] = []
        self.loaded = False
        self.source: GitSource | GitHubSource | None = None
        self.feed: dict[str, Any] = {"enabled": False, "last_poll": None, "last_error": None, "seen": {},
                                     "queue": [], "polls": 0}
        self.lock = asyncio.Lock()


def state(rt: "Runtime") -> HAState:
    st = getattr(rt, "_ha", None)
    if st is None:
        st = HAState()
        rt._ha = st  # type: ignore[attr-defined]
    return st


def snapshot_path(rt: "Runtime") -> str:
    return rt.settings.ha_snapshot or default_snapshot_path()


async def ensure_loaded(rt: "Runtime", force: bool = False) -> bool:
    """Load the snapshot into FalkorDB when the graph is missing or was built from another snapshot."""
    st = state(rt)
    path = snapshot_path(rt)
    if not Path(path).is_file():
        log.warning("Home Assistant snapshot not found at %s; the HA workspace is off "
                    "(build one with `python -m shadowgraph ha-import`, see docs/HOME_ASSISTANT.md)", path)
        st.loaded = False
        return False
    async with st.lock:
        graph = live_graph(TENANT)
        snap = None
        if not st.meta or st.snapshot_path != path:
            snap = await asyncio.to_thread(read_snapshot_file, path)
            st.meta, st.core_files, st.snapshot_path = snap["meta"], snap.get("core_files", []), path
        sig_wanted = None
        current = None
        if await rt.db.exists(graph):
            rows = await rt.db.query(graph, "MATCH (m:Meta {id: 'meta'}) RETURN m.signature AS sig", emit=False)
            current = rows[0]["sig"] if rows else None
        if snap is None and (force or current is None):
            snap = await asyncio.to_thread(read_snapshot_file, path)
        if snap is not None:
            sig_wanted = snapshot_signature(snap, rt.embedder.name)
        elif current:
            sig_wanted = current  # snapshot unchanged since we last read it
        if force or current != sig_wanted:
            if snap is None:
                snap = await asyncio.to_thread(read_snapshot_file, path)
            log.info("loading Home Assistant snapshot %s into %s", path, graph)
            await load_snapshot(rt, snap)
        st.loaded = True
        return True


def get_source(rt: "Runtime") -> GitSource | GitHubSource:
    st = state(rt)
    if st.source is not None:
        return st.source
    s = rt.settings
    clone = s.ha_clone
    want = s.ha_source
    if want == "git" or (want == "auto" and clone and Path(clone, ".git").exists()):
        if not clone or not Path(clone, ".git").exists():
            raise NotAvailable("HA_SOURCE=git needs HA_CLONE pointing at a clone of home-assistant/core")
        st.source = GitSource(clone, s.ha_repo)
    else:
        st.source = GitHubSource(s.ha_repo, s.github_token, st.core_files)
    return st.source


def _check_loaded(rt: "Runtime") -> None:
    if not state(rt).loaded:
        raise NotAvailable("the Home Assistant snapshot is not loaded (see docs/HOME_ASSISTANT.md)")


# --------------------------------------------------------------------------------------------- changes
async def analyze_pr(rt: "Runtime", number: int, *, run: str | None = None, agent: str | None = None,
                     save: bool = True) -> dict[str, Any]:
    """An open PR (or a merged one: then it is replayed from its squash commit)."""
    _check_loaded(rt)
    src = get_source(rt)
    rt.hub.emit(run, "fetch", agent=agent, summary=f"Fetching PR #{number} from {src.name} (read-only)")
    if isinstance(src, GitSource):
        try:
            change, base, head = await src.fetch_pr(number)
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or "").strip().splitlines()
            if not any("couldn't find remote ref" in line or "not our ref" in line for line in err):
                raise NotAvailable(f"git could not fetch PR #{number}: {err[-1] if err else exc}") from exc
            sha = await src.find_merged(number)  # no merge ref: closed or merged; replay it if it is on dev
            if not sha:
                raise NotAvailable(f"PR #{number} is not open and its squash commit is not in the local clone's "
                                   "dev history (deepen the clone, or set GITHUB_TOKEN to use the API)") from exc
            change, base, head = await src.fetch_commit(sha, number)
    else:
        change, base, head = await src.fetch_pr(number)
    return await impact.analyze(rt, change, base, head, run=run, agent=agent, save=save)


async def analyze_commit(rt: "Runtime", sha: str, *, number: int | None = None, run: str | None = None,
                         agent: str | None = None, save: bool = True) -> dict[str, Any]:
    _check_loaded(rt)
    src = get_source(rt)
    if not isinstance(src, GitSource):
        raise NotAvailable("replaying a commit needs the git source (set HA_CLONE)")
    change, base, head = await src.fetch_commit(sha, number)
    return await impact.analyze(rt, change, base, head, run=run, agent=agent, save=save)


class OverlayTree:
    """A base tree plus edited files: what the code would look like after an agent's planned change."""

    def __init__(self, base, edits: dict[str, str | None]) -> None:
        self.base, self.edits = base, edits
        self.sha = f"{base.sha}+edits"
        mods = dict(base.modules())
        for path, content in edits.items():
            if path.startswith("homeassistant/") and path.endswith(".py"):
                if content is None:
                    mods.pop(module_name(path), None)
                else:
                    mods[module_name(path)] = path
        self._mods = mods

    def read(self, path: str) -> str | None:
        return self.edits[path] if path in self.edits else self.base.read(path)

    def modules(self) -> dict[str, str]:
        return self._mods

    def close(self) -> None:
        close = getattr(self.base, "close", None)
        if close:
            close()


async def analyze_edits(rt: "Runtime", edits: dict[str, str | None], title: str = "Planned change", *,
                        run: str | None = None, agent: str | None = None, save: bool = False) -> dict[str, Any]:
    """Pre-flight for a change that does not exist yet (an AI agent's plan): new contents per path,
    None to delete. Compared against the snapshot's commit."""
    _check_loaded(rt)
    st = state(rt)
    sha = st.meta.get("core_sha", "")
    clone = rt.settings.ha_clone
    if clone and Path(clone, ".git").exists():
        base = GitCommitTree(clone, sha)
        try:
            base.modules()
        except Exception:
            base = None
    else:
        base = None
    if base is None:
        src = get_source(rt)
        http = src.http if isinstance(src, GitHubSource) else None
        if http is None:
            raise NotAvailable("pre-flight needs HA_CLONE or the GitHub source")
        base = RawTree(http, rt.settings.ha_repo, sha, {module_name(p): p for p in st.core_files})
        await base.prefetch([p for p in edits if p.endswith(".py")])
    files = []
    for path, content in edits.items():
        existed = base.read(path) is not None
        files.append(ChangedFile(path, "removed" if content is None else ("modified" if existed else "added")))
    change = ChangeRef(number=None, title=title, state="draft-local", base_sha=sha, head_sha="(planned)",
                       files=files, source="preflight")
    return await impact.analyze(rt, change, base, OverlayTree(base, edits), run=run, agent=agent, save=save)


CHANGES = """
MATCH (c:Change)
RETURN c.id AS id, c.number AS number, c.title AS title, c.author AS author, c.url AS url, c.state AS state,
       c.level AS level, c.headline AS headline, c.analyzed_at AS analyzed_at, c.files AS files,
       c.breaks AS breaks, c.warns AS warns, c.source AS source
ORDER BY c.analyzed_at DESC LIMIT $limit"""


async def list_changes(rt: "Runtime", limit: int = 100) -> list[dict[str, Any]]:
    _check_loaded(rt)
    return await rt.db.query(live_graph(TENANT), CHANGES, {"limit": limit}, emit=False, read_only=True)


async def get_change(rt: "Runtime", change_id: str) -> dict[str, Any] | None:
    _check_loaded(rt)
    rows = await rt.db.query(live_graph(TENANT), "MATCH (c:Change {id: $id}) RETURN c.report_json AS r",
                             {"id": change_id}, emit=False, read_only=True)
    return json.loads(rows[0]["r"]) if rows and rows[0]["r"] else None


# --------------------------------------------------------------------------------------------- overview
OVERVIEW = """
MATCH (m:Meta {id: 'meta'})
OPTIONAL MATCH (r:Release {is_next: true})
RETURN m.core_repo AS core_repo, m.core_sha AS core_sha, m.core_date AS core_date, m.generated_at AS generated_at,
       m.core_integrations AS core_integrations, m.external_integrations AS external_integrations,
       m.external_repos AS external_repos, m.uses AS uses, r.id AS next_release, r.date AS next_release_date"""

async def overview(rt: "Runtime") -> dict[str, Any]:
    st = state(rt)
    if not st.loaded:
        return {"loaded": False, "snapshot": snapshot_path(rt)}
    g = live_graph(TENANT)
    meta = (await rt.db.query(g, OVERVIEW, emit=False, read_only=True) or [{}])[0]
    counts = await rt.db.query(g, """MATCH (s:Symbol) RETURN count(s) AS symbols,
        sum(CASE WHEN s.deprecated THEN 1 ELSE 0 END) AS deprecated""", emit=False, read_only=True)
    changes = await rt.db.query(g, "MATCH (c:Change) RETURN count(c) AS n", emit=False, read_only=True)
    broken = await already_broken(rt)
    src = None
    try:
        src = get_source(rt).name
    except NotAvailable:
        pass
    feed = st.feed
    if counts:
        counts[0]["deprecated"] = int(counts[0].get("deprecated") or 0)
    try:
        families = len((await retirements(rt))["cards"])
    except Exception:
        families = None
    return {"loaded": True, **meta, **(counts[0] if counts else {}), "retirements": families, "changes": changes[0]["n"] if changes else 0,
            "already_broken": len({b["integration"]["id"] for b in broken["items"] if b["released"]}),
            "breaks_next": len({b["integration"]["id"] for b in broken["items"] if not b["released"]}),
            "source": src, "token": bool(rt.settings.github_token),
            "feed": {k: feed[k] for k in ("enabled", "last_poll", "last_error", "polls")} | {"queued": len(feed["queue"])}}


# --------------------------------------------------------------------------------------------- symbols
SYMBOL = """
MATCH (s:Symbol {id: $id})
OPTIONAL MATCH (p:Post)-[:MENTIONS]->(s)
WITH s, collect(DISTINCT {id: p.id, title: p.title, date: p.date, url: p.url, author: p.author}) AS posts
RETURN s AS symbol, posts"""

USERS = """
MATCH (i:Integration)-[u:USES]->(s:Symbol {id: $id})
RETURN i.id AS id, i.kind AS kind, i.name AS name, i.domain AS domain, i.repo AS repo, i.url AS url, i.sha AS sha,
       i.last_commit AS last_commit, i.min_ha AS min_ha, i.files_json AS files_json, u.context AS context,
       u.n AS n, u.sites AS sites
ORDER BY kind DESC, last_commit DESC"""

SEARCH = """
MATCH (s:Symbol)
WHERE toLower(s.name) CONTAINS toLower($q) OR toLower(s.id) CONTAINS toLower($q)
RETURN s.id AS id, s.module AS module, s.name AS name, s.state AS state, s.deprecated AS deprecated,
       s.breaks_in AS breaks_in, s.removed_in AS removed_in, s.users_core AS users_core,
       s.users_external AS users_external
ORDER BY s.users_core + s.users_external DESC LIMIT $limit"""


async def search_symbols(rt: "Runtime", q: str, limit: int = 20) -> list[dict[str, Any]]:
    _check_loaded(rt)
    q = q.strip()
    if len(q) < 2:
        return []
    return await rt.db.query(live_graph(TENANT), SEARCH, {"q": q, "limit": limit}, emit=False, read_only=True)


def _users(rows: list[dict[str, Any]], how: str = "removed") -> list[dict[str, Any]]:
    out = []
    for r in rows:
        sites = json.loads(r.get("sites") or "[]")
        files = json.loads(r.get("files_json") or "{}")
        it = {"repo": r["repo"], "sha": r["sha"]}
        uses = []
        for path, line, ctx in sites:
            level, text = impact.removal_effect(ctx, path, files.get(path, []))
            uses.append({"file": path, "line": line, "context": ctx, "if_removed": level,
                         "effect": text, "url": impact.site_url(it, path, line)})
        out.append({k: r[k] for k in ("id", "kind", "name", "domain", "repo", "url", "last_commit", "min_ha")}
                   | {"context": r["context"], "n": r["n"], "uses": uses,
                      "if_removed": uses[0]["if_removed"] if uses else "safe",
                      "effect": uses[0]["effect"] if uses else ""})
    return out


async def symbol_detail(rt: "Runtime", symbol_id: str) -> dict[str, Any] | None:
    _check_loaded(rt)
    g = live_graph(TENANT)
    rows = await rt.db.query(g, SYMBOL, {"id": symbol_id}, emit=False, read_only=True)
    if not rows:
        return None
    sym = {k: v for k, v in rows[0]["symbol"].items() if k not in ("via", "_labels")}
    posts = [p for p in rows[0]["posts"] if p.get("id")]
    users = _users(await rt.db.query(g, USERS, {"id": symbol_id}, emit=False, read_only=True))
    summary = {"core": sum(1 for u in users if u["kind"] == "core"),
               "external": sum(1 for u in users if u["kind"] == "external"),
               "would_break": sum(1 for u in users if u["if_removed"] == "breaks"),
               "would_break_external": sum(1 for u in users if u["if_removed"] == "breaks" and u["kind"] == "external"),
               "fallback_or_type_only": sum(1 for u in users if u["if_removed"] == "safe")}
    return {"symbol": sym, "posts": posts, "users": users, "summary": summary,
            "can_remove": summary["would_break"] == 0,
            "alternatives": await alternatives(rt, sym["name"], symbol_id)}


ALTERNATIVES = """
MATCH (s:Symbol {name: $name})
WHERE s.id <> $id AND s.state = 'exists' AND s.deprecated = false AND s.private = false
RETURN s.id AS id, s.module AS module, s.reexport AS reexport, s.users_core + s.users_external AS users
ORDER BY s.reexport, users DESC LIMIT 5"""


async def alternatives(rt: "Runtime", name: str, exclude: str) -> list[dict[str, Any]]:
    """Other modules that export the same name today (where a removed re-export still lives)."""
    return await rt.db.query(live_graph(TENANT), ALTERNATIVES, {"name": name, "id": exclude}, emit=False,
                             read_only=True)


# --------------------------------------------------------------------------------------------- retirements
DEPRECATED = """
MATCH (s:Symbol {deprecated: true})
OPTIONAL MATCH (i:Integration)-[u:USES]->(s)
OPTIONAL MATCH (p:Post)-[:MENTIONS]->(s)
RETURN s.id AS id, s.module AS module, s.name AS name, s.breaks_in AS breaks_in, s.release_date AS release_date,
       s.replacement AS replacement, s.dep_how AS how, s.deprecated_in AS deprecated_in, s.path AS path,
       s.line AS line,
       collect(DISTINCT CASE WHEN i IS NULL THEN NULL ELSE i.id + '|' + i.kind + '|' + u.context END) AS users,
       collect(DISTINCT CASE WHEN p IS NULL THEN NULL ELSE p.id END) AS posts"""


def _family(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    prefix = os.path.commonprefix(names)
    cut = prefix.rfind("_")
    return (prefix[: cut + 1] + "*") if cut > 0 else ", ".join(sorted(names)[:3]) + ("…" if len(names) > 3 else "")


async def retirements(rt: "Runtime") -> dict[str, Any]:
    _check_loaded(rt)
    g = live_graph(TENANT)
    rows = await rt.db.query(g, DEPRECATED, emit=False, read_only=True)
    post_rows = await rt.db.query(g, "MATCH (p:Post) WHERE (p)-[:MENTIONS]->(:Symbol {deprecated: true}) "
                                  "RETURN p.id AS id, p.title AS title, p.url AS url, p.date AS date, "
                                  "p.author AS author", emit=False, read_only=True)
    posts = {p["id"]: p for p in post_rows}
    nxt = (await rt.db.query(g, impact.NEXT_RELEASE, emit=False, read_only=True) or [{}])[0]
    # a family = deprecations declared together: same module, removal version and mechanism, adjacent lines
    groups: dict[tuple, list[dict[str, Any]]] = {}
    keyed: dict[tuple, list[dict[str, Any]]] = {}
    for r in rows:
        keyed.setdefault((r["module"], r["breaks_in"] or "", r["how"] or "", "." in r["name"]), []).append(r)
    for key, items in keyed.items():
        items.sort(key=lambda x: x["line"] or 0)
        n = 0
        prev = None
        for it in items:
            if prev is not None and (key[3] or (it["line"] or 0) - (prev["line"] or 0) > 8):
                n += 1
            groups.setdefault(key + (n,), []).append(it)
            prev = it
    today = dt.date.today()
    cards = []
    for (mod, breaks, how, _member, _n), items in groups.items():
        users: dict[str, dict[str, Any]] = {}
        for it in items:
            for u in it["users"]:
                iid, kind, ctx = u.split("|")
                cur = users.setdefault(iid, {"id": iid, "kind": kind, "contexts": set(), "symbols": set()})
                cur["contexts"].add(ctx)
                cur["symbols"].add(it["id"])
        blocking = [u for u in users.values() if u["contexts"] & {"load", "runtime", "conditional"}]
        date = items[0]["release_date"] or ""
        days = (dt.date.fromisoformat(date) - today).days if date else None
        card_posts = sorted({p for it in items for p in it["posts"]})
        status = ("ready" if not blocking else "overdue" if days is not None and days < 0 else "migrating")
        cards.append({
            "status": status,
            "id": f"{mod}:{_family(sorted(i['name'] for i in items))}",
            "family": _family(sorted(i["name"] for i in items)), "module": mod, "how": how,
            "names": sorted(i["name"] for i in items), "symbols": sorted(i["id"] for i in items),
            "replacements": sorted({i["replacement"] for i in items if i["replacement"]}),
            "breaks_in": breaks, "release_date": date, "days_left": days,
            "deprecated_in": min((i["deprecated_in"] for i in items if i["deprecated_in"]), default=""),
            "path": items[0]["path"], "line": min(i["line"] for i in items),
            "users_core": sum(1 for u in users.values() if u["kind"] == "core"),
            "users_external": sum(1 for u in users.values() if u["kind"] == "external"),
            "blocking": len(blocking), "blocking_external": sum(1 for u in blocking if u["kind"] == "external"),
            "condition": "Remove when no integration still uses it",
            "ready": len(blocking) == 0,
            "posts": [posts[p] for p in card_posts if p in posts],
        })
    cards.sort(key=lambda c: (c["release_date"] or "9999", -c["blocking"], c["family"]))
    return {"next_release": nxt, "cards": cards,
            "totals": {"families": len(cards), "names": len(rows),
                       "ready": sum(1 for c in cards if c["ready"])}}


async def retirement_users(rt: "Runtime", symbols: list[str]) -> list[dict[str, Any]]:
    _check_loaded(rt)
    g = live_graph(TENANT)
    out: dict[str, dict[str, Any]] = {}
    for sid in symbols:
        for u in _users(await rt.db.query(g, USERS, {"id": sid}, emit=False, read_only=True)):
            cur = out.setdefault(u["id"], {**{k: u[k] for k in ("id", "kind", "name", "repo", "url", "last_commit",
                                                                 "min_ha")}, "uses": []})
            cur["uses"] += [dict(x, symbol=sid) for x in u["uses"]]
    rank = {"breaks": 0, "may_break": 1, "safe": 2}
    users = list(out.values())
    for u in users:
        u["uses"].sort(key=lambda x: rank.get(x["if_removed"], 3))
        u["if_removed"] = u["uses"][0]["if_removed"] if u["uses"] else "safe"
    users.sort(key=lambda u: (rank.get(u["if_removed"], 3), u["kind"] != "external", u["name"].lower()))
    return users


BROKEN = """
MATCH (i:Integration {kind: 'external'})-[u:USES]->(s:Symbol {state: 'missing'})
WHERE u.context IN ['load', 'runtime']
RETURN i.id AS id, i.name AS name, i.repo AS repo, i.url AS url, i.sha AS sha, i.last_commit AS last_commit,
       i.min_ha AS min_ha, i.files_json AS files_json, s.id AS symbol, s.removed_in AS removed_in,
       s.never_existed AS never_existed, u.context AS context, u.sites AS sites
ORDER BY last_commit DESC"""


async def already_broken(rt: "Runtime") -> dict[str, Any]:
    """External integrations that import names core no longer has (at the snapshot's commit)."""
    g = live_graph(TENANT)
    rows = await rt.db.query(g, BROKEN, emit=False, read_only=True)
    nxt = (await rt.db.query(g, impact.NEXT_RELEASE, emit=False, read_only=True) or [{"version": ""}])[0]
    items = []
    for r in rows:
        sites = [s for s in json.loads(r["sites"] or "[]") if s[2] in ("load", "runtime")]
        files = json.loads(r["files_json"] or "{}")
        it = {"repo": r["repo"], "sha": r["sha"]}
        path, line, ctx = sites[0]
        level, text = impact.removal_effect(ctx, path, files.get(path, []))
        released = bool(r["removed_in"]) and r["removed_in"] != nxt.get("version") or bool(r["never_existed"])
        items.append({"integration": {k: r[k] for k in ("id", "name", "repo", "url", "last_commit", "min_ha")},
                      "symbol": r["symbol"], "removed_in": r["removed_in"], "never_existed": r["never_existed"],
                      "released": released, "file": path, "line": line, "context": ctx, "effect": text,
                      "url": impact.site_url(it, path, line)})
    return {"next_release": nxt, "items": items}


# --------------------------------------------------------------------------------------------- drafts
async def draft_migration(rt: "Runtime", symbol_id: str, integration_id: str) -> dict[str, Any]:
    """A patch the consumer's maintainer could apply. Shown only; never posted, never pushed."""
    from . import migrate
    from .sources import raw_file

    detail = await symbol_detail(rt, symbol_id)
    if detail is None:
        raise NotAvailable(f"unknown name {symbol_id}")
    user = next((u for u in detail["users"] if u["id"] == integration_id), None)
    if user is None:
        raise NotAvailable(f"{integration_id} does not use {symbol_id}")
    st = state(rt)
    known = {module_name(p) for p in st.core_files} | {"homeassistant"}
    plan = migrate.plan_for(detail["symbol"], detail["alternatives"], known)
    rows = await rt.db.query(live_graph(TENANT), "MATCH (i:Integration {id: $id}) RETURN i.repo AS repo, i.sha AS sha",
                             {"id": integration_id}, emit=False, read_only=True)
    repo, sha = rows[0]["repo"], rows[0]["sha"]
    files = sorted({u["file"] for u in user["uses"]})
    patches, notes = [], [plan.note] if plan.note else []
    if plan.kind != "manual":
        for path in files:
            src = None
            if user["kind"] == "core" and rt.settings.ha_clone and Path(rt.settings.ha_clone, ".git").exists():
                tree = GitCommitTree(rt.settings.ha_clone, sha)
                src = tree.read(path)
                tree.close()
            if src is None:
                try:
                    src = await raw_file(repo, sha, path)
                except Exception as exc:  # network
                    notes.append(f"could not fetch {path}: {exc}")
                    continue
            if src is None:
                notes.append(f"could not fetch {path} at {sha[:10]}")
                continue
            new, n = migrate.apply_plan(src, plan, known)
            notes += n
            problem = migrate.validate(new, symbol_id, known) if new is not None else "no automatic edit"
            if problem:
                notes.append(f"{path}: {problem}; fix it by hand")
                continue
            patches.append({"file": path, "diff": migrate.unified(src, new, path),
                            "url": f"https://github.com/{repo}/blob/{sha}/{path}"})
    return {"symbol": symbol_id, "integration": {k: user[k] for k in ("id", "name", "kind", "repo", "url")},
            "plan": {"kind": plan.kind, "new_module": plan.new_module, "new_expr": plan.new_expr},
            "patches": patches, "notes": [n for n in notes if n], "posted": False,
            "disclaimer": "Draft only. ShadowGraph never posts, comments or opens pull requests on other repositories."}
