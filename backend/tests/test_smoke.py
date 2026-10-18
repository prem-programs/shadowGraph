"""Every FalkorDB behaviour the product depends on, checked on the real server."""
from __future__ import annotations

import asyncio

GRAPH = "test_smoke"


async def _fresh(rt, name=GRAPH):
    await rt.db.delete(name)
    return name


async def test_graph_copy_is_independent_and_keeps_indexes(rt):
    g = await _fresh(rt)
    await rt.db.query(g, "CREATE (:S {id:'a'})-[:C {via:'x'}]->(:S {id:'b'})", emit=False)
    await rt.db.query(g, "CREATE INDEX FOR (s:S) ON (s.id)", emit=False)
    await rt.db.query(g, "CREATE VECTOR INDEX FOR (d:D) ON (d.v) OPTIONS {dimension: 2, similarityFunction: 'cosine'}",
                      emit=False)
    copy = await _fresh(rt, "test_smoke_copy")
    ms = await rt.db.copy(g, copy)
    assert ms > 0
    await rt.db.query(copy, "MATCH ()-[c:C]->() DELETE c", emit=False)
    assert (await rt.db.query(g, "MATCH ()-[c:C]->() RETURN count(c) AS n", emit=False))[0]["n"] == 1
    indexes = await rt.db.query(copy, "CALL db.indexes() YIELD label, properties RETURN label, properties", emit=False)
    assert {i["label"] for i in indexes} == {"S", "D"}
    await rt.db.delete(copy)


async def test_concurrent_copies_are_queued(rt):
    g = await _fresh(rt)
    await rt.db.query(g, "UNWIND range(1, 200) AS i CREATE (:S {id: i})", emit=False)
    names = [f"test_smoke_par_{i}" for i in range(4)]
    for n in names:
        await rt.db.delete(n)
    times = await asyncio.gather(*(rt.db.copy(g, n) for n in names))  # raw GRAPH.COPY would fail: 'could not fork'
    assert len(times) == 4 and all(t > 0 for t in times)
    for n in names:
        assert (await rt.db.query(n, "MATCH (s:S) RETURN count(s) AS n", emit=False))[0]["n"] == 200
        await rt.db.delete(n)


async def test_wcc_map_config_and_isolated_nodes(rt):
    g = await _fresh(rt)
    await rt.db.query(g, "CREATE (:S {id:'a', tier:0})-[:C]->(:S {id:'b', tier:1}), (:S {id:'lonely', tier:2})",
                      emit=False)
    rows = await rt.db.query(g, """CALL algo.WCC({nodeLabels:['S'], relationshipTypes:['C']}) YIELD node, componentId
                                   RETURN componentId, collect(node.id) AS ids""", emit=False)
    assert sorted(sorted(r["ids"]) for r in rows) == [["a", "b"], ["lonely"]]


async def test_exact_betweenness_query_and_sampled_algo(rt):
    from shadowgraph.graph import queries as Q

    g = await _fresh(rt)
    await rt.db.query(g, """CREATE (a:Service {id:'a'})-[:CALLS]->(b:Service {id:'b'})-[:CALLS]->(c:Service {id:'c'}),
                            (c)-[:CALLS]->(d:Service {id:'d'}), (a)-[:CALLS]->(e:Service {id:'e'})-[:CALLS]->(d)""",
                      emit=False)
    exact = {r["service"]: r["score"] for r in await rt.db.query(g, Q.Q5_BETWEENNESS, emit=False)}
    # pairs with an intermediate node: a->c via b, b->d via c, a->d via e (2 hops beats a->b->c->d)
    assert exact == {"b": 1.0, "c": 1.0, "e": 1.0}
    sampled = await rt.db.query(g, Q.Q5_SAMPLED_BETWEENNESS, emit=False)
    assert {r["service"] for r in sampled} == {"a", "b", "c", "d", "e"}  # runs, but samples sources: not exact


async def test_pagerank_ignores_parallel_edges(rt):
    g = await _fresh(rt)
    await rt.db.query(g, """CREATE (a:E {id:'a'}), (b:E {id:'b'}), (c:E {id:'c'}), (d:E {id:'d'}),
                            (a)-[:D]->(c), (a)-[:D]->(c), (a)-[:D]->(c), (b)-[:D]->(d)""", emit=False)
    rows = await rt.db.query(g, "CALL algo.pageRank('E', 'D') YIELD node, score RETURN node.id AS id, score", emit=False)
    scores = {r["id"]: r["score"] for r in rows}
    assert abs(scores["c"] - scores["d"]) < 1e-9  # three edges count the same as one


async def test_vector_score_is_a_distance(rt):
    g = await _fresh(rt)
    await rt.db.query(g, "CREATE VECTOR INDEX FOR (d:D) ON (d.v) OPTIONS {dimension: 2, similarityFunction: 'cosine'}",
                      emit=False)
    await rt.db.query(g, "CREATE (:D {id:'same', v: vecf32([1.0, 0.0])}), (:D {id:'other', v: vecf32([0.0, 1.0])})",
                      emit=False)
    rows = await rt.db.query(g, """CALL db.idx.vector.queryNodes('D', 'v', 2, vecf32($q)) YIELD node, score
                                   RETURN node.id AS id, score ORDER BY score ASC""", {"q": [1.0, 0.0]}, emit=False)
    assert rows[0]["id"] == "same" and rows[0]["score"] < 1e-6 < rows[1]["score"]


async def test_shortest_path_in_with_and_zero_length_paths(rt):
    g = await _fresh(rt)
    await rt.db.query(g, "CREATE (:S {id:'a'})-[:C]->(:S {id:'b'})-[:C]->(:S {id:'c'})", emit=False)
    rows = await rt.db.query(g, """MATCH (a:S {id:'a'}), (c:S {id:'c'})
                                   WITH a, c, shortestPath((a)-[:C*1..6]->(c)) AS p WHERE p IS NOT NULL
                                   RETURN [n IN nodes(p) | n.id] AS chain""", emit=False)
    assert rows[0]["chain"] == ["a", "b", "c"]
    rows = await rt.db.query(g, "MATCH p = (:S {id:'a'})-[:C*0..5]->(x) RETURN x.id AS id, length(p) AS hops "
                                "ORDER BY hops", emit=False)
    assert [r["id"] for r in rows] == ["a", "b", "c"]
    await rt.db.delete(g)
