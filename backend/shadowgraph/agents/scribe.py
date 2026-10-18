"""Scribe: claims 'brain' hypotheses: which decision is in force, and who must review."""
from __future__ import annotations

from typing import Any

from .base import Agent


class Scribe(Agent):
    role = "scribe"
    kind = "brain"

    async def work(self) -> None:
        while True:
            claimed = await self.call("memory_claim", episode_id=self.ctx.episode_id, kind=self.kind)
            if not claimed.get("claimed"):
                return
            if claimed["check"] == "decision":
                await self.decision(claimed)
            else:
                await self.expert(claimed)

    async def decision(self, h: dict[str, Any]) -> None:
        d = await self.call("brain_trace_decision", pr_number=self.ctx.pr_number)
        # The PR author's belief, from the decision they cite (kept unless memory already knows better).
        for item in d["decisions_in_force"]:
            if item.get("cited") and item["cited"] != item["adr"]:
                await self.call("memory_record_fact", entity=self.ctx.target["target"], attribute="safe_to_remove",
                                value=True, source=f"{item['cited']} (cited by the PR author)",
                                episode_id=self.ctx.episode_id, if_absent=True)
                break
        ok = bool(d["blockers"])
        if ok:
            b = d["blockers"][0]
            summary = f"{b['adr']} is in force and forbids it" + (f" (PR cites {b['cited']}, replaced)"
                                                                     if b.get("cited") and b["cited"] != b["adr"] else "")
        else:
            active = ", ".join(x["adr"] for x in d["decisions_in_force"]) or "none"
            summary = f"No decision in force forbids it (in force: {active})"
        self.ctx.results["decision"] = {"status": "CONFIRMED" if ok else "REJECTED", "summary": summary,
                                        "decision": d, "agent": self.name}
        await self.call("memory_resolve", hyp_id=h["claimed"], status="CONFIRMED" if ok else "REJECTED",
                        result={"summary": summary, "in_force": [x["adr"] for x in d["decisions_in_force"]]})

    async def expert(self, h: dict[str, Any]) -> None:
        e = await self.call("brain_find_expert", service_id=self.ctx.target["provider"])
        top = e["experts"][0] if e["experts"] else None
        summary = f"@{top['engineer']} should review" if top else "No contributors found"
        self.ctx.results["expert"] = {"status": "CONFIRMED" if top else "REJECTED", "summary": summary,
                                      "expert": e, "agent": self.name}
        await self.call("memory_resolve", hyp_id=h["claimed"], status="CONFIRMED" if top else "REJECTED",
                        result={"summary": summary})
