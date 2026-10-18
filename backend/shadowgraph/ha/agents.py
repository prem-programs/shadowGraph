"""The agent team for a Home Assistant change. Every step is an MCP tool call (same server as external agents).

Scout     opens the change (fetch read-only, API diff, consumers), recalls a playbook, posts hypotheses H1-H5
Simulators race for H1-H3; each forks its own shadow copy of the HA graph (GRAPH.COPY), applies the change,
          measures (who loses an import, how failures spread through manifest dependencies, who still uses
          newly deprecated names), drops the copy
Scribe    takes H4-H5: announcements and decisions, reviewers
Judge     cross-checks the shadow measurements against the engine, saves the report, closes the episode,
          records facts and the playbook. Nothing is posted to GitHub.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client

from ..agents.base import Agent, RunContext
from ..core.memory import CHECKS, HA_CHECKS
from ..mcp_server import server
from ..runtime import RunRecord, Runtime
from . import mcp_tools

log = logging.getLogger("shadowgraph.ha.agents")
TENANT = "ha"
SIM_STEPS = ["memory_claim", "sim_fork", "ha_sim_apply", "ha_sim_measure", "sim_drop", "memory_resolve"]
STEPS_BY_CHECK = {"consumers": SIM_STEPS, "cascade": SIM_STEPS, "deadline": SIM_STEPS,
                  "announced": ["memory_claim", "ha_find_announcements", "memory_resolve"],
                  "reviewers": ["memory_claim", "ha_find_reviewers", "memory_resolve"]}


class HAScout(Agent):
    role = "scout"

    async def open(self, sha: str) -> dict[str, Any]:
        opened = await self.call("ha_open_change", number=self.ctx.pr_number, sha=sha)
        self.ctx.episode_id = opened["episode_id"]
        self.ctx.target = opened["target"]
        self.ctx.pr = opened["preview"]
        recall = await self.call("memory_recall_playbook", text=opened["symptom_text"],
                                 exclude_episode=self.ctx.episode_id)
        self.ctx.playbook = recall if recall.get("playbook") else None
        return opened

    def plan(self) -> list[str]:
        p = self.ctx.pr
        if self.ctx.playbook:
            decisive = [c for c in self.ctx.playbook.get("decisive", []) if c in HA_CHECKS]
            checks = list(dict.fromkeys(["consumers", *decisive, "announced", "reviewers"]))
            self.ctx.emit("plan", agent=self.name,
                          summary=f"A similar past change ({self.ctx.playbook.get('name')}) says these checks "
                                  f"decided it: {', '.join(checks)}")
            return checks
        checks = ["consumers", "cascade"]
        if p.get("deprecated"):
            checks.append("deadline")
        checks += ["announced", "reviewers"]
        self.ctx.emit("plan", agent=self.name, summary="Checks: " + ", ".join(
            f"{CHECKS[c][1]} {c}" for c in checks))
        return checks

    async def post(self, checks: list[str]) -> None:
        posted = await self.call("memory_post_hypotheses", episode_id=self.ctx.episode_id, checks=checks,
                                 target_id=self.ctx.target["target"])
        for h in posted["hypotheses"]:
            self.ctx.hypotheses[h["label"]] = h


class HASimulator(Agent):
    role = "simulator"

    async def work(self) -> None:
        h1 = self.ctx.hypotheses.get("H1")
        prefer = h1["id"] if h1 else ""
        while True:
            claimed = await self.call("memory_claim", episode_id=self.ctx.episode_id, kind="sim", prefer=prefer)
            prefer = ""
            if not claimed.get("claimed"):
                return
            await self.investigate(claimed)

    async def investigate(self, h: dict[str, Any]) -> None:
        fork = await self.call("sim_fork", hyp_id=h["label"])
        self.ctx.forks.append(fork["copy_ms"])
        try:
            await self.call("ha_sim_apply", shadow=fork["shadow"])
            if h["check"] == "cascade":  # the cascade needs the consumers marked first
                await self.call("ha_sim_measure", shadow=fork["shadow"], check="consumers")
            m = await self.call("ha_sim_measure", shadow=fork["shadow"], check=h["check"])
        finally:
            await self.call("sim_drop", shadow=fork["shadow"])
        check = h["check"]
        if check == "consumers":
            ok = bool(m["failing_external"] or m["failing_core"])
            summary = (f"{m['failing_external']} custom and {m['failing_core']} core integrations lose a name they "
                       f"import at load or run time; {m['soft_users']} more have a fallback or a type-hint use"
                       if ok else "No integration loses a name it imports")
        elif check == "cascade":
            ok = bool(m["dependents_failing"])
            summary = (f"{m['dependents_failing']} more integrations depend on one that fails to set up"
                       if ok else f"No dependents affected ({m['fail_setup']} integrations fail to set up)")
        else:
            ok = bool(m["users"])
            summary = (f"{m['users']} integrations use names this change deprecates" if ok
                       else "Nobody uses the newly deprecated names")
        self.ctx.results[check] = {"status": "CONFIRMED" if ok else "REJECTED", "summary": summary, "measure": m,
                                   "copy_ms": fork["copy_ms"], "agent": self.name, "label": h["label"]}
        await self.call("memory_resolve", hyp_id=h["claimed"], status="CONFIRMED" if ok else "REJECTED",
                        result={"summary": summary})


class HAScribe(Agent):
    role = "scribe"

    async def work(self) -> None:
        while True:
            claimed = await self.call("memory_claim", episode_id=self.ctx.episode_id, kind="brain")
            if not claimed.get("claimed"):
                return
            if claimed["check"] == "announced":
                a = await self.call("ha_find_announcements")
                ok = bool(a["linked_posts"] or a["decisions"])
                first = (a["linked_posts"] or a["similar_posts"] or [{}])[0]
                summary = (f"Announced: \"{first.get('title')}\"" if a["linked_posts"] else
                           (f"No post names these symbols; closest: \"{first.get('title')}\"" if first else
                            "No announcement found"))
                self.ctx.results["announced"] = {"status": "CONFIRMED" if ok else "REJECTED", "summary": summary,
                                                 "data": a, "agent": self.name, "label": claimed["label"]}
            else:
                rv = await self.call("ha_find_reviewers")
                people = rv["codeowners"][:4] + [p for p in rv["announced_by"] if p not in rv["codeowners"]][:2]
                ok = bool(people)
                summary = ("Review: " + ", ".join(f"@{p}" for p in people)) if ok else "No code owners found"
                self.ctx.results["reviewers"] = {"status": "CONFIRMED" if ok else "REJECTED", "summary": summary,
                                                 "data": rv, "agent": self.name, "label": claimed["label"]}
            await self.call("memory_resolve", hyp_id=claimed["claimed"],
                            status=self.ctx.results[claimed["check"]]["status"],
                            result={"summary": self.ctx.results[claimed["check"]]["summary"]})


class HAJudge(Agent):
    role = "judge"

    async def act(self) -> dict[str, Any]:
        ctx = self.ctx
        stats = {"forks": len(ctx.forks), "copy_ms": round(min(ctx.forks), 1) if ctx.forks else None,
                 "steps": ctx.steps + 4, "ms": ctx.elapsed_ms}
        results = {k: {kk: vv for kk, vv in v.items()} for k, v in ctx.results.items()}
        saved = await self.call("ha_post_report", results=results, stats=stats)
        report = mcp_tools.report_of(ctx.rt, ctx.run_id) or {}
        removed = [s for s in report.get("api", {}).get("removed", []) if not s.get("consequence_of")]
        for s in removed[:10]:
            users = [c for c in report.get("consumers", []) if s["id"] in c.get("symbols", [])
                     and c["level"] == "breaks"]
            await self.call("memory_record_fact", entity=s["id"], attribute="safe_to_remove", value=not users,
                            source=f"{saved['change_id']}: {len(users)} integrations break", episode_id=ctx.episode_id)
        decisive = [c for c, r in ctx.results.items() if r["status"] == "CONFIRMED"]
        if ctx.playbook:
            await self.call("memory_save_playbook", episode_id=ctx.episode_id, reuse_of=ctx.playbook["playbook"],
                            ok=True)
        else:
            steps = [{"tool": t, "args": {}} for t in ("ha_open_change", "memory_recall_playbook",
                                                       "memory_post_hypotheses")]
            for check in decisive or ["consumers"]:
                steps += [{"tool": t, "args": {"check": check}} for t in STEPS_BY_CHECK[check]]
            steps += [{"tool": "ha_post_report", "args": {}}, {"tool": "memory_save_playbook", "args": {}}]
            kind = ("API removal" if removed else "deprecation" if report.get("api", {}).get("deprecated")
                    else "core change")
            await self.call("memory_save_playbook", episode_id=ctx.episode_id,
                            name=f"Home Assistant {kind}: {', '.join(report.get('modules', [])[:2]) or 'core'}",
                            pattern=kind, decisive=decisive, steps=steps)
        report["agent_stats"] = stats | {"steps": ctx.steps, "ms": ctx.elapsed_ms}
        report["hypotheses"] = [{"label": h["label"], "check": h["check"],
                                 "status": ctx.results.get(h["check"], {}).get("status", "OPEN"),
                                 "summary": ctx.results.get(h["check"], {}).get("summary", ""),
                                 "agent": ctx.results.get(h["check"], {}).get("agent"),
                                 "copy_ms": ctx.results.get(h["check"], {}).get("copy_ms")}
                                for h in sorted(ctx.hypotheses.values(), key=lambda x: x["label"])]
        report["playbook_reused"] = ({k: ctx.playbook.get(k) for k in ("playbook", "name", "from_pr", "distance")}
                                     if ctx.playbook else None)
        from .impact import save_change
        await save_change(ctx.rt, report)  # with the agent trace included
        return report


async def run_change(rt: Runtime, number: int, sha: str = "", run_id: str = "", workers: int = 3) -> dict[str, Any]:
    await rt.ensure_ready()
    ctx = RunContext(rt=rt, run_id=run_id, tenant=TENANT, pr_number=int(number or 0), mode="rules")
    label = f"PR #{number}" if number else f"commit {sha[:10]}"
    ctx.emit("run_started", agent="orchestrator", summary=f"{label} on home-assistant/core: Scout, Scribe, "
             f"{workers} Simulators, Judge (read-only)")
    failure: Exception | None = None
    report: dict[str, Any] = {}
    try:
        async with AsyncExitStack() as stack:
            async def session() -> Client:
                return await stack.enter_async_context(Client(server, raise_exceptions=False))
            try:
                scout = HAScout("scout", ctx, await session())
                scribe = HAScribe("scribe", ctx, await session())
                sims = [HASimulator(f"sim-{i + 1}", ctx, await session()) for i in range(workers)]
                judge = HAJudge("judge", ctx, await session())
                await scout.open(sha)
                await scout.post(scout.plan())
                outcomes = await asyncio.gather(scribe.work(), *(s.work() for s in sims), return_exceptions=True)
                errors = [o for o in outcomes if isinstance(o, BaseException)]
                if errors:
                    raise errors[0]
                report = await judge.act()
            except Exception as exc:
                failure = exc
        if failure is not None:
            raise failure
        v = report["verdict"]
        ctx.emit("run_finished", agent="orchestrator", status="done", verdict=v["level"],
                 summary=f"{v['level']}: {v['headline']} ({ctx.elapsed_ms} ms, {ctx.steps} agent steps)",
                 steps=ctx.steps, ms=ctx.elapsed_ms)
        return {"run_id": run_id, "tenant": TENANT, "report": report, "steps": ctx.steps, "ms": ctx.elapsed_ms,
                "change_id": report.get("change", {}) and _cid(report)}
    except Exception as exc:
        log.exception("HA run %s failed", run_id)
        msg = str(exc).split("\n")[0]
        ctx.emit("error", agent="orchestrator", summary=msg)
        ctx.emit("run_finished", agent="orchestrator", status="error", summary=f"Run failed: {msg}",
                 steps=ctx.steps, ms=ctx.elapsed_ms)
        raise
    finally:
        mcp_tools.forget_run(rt, run_id)


def _cid(report: dict[str, Any]) -> str:
    from .impact import change_id
    return change_id(report["change"])


def start_change_run(rt: Runtime, number: int, sha: str = "") -> RunRecord:
    from ..agents.orchestrator import new_run_id

    run_id = new_run_id()
    record = RunRecord(run_id=run_id, tenant=TENANT, pr_number=int(number or 0), mode="rules",
                       started_at=int(time.time() * 1000))
    rt.remember_run(record)

    async def runner() -> None:
        try:
            record.result = await run_change(rt, number, sha, run_id, rt.settings.sim_workers)
            record.status = "done"
        except Exception as exc:
            record.status = "error"
            record.error = str(exc).split("\n")[0]

    record.task = asyncio.create_task(runner())
    return record
