# ShadowGraph

**Know who you'll break before you merge, whether a person or an AI agent wrote the change.**

Code that other teams build on (a platform SDK, an internal library, a public API) breaks people in repositories
its CI never sees. ShadowGraph keeps a FalkorDB graph of who imports what, down to the file and line, and checks
every change against it: which names disappear or get deprecated, who still uses them, what exactly fails (the
whole integration, one platform, or nothing because there is a fallback), when that ships, and a draft fix for
each user. Coding agents get the same check as MCP tools, so an agent asks *"can I remove this?"* before it does.

It runs on a real codebase, not invented data: **Home Assistant core** (1,510 integrations, about 130 merged PRs a
day) and **all 3,304 HACS custom integrations** that build on its API from 3,303 other repositories.
Read-only: it never posts to their repositories.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), entered in all three tracks.

![A real Home Assistant PR: breaks 32 custom integrations](docs/img/ha-report.png)

## What it found in one real week

| Change (home-assistant/core, Oct 11-18, 2026) | ShadowGraph |
|---|---|
| [#186863](https://github.com/home-assistant/core/pull/186863) Move sensor entity classes to entity module | **Breaks 32 custom integrations on 2026.11** (Nov 4). A green refactor stopped re-exporting 10 names that custom integrations import from `homeassistant.components.sensor`: `StateType`, `RestoreEntity`, `EntityCategory`, even `datetime`. |
| [#184756](https://github.com/home-assistant/core/pull/184756) Remove deprecated `async_track_state_change` | **Breaks 11 custom integrations on 2026.11**, all at import time, despite a developer-blog announcement in 2024. |
| [#186267](https://github.com/home-assistant/core/pull/186267) Deprecate config flow connection class constants | **Starts retiring `CONN_CLASS_*`: 343 integrations still use them**, removal planned for 2027.5. |

Across the snapshot: **59** custom integrations stop loading (fully or one platform) in 2026.11 unless fixed first,
36 of them updated since July; **95** already fail on the current release; **17** retirements in progress, 10 ready
to remove today, 1 past its planned release. Details and method: [docs/HOME_ASSISTANT.md](docs/HOME_ASSISTANT.md).

## The product

| Page | What you get |
|---|---|
| **Pull requests** | Every checked PR with a verdict (Breaks / Retires / Review / Clear). The live feed checks new open PRs; most say "no public API change" in a second. Each report: who is affected, with `file:line` and what fails; what changes in the API; when it ships; announcements and reviewers; how it was checked. |
| **Retirements** | Every deprecated name, the release it stops working in, days left, who still uses it, and an executable condition: *remove when no integration imports it at load or run time*. |
| **Broken by core** | Custom integrations that import names core no longer has: breaking in the next release, or already broken. |
| **Who uses…** | Any core name: every user, how it uses it, what removal would do, where else the name lives. |
| **Draft a fix** | A unified diff for one user (move an import, swap a deprecated constant, drop a dead one), parsed and re-checked before it is shown. A draft: nothing is posted. |
| **Ask** | Questions answered from the graph, the developer blog, ADRs and past checks, with sources and saved sessions. |
| **Connect an agent** | MCP setup for Claude Code and Cursor, plus the rule to paste into `CLAUDE.md`. |

<p>
<img src="docs/img/ha-retirements.png" width="49%" alt="Retirement board">
<img src="docs/img/ha-broken.png" width="49%" alt="Broken by core">
</p>

## How the three tracks fit

1. **Agents that act on connected data.** A team of agents checks each change: Scout opens it and plans, three
   Simulators race for hypotheses, each copies the live graph with `GRAPH.COPY`, applies the change in its
   shadow, measures who loses an import and how failures spread through manifest dependencies, and drops the
   copy. Scribe finds announcements and reviewers; Judge cross-checks the shadow count against the engine (they
   agree: 32 = 32 for #186863) and files the report. Every step is an MCP tool call.
2. **Agent memory and coordination.** Hypotheses are locks in a memory graph (atomic claims, no double work).
   Each check is an episode; facts like "`homeassistant.components.sensor.EntityCategory` is safe to remove = false"
   are versioned; the checks that decided a verdict become a playbook that the next similar change reuses.
3. **Company brain.** One graph joins code (modules, names, imports), people (CODEOWNERS), decisions (ADRs with
   `SUPERSEDED_BY`), announcements (developer-blog posts linked to the names they mention), releases and past
   checks. Ask answers "who still uses `async_track_state_change`?" with sources.

## Quick start

**Docker.** Needs Docker Desktop.

```bash
cp .env.example .env        # optional; set GITHUB_TOKEN for real PR titles and a higher API limit
docker compose up --build
```

Open <http://localhost:8000>. The Home Assistant snapshot (`data/ha_snapshot.json.gz`) loads on first start
(about 20 s). Replay the three changes above from the Pull requests page, or press **Poll now** to check the
newest open PRs. Windows: `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`.

**Without Docker.** Python 3.12+ and Node 20.19+; a FalkorDB server (or FalkorDB Cloud).

```bash
cd backend && python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m shadowgraph serve                                    # API + /mcp + dashboard on :8000
cd ../frontend && npm ci && npm run build                      # or npm run dev for :5173
```

Optional: `HA_CLONE=/path/to/ha-core` (a shallow clone of home-assistant/core) to read PRs with git instead of
the GitHub API; `HA_FEED=1` to watch open PRs continuously. Refresh the snapshot with
`python scripts/fetch_ha_sources.py --dir ../ha-sources --build`.

**Tests** (a FalkorDB at `FALKORDB_URL`; they only touch `test_`-prefixed graphs):

```bash
cd backend && python -m pytest -q        # 46 tests, including a miniature Home Assistant built as a real git repo
```

## How it works

```mermaid
flowchart LR
  GH[GitHub: home-assistant/core<br/>open PRs, read-only] -- git refs/pull/N/merge or REST --> SRC[Change source]
  UI[Dashboard] -- REST + SSE --> API
  AG[Claude Code / Cursor] -- MCP --> MCP
  subgraph Backend [FastAPI backend]
    SRC --> ENG[Impact engine: API diff of both trees,<br/>users from the graph, effects, cascade]
    API --> ORCH[Agent team]
    ORCH --> SC[Scout] & SB[Scribe] & S1[Simulator x3] & JG[Judge]
    SC & SB & S1 & JG -- MCP tool calls --> MCP[MCP server: 30 tools]
    MCP --> ENG
  end
  ENG -- Cypher --> FDB[(FalkorDB)]
  S1 -- GRAPH.COPY --> FDB
  FDB --- LIVE[ha_live] & SIM[ha_sim_run_hyp] & MEM[ha_memory]
  SNAP[Snapshot builder:<br/>core + 3,303 HACS repos<br/>+ blog + ADRs] --> LIVE
```

- **Snapshot** (`backend/shadowgraph/ha/importer.py`): static analysis of every Python file in core and in each
  HACS integration. Each import of a Home Assistant name is stored with file, line and context: *load* (module
  level or class body), *runtime* (inside a function), *conditional*, *shimmed* (try/except fallback) or *type*
  (annotations only). Each integration's own imports are followed from `__init__.py` and every platform file, so
  a failure is described exactly. Names resolve through re-exports, aliases, star imports and base classes;
  anything undecidable is *unknown*, never guessed. Self-test: all of core's own imports resolve.
- **Graph**: `(Integration)-[:USES {sites, context}]->(Symbol)<-[:EXPORTS]-(Module)`, `DEPENDS_ON` from
  manifests, `CODEOWNER`, `(Symbol)-[:REMOVED_IN|DEPRECATED_IN|BREAKS_IN]->(Release)`,
  `(Post)-[:MENTIONS]->(Symbol)`, `(Decision)-[:SUPERSEDED_BY]->(Decision)`, `(Change)-[:CHANGES]->(Symbol)`.
- **A change**: the base and result trees (GitHub's test-merge ref for open PRs, the squash commit for merged
  ones, or an agent's planned edits), the API of every changed module diffed name by name, then one graph query
  for who uses what changed. 0.2 to 1 s per PR on the full graph.

Effects, in words a maintainer understands:

| use of a removed name | result |
|---|---|
| module level in `__init__.py` or anything it imports | the integration fails to set up |
| module level in a platform file (`sensor.py`) | its sensor platform fails to load |
| inside a function | raises when that code runs |
| inside `try/except ImportError` | has a fallback, keeps working |
| annotation only | no runtime effect (lazy annotations, Python 3.14) |

## Queries (services sample)

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

## MCP: the pre-flight check for coding agents

`http://localhost:8000/mcp` (streamable HTTP) or stdio with `python -m shadowgraph mcp`. Setup per client:
[docs/MCP_CLIENTS.md](docs/MCP_CLIENTS.md).

| Tool | Use it |
|---|---|
| `ha_can_remove(symbol)` | before deleting or renaming a core name |
| `ha_check_edits(files)` | before committing: the new content of the files you changed |
| `ha_who_uses(symbol)`, `ha_search(query)` | every user, with `file:line` |
| `ha_check_pr(pr_number)`, `ha_retirements()`, `ha_draft_migration(symbol, integration)` | reports, the board, drafts |

Paste into `CLAUDE.md` / `AGENTS.md`: *"Before you remove, rename or change the signature of anything under
`homeassistant/`, call `ha_can_remove` (or `ha_check_edits`). If integrations would break, deprecate instead."*

## The services sample (Acme Payments)

The same engine on a different kind of API: microservices calling each other's HTTP endpoints. Pick **Acme
Payments (sample)** in the workspace menu. PR #1 removes `/v1/token-verify`: simulators fork the service graph,
`algo.WCC` shows the mesh splitting into three groups (one holds a tier 0 service), the decision cited by the PR
(ADR-08) was superseded by ADR-19 which forbids the removal, and PageRank ranks the reviewer. PR #2 reuses the
playbook and finishes in fewer steps. With a demo repo and token it posts the verdict as a PR comment and status
([demo-repo/](demo-repo/acme-payments-demo)); Home Assistant is never posted to.

## Configuration

Everything is optional; see [.env.example](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `FALKORDB_URL` | `redis://localhost:6379` | FalkorDB server (Cloud: `redis://default:PASS@HOST:PORT`) |
| `TENANTS` | `ha,acme,globex` | Workspaces to load |
| `HA_SOURCE`, `HA_CLONE` | `auto`, empty | Read PRs from the GitHub API or from a local clone with git |
| `HA_FEED`, `HA_FEED_INTERVAL`, `HA_FEED_BATCH` | `0`, `180`, `12` | Live feed of open PRs |
| `HA_SNAPSHOT` | `data/ha_snapshot.json.gz` | Snapshot file |
| `GITHUB_TOKEN` | empty | HA: read-only API use (titles, rate limit). Sample: PR comments with `GITHUB_REPO` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | empty, `gemini-2.5-flash` | Optional LLM wording (answers, summaries) |
| `EMBED_BACKEND` | `auto` | `fastembed` (all-MiniLM-L6-v2) when available, else a built-in hashing embedder |
| `PUBLIC_URL`, `ADMIN_TOKEN` | `http://localhost:8000`, empty | Links and allowed MCP host; protects reset and poll |

## Deploy

One Docker image serves the API, `/mcp` and the dashboard, with the snapshot baked in. FalkorDB Cloud (free tier)
plus Render: [docs/DEPLOY.md](docs/DEPLOY.md).

## Repository layout

```text
backend/shadowgraph/ha/   importer (snapshot), surface + usage (static analysis), load (graph), impact (reports),
                          sources (git / GitHub, read-only), agents, mcp_tools, migrate (drafts), feed, ask, service
backend/shadowgraph/      api.py, mcp_server.py, agents/, core/, graph/ (the services sample), db.py, runtime.py
backend/tests/            46 tests against a real FalkorDB (tests/ha_fixture.py builds a miniature Home Assistant)
frontend/src/             React dashboard (ha/ pages, acme/ sample, components/ui.tsx, styles/)
data/ha_snapshot.json.gz  the Home Assistant snapshot (about 6 MB)
scripts/                  fetch_ha_sources.py (rebuild the snapshot), start/package scripts
docs/                     HOME_ASSISTANT.md, MCP_CLIENTS.md, DEPLOY.md, DEMO_SCRIPT.md, QUERIES.md, TRANSFER.md
implemented.md            what was built, how, and how it was verified
AGENT_HANDOFF.md          what to finish on another machine
```

## AI assistance (disclosure)

Built with Claude (Anthropic) as a coding assistant. The project owner set the product direction (a startup
aimed at platform teams, Home Assistant as the real-data proof, read-only by design), chose scope and stack, and
reviews, runs and explains the code; Claude wrote most of the code and tests under that direction. Claims in this
README were measured on the data in `data/` and can be reproduced from the dashboard.

## Limitations

- Coverage is the HACS default list; private and unlisted integrations are invisible. Dead code still counts.
- Attribute access on instances is not tracked; only names reached through imports (and `Class.member`).
- No install counts yet (Home Assistant publishes analytics per custom integration; adding them is small).
- Future release dates follow the monthly schedule and are expectations; "breaks in 2026.11" assumes nothing
  changes before the release.
- Python only. The model (modules, names, users, contexts) carries to TypeScript or Java with a different parser.

## License

MIT
