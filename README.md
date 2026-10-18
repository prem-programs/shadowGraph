# ShadowGraph

**A pull-request merge gate that simulates the change on a copy of your company graph before it lands.**
ShadowGraph clones the live FalkorDB graph with `GRAPH.COPY`, applies the PR to the copy, measures what breaks
(partitions, chokepoints, cut-off entry points), finds the decision record in force and the right reviewer, then
blocks or approves the PR on GitHub with the proof. Agents coordinate through locks in a memory graph and turn every
investigation into a playbook, so the next similar PR takes fewer steps.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB). Entered in **all three tracks**.

![ShadowGraph blocking PR #1](docs/img/dashboard-block.png)

## The demo in one PR

PR #1 removes `/v1/token-verify` and cites decision ADR-08. ShadowGraph:

1. **Company brain (Track 03):** follows `SUPERSEDED_BY` from ADR-08 to **ADR-19**, which is in force and forbids
   the removal; links the Slack-style thread; ranks **@priya** first with PageRank.
2. **Acts on connected data (Track 01):** Scout posts 5 hypotheses; 3 Simulator agents race for them (two lose the
   lock and take the next one); each forks its own shadow graph, deletes the endpoint and its calls, and runs
   `algo.WCC`, exact betweenness and `shortestPath`. The mesh goes from **1 connected group to 3**; the cut-off
   group holds a tier 0 service. Verdict **BLOCK**, posted to the PR as a comment plus a failed commit status.
3. **Memory and coordination (Track 02):** the fact "`/v1/token-verify` is safe to remove" (from ADR-08) is
   overridden by "not safe" (from this run); the steps that decided the verdict become a playbook. PR #2
   (`/v1/session-refresh`) recalls it by similarity and finishes in **23 agent steps instead of 35**, still
   re-running the safety simulation. Chat answers "did we see this before?" from past episodes and sessions.

Measured in development (25 services, laptop-class machine): one full run 0.2–0.5 s; `GRAPH.COPY` 25–100 ms.

## Quick start

**Docker (recommended).** Needs Docker Desktop.

```bash
cp .env.example .env        # optional: every value in it is optional
docker compose up --build
```

Open <http://localhost:8000>, pick **PR #1** and press **Run check**. FalkorDB's own browser is at
<http://localhost:3000>. Windows: `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`.

**Without Docker.** Python 3.12/3.13 and Node 20.19+; FalkorDB from FalkorDB Cloud (free) or any FalkorDB server.

```bash
cd backend && python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export FALKORDB_URL=redis://default:PASSWORD@HOST:PORT          # or put it in ../.env
python -m shadowgraph serve                                    # API + /mcp on :8000
cd ../frontend && npm ci && npm run dev                        # dashboard on :5173
```

Terminal only: `python -m shadowgraph run acme 1` prints the PR comment and the verdict.

**Tests** (need a FalkorDB at `FALKORDB_URL`; they use `test_`-prefixed graphs only):

```bash
cd backend && python -m pytest -q        # 33 tests: FalkorDB behaviour, queries, simulation, lock race, agents, API
```

## How it works

```mermaid
flowchart LR
  GH[GitHub PR] -- webhook --> API
  UI[Dashboard] -- REST + SSE --> API
  CUR[Cursor / Claude Desktop] -- MCP --> MCP
  subgraph Backend [FastAPI backend]
    API --> ORCH[Orchestrator]
    ORCH --> SC[Scout] & SB[Scribe] & S1[Simulator x3] & JG[Judge]
    SC & SB & S1 & JG -- MCP tool calls --> MCP[MCP server: 17 tools]
  end
  MCP -- Cypher, GRAPH.COPY --> FDB[(FalkorDB)]
  FDB --- LIVE[acme_live] & SIM[acme_sim_run_hyp] & MEM[acme_memory]
  JG -- comment + commit status --> GH
```

Every agent action is an MCP tool call, so the agents, Cursor and the tests exercise the same 17 tools. The verdict
comes from rules over graph results; Gemini (optional) only plans which checks to run and writes the opening
sentences of the comment. Without a key everything runs in rules mode.

| Agent | Does |
| --- | --- |
| Scout | Opens an episode, recalls a playbook by vector similarity, posts hypotheses (Gemini can choose them in LLM mode) |
| Simulator ×3 | Race for `sim` hypotheses; each forks its own shadow graph, applies the change, measures, drops it |
| Scribe | Claims `brain` hypotheses: decision in force (follows `SUPERSEDED_BY`), expert (PageRank) |
| Judge | Applies the verdict rules, posts to GitHub, records facts, saves or reuses the playbook |

Verdict rules: **BLOCK** if a new island holds a tier 0/1 service or a decision in force forbids the removal;
**WARN** for low-tier islands or a chokepoint whose betweenness rises 50%+ into the top 3; otherwise **PASS**.

## Graph data model

Per tenant: `{tenant}_live` (the company), `{tenant}_sim_{run}_{hyp}` (short-lived `GRAPH.COPY` of live, one per
simulation hypothesis), `{tenant}_memory` (agent memory). Two tenants are seeded: **Acme Payments** (25 services, the
demo) and **Globex** (8 services, shows that memory is separate per tenant).

```mermaid
erDiagram
  Team ||--o{ Service : OWNS
  Service ||--o{ Endpoint : EXPOSES
  Service }o--o{ Endpoint : DEPENDS_ON
  Service }o--o{ Service : "CALLS {via endpoint}"
  PullRequest }o--o{ Endpoint : "MODIFIES {change}"
  PullRequest }o--o{ DecisionRecord : CITES
  DecisionRecord }o--o{ Endpoint : GOVERNS
  DecisionRecord ||--o| DecisionRecord : "SUPERSEDED_BY {reason}"
  DecisionRecord }o--|| ChatThread : DISCUSSED_IN
  Engineer }o--o{ Engineer : "DEFERRED_TO {count}"
  Engineer }o--o{ Service : "CONTRIBUTED_TO {commits}"
  Engineer }o--|| Team : MEMBER_OF
  Engineer ||--o{ PullRequest : AUTHORED
```

Memory graph: `(Session)-[:HAS_TURN]->(Message)-[:NEXT]->(Message)`, `(Session)-[:ABOUT]->(Episode)`,
`(Episode)-[:INVESTIGATING]->(Hypothesis)`, `(Episode)-[:PRODUCED]->(Fact)-[:OVERRIDES]->(Fact)`,
`(Episode)-[:CRYSTALLIZED_INTO|REUSED]->(Playbook)-[:HAS_STEP]->(ToolStep)`. Vector indexes (384 dims, cosine) on
`DecisionRecord.embedding`, `Episode.symptom_embedding` and `Message.embedding`.

Decisions carry two time axes: `valid_from`/`valid_until` (when they apply) and `recorded_at` (when the graph
learned them). Facts in memory are versioned the same way: a new fact closes the old one and links to it.

## The queries the product depends on

All of them live in [`backend/shadowgraph/graph/queries.py`](backend/shadowgraph/graph/queries.py); the full list
is in [docs/QUERIES.md](docs/QUERIES.md) (generated from the code).

**Partition check**, on live and on the shadow copy:

```cypher
CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['CALLS']})
YIELD node, componentId
RETURN componentId, count(node) AS services, collect(node.id) AS ids, min(node.tier) AS best_tier
```

**Decision in force** for a PR:

```cypher
MATCH (pr:PullRequest {number: $pr})-[:CITES]->(cited:DecisionRecord)
MATCH p = (cited)-[:SUPERSEDED_BY*0..5]->(current:DecisionRecord)
WHERE NOT (current)-[:SUPERSEDED_BY]->()
...
```

**Exact betweenness** inside FalkorDB (why not `algo.betweenness`: see below):

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

**Expert**: `CALL algo.pageRank('Engineer', 'DEFERRED_TO')` × commits to the service. **Hypothesis lock**
(atomic, because FalkorDB runs write queries on a graph one at a time; `test_lock_race.py` claims 150 hypotheses
with 3 workers on separate connections and finds zero double claims):

```cypher
MATCH (:Episode {id: $episode_id})-[:INVESTIGATING]->(h:Hypothesis)
WHERE h.status = 'OPEN' AND h.kind = $kind
WITH h ORDER BY h.priority DESC, h.id ASC LIMIT 1
SET h.status = 'CLAIMED', h.claimed_by = $agent_id, h.claimed_at = timestamp()
RETURN h.id AS claimed
```

## What we learned about FalkorDB (all checked in `tests/test_smoke.py`)

- **`GRAPH.COPY` forks the server process**, and Redis allows one fork at a time: three simultaneous copies fail
  with "could not fork". ShadowGraph queues copies (and retries across processes); each simulator still gets its
  own shadow graph. On a 25-service graph a copy takes 25–100 ms, not the "<5 ms" sometimes quoted.
- **`algo.betweenness` samples its source nodes** (with replacement), even when `samplingSize` exceeds the node
  count, so scores are unscaled estimates and the sample shifts between two graphs. Before/after deltas built on it
  are noise. ShadowGraph computes exact betweenness with `allShortestPaths` in Cypher instead (about 8 ms here),
  verified against an independent Brandes implementation.
- **`algo.pageRank` ignores parallel edges**: three edges between two people count as one. `DEFERRED_TO` is stored
  once per pair with a `count`.
- Vector query `score` is a **distance** (0 = identical); `shortestPath` needs both ends bound first and works
  inside `WITH`; after an aggregation, `ORDER BY` must use a returned column.

## MCP: use the same tools from Cursor or Claude Desktop

The server is at `http://localhost:8000/mcp` (streamable HTTP) or over stdio with `python -m shadowgraph mcp`.
Setup for each client: [docs/MCP_CLIENTS.md](docs/MCP_CLIENTS.md). Try: *"Use shadowgraph to check PR 1 in tenant
acme."* The one-call tool is `simulate_change`.

## GitHub

1. Push [`demo-repo/acme-payments-demo`](demo-repo/acme-payments-demo) to a new public repo and open its three PRs
   (script and steps in its README).
2. In `.env`: `GITHUB_TOKEN` (fine-grained, that repo only, *Pull requests* and *Commit statuses* read and write)
   and `GITHUB_REPO=you/acme-payments-demo`.
3. Optional webhook (needs a public URL): payload URL `https://<host>/api/webhooks/github`, content type JSON,
   secret = `GITHUB_WEBHOOK_SECRET`, events: *Pull requests*. A new PR whose description says
   `Removes: /v1/route` and `Cites: ADR-xx` is added to the company graph and checked automatically.

ShadowGraph updates its own comment on re-runs instead of posting a new one.

## Configuration

Everything is optional; see [.env.example](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `FALKORDB_URL` | `redis://localhost:6379` | FalkorDB server (Cloud: `redis://default:PASS@HOST:PORT`) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | empty, `gemini-2.5-flash` | Enables LLM mode |
| `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_WEBHOOK_SECRET` | empty | Posting verdicts, webhook |
| `PR_TOKEN_VERIFY`, `PR_SESSION_REFRESH`, `PR_JWKS` | 1, 2, 3 | Numbers of the demo PRs in your repo |
| `EMBED_BACKEND` | `auto` | `fastembed` (all-MiniLM-L6-v2) when available, else a built-in hashing embedder |
| `PUBLIC_URL` | `http://localhost:8000` | Links in GitHub statuses; allowed host for `/mcp` |
| `ADMIN_TOKEN` | empty | Protects the Reset endpoint on public deployments |

## Deploy

One Docker image serves the API, `/mcp` and the dashboard. FalkorDB Cloud (free tier) + Render (blueprint in
[render.yaml](render.yaml)): steps in [docs/DEPLOY.md](docs/DEPLOY.md).

## Repository layout

```text
backend/shadowgraph/   api.py, mcp_server.py, agents/, core/ (sim, brain, memory, verdict, github, chat, ingest),
                       graph/ (queries.py, schema.py, seed*.py), db.py, embed.py, events.py, runtime.py
backend/tests/         33 tests against a real FalkorDB
frontend/src/          React dashboard: timeline, Cytoscape graph, proof, memory, chat
demo-repo/             the public demo repo with the three PRs
docs/                  QUERIES.md, MCP_CLIENTS.md, DEPLOY.md, DEMO_SCRIPT.md, BLOG_DRAFT.md, TRANSFER.md
implemented.md         what was built, how, and how it was verified
```

## AI assistance (disclosure)

Built with Claude (Anthropic) as a coding assistant. The project owner set the product direction, chose the scope
and stack through a design questionnaire, and reviews, runs and explains the code; Claude wrote most of the code
and tests under that direction. Every FalkorDB behaviour the design relies on was verified against a real FalkorDB
4.18 server, and three wrong assumptions in the original plan were found and fixed that way (see "What we learned").
Details in [implemented.md](implemented.md).

## Limitations

- The company graph is seeded, not built from real code and traces (a natural next step: parse imports and OpenAPI
  specs, ingest traces).
- Chat and Slack-style threads are synthetic; the `.example` links are deliberately not real.
- Exact betweenness over all pairs suits hundreds of services, not tens of thousands.

## License

MIT
