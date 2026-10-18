"""Ask the Home Assistant brain: answers from the graph (names, users, deprecations), the developer blog,
architecture decisions and past checks, always with sources. Conversations are saved in ha_memory."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..agents import llm
from ..core import memory
from ..db import live_graph
from . import service
from .mcp_tools import ADR_SEARCH, POST_SEARCH

if TYPE_CHECKING:
    from ..runtime import Runtime

TENANT = "ha"
IDENT = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`?")
STOP = {"what", "which", "when", "where", "does", "will", "with", "from", "that", "this", "uses", "using", "remove",
        "removed", "break", "breaks", "still", "there", "anyone", "should", "could", "would", "about", "home",
        "assistant", "integration", "integrations", "custom", "core", "deprecated", "deprecation", "change", "safe"}
CHANGES_LIKE = """
MATCH (c:Change)
WHERE any(w IN $words WHERE toLower(c.title) CONTAINS w)
RETURN c.id AS id, c.number AS number, c.title AS title, c.level AS level, c.headline AS headline
ORDER BY c.analyzed_at DESC LIMIT 3"""


def _candidates(question: str) -> list[str]:
    out = []
    for m in IDENT.finditer(question):
        tok = m.group(1)
        if tok.lower() in STOP or len(tok) < 5:
            continue
        if "." in tok or "_" in tok or re.search(r"[a-z][A-Z]", tok) or tok.isupper() or "`" in m.group(0):
            out.append(tok)
    return list(dict.fromkeys(out))[:4]


async def answer(rt: "Runtime", session_id: str | None, message: str, mode: str = "rules") -> dict[str, Any]:
    message = message.strip()
    if not message:
        raise ValueError("message is empty")
    session_id = session_id or memory.new_id("ses")
    g = live_graph(TENANT)
    symbols: list[dict[str, Any]] = []
    for tok in _candidates(message):
        hits = await service.search_symbols(rt, tok, 5)
        exact = [h for h in hits if h["id"] == tok or h["id"].endswith("." + tok) or h["name"] == tok]
        for h in (exact or hits)[:2]:
            d = await service.symbol_detail(rt, h["id"])
            if d:
                symbols.append(d)
    q = await rt.embedder.embed_one(message)
    posts = [p for p in await rt.db.query(g, POST_SEARCH, {"q": q}, emit=False, read_only=True)
             if float(p["score"]) < 0.6][:3]
    adrs = [a for a in await rt.db.query(g, ADR_SEARCH, {"q": q}, emit=False, read_only=True)
            if float(a["score"]) < 0.55][:2]
    words = [w for w in re.findall(r"[a-z_]{5,}", message.lower()) if w not in STOP][:6]
    changes = await rt.db.query(g, CHANGES_LIKE, {"words": words}, emit=False, read_only=True) if words else []
    recall = await memory.recall_conversation(rt, TENANT, message, agent="ask", emit_run=f"ask-{session_id}")
    rt.hub.forget(f"ask-{session_id}")
    earlier = [m for m in recall["messages"] if m["distance"] < 0.35 and m["role"] == "user"
               and m["text"].strip().lower() != message.lower()][:1]

    sources: list[dict[str, str]] = []
    for d in symbols:
        for p in d["posts"][:2]:
            sources.append({"label": p["title"], "url": p["url"]})
    sources += [{"label": p["title"], "url": p["url"]} for p in posts]
    sources += [{"label": f"{a['id']} {a['title']}", "url": a["url"]} for a in adrs]
    sources += [{"label": f"PR #{c['number']}" if c["number"] else c["id"], "url": f"#/changes/{c['id']}"}
                for c in changes]
    seen, uniq = set(), []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            uniq.append(s)

    context = {
        "names": [{"name": d["symbol"]["id"], "state": d["symbol"].get("state"),
                   "deprecated": d["symbol"].get("deprecated"), "breaks_in": d["symbol"].get("breaks_in"),
                   "removed_in": d["symbol"].get("removed_in"), "summary": d["summary"],
                   "top_users": [u["name"] for u in d["users"][:5]]} for d in symbols],
        "posts": [{"title": p["title"], "date": p["date"]} for p in posts],
        "decisions": [{"id": a["id"], "title": a["title"], "status": a["status"]} for a in adrs],
        "past_checks": [{"change": c["id"], "level": c["level"], "headline": c["headline"]} for c in changes],
    }
    text = None
    if mode == "llm" and rt.settings.llm_available:
        text, _err = await llm.write_text(
            rt.settings,
            system=("You answer questions about Home Assistant's code from a graph. Use ONLY the JSON context; "
                    "quote names, numbers and versions exactly. If the context has nothing relevant, say so. "
                    "2 to 5 sentences, no headings."),
            prompt=f"Question: {message}\n\nContext (JSON):\n{json.dumps(context, default=str)}")
    if not text:
        text = _rules_answer(context, earlier)
    await memory.append_message(rt, TENANT, session_id, "user", message, title=message[:60])
    await memory.append_message(rt, TENANT, session_id, "assistant", text, sources=[s["label"] for s in uniq])
    return {"session_id": session_id, "answer": text, "sources": uniq, "context": context,
            "mode": "llm" if (mode == "llm" and rt.settings.llm_available and text) else "rules"}


def _rules_answer(ctx: dict[str, Any], earlier: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for n in ctx["names"]:
        s = n["summary"]
        state = n["state"]
        if state == "missing":
            when = f" (removed in {n['removed_in']})" if n.get("removed_in") else ""
            parts.append(f"`{n['name']}` no longer exists in core{when}; {s['would_break_external']} custom "
                         f"integrations still import it in a way that fails.")
        else:
            dep = f" It is deprecated and stops working in {n['breaks_in']}." if n.get("deprecated") and n.get("breaks_in") else ""
            parts.append(f"`{n['name']}` is used by {s['core']} core and {s['external']} custom integrations; "
                         f"removing it today would break {s['would_break']} of them.{dep}")
        if n["top_users"]:
            parts.append("Users include " + ", ".join(n["top_users"][:3]) + ".")
    for c in ctx["past_checks"][:2]:
        parts.append(f"Past check {c['change']}: {c['headline']}.")
    if ctx["posts"]:
        parts.append("Related on the developer blog: " + "; ".join(f"\"{p['title']}\" ({p['date']})"
                                                               for p in ctx["posts"][:2]) + ".")
    for a in ctx["decisions"][:1]:
        parts.append(f"Architecture decision {a['id']} \"{a['title']}\" ({a['status']}).")
    if earlier:
        parts.append(f"You asked something similar before: \"{earlier[0]['text']}\".")
    return " ".join(parts) or "I found nothing about that in the graph, the developer blog or past checks."
