"""The seeded company answers the demo's questions correctly."""
from __future__ import annotations

from shadowgraph.core import brain
from shadowgraph.db import live_graph
from shadowgraph.graph import queries as Q


async def test_seed_shape(fresh):
    g = live_graph("acme")
    counts = {lbl: (await fresh.db.query(g, f"MATCH (n:{lbl}) RETURN count(n) AS n", emit=False))[0]["n"]
              for lbl in ("Service", "Endpoint", "PullRequest", "DecisionRecord", "Engineer", "Team")}
    assert counts == {"Service": 25, "Endpoint": 29, "PullRequest": 30, "DecisionRecord": 20, "Engineer": 15, "Team": 5}
    wcc = await fresh.db.query(g, Q.Q4_WCC, emit=False)
    assert len(wcc) == 1 and wcc[0]["services"] == 25  # one connected company


async def test_q1_follows_superseded_by(fresh):
    d = await brain.trace_decision(fresh, "acme", 1)
    item = d["decisions_in_force"][0]
    assert item["cited"] == "ADR-08" and item["adr"] == "ADR-19" and item["chain"] == ["ADR-08", "ADR-19"]
    assert [b["adr"] for b in d["blockers"]] == ["ADR-19"]
    assert item["author"] == "priya" and item["discussion"].startswith("https://chat.acme.example/")


async def test_q1_pass_case_has_no_blocker(fresh):
    d = await brain.trace_decision(fresh, "acme", 3)
    assert d["decisions_in_force"][0]["adr"] == "ADR-15" and d["blockers"] == []


async def test_q2_skips_replaced_decisions(fresh):
    res = await brain.search_decisions(fresh, "acme", "what is our retry policy")
    ids = [x["adr"] for x in res["decisions"]]
    assert ids[0] == "ADR-17" and "ADR-04" not in ids
    assert res["decisions"][0]["replaces"] == ["ADR-04"]


async def test_q7_expert_is_priya(fresh):
    res = await brain.find_expert(fresh, "acme", "auth-service")
    assert res["experts"][0]["engineer"] == "priya"


async def test_q6_cascade_chain(fresh):
    rows = await fresh.db.query(live_graph("acme"), Q.Q6_CASCADE, {"endpoint_id": "auth-service:/v1/token-verify"},
                                emit=False)
    assert rows[0]["chain"] == ["partner-edge", "partner-gateway"]


async def test_q5_exact_betweenness_matches_brandes(fresh):
    """Independent check: Brandes' algorithm in Python on the same edges gives the same scores."""
    from collections import defaultdict, deque

    g = live_graph("acme")
    edges = [(r["a"], r["b"]) for r in await fresh.db.query(
        g, "MATCH (a:Service)-[:CALLS]->(b:Service) RETURN DISTINCT a.id AS a, b.id AS b", emit=False)]
    nodes = [r["id"] for r in await fresh.db.query(g, "MATCH (s:Service) RETURN s.id AS id", emit=False)]
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
    expected = dict.fromkeys(nodes, 0.0)
    for s in nodes:
        order, preds, sigma, dist = [], defaultdict(list), dict.fromkeys(nodes, 0), dict.fromkeys(nodes, -1)
        sigma[s], dist[s], queue = 1, 0, deque([s])
        while queue:
            v = queue.popleft()
            order.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    queue.append(w)
                    dist[w] = dist[v] + 1
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta = dict.fromkeys(nodes, 0.0)
        while order:
            w = order.pop()
            for v in preds[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                expected[w] += delta[w]
    got = dict.fromkeys(nodes, 0.0)
    got.update({r["service"]: r["score"] for r in await fresh.db.query(g, Q.Q5_BETWEENNESS, emit=False)})
    assert all(abs(expected[n] - got[n]) < 1e-9 for n in nodes)
