"""Gemini helpers. Every function returns None on any failure; callers then fall back to rules."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from ..config import Settings

log = logging.getLogger("shadowgraph.llm")
INJECTED = {"tenant", "run_id", "agent"}  # filled in by the agent, never by the model


def _client(cfg: Settings):
    from google import genai  # imported lazily so the package works without it configured

    return genai.Client(api_key=cfg.gemini_api_key)


def _strip_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = json.loads(json.dumps(schema or {"type": "object", "properties": {}}))
    props = schema.get("properties", {})
    for key in INJECTED:
        props.pop(key, None)
    schema["required"] = [r for r in schema.get("required", []) if r not in INJECTED]
    if not schema["required"]:
        schema.pop("required")
    return schema


async def write_text(cfg: Settings, system: str, prompt: str, timeout: float = 40.0) -> tuple[str | None, str | None]:
    """Returns (text, error)."""
    if not cfg.llm_available:
        return None, "no GEMINI_API_KEY"
    try:
        from google.genai import types

        client = _client(cfg)
        resp = await asyncio.wait_for(client.aio.models.generate_content(
            model=cfg.gemini_model, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.2, max_output_tokens=600)),
            timeout)
        text = (resp.text or "").strip()
        return (text or None), (None if text else "empty response")
    except Exception as exc:
        log.warning("Gemini text call failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"[:300]


async def plan_checks(cfg: Settings, tools: list[Any], allowed: set[str], pr_brief: str, checks: dict[str, str],
                      call_tool: Callable[[str, dict], Awaitable[dict]], max_calls: int = 8,
                      timeout: float = 40.0) -> tuple[list[str] | None, str, list[dict]]:
    """Let Gemini pick MCP tools to investigate, then propose which checks to run.

    Returns (checks or None, reasoning or error, transcript of tool calls)."""
    transcript: list[dict] = []
    if not cfg.llm_available:
        return None, "no GEMINI_API_KEY", transcript
    try:
        from google.genai import types

        decls = [types.FunctionDeclaration(name=t.name, description=t.description or t.name,
                                           parameters_json_schema=_strip_schema(t.input_schema))
                 for t in tools if t.name in allowed]
        decls.append(types.FunctionDeclaration(
            name="propose_hypotheses",
            description="Finish planning: choose which checks the simulator and scribe agents should run.",
            parameters_json_schema={"type": "object", "properties": {
                "checks": {"type": "array", "items": {"type": "string", "enum": sorted(checks)},
                           "description": "; ".join(f"{k}: {v}" for k, v in checks.items())},
                "reasoning": {"type": "string", "description": "One sentence on why"}},
                "required": ["checks", "reasoning"]}))
        config = types.GenerateContentConfig(
            system_instruction=("You plan a blast-radius investigation for a pull request in a company graph. "
                                "You may call the provided tools to look things up, then you MUST call "
                                "propose_hypotheses. Always include 'partition' and 'decision'."),
            tools=[types.Tool(function_declarations=decls)], temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        client = _client(cfg)
        contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=pr_brief)])]
        for _ in range(max_calls):
            resp = await asyncio.wait_for(client.aio.models.generate_content(
                model=cfg.gemini_model, contents=contents, config=config), timeout)
            calls = resp.function_calls or []
            if not calls:
                return None, "model stopped without proposing checks", transcript
            contents.append(resp.candidates[0].content)
            parts = []
            for fc in calls:
                args = dict(fc.args or {})
                if fc.name == "propose_hypotheses":
                    chosen = [c for c in args.get("checks", []) if c in checks]
                    for must in ("partition", "decision"):
                        if must not in chosen:
                            chosen.append(must)
                    return chosen, str(args.get("reasoning", ""))[:300], transcript
                if fc.name not in allowed:
                    result: dict = {"error": f"tool {fc.name} is not available"}
                else:
                    try:
                        result = await call_tool(fc.name, args)
                    except Exception as exc:
                        result = {"error": str(exc)[:300]}
                transcript.append({"tool": fc.name, "args": args})
                parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
            contents.append(types.Content(role="user", parts=parts))
        return None, f"no proposal within {max_calls} calls", transcript
    except Exception as exc:
        log.warning("Gemini planning failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"[:300], transcript
