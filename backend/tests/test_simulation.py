"""Fork, change, measure: the three demo PRs give the verdicts the story needs, and the live graph is untouched."""
from __future__ import annotations

import pytest

from shadowgraph.core import brain, sim, verdict
from shadowgraph.db import live_graph


async def _simulate(rt, endpoint_id: str, pr: int):
    forked = await sim.fork(rt, "acme", "t1", f"pr{pr}", emit_run="test")
    try:
        await sim.apply_change(rt, "acme", forked["shadow"], {"type": "remove_endpoint", "endpoint_id": endpoint_id},
                               emit_run="test")
        m = await sim.measure(rt, "acme", forked["shadow"], endpoint_id=endpoint_id, emit_run="test")
    finally:
        await sim.drop(rt, "acme", forked["shadow"], emit_run="test")
    d = await brain.trace_decision(rt, "acme", pr)
    return m, verdict.decide(m, d)


async def test_token_verify_blocks(fresh):
    calls_before = (await fresh.db.query(live_graph("acme"), "MATCH ()-[c:CALLS]->() RETURN count(c) AS n",
                                         emit=False))[0]["n"]
    m, v = await _simulate(fresh, "auth-service:/v1/token-verify", 1)
    assert m["components_live"] == 1 and m["components_shadow"] == 3
    islands = sorted(sorted(i["ids"]) for i in m["islands"])
    assert islands == [["legacy-mobile-bff", "mobile-push"],
                       ["partner-billing", "partner-edge", "partner-gateway", "partner-webhooks"]]
    assert m["entries_cut_off"] == ["partner-edge"]
    assert v["verdict"] == "BLOCK" and {r["check"] for r in v["reasons"]} == {"partition", "decision"}
    calls_after = (await fresh.db.query(live_graph("acme"), "MATCH ()-[c:CALLS]->() RETURN count(c) AS n",
                                        emit=False))[0]["n"]
    assert calls_before == calls_after == 42  # the live graph never changes
    assert not [g for g in await fresh.db.list_graphs() if g.startswith("test_acme_sim_")]


async def test_session_refresh_blocks(fresh):
    m, v = await _simulate(fresh, "auth-service:/v1/session-refresh", 2)
    assert [sorted(i["ids"]) for i in m["islands"]] == [["kiosk-bff", "kiosk-sync"]]
    assert v["verdict"] == "BLOCK"


async def test_jwks_passes(fresh):
    m, v = await _simulate(fresh, "auth-service:/v1/jwks", 3)
    assert m["islands"] == [] and m["components_shadow"] == 1
    assert v["verdict"] == "PASS"


async def test_drop_refuses_live_graph(fresh):
    with pytest.raises(sim.SimError):
        await sim.drop(fresh, "acme", live_graph("acme"))
    assert await fresh.db.exists(live_graph("acme"))
