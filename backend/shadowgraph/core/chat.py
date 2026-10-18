"""Chat: answers from past episodes, earlier conversations and decisions in force, with sources."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..agents import llm
from ..db import live_graph
from ..graph import queries as Q
from . import brain, memory

if TYPE_CHECKING:
    from ..runtime import Runtime

RELATED = 0.95  # cosine distance below which something counts as related


async def answer(rt: "Runtime", tenant: str, session_id: str | None, message: str, mode: str = "rules") -> dict[str, Any]:
    message = message.strip()
    if not message:
        raise ValueError("message is empty")
    session_id = session_id or memory.new_id("ses")
    run = f"chat-{session_id}"
    recall = await memory.recall_conversation(rt, tenant, message, agent="chat", emit_run=run)
    decisions = await brain.search_decisions(rt, tenant, message, agent="chat", emit_run=run)
    services = [r["id"] for r in await rt.db.query(live_graph(tenant), Q.GRAPH_SERVICES, emit=False, read_only=True)]
    mentioned = [s for s in services if re.search(rf"\b{re.escape(s)}\b", message.lower())]
    experts = [await brain.find_expert(rt, tenant, s, agent="chat", emit_run=run) for s in mentioned[:2]]

    episodes = [e for e in recall["episodes"] if e["distance"] < RELATED][:3]
    earlier = [m for m in recall["messages"] if m["distance"] < RELATED and m["role"] == "user"
               and m["text"].strip().lower() != message.lower()][:2]
    in_force = [d for d in decisions["decisions"] if d["distance"] < RELATED][:2]

    sources: list[str] = [f"PR #{e['pr_number']}" for e in episodes] + [d["adr"] for d in in_force]
    sources += [f"session {m['session']}" for m in earlier]
    context = {"episodes": episodes, "earlier_questions": earlier, "decisions_in_force": in_force,
               "experts": [{"service": e["service_id"], "top": e["experts"][:2]} for e in experts]}

    text = None
    if mode == "llm" and rt.settings.llm_available:
        text, _err = await llm.write_text(
            rt.settings,
            system=("You answer questions about a company's services from its memory graph. Use ONLY the JSON "
                    "context. Name PR numbers, decision ids (ADR-xx) and @handles exactly as given. If the "
                    "context has nothing relevant, say so. 2 to 5 sentences, no headings."),
            prompt=f"Question: {message}\n\nContext (JSON):\n{json.dumps(context, default=str)}")
    if not text:
        text = _rules_answer(episodes, earlier, in_force, experts)

    await memory.append_message(rt, tenant, session_id, "user", message, title=message[:60])
    await memory.append_message(rt, tenant, session_id, "assistant", text, sources=sources)
    await memory.link_session_episodes(rt, tenant, session_id, [e["pr_number"] for e in episodes])
    rt.hub.forget(run)  # chat events are not streamed anywhere; don't keep them
    return {"session_id": session_id, "answer": text, "sources": sources, "context": context,
            "mode": "llm" if (mode == "llm" and rt.settings.llm_available) else "rules"}


def _rules_answer(episodes: list[dict], earlier: list[dict], in_force: list[dict], experts: list[dict]) -> str:
    parts: list[str] = []
    if episodes:
        e = episodes[0]
        parts.append(f"Yes. The closest past check is PR #{e['pr_number']} \"{e['title']}\": "
                     f"{e['verdict'] or 'open'}{' (' + e['summary'] + ')' if e.get('summary') else ''}.")
        for other in episodes[1:]:
            parts.append(f"Also related: PR #{other['pr_number']} \"{other['title']}\" ({other['verdict'] or 'open'}).")
    if earlier:
        parts.append(f"You asked something similar before: \"{earlier[0]['text']}\".")
    for d in in_force:
        replaced = f" It replaced {', '.join(d['replaces'])}." if d.get("replaces") else ""
        parts.append(f"Decision in force: {d['adr']} {d['title']}: {d['summary']}{replaced}")
    for e in experts:
        if e["experts"]:
            top = e["experts"][0]
            parts.append(f"For {e['service_id']}, ask @{top['engineer']} ({top['channel']}).")
    if not parts:
        return "I found nothing close to that in past episodes, earlier chats or decisions in force."
    return " ".join(parts)
