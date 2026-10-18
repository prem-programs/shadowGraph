"""The whole agent loop through MCP: plan, race, simulate, decide, remember, and reuse the playbook."""
from __future__ import annotations

import json

from mcp import Client

from shadowgraph.agents.orchestrator import run_pr
from shadowgraph.core import chat, memory
from shadowgraph.mcp_server import server


async def test_mcp_exposes_17_tools(rt):
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert len(names) == 17 and {"simulate_change", "memory_claim", "sim_fork", "action_post_verdict"} <= names


async def test_simulate_change_tool(rt):
    async with Client(server) as c:
        res = await c.call_tool("simulate_change", {"pr_number": 1})
    data = json.loads(res.content[0].text)
    assert data["verdict"] == "BLOCK" and len(data["measure"]["islands"]) == 2


async def test_full_loop_then_playbook_reuse(fresh):
    first = await run_pr(fresh, "acme", 1, "rules")
    assert first["verdict"]["verdict"] == "BLOCK" and first["playbook_reused"] is None
    events = fresh.hub.history(first["run_id"])
    assert sum(e["type"] == "lock_conflict" for e in events) == 2  # two simulators lose the race for H1
    assert sum(e["type"] == "fork_created" for e in events) == 3  # three shadow graphs, one per sim hypothesis
    statuses = {h["check"]: h["status"] for h in first["report"]["hypotheses"]}
    assert statuses == {"partition": "CONFIRMED", "chokepoint": "REJECTED", "entry_path": "CONFIRMED",
                        "decision": "CONFIRMED", "expert": "CONFIRMED"}
    assert "@priya" in first["comment"] and "ADR-08 → ADR-19" in first["comment"]

    second = await run_pr(fresh, "acme", 2, "rules")
    assert second["verdict"]["verdict"] == "BLOCK"
    assert second["playbook_reused"] and second["playbook_reused"]["from_pr"] == 1
    assert second["steps"] < first["steps"]
    assert {h["check"] for h in second["report"]["hypotheses"]} == {"partition", "decision", "expert"}

    third = await run_pr(fresh, "acme", 3, "rules")
    assert third["verdict"]["verdict"] == "PASS"

    ov = await memory.overview(fresh, "acme")
    facts = [f for f in ov["facts"] if f["entity"] == "auth-service:/v1/token-verify"]
    current = [f for f in facts if f["valid_until"] is None]
    assert len(current) == 1 and current[0]["value"] is False and current[0]["overrides"]
    assert ov["playbooks"][0]["executions"] == 3
    assert not [g for g in await fresh.db.list_graphs() if "_sim_" in g and g.startswith("test_")]


async def test_llm_mode_without_key_falls_back(fresh):
    res = await run_pr(fresh, "acme", 1, "llm")
    assert res["mode"] == "rules" and res["verdict"]["verdict"] == "BLOCK"
    assert any(e["type"] == "llm_fallback" for e in fresh.hub.history(res["run_id"]))


async def test_chat_remembers(fresh):
    await run_pr(fresh, "acme", 1, "rules")
    a = await chat.answer(fresh, "acme", None, "Did we see a token-verify removal before?")
    assert "PR #1" in a["sources"] and "BLOCK" in a["answer"]
    b = await chat.answer(fresh, "acme", a["session_id"], "what is our retry policy?")
    assert "ADR-17" in b["sources"]
    msgs = await memory.session_messages(fresh, "acme", a["session_id"])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


async def test_tenants_are_isolated(fresh):
    from shadowgraph.graph.seed import reset_tenant

    await reset_tenant(fresh, "globex")
    await run_pr(fresh, "acme", 1, "rules")
    acme = await memory.overview(fresh, "acme")
    globex = await memory.overview(fresh, "globex")
    assert acme["counts"]["episodes"] == 1 and globex["counts"]["episodes"] == 0
