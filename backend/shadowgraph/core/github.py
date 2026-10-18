"""GitHub: post (or update) the verdict comment and set a commit status. Never raises."""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from ..config import Settings
from .comment import MARKER

log = logging.getLogger("shadowgraph.github")
API = "https://api.github.com"
CONTEXT = "shadowgraph/blast-radius"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret:
        return True  # no secret configured: accept (local demos)
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def post_verdict(cfg: Settings, pr_number: int, verdict: str, headline: str, body: str,
                       target_url: str) -> dict[str, Any]:
    if not cfg.github_enabled:
        return {"posted": False, "reason": "GITHUB_TOKEN or GITHUB_REPO not set; comment shown in the dashboard only"}
    headers = {"Authorization": f"Bearer {cfg.github_token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "shadowgraph"}
    repo = cfg.github_repo
    out: dict[str, Any] = {"posted": False}
    try:
        async with httpx.AsyncClient(base_url=API, headers=headers, timeout=15) as gh:
            existing = None
            page = await gh.get(f"/repos/{repo}/issues/{pr_number}/comments", params={"per_page": 100})
            if page.status_code == 200:
                existing = next((c for c in page.json() if MARKER in (c.get("body") or "")), None)
            if existing:
                resp = await gh.patch(f"/repos/{repo}/issues/comments/{existing['id']}", json={"body": body})
            else:
                resp = await gh.post(f"/repos/{repo}/issues/{pr_number}/comments", json={"body": body})
            if resp.status_code >= 300:
                return {"posted": False, "reason": f"comment failed: HTTP {resp.status_code} {resp.text[:200]}"}
            out.update(posted=True, comment_url=resp.json().get("html_url"), updated=bool(existing))

            pr = await gh.get(f"/repos/{repo}/pulls/{pr_number}")
            if pr.status_code != 200:
                out["status"] = f"skipped: could not read PR (HTTP {pr.status_code})"
                return out
            sha = pr.json()["head"]["sha"]
            state = "failure" if verdict == "BLOCK" else "success"
            st = await gh.post(f"/repos/{repo}/statuses/{sha}", json={
                "state": state, "context": CONTEXT, "description": f"{verdict}: {headline}"[:140],
                "target_url": target_url})
            out["status"] = state if st.status_code < 300 else f"failed: HTTP {st.status_code}"
            out["sha"] = sha
    except httpx.HTTPError as exc:
        out.update(posted=out.get("posted", False), reason=f"network error: {exc}")
    return out
