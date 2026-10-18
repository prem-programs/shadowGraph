"""Scout: opens the episode, recalls a playbook, and posts the hypotheses the others will race for."""
from __future__ import annotations

from ..core.memory import CHECKS, DEFAULT_CHECKS
from . import llm
from .base import Agent

PLANNER_TOOLS = {"brain_trace_decision", "brain_search_decisions", "brain_find_expert", "memory_recall_playbook"}
CHECK_HELP = {
    "partition": "does removing it split the service mesh (WCC on a shadow copy)",
    "chokepoint": "does any service's betweenness jump (betweenness on a shadow copy)",
    "entry_path": "do tier 0 entry points lose their path to the provider",
    "decision": "does a decision in force forbid the change",
    "expert": "who should review it (PageRank)",
}


class Scout(Agent):
    role = "scout"

    async def open(self) -> None:
        opened = await self.call("memory_open_episode", pr_number=self.ctx.pr_number)
        self.ctx.episode_id = opened["episode_id"]
        self.ctx.pr = opened["pr"]
        self.ctx.target = opened["target"] or {}
        if not self.ctx.pr.get("removals"):
            raise ValueError(f"PR #{self.ctx.pr_number} removes nothing that can be simulated "
                             "(add a line like 'Removes: /v1/route' to its description)")
        recall = await self.call("memory_recall_playbook", text=opened["symptom_text"],
                                 exclude_episode=self.ctx.episode_id)
        self.ctx.playbook = recall if recall.get("playbook") else None

    async def plan(self) -> list[str]:
        if self.ctx.playbook:
            decisive = [c for c in self.ctx.playbook.get("decisive", []) if c in CHECKS]
            checks = list(dict.fromkeys(["partition", *decisive, "decision", "expert"]))
            self.ctx.emit("plan", agent=self.name, summary=f"Playbook says only these checks matter: {', '.join(checks)}")
            return checks
        if self.ctx.mode == "llm":
            checks = await self._llm_plan()
            if checks:
                return checks
        return list(DEFAULT_CHECKS)

    async def _llm_plan(self) -> list[str] | None:
        cfg = self.ctx.rt.settings
        tools = (await self.client.list_tools()).tools
        pr = self.ctx.pr
        brief = (f"PR #{pr['number']}: {pr['title']}\n{pr.get('description') or ''}\n"
                 f"Change: remove {self.ctx.target.get('route')} on {self.ctx.target.get('provider')} "
                 f"(endpoint id {self.ctx.target.get('target')}). Tenant: {self.ctx.tenant}.")

        async def call_tool(name: str, args: dict) -> dict:
            return await self.call(name, **args)

        checks, why, transcript = await llm.plan_checks(cfg, tools, PLANNER_TOOLS, brief, CHECK_HELP, call_tool)
        if checks:
            self.ctx.emit("plan", agent=self.name, summary=f"Gemini chose {', '.join(checks)}: {why}",
                          llm_tools=[t["tool"] for t in transcript])
            return checks
        self.ctx.emit("llm_fallback", agent=self.name, summary=f"Planning switched to rules: {why}")
        return None

    async def post(self, checks: list[str]) -> None:
        posted = await self.call("memory_post_hypotheses", episode_id=self.ctx.episode_id, checks=checks,
                                 target_id=self.ctx.target["target"])
        for h in posted["hypotheses"]:
            self.ctx.hypotheses[h["label"]] = h
