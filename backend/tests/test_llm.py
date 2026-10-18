"""Gemini plumbing with a fake client: builds the real google-genai request objects, no network."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from google.genai import types

from shadowgraph.agents import llm
from shadowgraph.agents.orchestrator import run_pr
from shadowgraph.config import settings


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def generate_content(self, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        step = self.script.pop(0)
        if step["kind"] == "text":
            return SimpleNamespace(text=step["text"], function_calls=None, candidates=[])
        call = types.FunctionCall(name=step["name"], args=step["args"])
        content = types.Content(role="model", parts=[types.Part(function_call=call)])
        return SimpleNamespace(text=None, function_calls=[call], candidates=[SimpleNamespace(content=content)])


def _fake(monkeypatch, script):
    models = FakeModels(script)
    monkeypatch.setattr(llm, "_client", lambda cfg: SimpleNamespace(aio=SimpleNamespace(models=models)))
    return models


async def test_planner_calls_mcp_tool_then_proposes(fresh, monkeypatch):
    models = _fake(monkeypatch, [
        {"kind": "call", "name": "brain_trace_decision", "args": {"pr_number": 1}},
        {"kind": "call", "name": "propose_hypotheses", "args": {"checks": ["partition", "expert"], "reasoning": "x"}},
        {"kind": "text", "text": "This PR would cut off partner traffic. ADR-19 forbids it."},
    ])
    cfg = replace(settings, gemini_api_key="fake-key")
    monkeypatch.setattr(fresh, "settings", cfg)
    res = await run_pr(fresh, "acme", 1, "llm")
    assert res["mode"] == "llm" and res["verdict"]["verdict"] == "BLOCK"
    checks = {h["check"] for h in res["report"]["hypotheses"]}
    assert checks == {"partition", "expert", "decision"}  # 'decision' is always added
    assert "cut off partner traffic" in res["comment"]
    tool_names = {d.name for d in models.requests[0]["config"].tools[0].function_declarations}
    assert {"brain_trace_decision", "propose_hypotheses"} <= tool_names and "sim_drop" not in tool_names
    events = [e["type"] for e in fresh.hub.history(res["run_id"])]
    assert "plan" in events and "llm_text" in events and "llm_fallback" not in events


async def test_planner_failure_falls_back(fresh, monkeypatch):
    def boom(cfg):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(llm, "_client", boom)
    monkeypatch.setattr(fresh, "settings", replace(settings, gemini_api_key="fake-key"))
    res = await run_pr(fresh, "acme", 1, "llm")
    assert res["verdict"]["verdict"] == "BLOCK" and len(res["report"]["hypotheses"]) == 5
    assert sum(e["type"] == "llm_fallback" for e in fresh.hub.history(res["run_id"])) == 2  # plan + comment
