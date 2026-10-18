"""Runs one PR end to end: Scout plans, Scribe and the Simulators race in parallel, Judge decides and acts."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client

from ..mcp_server import server
from ..runtime import RunRecord, Runtime
from .base import RunContext
from .judge import Judge
from .scout import Scout
from .scribe import Scribe
from .simulator import Simulator

log = logging.getLogger("shadowgraph.orchestrator")


def new_run_id() -> str:
    return f"r{uuid.uuid4().hex[:8]}"


async def run_pr(rt: Runtime, tenant: str, pr_number: int, mode: str = "rules",
                 run_id: str | None = None) -> dict[str, Any]:
    await rt.ensure_ready()
    run_id = run_id or new_run_id()
    mode = mode if mode in ("rules", "llm") else "rules"
    if mode == "llm" and not rt.settings.llm_available:
        rt.hub.emit(run_id, "llm_fallback", agent="orchestrator",
                    summary="LLM mode asked for, but GEMINI_API_KEY is not set: running in rules mode")
        mode = "rules"
    ctx = RunContext(rt=rt, run_id=run_id, tenant=tenant, pr_number=int(pr_number), mode=mode)
    n_sims = rt.settings.sim_workers
    ctx.emit("run_started", agent="orchestrator", mode=mode,
             summary=f"PR #{pr_number} on {tenant}: Scout, Scribe, {n_sims} Simulators, Judge ({mode} mode)")
    try:
        failure: Exception | None = None
        done: dict[str, Any] = {}
        async with AsyncExitStack() as stack:
            async def session() -> Client:
                return await stack.enter_async_context(Client(server, raise_exceptions=False))

            try:
                scout = Scout("scout", ctx, await session())
                scribe = Scribe("scribe", ctx, await session())
                sims = [Simulator(f"sim-{i + 1}", ctx, await session()) for i in range(n_sims)]
                judge = Judge("judge", ctx, await session())

                await scout.open()
                checks = await scout.plan()
                await scout.post(checks)

                outcomes = await asyncio.gather(scribe.work(), *(s.work() for s in sims), return_exceptions=True)
                errors = [o for o in outcomes if isinstance(o, BaseException)]
                if errors:
                    raise errors[0]
                done = await judge.act()
            except Exception as exc:  # keep it inside so the MCP sessions close cleanly
                failure = exc
        if failure is not None:
            raise failure

        report = done["report"]
        result = {
            "run_id": run_id, "tenant": tenant, "pr_number": int(pr_number), "mode": mode,
            "verdict": report["verdict"], "report": report, "github": done["github"], "comment": done["comment"],
            "playbook": done["playbook"],
            "playbook_reused": ({k: ctx.playbook.get(k) for k in ("playbook", "name", "from_pr", "from_steps",
                                                                     "from_ms", "distance")}
                                if ctx.playbook else None),
            "steps": ctx.steps, "ms": ctx.elapsed_ms, "tool_log": ctx.tool_log, "episode_id": ctx.episode_id,
        }
        ctx.emit("run_finished", agent="orchestrator", status="done", verdict=report["verdict"]["verdict"],
                 summary=f"{report['verdict']['verdict']} in {ctx.elapsed_ms} ms, {ctx.steps} agent steps",
                 steps=ctx.steps, ms=ctx.elapsed_ms)
        return result
    except Exception as exc:
        log.exception("run %s failed", run_id)
        msg = str(exc)
        if "ToolCallError" in type(exc).__name__ or ":" in msg:
            msg = msg.split("\n")[0]
        ctx.emit("error", agent="orchestrator", summary=msg)
        ctx.emit("run_finished", agent="orchestrator", status="error", summary=f"Run failed: {msg}",
                 steps=ctx.steps, ms=ctx.elapsed_ms)
        raise


def start_run(rt: Runtime, tenant: str, pr_number: int, mode: str) -> RunRecord:
    """Start a run in the background and return its record (used by the API and the webhook)."""
    run_id = new_run_id()
    record = RunRecord(run_id=run_id, tenant=tenant, pr_number=int(pr_number), mode=mode,
                       started_at=int(time.time() * 1000))
    rt.remember_run(record)

    async def runner() -> None:
        try:
            record.result = await run_pr(rt, tenant, pr_number, mode, run_id=run_id)
            record.status = "done"
        except Exception as exc:
            record.status = "error"
            record.error = str(exc).split("\n")[0]

    record.task = asyncio.create_task(runner())
    return record
