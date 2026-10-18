#!/usr/bin/env python3
"""Download everything the Home Assistant snapshot is built from (read-only clones), then build it.

    python scripts/fetch_ha_sources.py --dir ../ha-sources            # fetch (about 3 GB, 15-25 minutes)
    python scripts/fetch_ha_sources.py --dir ../ha-sources --build    # fetch, then write data/ha_snapshot.json.gz

What it fetches (all public, all read-only):
  core/          home-assistant/core, dev branch since --since, plus the monthly release tags (depth 1)
  default/       hacs/default (the list of HACS custom integrations)
  hacs/          every listed integration: latest commit, Python files and manifests only (sparse, blobless)
  devdocs/       home-assistant/developers.home-assistant, blog/ only
  architecture/  home-assistant/architecture (ADRs)
Works on Windows, macOS and Linux (needs git 2.27+ and Python 3.11+). Re-running only fetches what is missing.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

GH = "https://github.com/"


def git(*args: str, cwd: Path | None = None, timeout: int = 900, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=check, env=env)


def release_tags(today: dt.date, months: int = 13) -> list[str]:
    tags = []
    y, m = today.year, today.month
    for _ in range(months):
        tags.append(f"{y}.{m}.0")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(tags, key=lambda t: tuple(int(x) for x in t.split(".")))


def fetch_core(root: Path, since: str, tags: list[str]) -> list[str]:
    core = root / "core"
    if not (core / ".git").exists():
        print("cloning home-assistant/core (dev, shallow since", since, ")")
        git("clone", "-q", f"--shallow-since={since}", "--branch", "dev", "--single-branch",
            GH + "home-assistant/core", str(core), timeout=3600)
    else:
        git("fetch", "-q", f"--shallow-since={since}", "origin", "dev", cwd=core, timeout=3600)
    have = []
    for t in tags:  # one at a time: concurrent fetches fight over the shallow lock
        r = git("fetch", "-q", "--depth=1", "origin", "tag", t, cwd=core, check=False)
        if r.returncode == 0:
            have.append(t)
        else:
            print(f"  tag {t} not available yet; skipped")
    return have


def clone_simple(root: Path, name: str, repo: str, sparse: list[str] | None = None) -> None:
    dest = root / name
    if (dest / ".git").exists():
        git("pull", "-q", "--ff-only", cwd=dest, check=False)
        return
    print("cloning", repo)
    args = ["clone", "-q", "--depth=1"]
    if sparse:
        args += ["--filter=blob:none", "--no-checkout"]
    git(*args, GH + repo, str(dest), timeout=1800)
    if sparse:
        git("sparse-checkout", "set", "--no-cone", *sparse, cwd=dest)
        git("checkout", "-q", cwd=dest)


def clone_integration(root: Path, full: str) -> str:
    dest = root / full.replace("/", "_", 1)
    if dest.exists():
        return "kept"
    try:
        git("clone", "-q", "--depth=1", "--filter=blob:none", "--no-checkout", GH + full, str(dest), timeout=120)
        git("sparse-checkout", "set", "--no-cone", "custom_components/**/*.py", "**/manifest.json", "hacs.json",
            "*.py", cwd=dest, timeout=60)
        git("checkout", "-q", cwd=dest, timeout=120)
        return "cloned"
    except Exception:
        import shutil
        shutil.rmtree(dest, ignore_errors=True)
        return "failed"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="../ha-sources", help="where to put the clones (outside the repo)")
    ap.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=14)).isoformat(),
                    help="how much dev history to keep (for replaying merged PRs)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="only the first N HACS integrations (for a quick try)")
    ap.add_argument("--build", action="store_true", help="build the snapshot afterwards")
    ap.add_argument("--out", default="", help="snapshot path (default: data/ha_snapshot.json.gz in this repo)")
    a = ap.parse_args()
    root = Path(a.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    tags = fetch_core(root, a.since, release_tags(dt.date.today()))
    clone_simple(root, "default", "hacs/default")
    clone_simple(root, "devdocs", "home-assistant/developers.home-assistant", sparse=["blog/**"])
    clone_simple(root, "architecture", "home-assistant/architecture")
    listed = json.loads((root / "default" / "integration").read_text(encoding="utf-8"))
    if a.limit:
        listed = listed[: a.limit]
    hacs = root / "hacs"
    hacs.mkdir(exist_ok=True)
    print(f"cloning {len(listed)} HACS integrations with {a.workers} workers")
    done = {"cloned": 0, "kept": 0, "failed": 0}
    with cf.ThreadPoolExecutor(a.workers) as pool:
        for i, result in enumerate(pool.map(lambda r: clone_integration(hacs, r), listed), 1):
            done[result] += 1
            if i % 200 == 0:
                print(f"  {i}/{len(listed)} {done}")
    print("HACS:", done)

    if a.build:
        repo = Path(__file__).resolve().parents[1]
        cmd = [sys.executable, "-m", "shadowgraph", "ha-import", "--core", str(root / "core"), "--hacs", str(hacs),
               "--devdocs", str(root / "devdocs"), "--architecture", str(root / "architecture"),
               "--releases", ",".join(tags), "--out", a.out or str(repo / "data" / "ha_snapshot.json.gz")]
        print("building the snapshot:", " ".join(cmd))
        env = {**os.environ, "PYTHONPATH": str(repo / "backend")}
        raise SystemExit(subprocess.call(cmd, cwd=repo / "backend", env=env))
    print(f"\nDone. Build the snapshot with:\n  python scripts/fetch_ha_sources.py --dir {a.dir} --build")


if __name__ == "__main__":
    main()
