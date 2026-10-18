"""Shared run context and the base agent, which reaches every capability through an MCP client session."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import Client

from ..runtime import Runtime


class ToolCallError(RuntimeError):
    pass


@dataclass
class RunContext:
    rt: Runtime
    run_id: str
    tenant: str
    pr_number: int
    mode: str  # rules | llm
    started: float = field(default_factory=time.perf_counter)
    steps: int = 0
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    episode_id: str = ""
    pr: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    playbook: dict[str, Any] | None = None
    hypotheses: dict[str, dict[str, Any]] = field(default_factory=dict)  # label -> posted hypothesis
    results: dict[str, dict[str, Any]] = field(default_factory=dict)  # check -> result
    forks: list[float] = field(default_factory=list)

    def emit(self, type: str, **fields: Any) -> None:
        self.rt.hub.emit(self.run_id, type, **fields)

    @property
    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)


class Agent:
    role = "agent"

    def __init__(self, name: str, ctx: RunContext, client: Client) -> None:
        self.name = name
        self.ctx = ctx
        self.client = client

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        """Call an MCP tool as this agent. Run id, tenant and agent name are injected."""
        payload = {"tenant": self.ctx.tenant, "run_id": self.ctx.run_id, "agent": self.name, **args}
        self.ctx.steps += 1
        self.ctx.tool_log.append({"agent": self.name, "tool": tool,
                                  "check": args.get("check") or args.get("kind") or ""})
        result = await self.client.call_tool(tool, payload)
        text = "".join(getattr(c, "text", "") for c in (result.content or []))
        if result.is_error:
            raise ToolCallError(f"{tool}: {text}")
        if result.structured_content is not None:
            data = result.structured_content
            return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {"text": text}
