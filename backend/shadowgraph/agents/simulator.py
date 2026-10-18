"""Simulator workers: race for 'sim' hypotheses; each forks its own shadow graph per hypothesis."""
from __future__ import annotations

from typing import Any

from .base import Agent


class Simulator(Agent):
    role = "simulator"
    kind = "sim"

    async def work(self) -> None:
        h1 = self.ctx.hypotheses.get("H1")
        prefer = h1["id"] if h1 else ""
        while True:
            claimed = await self.call("memory_claim", episode_id=self.ctx.episode_id, kind=self.kind, prefer=prefer)
            prefer = ""  # only the first claim targets H1, so the race is visible
            if not claimed.get("claimed"):
                return
            await self.investigate(claimed)

    async def investigate(self, h: dict[str, Any]) -> None:
        target = self.ctx.target
        fork = await self.call("sim_fork", hyp_id=h["label"])
        self.ctx.forks.append(fork["copy_ms"])
        try:
            await self.call("sim_apply_change", shadow=fork["shadow"], change_type="remove_endpoint",
                            endpoint_id=target["target"])
            m = await self.call("sim_measure", shadow=fork["shadow"], endpoint_id=target["target"])
        finally:
            await self.call("sim_drop", shadow=fork["shadow"])
        check = h["check"]
        if check == "partition":
            ok = bool(m["islands"])
            summary = (f"{m['components_shadow']} components (live {m['components_live']}); islands: "
                       + "; ".join(", ".join(i["ids"]) for i in m["islands"])) if ok else "No new islands"
        elif check == "chokepoint":
            ok = bool(m["rising_chokepoints"])
            summary = (", ".join(f"{c['service']} +{c['pct']}%" for c in m["rising_chokepoints"]) if ok
                       else "No service's betweenness rises 50% into the top 3")
        else:  # entry_path
            ok = bool(m["entries_cut_off"])
            summary = (f"Cut off from {m['provider']}: {', '.join(m['entries_cut_off'])}" if ok
                       else "Every tier 0 entry point keeps its path")
        self.ctx.results[check] = {"status": "CONFIRMED" if ok else "REJECTED", "summary": summary, "measure": m,
                                   "copy_ms": fork["copy_ms"], "agent": self.name}
        await self.call("memory_resolve", hyp_id=h["claimed"], status="CONFIRMED" if ok else "REJECTED",
                        result={"summary": summary, "components_shadow": m["components_shadow"],
                                "islands": [i["ids"] for i in m["islands"]]})
