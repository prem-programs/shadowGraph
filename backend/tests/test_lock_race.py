"""The hypothesis lock under real concurrency: separate connections, many workers, zero double claims."""
from __future__ import annotations

import asyncio
from collections import Counter

from shadowgraph.db import GraphDB, memory_graph
from shadowgraph.events import EventHub
from shadowgraph.graph import queries as Q


async def _setup(rt, episode: str, n: int) -> None:
    g = memory_graph("acme")
    await rt.db.query(g, "CREATE (:Episode {id: $id, status: 'open'})", {"id": episode}, emit=False)
    hyps = [{"id": f"{episode}:H{i}", "label": f"H{i}", "kind": "sim", "check": "partition", "target_id": "x",
             "summary": "race", "priority": i % 3} for i in range(n)]
    await rt.db.query(g, Q.Q8_POST_HYPOTHESES, {"episode_id": episode, "hyps": hyps}, emit=False)


async def test_150_claims_three_workers_no_double_claims(fresh):
    episode = "ep_race"
    await _setup(fresh, episode, 150)
    g = memory_graph("acme")
    # each worker gets its own client, i.e. its own connections, like separate processes would
    clients = [GraphDB(fresh.settings.falkordb_url, EventHub()) for _ in range(3)]

    async def worker(i: int, db: GraphDB) -> list[str]:
        got = []
        while True:
            rows = await db.query(g, Q.Q8_CLAIM_NEXT, {"episode_id": episode, "agent_id": f"w{i}", "kind": "sim"},
                                  emit=False)
            if not rows:
                return got
            got.append(rows[0]["claimed"])

    results = await asyncio.gather(*(worker(i, db) for i, db in enumerate(clients)))
    claimed = [h for r in results for h in r]
    assert len(claimed) == 150 and len(set(claimed)) == 150  # every hypothesis exactly once
    assert all(len(r) > 0 for r in results)  # all three workers really competed
    owners = await fresh.db.query(g, """MATCH (:Episode {id: $e})-[:INVESTIGATING]->(h:Hypothesis)
                                         RETURN h.claimed_by AS who, count(h) AS n""", {"e": episode}, emit=False)
    assert sum(o["n"] for o in owners) == 150
    for db in clients:
        await db.close()


async def test_ten_workers_one_hypothesis_one_winner(fresh):
    episode = "ep_one"
    await _setup(fresh, episode, 1)
    g = memory_graph("acme")
    clients = [GraphDB(fresh.settings.falkordb_url, EventHub()) for _ in range(10)]
    rows = await asyncio.gather(*(db.query(g, Q.Q8_CLAIM_SPECIFIC, {"episode_id": episode, "hyp_id": f"{episode}:H0",
                                                                   "agent_id": f"w{i}", "kind": "sim"}, emit=False)
                                  for i, db in enumerate(clients)))
    winners = Counter(len(r) for r in rows)
    assert winners[1] == 1 and winners[0] == 9
    for db in clients:
        await db.close()
