---
title: "Fork your company before you merge: counterfactual PR checks with FalkorDB"
tags: falkordb, graphdb, ai, hackathon
---

# Fork your company before you merge

*Draft for Dev.to / Hashnode. Edit freely: add your own voice, a screenshot or GIF, and the repo link.*

A reviewer approves a pull request that deletes an old endpoint. The diff is clean, the tests pass, the code search
finds no callers left in the main repo. Two days later the partner API is down: a gateway three hops away still
verified tokens through that endpoint. Nobody could see it, because the dependency lived in a graph nobody had
drawn.

For Graph Hacks (WeMakeDevs × FalkorDB) I built **ShadowGraph**, a merge gate that answers one question before a
PR lands: *what would this break?* It does that by copying the company graph, applying the PR to the copy, and
measuring the damage.

## Why a graph, and why vector search alone is not enough

The company lives in FalkorDB as one graph: services, the endpoints they expose and call, pull requests, decision
records, chat threads, engineers and teams. The PR in the demo cites decision ADR-08 ("retire v1 after the v2
rollout"). A vector search for "token-verify removal" happily returns ADR-08: it is the most similar text. But
ADR-08 was replaced. In the graph that is one edge, `(ADR-08)-[:SUPERSEDED_BY]->(ADR-19)`, and one query follows it:

```cypher
MATCH (pr:PullRequest {number: $pr})-[:CITES]->(cited:DecisionRecord)
MATCH p = (cited)-[:SUPERSEDED_BY*0..5]->(current:DecisionRecord)
WHERE NOT (current)-[:SUPERSEDED_BY]->()
RETURN cited.id, current.id, current.blocks_removal
```

ADR-19 says: keep `/v1/token-verify` until the partner gateway and the old mobile backend move. Search found the
words; the graph found what is true now.

## GRAPH.COPY as a sandbox for "what if"

FalkorDB can copy a whole graph with one command, `GRAPH.COPY`. ShadowGraph copies the live graph into a shadow
graph, deletes the endpoint and every call that went through it, and compares the two with `algo.WCC`:

```cypher
CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['CALLS']})
YIELD node, componentId
RETURN componentId, count(node) AS services, collect(node.id) AS ids, min(node.tier) AS best_tier
```

Live: one connected company. Shadow: three groups. One of the cut-off groups holds a tier 0 service. That is a
BLOCK, posted to the PR as a comment and a failed commit status, with the cascade chain from `shortestPath` and the
decision that forbids it.

## Three things I got wrong, and the server told me

I wrote the plan first and then tested every assumption against a real FalkorDB 4.18. Three were wrong.

**1. Copies are not free, and they do not run in parallel.** My three simulator agents each fork their own shadow
graph. Run simultaneously, `GRAPH.COPY` failed with `could not fork`: the copy forks the server process, and Redis
allows one fork at a time. The fix is a queue (plus a retry across processes). Each copy of the 25-service graph
took 25–100 ms, not the "under 5 ms" I had assumed.

**2. `algo.betweenness` samples.** I planned to flag chokepoints by comparing betweenness before and after the
change. FalkorDB's betweenness picks a sample of source nodes (with replacement), even when you ask for more
samples than there are nodes, and the sample shifts between two graphs. With one seed, a service's score "rose"
from 11 to 51 after a change that could not route a single new path through it. Deltas built on that are noise.
At this size the exact answer is cheap, and FalkorDB can compute it itself:

```cypher
MATCH (a:Service), (b:Service) WHERE a <> b
WITH a, b
MATCH p = allShortestPaths((a)-[:CALLS*]->(b))
WITH a, b, collect(p) AS paths
WITH paths, toFloat(size(paths)) AS n
UNWIND paths AS p
WITH p, n, nodes(p) AS ns
UNWIND ns[1..size(ns) - 1] AS mid
RETURN mid.id AS service, sum(1.0 / n) AS score
```

About 8 ms, and it matches an independent Brandes implementation on every node (that check is in the test suite).

**3. PageRank ignores parallel edges.** I modelled "Ana deferred to Priya three times" as three edges, expecting
weight. FalkorDB's PageRank counted them as one. So the graph stores one `DEFERRED_TO` edge per pair, and the
expert score is PageRank (how many distinct people defer to you) times your commits to the service.

## Agents that share a memory, not a chat log

Five hypotheses ("removal splits the mesh", "a decision forbids it", …) are nodes in a memory graph. Three
simulator agents race for them with one atomic query:

```cypher
MATCH (:Episode {id: $e})-[:INVESTIGATING]->(h:Hypothesis)
WHERE h.status = 'OPEN' AND h.kind = $kind
WITH h ORDER BY h.priority DESC LIMIT 1
SET h.status = 'CLAIMED', h.claimed_by = $agent
RETURN h.id
```

FalkorDB runs write queries against a graph one at a time, so two agents can never claim the same hypothesis. The
test claims 150 hypotheses with three workers on separate connections and finds zero double claims. In the
dashboard you see it happen: two simulators lose the race for H1 and take the next one.

Memory updates itself. The PR author's belief ("safe to remove", from ADR-08) is stored as a fact; the simulation's
result is stored as a newer fact that `OVERRIDES` it. The steps that decided the verdict become a playbook. The next
PR that looks similar (found by vector search over past episodes) skips the exploration, keeps the safety
simulation, and finishes in 23 agent steps instead of 35. Each tenant gets its own memory graph, so one company's
lessons never leak into another's.

## Tools, not glue

Every agent action is an MCP tool call: 17 tools over the company graph, the shadow graphs and memory. The agents
use them in-process; Cursor or Claude Desktop use the same server over HTTP or stdio. An optional Gemini mode lets
the model choose which checks to run by calling those tools, but the verdict always comes from rules over graph
results. The model only writes the opening sentences of the comment.

## What I would do next

Build the service graph from real code (imports, OpenAPI specs, traces) instead of seeding it, and replace the
exact betweenness with an incremental one for large meshes.

*Repo: <link>. Built with Claude as a coding assistant; every FalkorDB behaviour above was checked against a real
server.*
