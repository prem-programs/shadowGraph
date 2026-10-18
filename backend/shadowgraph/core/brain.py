"""Company brain: PR context, decision lineage, decision search, expert ranking."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import live_graph
from ..graph import queries as Q

if TYPE_CHECKING:
    from ..runtime import Runtime


class NotFound(LookupError):
    pass


async def pr_context(rt: "Runtime", tenant: str, pr_number: int, *, agent: str | None = None,
                     emit_run: str | None = None, tool: str = "brain") -> dict[str, Any]:
    rows = await rt.db.query(live_graph(tenant), Q.PR_CONTEXT, {"pr": int(pr_number)}, run_id=emit_run,
                             agent=agent, tool=tool, label="PR context", read_only=True)
    if not rows:
        raise NotFound(f"PR #{pr_number} is not in the {tenant} company graph")
    pr = rows[0]
    pr["modifies"] = [m for m in pr.get("modifies", []) if m.get("target")]
    pr["removals"] = [m for m in pr["modifies"] if m.get("change") == "remove" and m.get("route")]
    return pr


async def trace_decision(rt: "Runtime", tenant: str, pr_number: int, *, agent: str | None = None,
                         emit_run: str | None = None) -> dict[str, Any]:
    """Q1 from the cited decision, plus any decision in force that governs what the PR removes."""
    graph = live_graph(tenant)
    kw = {"run_id": emit_run, "agent": agent, "tool": "brain_trace_decision", "read_only": True}
    pr = await pr_context(rt, tenant, pr_number, agent=agent, emit_run=emit_run, tool="brain_trace_decision")
    lineage = await rt.db.query(graph, Q.Q1_TRACE_DECISION, {"pr": int(pr_number)}, label="decision lineage", **kw)
    governing: list[dict] = []
    seen = set()
    for m in pr["modifies"]:
        for row in await rt.db.query(graph, Q.GOVERNING_IN_FORCE, {"target": m["target"]},
                                     label="decisions in force for target", **kw):
            if row["adr"] not in seen:
                seen.add(row["adr"])
                governing.append(dict(row, target=m["target"]))
    in_force = []
    for row in lineage:
        in_force.append({"adr": row["active_adr"], "title": row["title"], "summary": row["summary"],
                         "blocks_removal": bool(row["blocks_removal"]), "discussion": row["discussion"],
                         "channel": row["channel"], "author": row["author"], "chain": row["chain"],
                         "reasons": row["reasons"], "cited": row["cited_adr"], "cited_title": row["cited_title"],
                         "governs": row["governs"]})
    for row in governing:
        if not any(d["adr"] == row["adr"] for d in in_force):
            in_force.append({"adr": row["adr"], "title": row["title"], "summary": row["summary"],
                             "blocks_removal": bool(row["blocks_removal"]), "discussion": row["discussion"],
                             "channel": row["channel"], "author": row["author"], "chain": [row["adr"]],
                             "reasons": [], "cited": None, "cited_title": None, "governs": [row["target"]]})
    removing = bool(pr["removals"])
    blockers = [d for d in in_force if d["blocks_removal"] and removing]
    superseded_cites = [d for d in in_force if d["cited"] and d["cited"] != d["adr"]]
    result = {"pr": {k: pr[k] for k in ("number", "title", "author", "cites", "modifies", "removals")},
              "decisions_in_force": in_force, "blockers": blockers, "superseded_citations": superseded_cites}
    nodes = [d["adr"] for d in in_force] + [c for d in in_force for c in d["chain"]]
    rt.hub.emit(emit_run, "decision_traced", agent=agent, tool="brain_trace_decision",
                summary=(f"PR cites {', '.join(pr['cites']) or 'nothing'}; in force: "
                         f"{', '.join(d['adr'] for d in in_force) or 'none'}"
                         + (f"; {blockers[0]['adr']} forbids the removal" if blockers else "")),
                nodes=sorted(set(nodes)))
    return result


async def search_decisions(rt: "Runtime", tenant: str, question: str, *, agent: str | None = None,
                           emit_run: str | None = None) -> dict[str, Any]:
    vec = await rt.embedder.embed_one(question)
    rows = await rt.db.query(live_graph(tenant), Q.Q2_SEARCH_DECISIONS, {"q": vec}, run_id=emit_run, agent=agent,
                             tool="brain_search_decisions", label="search decisions in force", read_only=True)
    return {"question": question, "decisions": [
        {"adr": r["adr"], "title": r["title"], "summary": r["summary"], "distance": round(float(r["score"]), 4),
         "source": r["source"], "governs": r["governs"], "replaces": r["replaces"]} for r in rows]}


async def find_expert(rt: "Runtime", tenant: str, service_id: str, *, agent: str | None = None,
                      emit_run: str | None = None) -> dict[str, Any]:
    rows = await rt.db.query(live_graph(tenant), Q.Q7_EXPERT, {"service_id": service_id}, run_id=emit_run,
                             agent=agent, tool="brain_find_expert", label="expert ranking", read_only=True)
    experts = [{"engineer": r["engineer"], "name": r["name"], "timezone": r["timezone"],
                "authority": round(float(r["authority"]), 4), "commits": r["commits"],
                "expertise": round(float(r["expertise"]), 3), "channel": r["channel"]} for r in rows]
    rt.hub.emit(emit_run, "expert_found", agent=agent, tool="brain_find_expert",
                summary=(f"Top expert for {service_id}: @{experts[0]['engineer']}" if experts
                         else f"No contributors found for {service_id}"))
    return {"service_id": service_id, "experts": experts}
