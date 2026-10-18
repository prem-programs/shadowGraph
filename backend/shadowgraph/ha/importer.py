"""Build the Home Assistant snapshot: core API surface, who uses it (core + HACS), deprecations, history,
owners, developer-blog announcements and architecture decisions.

    python -m shadowgraph ha-import --core <ha-core clone> --hacs <folder of HACS clones> \
        [--devdocs <developers.home-assistant clone>] [--architecture <architecture clone>] \
        [--ref origin/dev] [--out data/ha_snapshot.json.gz]

The core clone may be shallow; release tags listed in --releases must be fetched (depth 1 is enough).
Everything is static analysis: no third-party code is imported or executed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import gzip
import json
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .surface import EXISTS, MISSING, UNKNOWN, Export, Resolver, Surface, module_name, module_surface
from .usage import scan_source

log = logging.getLogger("shadowgraph.ha.import")
SNAPSHOT_VERSION = 1
DEFAULT_RELEASES = ["2025.10.0", "2025.11.0", "2025.12.0", "2026.1.0", "2026.2.0", "2026.3.0", "2026.4.0",
                    "2026.5.0", "2026.6.0", "2026.7.0", "2026.8.0", "2026.9.0", "2026.10.0"]
CORE_REPO = "home-assistant/core"


# ---------------------------------------------------------------------------------------------- git helpers
class GitTree:
    """Read files of one commit from a local clone (one long-lived `git cat-file --batch` process)."""

    def __init__(self, repo: str, ref: str) -> None:
        self.repo, self.ref = repo, ref
        self.sha = self._git("rev-parse", ref).strip()
        self.date = self._git("log", "-1", "--format=%cI", ref).strip()
        self.files = [f for f in self._git("ls-tree", "-r", "--name-only", ref).split("\n") if f]
        self._proc: subprocess.Popen | None = None

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.repo, *args], capture_output=True, text=True, check=True).stdout

    def read(self, path: str) -> str | None:
        if self._proc is None:
            self._proc = subprocess.Popen(["git", "-C", self.repo, "cat-file", "--batch"],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(f"{self.sha}:{path}\n".encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().split()
        if len(header) < 3 or header[-1] == b"missing":
            return None
        size = int(header[2])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)
        return data.decode("utf-8", "replace")

    def read_many(self, paths: Iterable[str]) -> dict[str, str]:
        out = {}
        for p in paths:
            s = self.read(p)
            if s is not None:
                out[p] = s
        return out

    def close(self) -> None:
        if self._proc is not None:
            self._proc.stdin.close()  # type: ignore[union-attr]
            self._proc.wait(timeout=10)
            self._proc = None


class SurfaceStore:
    """Lazily parsed module surfaces of one core commit, plus a Resolver over them."""

    def __init__(self, tree: GitTree) -> None:
        self.tree = tree
        self.modules = py_modules(tree.files)
        self.cache: dict[str, Surface] = {}
        self.resolver = Resolver(self.get, self.is_package)
        self.trace: set[str] | None = None  # modules consulted while resolving one name

    def get(self, mod: str) -> Surface | None:
        if self.trace is not None and mod in self.modules:
            self.trace.add(mod)
        if mod in self.cache:
            return self.cache[mod]
        path = self.modules.get(mod)
        if path is None:
            return None
        src = self.tree.read(path)
        surf = module_surface(src or "", mod) if src is not None else None
        if surf is not None:
            self.cache[mod] = surf
        return surf

    def is_package(self, mod: str) -> bool:
        return self.modules.get(mod, "").endswith("__init__.py")


def py_modules(files: list[str]) -> dict[str, str]:
    """module name -> path, for every Python file under homeassistant/."""
    return {module_name(f): f for f in files if f.startswith("homeassistant/") and f.endswith(".py")}


# ---------------------------------------------------------------------------------------------- core parts
def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rules.append((parts[0], [p.lstrip("@") for p in parts[1:]]))
    return rules


def owners_for(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    """Last matching CODEOWNERS rule wins (GitHub semantics, simplified to the patterns HA uses)."""
    found: list[str] = []
    p = "/" + path
    for pattern, owners in rules:
        pat = pattern if pattern.startswith("/") else "/**/" + pattern
        if pat.endswith("/"):
            hit = p.startswith(pat) or fnmatch.fnmatch(p, pat + "*")
        else:
            hit = fnmatch.fnmatch(p, pat) or p.startswith(pat.rstrip("*") + "/")
        if hit:
            found = owners
    return found


def platform_values(const_src: str) -> list[str]:
    m = re.search(r"class (?:Platform|EntityPlatforms)\(StrEnum\):(.*?)(?:\n\S|\Z)", const_src, re.S)
    if not m:
        return []
    return re.findall(r'^\s+[A-Z_]+\s*=\s*"([a-z_]+)"', m.group(1), re.M)


def _rel_entries(files: dict[str, list[str]], root_prefix: str, platforms: set[str]) -> dict[str, list[str]]:
    """For each file of one integration: which entry points load it at import time.

    files: path -> module-level local imports (as 'a/b' relative to the integration root).
    Entry points: 'setup' (__init__.py), 'platform:<name>', 'config_flow', or 'module:<name>' for others.
    Importing any submodule first imports the package __init__, so 'setup' failures take everything down.
    """
    rel = {p[len(root_prefix):]: p for p in files}
    graph: dict[str, set[str]] = {}
    for rpath, path in rel.items():
        targets = set()
        for imp in files[path]:
            for cand in (imp + ".py", imp + "/__init__.py"):
                if cand in rel:
                    targets.add(cand)
        graph[rpath] = targets

    def entry_of(rpath: str) -> str:
        stem = rpath[:-3]
        if rpath == "__init__.py":
            return "setup"
        if stem in platforms:
            return f"platform:{stem}"
        if stem == "config_flow":
            return "config_flow"
        return f"module:{stem.replace('/__init__', '')}"

    entries: dict[str, set[str]] = defaultdict(set)
    for start in rel:
        e = entry_of(start)
        stack, seen = [start], set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            entries[cur].add(e)
            stack.extend(graph.get(cur, ()))
    out = {}
    for rpath, path in rel.items():
        es = entries.get(rpath, {entry_of(rpath)})
        if "setup" in es:
            es = {"setup"}
        out[path] = sorted(es)
    return out


# ---------------------------------------------------------------------------------------------- consumers
def scan_integration(sources: dict[str, str], root_prefix: str, known: set[str], own_module: str | None,
                     platforms: set[str]) -> tuple[list[tuple[str, str, int, str]], dict[str, list[str]], int]:
    """Return (uses as (symbol_key, path, line, context), file entries, files that failed to parse)."""
    uses: list[tuple[str, str, int, str]] = []
    local: dict[str, list[str]] = {}
    failed = 0
    for path, src in sources.items():
        rel = path[len(root_prefix):]
        pkg = rel.split("/")[:-1]
        scan = scan_source(src, known, pkg)
        if not scan.parsed:
            failed += 1
            local[path] = []
            continue
        local[path] = scan.local_imports
        for u in scan.uses:
            if own_module and (u.module == own_module or u.module.startswith(own_module + ".")):
                continue  # an integration using itself through absolute imports
            uses.append((u.key(), path, u.line, u.context))
    return uses, _rel_entries(local, root_prefix, platforms), failed


def find_external_roots(repo_dir: Path) -> list[tuple[Path, dict]]:
    """custom_components/<domain>/ folders (or the repo root when hacs.json says content_in_root)."""
    roots: list[tuple[Path, dict]] = []
    hacs = {}
    if (repo_dir / "hacs.json").is_file():
        try:
            hacs = json.loads((repo_dir / "hacs.json").read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            hacs = {}
    cc = repo_dir / "custom_components"
    if cc.is_dir():
        for d in sorted(cc.iterdir()):
            if d.is_dir() and any(d.glob("*.py")):
                roots.append((d, hacs))
    elif hacs.get("content_in_root") and any(repo_dir.glob("*.py")):
        roots.append((repo_dir, hacs))
    return roots


def read_tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if any(part in ("tests", "test") for part in rel.split("/")[:-1]) or p.name.startswith("test_"):
            continue
        try:
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


def git_head(repo_dir: Path) -> tuple[str, str]:
    try:
        out = subprocess.run(["git", "-C", str(repo_dir), "log", "-1", "--format=%H|%cI"], capture_output=True,
                             text=True, timeout=20).stdout.strip()
        sha, date = out.split("|", 1)
        return sha, date
    except Exception:
        return "", ""


# ---------------------------------------------------------------------------------------------- docs
FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def parse_posts(blog_dir: Path) -> list[dict[str, Any]]:
    posts = []
    for p in sorted(blog_dir.glob("*.md")):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})-(.+)\.md$", p.name)
        if not m:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = FRONT.match(text)
        meta: dict[str, str] = {}
        body = text
        if fm:
            body = text[fm.end():]
            for line in fm.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip('"').strip("'")
        paras = [x.strip() for x in re.split(r"\n\s*\n", re.sub(r"^#.*$", "", body, flags=re.M)) if x.strip()]
        summary = next((x for x in paras if not x.startswith(("```", "|", "<", "import ", ":::"))), "")
        y, mo, d, slug = m.groups()
        posts.append({
            "id": f"blog:{y}-{mo}-{d}-{slug}", "title": meta.get("title", slug), "author": meta.get("author", ""),
            "author_url": meta.get("authorURL", ""), "date": f"{y}-{mo}-{d}",
            "url": f"https://developers.home-assistant.io/blog/{y}/{mo}/{d}/{slug}",
            "summary": re.sub(r"\s+", " ", summary)[:600],
            "idents": sorted(set(re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", body))),
            "versions": sorted(set(re.findall(r"\b(20\d\d\.\d{1,2})\b", body))),
        })
    return posts


def parse_adrs(adr_dir: Path) -> list[dict[str, Any]]:
    adrs = []
    for p in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        num = p.name[:4]
        title_m = re.search(r"^#\s+(.+)$", text, re.M)
        status_m = re.search(r"^##\s+Status\s*\n+(.+?)(?:\n\s*\n|\n##)", text, re.M | re.S)
        status = re.sub(r"\s+", " ", status_m.group(1)).strip() if status_m else ""
        sup = sorted(set(re.findall(r"(\d{4})-[a-z0-9-]+\.md", status)))
        ctx_m = re.search(r"^##\s+Context\s*\n+(.+?)(?:\n##|\Z)", text, re.M | re.S)
        dec_m = re.search(r"^##\s+Decision\s*\n+(.+?)(?:\n##|\Z)", text, re.M | re.S)
        adrs.append({
            "id": f"ADR-{num}", "title": (title_m.group(1).strip() if title_m else p.stem),
            "status": status[:200], "superseded_by": [f"ADR-{s}" for s in sup if s != num and "uperseded" in status],
            "url": f"https://github.com/home-assistant/architecture/blob/master/adr/{p.name}",
            "summary": re.sub(r"\s+", " ", (dec_m or ctx_m).group(1)).strip()[:600] if (dec_m or ctx_m) else "",
        })
    return adrs


# ---------------------------------------------------------------------------------------------- releases
def first_wednesday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    return d + dt.timedelta(days=(2 - d.weekday()) % 7)


def release_date(version: str) -> str:
    """HA ships x.y.0 on the first Wednesday of month y (an expectation for future releases)."""
    y, m = version.split(".")[:2]
    return first_wednesday(int(y), int(m)).isoformat()


def short_version(v: str | None) -> str | None:
    if not v:
        return None
    parts = v.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else v


# ---------------------------------------------------------------------------------------------- main build
def build(core: str, hacs: str | None, devdocs: str | None, architecture: str | None, ref: str,
          releases: list[str], limit_hacs: int | None = None) -> dict[str, Any]:
    t0 = time.time()
    tree = GitTree(core, ref)
    modules = py_modules(tree.files)
    known = set(modules)
    histories = {}
    for tag in releases:
        try:
            histories[tag] = GitTree(core, tag)
        except subprocess.CalledProcessError:
            log.warning("release tag %s not available; skipping it in history", tag)
    for h in histories.values():
        known |= set(py_modules(h.files))
    known.add("homeassistant")
    log.info("core %s @ %s: %d modules", ref, tree.sha[:10], len(modules))

    meta_files = tree.read_many(["CODEOWNERS", "homeassistant/const.py",
                                 "homeassistant/generated/entity_platforms.py"])
    rules = parse_codeowners(meta_files.get("CODEOWNERS", ""))
    platforms = set(platform_values(meta_files.get("homeassistant/generated/entity_platforms.py", "")) or
                    platform_values(meta_files.get("homeassistant/const.py", "")))

    # -- core integrations
    comp_files: dict[str, list[str]] = defaultdict(list)
    manifests: list[str] = []
    for f in tree.files:
        m = re.match(r"homeassistant/components/([a-z0-9_]+)/(.+)$", f)
        if not m:
            continue
        if f.endswith(".py"):
            comp_files[m.group(1)].append(f)
        elif m.group(2) == "manifest.json":
            manifests.append(f)
    manifest_src = tree.read_many(manifests)
    integrations: list[dict[str, Any]] = []
    uses: list[list[Any]] = []
    parse_failures = {"core": 0, "external": 0}
    for domain in sorted(comp_files):
        mpath = f"homeassistant/components/{domain}/manifest.json"
        try:
            man = json.loads(manifest_src.get(mpath, "{}"))
        except ValueError:
            man = {}
        sources = tree.read_many(comp_files[domain])
        root = f"homeassistant/components/{domain}/"
        u, entries, failed = scan_integration(sources, root, known, f"homeassistant.components.{domain}", platforms)
        parse_failures["core"] += failed
        idx = len(integrations)
        integrations.append({
            "id": f"core:{domain}", "kind": "core", "domain": domain, "name": man.get("name", domain),
            "repo": CORE_REPO, "sha": tree.sha, "root": root,
            "url": f"https://github.com/{CORE_REPO}/tree/{tree.sha}/{root}",
            "owners": [o.lstrip("@") for o in man.get("codeowners", [])] or owners_for(root, rules),
            "deps": man.get("dependencies", []), "after_deps": man.get("after_dependencies", []),
            "integration_type": man.get("integration_type", ""), "iot_class": man.get("iot_class", ""),
            "quality_scale": man.get("quality_scale", ""), "last_commit": tree.date[:10], "min_ha": "",
            "files": {p: e for p, e in entries.items()},
        })
        uses += [[idx, k, p, line, c] for k, p, line, c in u]
    n_core = len(integrations)
    log.info("core integrations: %d, uses: %d", n_core, len(uses))

    # -- external (HACS) integrations
    if hacs:
        repos = sorted(d for d in Path(hacs).iterdir() if d.is_dir())
        if limit_hacs:
            repos = repos[:limit_hacs]
        for repo_dir in repos:
            owner, _, name = repo_dir.name.partition("_")
            full = f"{owner}/{name}"
            sha, date = git_head(repo_dir)
            for root, hacs_json in find_external_roots(repo_dir):
                man = {}
                if (root / "manifest.json").is_file():
                    try:
                        man = json.loads((root / "manifest.json").read_text(encoding="utf-8", errors="replace"))
                    except ValueError:
                        man = {}
                domain = man.get("domain") or root.name
                rel_root = "" if root == repo_dir else root.relative_to(repo_dir).as_posix() + "/"
                sources = {rel_root + k: v for k, v in read_tree(root).items()}
                u, entries, failed = scan_integration(sources, rel_root, known, None, platforms)
                parse_failures["external"] += failed
                idx = len(integrations)
                integrations.append({
                    "id": f"hacs:{full}" + ("" if root.name == domain or rel_root == "" else f"#{domain}"),
                    "kind": "external", "domain": domain, "name": man.get("name") or hacs_json.get("name") or domain,
                    "repo": full, "sha": sha, "root": rel_root,
                    "url": f"https://github.com/{full}",
                    "owners": [o.lstrip("@") for o in man.get("codeowners", [])],
                    "deps": man.get("dependencies", []), "after_deps": man.get("after_dependencies", []),
                    "integration_type": man.get("integration_type", ""), "iot_class": man.get("iot_class", ""),
                    "quality_scale": "", "version": str(man.get("version", "")), "last_commit": date[:10],
                    "min_ha": str(hacs_json.get("homeassistant", "") or ""),
                    "files": entries,
                })
                uses += [[idx, k, p, line, c] for k, p, line, c in u]
        # an external integration may override or duplicate a domain; ids stay unique per repo+domain
        seen_ids: dict[str, int] = {}
        for it in integrations[n_core:]:
            if it["id"] in seen_ids:
                seen_ids[it["id"]] += 1
                it["id"] = f"{it['id']}~{seen_ids[it['id']]}"
            else:
                seen_ids[it["id"]] = 0
        log.info("external integrations: %d, total uses: %d", len(integrations) - n_core, len(uses))

    # -- does each used name exist today? when did it disappear or get deprecated?
    mod_of_key: dict[str, tuple[str, str]] = {}
    for _, key, *_ in uses:
        if key not in mod_of_key:
            mod_of_key[key] = split_key(key, known)
    nxt = _next_version(short_version(releases[-1])) if releases else None
    store = SurfaceStore(tree)
    hist_stores = {tag: SurfaceStore(h) for tag, h in histories.items()}
    for mod, path in store.modules.items():  # every API module, so unused deprecations are listed too
        if _is_api_module(path):
            store.get(mod)

    def symbol_record(mod: str, name: str) -> dict[str, Any]:
        store.trace = set()
        state = store.resolver.state(mod, name)
        e = store.resolver.export(mod, name) if state == EXISTS else None
        via = sorted(store.trace)
        store.trace = None
        rec: dict[str, Any] = {"module": mod, "name": name, "state": state, "exists": state != MISSING,
                               "module_exists": mod in store.modules, "path": store.modules.get(mod),
                               "via": via if via != [mod] else None}
        if e is not None:
            rec.update(kind=e.kind, line=e.line, in_all=e.in_all, origin=e.origin, deprecated=e.deprecated,
                       params=e.params)
        present: list[tuple[str, str]] = []
        dep_seen = None
        for tag in releases:
            hstore = hist_stores.get(tag)
            if hstore is None:
                continue
            hstate = hstore.resolver.state(mod, name)
            present.append((tag, hstate))
            if hstate == EXISTS and dep_seen is None:
                he = hstore.resolver.export(mod, name)
                if he is not None and he.deprecated:
                    dep_seen = tag
        if state == MISSING:
            seen = [t for t, s in present if s == EXISTS]
            if seen:
                after = releases.index(seen[-1]) + 1
                rec["removed_in"] = short_version(releases[after]) if after < len(releases) else nxt
                rec["last_seen_in"] = short_version(seen[-1])
            else:
                rec["never_existed"] = True  # not in any release we looked at (typo, private or very old)
        if dep_seen:
            rec["deprecated_in"] = short_version(dep_seen)
        elif rec.get("deprecated"):
            rec["deprecated_in"] = nxt  # deprecated on the development branch, not released yet
        if state == EXISTS and present and all(s == MISSING for _, s in present):
            rec["added_in"] = nxt
        return {k: v for k, v in rec.items() if v is not None}

    symbols: dict[str, dict[str, Any]] = {}
    for i, (key, (mod, name)) in enumerate(mod_of_key.items()):
        symbols[key] = symbol_record(mod, name)
        if i and i % 5000 == 0:
            log.info("resolved %d/%d used names", i, len(mod_of_key))
    for mod, surf in list(store.cache.items()):
        for name, e in surf.exports.items():
            if e.deprecated:
                key = f"{mod}.{name}"
                if key not in symbols:
                    symbols[key] = symbol_record(mod, name)
    for rec in symbols.values():
        d = rec.get("deprecated")
        if d and d.get("breaks_in"):
            d["release_date"] = release_date(d["breaks_in"])
    for h in [tree, *histories.values()]:
        h.close()
    unparsed = sorted(m for m, s in store.cache.items() if not s.parsed)
    modules = store.modules

    # -- module owners (core paths) and the integration a component module belongs to
    mod_meta = {}
    for m in {s["module"] for s in symbols.values()}:
        path = modules.get(m)
        comp = re.match(r"homeassistant\.components\.([a-z0-9_]+)", m)
        mod_meta[m] = {"path": path, "integration": f"core:{comp.group(1)}" if comp else None,
                       "owners": owners_for(path, rules) if path else []}

    posts = parse_posts(Path(devdocs) / "blog") if devdocs and (Path(devdocs) / "blog").is_dir() else []
    link_posts(posts, symbols)
    adrs = parse_adrs(Path(architecture) / "adr") if architecture and (Path(architecture) / "adr").is_dir() else []

    rel_list = [{"version": short_version(t), "tag": t, "date": release_date(t),
                 "sha": histories[t].sha if t in histories else None} for t in releases if t in histories]
    future = sorted({s["deprecated"]["breaks_in"] for s in symbols.values()
                     if s.get("deprecated") and s["deprecated"].get("breaks_in")})
    known_versions = {r["version"] for r in rel_list}
    for v in future:
        if short_version(v) not in known_versions:
            rel_list.append({"version": short_version(v), "tag": None, "date": release_date(v), "sha": None,
                             "expected": True})
            known_versions.add(short_version(v))
    if nxt and nxt not in known_versions:
        rel_list.append({"version": nxt, "tag": None, "date": release_date(nxt), "sha": None, "expected": True,
                         "is_next": True})
    rel_list.sort(key=lambda r: tuple(int(x) for x in r["version"].split(".")))

    snapshot = {
        "version": SNAPSHOT_VERSION,
        "meta": {"core_repo": CORE_REPO, "core_ref": ref, "core_sha": tree.sha, "core_date": tree.date,
                 "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 "core_integrations": n_core, "external_integrations": len(integrations) - n_core,
                 "external_repos": len({i["repo"] for i in integrations[n_core:]}),
                 "uses": len(uses), "symbols": len(symbols), "parse_failures": parse_failures,
                 "unparsed_core_modules": unparsed[:50], "seconds": round(time.time() - t0, 1),
                 "next_release": nxt},
        "platforms": sorted(platforms),
        "modules": mod_meta,
        "symbols": symbols,
        "integrations": integrations,
        "uses": uses,
        "posts": [p for p in posts if p.get("symbols") or p["date"] >= "2025-06-01"],
        "adrs": adrs,
        "releases": rel_list,
        "core_files": sorted(p for p in tree.files if p.startswith("homeassistant/") and p.endswith(".py")),
    }
    log.info("snapshot built in %.1fs", time.time() - t0)
    return snapshot


def _next_version(v: str) -> str:
    y, m = (int(x) for x in v.split(".")[:2])
    return f"{y + 1}.1" if m == 12 else f"{y}.{m + 1}"


def _is_api_module(path: str) -> bool:
    return bool(re.match(r"homeassistant/(?:[a-z_]+\.py|helpers/.+\.py|util/.+\.py|"
                         r"components/[a-z0-9_]+/(?:__init__|const)\.py)$", path))


def split_key(key: str, known: set[str]) -> tuple[str, str]:
    """'homeassistant.components.sensor.SensorDeviceClass.TEMPERATURE' -> (module, 'SensorDeviceClass.TEMPERATURE')."""
    parts = key.split(".")
    for i in range(len(parts) - 1, 0, -1):
        mod = ".".join(parts[:i])
        if mod in known:
            return mod, ".".join(parts[i:])
    return ".".join(parts[:-1]), parts[-1]


def link_posts(posts: list[dict[str, Any]], symbols: dict[str, dict[str, Any]]) -> None:
    """A post is linked to a symbol when it names it in backticks: fully qualified, as `module.Name` suffix,
    or by a bare name that only one place defines (re-exports never match a bare name, so a post about
    `ConfigType` links to helpers.typing, not to every module that happens to import it)."""
    defined: dict[str, list[str]] = defaultdict(list)
    for key, s in symbols.items():
        if s.get("kind") not in (None, "reexport") and not s["name"].startswith("_"):
            defined[s["name"]].append(key)
    for post in posts:
        linked: set[str] = set()
        for ident in post["idents"]:
            if ident in symbols:
                linked.add(ident)
                continue
            if "." in ident:
                tail = [k for k in symbols if k.endswith("." + ident) and symbols[k].get("kind") != "reexport"]
                if 0 < len(tail) <= 2:
                    linked.update(tail)
                continue
            cands = defined.get(ident, [])
            if len(cands) == 1 and len(ident) >= 8:
                linked.update(cands)
        post["symbols"] = sorted(linked)


def save(snapshot: dict[str, Any], out: str) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(snapshot, separators=(",", ":"), default=str).encode()
    with gzip.open(out, "wb", compresslevel=9) as f:
        f.write(raw)
    log.info("wrote %s (%.1f MB, %.1f MB uncompressed)", out, os.path.getsize(out) / 1e6, len(raw) / 1e6)


def load_snapshot(path: str) -> dict[str, Any]:
    with gzip.open(path, "rb") as f:
        return json.loads(f.read())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="shadowgraph ha-import", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", required=True)
    ap.add_argument("--hacs")
    ap.add_argument("--devdocs")
    ap.add_argument("--architecture")
    ap.add_argument("--ref", default="origin/dev")
    ap.add_argument("--releases", default=",".join(DEFAULT_RELEASES))
    ap.add_argument("--limit-hacs", type=int)
    ap.add_argument("--out", default="data/ha_snapshot.json.gz")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    snap = build(a.core, a.hacs, a.devdocs, a.architecture, a.ref,
                 [r for r in a.releases.split(",") if r], a.limit_hacs)
    save(snap, a.out)
