"""Judge: applies the verdict rules, publishes the verdict, and writes what was learned to memory."""
from __future__ import annotations

import json
from typing import Any

from ..core import verdict as rules
from . import llm
from .base import Agent

SIM_STEPS = ["memory_claim", "sim_fork", "sim_apply_change", "sim_measure", "sim_drop", "memory_resolve"]
STEPS_BY_CHECK = {
    "partition": SIM_STEPS, "chokepoint": SIM_STEPS, "entry_path": SIM_STEPS,
    "decision": ["memory_claim", "brain_trace_decision", "memory_record_fact", "memory_resolve"],
    "expert": ["memory_claim", "brain_find_expert", "memory_resolve"],
}


class Judge(Agent):
    role = "judge"

    def _measure(self) -> dict[str, Any] | None:
        for check in ("partition", "entry_path", "chokepoint"):
            if check in self.ctx.results:
                return self.ctx.results[check]["measure"]
        return None

    async def act(self) -> dict[str, Any]:
        ctx = self.ctx
        measure = self._measure()
        decision = ctx.results.get("decision", {}).get("decision")
        v = rules.decide(measure, decision)
        ctx.emit("verdict", agent=self.name, verdict=v["verdict"], summary=f"{v['verdict']}: {v['headline']}",
                 nodes=sorted({s for i in (measure or {}).get("islands", []) for s in i["ids"]}))
        hyps = [{"label": h["label"], "check": h["check"], "status": ctx.results.get(h["check"], {}).get("status", "OPEN"),
                 "summary": ctx.results.get(h["check"], {}).get("summary", ""),
                 "agent": ctx.results.get(h["check"], {}).get("agent")}
                for h in sorted(ctx.hypotheses.values(), key=lambda x: x["label"])]
        report = {
            "run_id": ctx.run_id, "tenant": ctx.tenant,
            "pr": {k: ctx.pr.get(k) for k in ("number", "title", "author", "cites")},
            "target": ctx.target, "verdict": v, "measure": measure, "decision": decision,
            "expert": ctx.results.get("expert", {}).get("expert"), "hypotheses": hyps,
            "stats": {"forks": len(ctx.forks), "copy_ms": round(min(ctx.forks), 1) if ctx.forks else None,
                      "steps": ctx.steps + 3, "ms": ctx.elapsed_ms},
        }

        summary = None
        if ctx.mode == "llm":
            facts = {"verdict": v, "target": ctx.target, "pr": report["pr"],
                     "islands": [i["ids"] for i in (measure or {}).get("islands", [])],
                     "cascade": [c["chain"] for c in (measure or {}).get("cascade", [])],
                     "decisions": [{"adr": d["adr"], "title": d["title"], "cited": d.get("cited")}
                                   for d in (decision or {}).get("decisions_in_force", [])],
                     "expert": (report["expert"] or {}).get("experts", [])[:1]}
            summary, err = await llm.write_text(
                ctx.rt.settings,
                system=("You write the opening of a pull request review comment. 2 or 3 plain sentences. Use ONLY "
                        "the facts given; do not add numbers, names or links that are not in them. No headings."),
                prompt="Facts (JSON):\n" + json.dumps(facts, default=str))
            if summary:
                ctx.emit("llm_text", agent=self.name, summary="Gemini wrote the comment summary")
            else:
                ctx.emit("llm_fallback", agent=self.name, summary=f"Comment summary switched to the template: {err}")

        posted = await self.call("action_post_verdict", episode_id=ctx.episode_id, pr_number=ctx.pr_number,
                                 report=report, summary=summary or "", steps=ctx.steps + 3, ms=ctx.elapsed_ms)
        await self.call("memory_record_fact", entity=ctx.target["target"], attribute="safe_to_remove",
                        value=v["verdict"] == "PASS", source=f"simulation run {ctx.run_id}: {v['headline']}",
                        episode_id=ctx.episode_id)

        decisive = list(dict.fromkeys(["partition"] + [r["check"] for r in v["reasons"]] + ["decision", "expert"]))
        if ctx.playbook:
            pb = await self.call("memory_save_playbook", episode_id=ctx.episode_id, reuse_of=ctx.playbook["playbook"],
                                 ok=True)
        else:
            steps = [{"tool": t, "args": {}} for t in ("memory_open_episode", "memory_recall_playbook",
                                                       "memory_post_hypotheses")]
            for check in decisive:
                steps += [{"tool": t, "args": {"check": check}} for t in STEPS_BY_CHECK[check]]
            steps += [{"tool": t, "args": {}} for t in ("action_post_verdict", "memory_record_fact",
                                                       "memory_save_playbook")]
            route = ctx.target.get("route") or ctx.target.get("target")
            pb = await self.call("memory_save_playbook", episode_id=ctx.episode_id,
                                 name=f"Removing a shared endpoint ({route})",
                                 pattern=f"remove endpoint on {ctx.target.get('provider')}",
                                 decisive=decisive, steps=steps)
        report["stats"]["steps"] = ctx.steps
        report["stats"]["ms"] = ctx.elapsed_ms
        return {"report": report, "github": posted["github"], "comment": posted["comment"], "playbook": pb}
