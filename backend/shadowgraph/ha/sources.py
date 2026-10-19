"""Where changes come from, read-only: a local git clone, or the GitHub REST API (+ raw file contents).

Neither source ever writes to the upstream repository: no comments, no statuses, no pushes.

GitSource   uses `refs/pull/N/merge` (GitHub's test merge of the PR into its base): its first parent is the
            base, the merge commit itself is the result. Diffing the two gives exactly what merging would
            change today. Open PRs are the ones that still have a merge ref. Needs only git access.
GitHubSource uses the REST API for the PR list, titles and changed files, and raw.githubusercontent.com
            for file contents. A token raises the rate limit from 60 to 5,000 requests per hour.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx

from .surface import module_name

log = logging.getLogger("shadowgraph.ha.sources")


@dataclass
class ChangedFile:
    path: str
    status: str  # added | modified | removed | renamed
    previous: str | None = None


@dataclass
class ChangeRef:
    number: int | None
    title: str
    author: str = ""
    url: str = ""
    state: str = "open"  # open | merged | closed | draft-local
    base_sha: str = ""
    head_sha: str = ""
    created_at: str = ""
    updated_at: str = ""
    merged_at: str = ""
    body: str = ""
    labels: list[str] = field(default_factory=list)
    files: list[ChangedFile] = field(default_factory=list)
    source: str = "git"
    title_is_commit: bool = False  # the title is a commit subject, not the PR title (no API access)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["files"] = [asdict(f) for f in self.files]
        return d


class Tree(Protocol):
    sha: str

    def read(self, path: str) -> str | None: ...

    def modules(self) -> dict[str, str]: ...


# ------------------------------------------------------------------------------------------------- git
class GitCommitTree:
    """Files of one commit in a local clone, read through one long-lived `git cat-file --batch`."""

    def __init__(self, repo: str, sha: str) -> None:
        self.repo, self.sha = repo, sha
        self._proc: subprocess.Popen | None = None
        self._mods: dict[str, str] | None = None
        self._cache: dict[str, str | None] = {}

    def read(self, path: str) -> str | None:
        if path in self._cache:
            return self._cache[path]
        if self._proc is None:
            self._proc = subprocess.Popen(["git", "-C", self.repo, "cat-file", "--batch"],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(f"{self.sha}:{path}\n".encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().split()
        if len(header) < 3 or header[-1] == b"missing":
            self._cache[path] = None
            return None
        data = self._proc.stdout.read(int(header[2]))
        self._proc.stdout.read(1)
        text = data.decode("utf-8", "replace")
        self._cache[path] = text
        return text

    def modules(self) -> dict[str, str]:
        if self._mods is None:
            out = subprocess.run(["git", "-C", self.repo, "ls-tree", "-r", "--name-only", self.sha, "homeassistant/"],
                                 capture_output=True, text=True, check=True).stdout.split("\n")
            self._mods = {module_name(p): p for p in out if p.endswith(".py")}
        return self._mods

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.close()  # type: ignore[union-attr]
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None


def _git(repo: str, *args: str, timeout: int = 120) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True,
                          timeout=timeout).stdout


def _name_status(repo: str, base: str, head: str) -> list[ChangedFile]:
    out = _git(repo, "diff", "--name-status", "-M", base, head)
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        code = parts[0][:1]
        if code == "R" and len(parts) >= 3:
            files.append(ChangedFile(parts[2], "renamed", parts[1]))
        elif code in ("A", "M", "D", "T", "C") and len(parts) >= 2:
            files.append(ChangedFile(parts[-1], {"A": "added", "D": "removed"}.get(code, "modified")))
    return files


class GitSource:
    """PR refs are fetched with --depth=2 into a separate bare repository next to the clone. Fetching them
    into a shallow clone would mark the PR's base commit as a new shallow boundary and hide the clone's
    own dev history (which the replay of merged PRs needs)."""
    name = "git"

    def __init__(self, clone: str, repo: str = "home-assistant/core", remote: str = "origin",
                 pr_store: str | None = None) -> None:
        self.clone, self.repo, self.remote = clone, repo, remote
        self.pr_store = pr_store or (clone.rstrip("/\\") + "-prs.git")
        self._lock = asyncio.Lock()
        self._store_ready = False

    async def _run(self, *args: str, timeout: int = 180) -> str:
        return await asyncio.to_thread(_git, self.clone, *args, timeout=timeout)

    async def _store(self, *args: str, timeout: int = 180) -> str:
        if not self._store_ready:
            import os
            if not os.path.isdir(self.pr_store):
                url = (await self._run("remote", "get-url", self.remote)).strip()
                await asyncio.to_thread(subprocess.run, ["git", "init", "-q", "--bare", self.pr_store], check=True)
                await asyncio.to_thread(_git, self.pr_store, "remote", "add", "origin", url)
                # borrow the clone's objects, so a PR fetch only downloads what the clone does not have
                objects = os.path.join(self.clone, ".git", "objects")
                if os.path.isdir(objects):
                    with open(os.path.join(self.pr_store, "objects", "info", "alternates"), "w") as f:
                        f.write(os.path.abspath(objects) + "\n")
            self._store_ready = True
        self._sync_shallow()
        return await asyncio.to_thread(_git, self.pr_store, *args, timeout=timeout)

    def _sync_shallow(self) -> None:
        """Borrowed objects come from a shallow clone: the store must know the clone's shallow boundary,
        or git walks into parents that were never downloaded."""
        import os
        src = os.path.join(self.clone, ".git", "shallow")
        dst = os.path.join(self.pr_store, "shallow")
        if os.path.isfile(src):
            have = set(open(dst).read().split()) if os.path.isfile(dst) else set()
            want = set(open(src).read().split())
            if not want <= have:
                with open(dst, "w") as f:
                    f.write("".join(f"{x}\n" for x in sorted(have | want)))

    async def list_open(self, limit: int = 30) -> list[dict[str, Any]]:
        out = await self._run("ls-remote", self.remote, "refs/pull/*/merge")
        prs = []
        for line in out.splitlines():
            sha, ref = line.split("\t")
            m = re.match(r"refs/pull/(\d+)/merge$", ref)
            if m:
                prs.append({"number": int(m.group(1)), "merge_sha": sha})
        prs.sort(key=lambda p: -p["number"])
        return prs[:limit]

    async def fetch_pr(self, number: int) -> tuple[ChangeRef, GitCommitTree, GitCommitTree]:
        ref = f"refs/pr/{number}"
        async with self._lock:  # one fetch at a time (the store's shallow file is locked during a fetch)
            await self._store("fetch", "-q", "--depth=2", "origin", f"+refs/pull/{number}/merge:{ref}")
        merge = (await self._store("rev-parse", ref)).strip()
        base = (await self._store("rev-parse", f"{merge}^1")).strip()
        head = (await self._store("rev-parse", f"{merge}^2")).strip()
        info = (await self._store("log", "-1", "--format=%s%x1f%an%x1f%cI%x1f%b", head)).split("\x1f")
        files = await asyncio.to_thread(_name_status, self.pr_store, base, merge)
        change = ChangeRef(number=number, title=info[0].strip(), author=info[1], updated_at=info[2],
                           url=f"https://github.com/{self.repo}/pull/{number}", state="open",
                           base_sha=base, head_sha=head, files=files, source="git", title_is_commit=True,
                           body=(info[3] if len(info) > 3 else "").strip()[:2000])
        return change, GitCommitTree(self.pr_store, base), GitCommitTree(self.pr_store, merge)

    async def fetch_commit(self, sha: str, number: int | None = None) -> tuple[ChangeRef, GitCommitTree, GitCommitTree]:
        """A merged PR: HA squash-merges, so the PR is one commit on dev and its parent is the base."""
        where = self.clone
        try:
            await self._run("cat-file", "-e", f"{sha}^1")
        except subprocess.CalledProcessError:
            async with self._lock:
                await self._store("fetch", "-q", "--depth=2", "origin", sha)
            where = self.pr_store
        run = self._run if where == self.clone else self._store
        full = (await run("rev-parse", sha)).strip()
        base = (await run("rev-parse", f"{full}^1")).strip()
        info = (await run("log", "-1", "--format=%s%x1f%an%x1f%cI%x1f%b", full)).split("\x1f")
        title = info[0].strip()
        m = re.search(r"\(#(\d+)\)\s*$", title)
        num = number or (int(m.group(1)) if m else None)
        files = await asyncio.to_thread(_name_status, where, base, full)
        change = ChangeRef(number=num, title=re.sub(r"\s*\(#\d+\)\s*$", "", title), author=info[1],
                           merged_at=info[2], updated_at=info[2],
                           url=f"https://github.com/{self.repo}/pull/{num}" if num else
                           f"https://github.com/{self.repo}/commit/{full}",
                           state="merged", base_sha=base, head_sha=full, files=files, source="git",
                           body=(info[3] if len(info) > 3 else "").strip()[:2000])
        return change, GitCommitTree(where, base), GitCommitTree(where, full)

    async def find_merged(self, number: int, branch: str = "dev") -> str | None:
        out = await self._run("log", f"{self.remote}/{branch}", "--format=%H", "-n", "1", "--grep", rf"(#{number})$")
        return out.strip() or None


# ------------------------------------------------------------------------------------------------- GitHub API
class RawTree:
    """File contents at one commit from raw.githubusercontent.com; the module list comes from the snapshot
    (adjusted by the files the change adds or removes)."""

    def __init__(self, http: httpx.AsyncClient, repo: str, sha: str, modules: dict[str, str]) -> None:
        self.http, self.repo, self.sha = http, repo, sha
        self._mods = modules
        self._cache: dict[str, str | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def prefetch(self, paths: list[str]) -> None:
        async def one(p: str) -> None:
            if p in self._cache:
                return
            r = await self.http.get(f"https://raw.githubusercontent.com/{self.repo}/{self.sha}/{p}")
            self._cache[p] = r.text if r.status_code == 200 else None

        sem = asyncio.Semaphore(16)

        async def guarded(p: str) -> None:
            async with sem:
                await one(p)

        await asyncio.gather(*(guarded(p) for p in paths))

    def read(self, path: str) -> str | None:
        if path not in self._cache:  # synchronous fallback used by the resolver for rare extra modules
            try:
                r = httpx.get(f"https://raw.githubusercontent.com/{self.repo}/{self.sha}/{path}", timeout=20)
                self._cache[path] = r.text if r.status_code == 200 else None
            except httpx.HTTPError:
                self._cache[path] = None
        return self._cache[path]

    def modules(self) -> dict[str, str]:
        return self._mods

    def close(self) -> None:
        pass


class GitHubSource:
    name = "github"

    def __init__(self, repo: str = "home-assistant/core", token: str = "", core_files: list[str] | None = None) -> None:
        self.repo = repo
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "shadowgraph (read-only)"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True)
        self.core_files = core_files or []
        self.rate: dict[str, Any] = {}

    async def _get(self, url: str, **params: Any) -> Any:
        full_url = url if url.startswith("http") else f"https://api.github.com{url}"
        r = await self.http.get(full_url, params=params)
        self.rate = {"remaining": r.headers.get("x-ratelimit-remaining"), "limit": r.headers.get("x-ratelimit-limit"),
                     "reset": r.headers.get("x-ratelimit-reset")}
        if r.status_code == 401 and "Authorization" in self.http.headers:
            log.warning("GITHUB_TOKEN returned 401 Unauthorized (bad credentials). Retrying anonymously.")
            self.http.headers.pop("Authorization", None)
            r = await self.http.get(full_url, params=params)
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("GitHub API rate limit reached; set GITHUB_TOKEN (read-only scope is enough)")
        r.raise_for_status()
        return r.json()

    async def list_open(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = await self._get(f"/repos/{self.repo}/pulls", state="open", sort="updated", direction="desc",
                               per_page=min(limit, 100))
        return [{"number": p["number"], "title": p["title"], "author": (p.get("user") or {}).get("login", ""),
                 "updated_at": p.get("updated_at", ""), "draft": p.get("draft", False),
                 "head_sha": p["head"]["sha"], "labels": [l["name"] for l in p.get("labels", [])]} for p in rows]

    async def fetch_pr(self, number: int) -> tuple[ChangeRef, RawTree, RawTree]:
        pr = await self._get(f"/repos/{self.repo}/pulls/{number}")
        head = pr["head"]["sha"]
        cmp = await self._get(f"/repos/{self.repo}/compare/{pr['base']['sha']}...{head}")
        base = cmp["merge_base_commit"]["sha"]
        files: list[ChangedFile] = []
        page = 1
        while page <= 30:  # GitHub caps a PR's file list at 3,000
            batch = await self._get(f"/repos/{self.repo}/pulls/{number}/files", per_page=100, page=page)
            for f in batch:
                status = {"added": "added", "removed": "removed", "renamed": "renamed"}.get(f["status"], "modified")
                files.append(ChangedFile(f["filename"], status, f.get("previous_filename")))
            if len(batch) < 100:
                break
            page += 1
        state = "merged" if pr.get("merged_at") else pr.get("state", "open")
        change = ChangeRef(number=number, title=pr["title"], author=(pr.get("user") or {}).get("login", ""),
                           url=pr["html_url"], state=state, base_sha=base, head_sha=head,
                           created_at=pr.get("created_at", ""), updated_at=pr.get("updated_at", ""),
                           merged_at=pr.get("merged_at") or "", body=(pr.get("body") or "")[:2000],
                           labels=[l["name"] for l in pr.get("labels", [])], files=files, source="github")
        base_mods = {module_name(p): p for p in self.core_files}
        head_mods = dict(base_mods)
        for f in files:
            if f.path.startswith("homeassistant/") and f.path.endswith(".py"):
                if f.status == "removed":
                    head_mods.pop(module_name(f.path), None)
                else:
                    head_mods[module_name(f.path)] = f.path
                if f.status == "renamed" and f.previous:
                    head_mods.pop(module_name(f.previous), None)
        bt, ht = RawTree(self.http, self.repo, base, base_mods), RawTree(self.http, self.repo, head, head_mods)
        py = [f for f in files if f.path.endswith(".py")]
        await bt.prefetch([f.previous or f.path for f in py if f.status != "added"])
        await ht.prefetch([f.path for f in py if f.status != "removed"])
        return change, bt, ht

    async def close(self) -> None:
        await self.http.aclose()


async def raw_file(repo: str, sha: str, path: str) -> str | None:
    """One consumer file (for migration drafts). Public repos only; nothing is cached on disk."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "shadowgraph (read-only)"}) as http:
        r = await http.get(f"https://raw.githubusercontent.com/{repo}/{sha}/{path}")
        return r.text if r.status_code == 200 else None
