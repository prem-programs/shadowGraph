"""Webhook ingest: a GitHub pull_request event updates the company graph before the check runs.

A PR description declares what it changes with lines like
    Removes: /v1/token-verify        (or auth-service:/v1/token-verify)
    Cites: ADR-08
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..db import live_graph
from ..graph import queries as Q

if TYPE_CHECKING:
    from ..runtime import Runtime

REMOVES = re.compile(r"(?im)^\s*removes?\s*:\s*(\S+)")
CITES = re.compile(r"\b((?:G-)?ADR-\d+)\b")


def parse_body(body: str) -> dict[str, list[str]]:
    return {"removes": [m.strip().rstrip(".,") for m in REMOVES.findall(body or "")],
            "cites": list(dict.fromkeys(CITES.findall(body or "")))}


async def ingest_pull_request(rt: "Runtime", tenant: str, payload: dict[str, Any]) -> dict[str, Any]:
    pr = payload.get("pull_request") or {}
    number = int(pr.get("number") or payload.get("number") or 0)
    if not number:
        raise ValueError("payload has no pull request number")
    title = pr.get("title") or f"PR #{number}"
    body = pr.get("body") or ""
    status = "merged" if pr.get("merged") else (pr.get("state") or "open")
    graph = live_graph(tenant)
    parsed = parse_body(body)
    await rt.db.query(graph, Q.UPSERT_PR, {"number": number, "title": title, "description": body or title,
                                           "status": status, "created_at": (pr.get("created_at") or "")[:10]},
                      emit=False)
    linked: list[str] = []
    if parsed["removes"]:  # only replace links when the description declares them
        await rt.db.query(graph, Q.CLEAR_PR_LINKS, {"number": number}, emit=False)
        for item in parsed["removes"]:
            service, _, route = item.rpartition(":") if ":" in item else ("", "", item)
            rows = await rt.db.query(graph, Q.LINK_PR_TARGET, {"number": number, "route": route,
                                                               "service": service or None, "change": "remove"},
                                     emit=False)
            linked += [r["target"] for r in rows]
        for adr in parsed["cites"]:
            await rt.db.query(graph, Q.LINK_PR_CITES, {"number": number, "adr": adr}, emit=False)
    login = (pr.get("user") or {}).get("login")
    if login:
        await rt.db.query(graph, Q.LINK_PR_AUTHOR, {"number": number, "handle": login}, emit=False)
    return {"number": number, "title": title, "status": status, "removes": parsed["removes"],
            "cites": parsed["cites"], "linked_targets": linked}
