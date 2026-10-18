"""Agent memory in {tenant}_memory: episodes, hypothesis locks, facts, playbooks, chat sessions."""
from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from ..db import memory_graph
from ..graph import queries as Q

if TYPE_CHECKING:
    from ..runtime import Runtime

PLAYBOOK_MAX_DISTANCE = float(os.environ.get("PLAYBOOK_MAX_DISTANCE", "0.6"))
CHECKS = {
    # check: (kind, label, summary template, priority)
    "partition": ("sim", "H1", "Removing {route} splits the service mesh", 3),
    "chokepoint": ("sim", "H2", "Removing {route} overloads a chokepoint", 2),
    "entry_path": ("sim", "H3", "Tier 0 entry points lose their path to {provider}", 2),
    "decision": ("brain", "H4", "A decision in force forbids removing {route}", 3),
    "expert": ("brain", "H5", "Who must review changes to {provider}", 1),
    # Home Assistant workspace (code APIs instead of service endpoints)
    "consumers": ("sim", "H1", "Removed names break integrations outside core ({route})", 3),
    "cascade": ("sim", "H2", "Integrations that fail to set up take their dependents down", 2),
    "deadline": ("sim", "H3", "Deprecated names still have users before their removal release", 2),
    "announced": ("brain", "H4", "The change was announced (developer blog or architecture decision)", 2),
    "reviewers": ("brain", "H5", "Who must review changes to {provider}", 1),
}
DEFAULT_CHECKS = ["partition", "chokepoint", "entry_path", "decision", "expert"]
HA_CHECKS = ["consumers", "cascade", "deadline", "announced", "reviewers"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def open_episode(rt: "Runtime", tenant: str, pr: dict[str, Any], run_id: str, *,
                       agent: str | None = None, emit_run: str | None = None) -> dict[str, Any]:
    removal = pr["removals"][0] if pr.get("removals") else (pr["modifies"][0] if pr.get("modifies") else {})
    text = (f"{pr['title']}. {pr.get('description') or ''} "
            f"Change: {removal.get('change', 'modify')} {removal.get('route') or removal.get('target') or ''} "
            f"on {removal.get('provider') or ''}.").strip()
    episode_id = new_id("ep")
    vec = await rt.embedder.embed_one(text)
    await rt.db.query(memory_graph(tenant), Q.OPEN_EPISODE,
                      {"id": episode_id, "pr": int(pr["number"]), "title": pr["title"], "text": text,
                       "embedding": vec, "run_id": run_id},
                      run_id=emit_run, agent=agent, tool="memory_open_episode", label="open episode")
    rt.hub.emit(emit_run, "episode_opened", agent=agent, tool="memory_open_episode",
                summary=f"Episode {episode_id} for PR #{pr['number']}")
    return {"episode_id": episode_id, "symptom_text": text, "target": removal}


async def post_hypotheses(rt: "Runtime", tenant: str, episode_id: str, checks: list[str], target: dict[str, Any], *,
                          agent: str | None = None, emit_run: str | None = None) -> dict[str, Any]:
    hyps = []
    for check in checks:
        if check not in CHECKS:
            raise ValueError(f"unknown check '{check}'; use one of {sorted(CHECKS)}")
        kind, label, template, priority = CHECKS[check]
        hyps.append({
            "id": f"{episode_id}:{label}", "label": label, "kind": kind, "check": check,
            "target_id": target.get("target") or target.get("provider") or "",
            "summary": template.format(route=target.get("route") or target.get("target") or "the target",
                                       provider=target.get("provider") or "the service"),
            "priority": priority,
        })
    rows = await rt.db.query(memory_graph(tenant), Q.Q8_POST_HYPOTHESES, {"episode_id": episode_id, "hyps": hyps},
                             run_id=emit_run, agent=agent, tool="memory_post_hypotheses", label="post hypotheses")
    rt.hub.emit(emit_run, "hypotheses_posted", agent=agent, tool="memory_post_hypotheses",
                summary=f"Posted {len(rows)} hypotheses: " + ", ".join(f"{h['label']} {h['check']}" for h in hyps),
                hypotheses=[{"label": h["label"], "check": h["check"], "kind": h["kind"], "summary": h["summary"]}
                            for h in hyps])
    return {"episode_id": episode_id, "hypotheses": hyps}


async def claim(rt: "Runtime", tenant: str, episode_id: str, agent_id: str, kind: str, prefer: str | None = None, *,
                emit_run: str | None = None) -> dict[str, Any]:
    graph = memory_graph(tenant)
    kw = {"run_id": emit_run, "agent": agent_id, "tool": "memory_claim"}
    conflict = None
    if prefer:
        rows = await rt.db.query(graph, Q.Q8_CLAIM_SPECIFIC, {"episode_id": episode_id, "hyp_id": prefer,
                                                              "agent_id": agent_id, "kind": kind},
                                 label="claim specific hypothesis", **kw)
        if rows:
            row = rows[0]
            rt.hub.emit(emit_run, "lock_claimed", agent=agent_id, tool="memory_claim",
                        summary=f"Claimed {row['label']} ({row['check']})", hypothesis=row["label"])
            return dict(row, conflict=None)
        owner = await rt.db.query(graph, Q.HYPOTHESIS_OWNER, {"hyp_id": prefer}, read_only=True, **kw)
        held_by = owner[0]["claimed_by"] if owner else None
        label = prefer.rsplit(":", 1)[-1]
        conflict = {"hypothesis": label, "held_by": held_by, "status": owner[0]["status"] if owner else None}
        rt.hub.emit(emit_run, "lock_conflict", agent=agent_id, tool="memory_claim", hypothesis=label,
                    summary=f"{label} already claimed by {held_by or 'another agent'}; taking the next one")
    rows = await rt.db.query(graph, Q.Q8_CLAIM_NEXT, {"episode_id": episode_id, "agent_id": agent_id, "kind": kind},
                             label="claim next hypothesis", **kw)
    if not rows:
        rt.hub.emit(emit_run, "lock_none", agent=agent_id, tool="memory_claim",
                    summary=f"No open {kind} hypotheses left")
        return {"claimed": None, "conflict": conflict}
    row = rows[0]
    rt.hub.emit(emit_run, "lock_claimed", agent=agent_id, tool="memory_claim",
                summary=f"Claimed {row['label']} ({row['check']})", hypothesis=row["label"])
    return dict(row, conflict=conflict)


async def resolve(rt: "Runtime", tenant: str, hyp_id: str, agent_id: str, status: str, result: dict[str, Any], *,
                  emit_run: str | None = None) -> dict[str, Any]:
    if status not in ("CONFIRMED", "REJECTED"):
        raise ValueError("status must be CONFIRMED or REJECTED")
    rows = await rt.db.query(memory_graph(tenant), Q.Q8_RESOLVE,
                             {"hyp_id": hyp_id, "agent_id": agent_id, "status": status,
                              "result_json": json.dumps(result, default=str)},
                             run_id=emit_run, agent=agent_id, tool="memory_resolve", label="resolve hypothesis")
    if not rows:
        raise PermissionError(f"{agent_id} does not hold {hyp_id}")
    label = hyp_id.rsplit(":", 1)[-1]
    rt.hub.emit(emit_run, "hypothesis_resolved", agent=agent_id, tool="memory_resolve", hypothesis=label,
                status=status, summary=f"{label} {status.lower()}: {result.get('summary', '')}".strip())
    return {"hyp_id": hyp_id, "status": status}


async def record_fact(rt: "Runtime", tenant: str, entity: str, attribute: str, value: Any, source: str, *,
                      episode_id: str | None = None, agent: str | None = None, if_absent: bool = False,
                      emit_run: str | None = None) -> dict[str, Any]:
    graph = memory_graph(tenant)
    kw = {"run_id": emit_run, "agent": agent, "tool": "memory_record_fact"}
    if if_absent:
        existing = await rt.db.query(graph, """
            MATCH (f:Fact {entity_id: $entity, attribute: $attr}) WHERE f.valid_until IS NULL
            RETURN f.id AS id, f.value AS value, f.source AS source""",
                                     {"entity": entity, "attr": attribute}, read_only=True, **kw)
        if existing:
            return {"fact_id": existing[0]["id"], "recorded": False, "existing": existing[0]}
    fact_id = new_id("fact")
    rows = await rt.db.query(graph, Q.Q9_RECORD_FACT, {"id": fact_id, "entity": entity, "attr": attribute,
                                                         "value": value, "source": source, "agent": agent},
                             label="record fact", **kw)
    if episode_id:
        await rt.db.query(graph, Q.LINK_FACT_EPISODE, {"fact_id": fact_id, "episode_id": episode_id},
                          label="link fact to episode", **kw)
    overridden = rows[0] if rows else None
    rt.hub.emit(emit_run, "fact_recorded", agent=agent, tool="memory_record_fact",
                summary=(f"{entity} {attribute} = {value} (source: {source})"
                         + (f"; overrides {overridden['old_value']} from {overridden['old_source']}" if overridden else "")))
    return {"fact_id": fact_id, "recorded": True, "overridden": overridden}


async def recall_playbook(rt: "Runtime", tenant: str, text: str, *, exclude_episode: str = "",
                          agent: str | None = None, emit_run: str | None = None) -> dict[str, Any]:
    vec = await rt.embedder.embed_one(text)
    rows = await rt.db.query(memory_graph(tenant), Q.Q10_RECALL_PLAYBOOK, {"symptom_vec": vec, "exclude": exclude_episode},
                             run_id=emit_run, agent=agent, tool="memory_recall_playbook", label="recall playbook",
                             read_only=True)
    if not rows or float(rows[0]["score"]) > PLAYBOOK_MAX_DISTANCE:
        nearest = round(float(rows[0]["score"]), 3) if rows else None
        rt.hub.emit(emit_run, "playbook_miss", agent=agent, tool="memory_recall_playbook",
                    summary="No similar past episode with a playbook" + (f" (nearest distance {nearest})" if nearest is not None else ""))
        return {"playbook": None, "nearest_distance": nearest}
    best = rows[0]["playbook"]
    steps = [{"order": r["step"], "tool": r["tool"], "args": r["args"]} for r in rows if r["playbook"] == best]
    head = rows[0]
    result = {"playbook": best, "name": head["name"], "decisive": list(head["decisive"] or []),
              "executions": head["executions"], "success_rate": head["success_rate"],
              "from_episode": head["from_episode"], "from_pr": head["from_pr"],
              "from_steps": head["from_steps"], "from_ms": head["from_ms"],
              "distance": round(float(head["score"]), 4), "steps": steps}
    rt.hub.emit(emit_run, "playbook_reused", agent=agent, tool="memory_recall_playbook",
                summary=f"Reusing playbook '{head['name']}' from PR #{head['from_pr']} "
                        f"(distance {result['distance']}, {len(steps)} steps)")
    return result


async def save_playbook(rt: "Runtime", tenant: str, episode_id: str, *, name: str = "", pattern: str = "",
                        decisive: list[str] | None = None, steps: list[dict] | None = None,
                        reuse_of: str | None = None, ok: bool = True, agent: str | None = None,
                        emit_run: str | None = None) -> dict[str, Any]:
    graph = memory_graph(tenant)
    kw = {"run_id": emit_run, "agent": agent, "tool": "memory_save_playbook"}
    if reuse_of:
        stats = await rt.db.query(graph, Q.PLAYBOOK_STATS, {"pb_id": reuse_of}, read_only=True, **kw)
        if not stats:
            raise LookupError(f"playbook {reuse_of} not found")
        execs = int(stats[0]["executions"] or 0)
        rate = float(stats[0]["success_rate"] or 0.0)
        new_rate = round((rate * execs + (1.0 if ok else 0.0)) / (execs + 1), 4)
        rows = await rt.db.query(graph, Q.Q10_REUSE_PLAYBOOK, {"pb_id": reuse_of, "episode_id": episode_id,
                                                                 "success_rate": new_rate, "executions": execs + 1},
                                 label="record playbook reuse", **kw)
        rt.hub.emit(emit_run, "playbook_updated", agent=agent, tool="memory_save_playbook",
                    summary=f"Playbook {reuse_of}: {execs + 1} runs, success rate {new_rate}")
        return {"playbook": reuse_of, "reused": True, **(rows[0] if rows else {})}
    pb_id = new_id("pb")
    steps = steps or []
    rows = await rt.db.query(graph, Q.Q10_SAVE_PLAYBOOK, {
        "episode_id": episode_id, "pb_id": pb_id, "name": name or "Blast-radius check", "pattern": pattern,
        "decisive": decisive or [],
        "steps": [{"id": f"{pb_id}:{i}", "order": i, "tool": s["tool"], "args": json.dumps(s.get("args", {})),
                   "desc": s.get("desc", "")} for i, s in enumerate(steps, start=1)],
    }, label="save playbook", **kw)
    rt.hub.emit(emit_run, "playbook_saved", agent=agent, tool="memory_save_playbook",
                summary=f"Saved playbook '{name}' with {len(steps)} steps; decisive checks: {', '.join(decisive or [])}")
    return {"playbook": pb_id, "reused": False, "steps": len(steps), "saved": bool(rows)}


async def close_episode(rt: "Runtime", tenant: str, episode_id: str, verdict: str, summary: str, steps: int,
                        ms: float, *, agent: str | None = None, emit_run: str | None = None) -> None:
    await rt.db.query(memory_graph(tenant), Q.CLOSE_EPISODE,
                      {"id": episode_id, "verdict": verdict, "summary": summary, "steps": steps, "ms": ms},
                      run_id=emit_run, agent=agent, tool="action_post_verdict", label="close episode")


async def recall_conversation(rt: "Runtime", tenant: str, question: str, *, agent: str | None = None,
                              emit_run: str | None = None) -> dict[str, Any]:
    vec = await rt.embedder.embed_one(question)
    graph = memory_graph(tenant)
    kw = {"run_id": emit_run, "agent": agent, "tool": "memory_recall_conversation", "read_only": True}
    messages = await rt.db.query(graph, Q.Q11_RECALL_CONVERSATION, {"q": vec}, label="recall messages", **kw)
    episodes = await rt.db.query(graph, Q.SIMILAR_EPISODES, {"q": vec}, label="similar episodes", **kw)
    return {
        "messages": [{"session": m["session"], "role": m["role"], "text": m["text"], "ts": m["ts"],
                      "distance": round(float(m["score"]), 4), "episodes_pr": m["episodes_pr"]} for m in messages],
        "episodes": [{"episode": e["episode"], "pr_number": e["pr_number"], "title": e["title"],
                      "verdict": e["verdict"], "summary": e["summary"], "ended_at": e["ended_at"],
                      "distance": round(float(e["score"]), 4)} for e in episodes],
    }


async def append_message(rt: "Runtime", tenant: str, session_id: str, role: str, text: str,
                         sources: list[str] | None = None, title: str = "") -> dict[str, Any]:
    graph = memory_graph(tenant)
    await rt.db.query(graph, Q.ENSURE_SESSION, {"id": session_id, "user": "dashboard", "title": title or text[:60]},
                      emit=False)
    last = await rt.db.query(graph, Q.LAST_MESSAGE, {"sid": session_id}, emit=False)
    msg_id = new_id("msg")
    vec = await rt.embedder.embed_one(text)
    rows = await rt.db.query(graph, Q.APPEND_MESSAGE, {"sid": session_id, "id": msg_id, "role": role, "text": text,
                                                         "sources": sources or [], "embedding": vec}, emit=False)
    if last:
        await rt.db.query(graph, Q.LINK_MESSAGES, {"prev": last[0]["id"], "next": msg_id}, emit=False)
    return {"id": msg_id, "ts": rows[0]["ts"] if rows else None}


async def link_session_episodes(rt: "Runtime", tenant: str, session_id: str, pr_numbers: list[int]) -> None:
    for pr in {int(p) for p in pr_numbers if p is not None}:
        await rt.db.query(memory_graph(tenant), Q.LINK_SESSION_EPISODE, {"sid": session_id, "pr": pr}, emit=False)


async def overview(rt: "Runtime", tenant: str) -> dict[str, Any]:
    graph = memory_graph(tenant)
    if not await rt.db.exists(graph):
        return {"counts": {}, "facts": [], "playbooks": [], "episodes": []}
    counts = await rt.db.query(graph, Q.MEMORY_COUNTS, emit=False, read_only=True)
    facts = await rt.db.query(graph, Q.RECENT_FACTS, emit=False, read_only=True)
    playbooks = await rt.db.query(graph, Q.PLAYBOOKS, emit=False, read_only=True)
    episodes = await rt.db.query(graph, Q.RECENT_EPISODES, emit=False, read_only=True)
    return {"counts": counts[0] if counts else {}, "facts": facts, "playbooks": playbooks, "episodes": episodes}


async def sessions(rt: "Runtime", tenant: str) -> list[dict[str, Any]]:
    return await rt.db.query(memory_graph(tenant), Q.SESSIONS, emit=False, read_only=True)


async def session_messages(rt: "Runtime", tenant: str, session_id: str) -> list[dict[str, Any]]:
    return await rt.db.query(memory_graph(tenant), Q.SESSION_MESSAGES, {"sid": session_id}, emit=False,
                             read_only=True)
