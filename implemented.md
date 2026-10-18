# implemented.md: what was built, how, and how it was verified

Build date: October 18, 2026. Built from the "ShadowGraph — Product Design Document" with Claude writing the code.
This file is the honest record: what exists, how each part works, where the build departed from the design doc
and why, what was tested, and what still needs a check on your own machine.

## 1. Status in one paragraph

Everything in the design doc's scope is implemented and runs end to end: seeded company graphs for two tenants, the
counterfactual simulation on `GRAPH.COPY` shadow graphs, decision lineage, expert ranking, a 17-tool MCP server,
six agents that coordinate through hypothesis locks in a memory graph, facts that override older facts, playbooks
that make the next similar run shorter, persistent chat sessions, GitHub comment and commit status posting, a
webhook that adds new PRs to the graph, an optional Gemini mode with automatic fallback, and a React dashboard in
light and dark. **33 automated tests pass against a real FalkorDB 4.18 server.** The dashboard was driven in a real
Chromium browser with no console errors. Not run here (no access from the build environment): the Docker image build,
the PowerShell scripts, real Gemini calls and real GitHub posts. Section 8 says how to check each in a few minutes.

## 2. How it was built (in order)

1. **Proved the platform first.** Docker Hub was blocked in the build environment, so a real FalkorDB server
   (v4.18.03) was started from the `falkordblite` package's bundled Redis + FalkorDB module. Every FalkorDB feature
   the design relies on was tested before writing product code. That surfaced three wrong assumptions (section 5).
2. **Graph model and seed data** (`graph/seed_acme.py`, `seed_globex.py`, `seed.py`, `schema.py`), designed so the
   story's numbers are real outcomes of the topology, then checked by queries.
3. **Query library** (`graph/queries.py`): every Cypher statement in one file.
4. **Core domain** (`core/`): simulation, company brain, memory, verdict rules, PR comment, GitHub, chat, ingest.
5. **MCP server** (`mcp_server.py`, 17 tools) and **agents** (`agents/`), all talking through MCP client sessions.
6. **API** (`api.py`): REST, server-sent events, webhook, `/mcp` over HTTP, static dashboard.
7. **Tests** (`backend/tests/`), run after every change.
8. **Dashboard** (`frontend/`), then checked in a real browser at desktop and phone widths, light and dark.
9. **Packaging and docs**: Dockerfile, compose, CI, Render blueprint, scripts, README, docs, this file.

## 3. What is where

| Path | What it does |
| --- | --- |
| `backend/shadowgraph/config.py` | Settings from environment variables and an optional `.env` |
| `backend/shadowgraph/db.py` | Async FalkorDB wrapper: graph names, timing, one event per query, queued `GRAPH.COPY` |
| `backend/shadowgraph/events.py` | In-process event hub; every agent step and query becomes an event the dashboard streams |
| `backend/shadowgraph/embed.py` | 384-dim embeddings: fastembed all-MiniLM-L6-v2, or a built-in hashing embedder offline |
| `backend/shadowgraph/runtime.py` | One shared runtime per process (DB client, embedder, run registry) |
| `backend/shadowgraph/graph/seed_acme.py` | The synthetic company "Acme Payments": 25 services, 29 endpoints, 42 calls, 20 decisions, 15 engineers, 30 PRs |
| `backend/shadowgraph/graph/seed_globex.py` | Second tenant "Globex" (8 services) to show memory isolation |
| `backend/shadowgraph/graph/seed.py` | Builds the graphs; reseeds automatically if the seed version, embedder or demo PR numbers change |
| `backend/shadowgraph/graph/queries.py` | Every Cypher query (generated into `docs/QUERIES.md`) |
| `backend/shadowgraph/core/sim.py` | Fork, apply change, measure (WCC, exact betweenness, cascade, cut-off entry points), drop |
| `backend/shadowgraph/core/brain.py` | PR context, decision lineage and decisions in force, decision search, expert ranking |
| `backend/shadowgraph/core/memory.py` | Episodes, hypothesis locks, facts, playbooks, chat sessions, memory overview |
| `backend/shadowgraph/core/verdict.py` | BLOCK / WARN / PASS rules |
| `backend/shadowgraph/core/comment.py` | The markdown PR comment |
| `backend/shadowgraph/core/github.py` | Posts or updates the comment, sets the commit status, verifies webhook signatures |
| `backend/shadowgraph/core/ingest.py` | Webhook: upserts a PR and its `Removes:` / `Cites:` links into the graph |
| `backend/shadowgraph/core/chat.py` | Chat answers from past episodes, earlier chats and decisions in force |
| `backend/shadowgraph/mcp_server.py` | 17 MCP tools; also runnable over stdio |
| `backend/shadowgraph/agents/*` | Scout, Scribe, Simulator, Judge, the orchestrator, and the Gemini helpers |
| `backend/shadowgraph/api.py` | FastAPI app |
| `backend/shadowgraph/__main__.py` | CLI: `serve`, `seed`, `reset`, `run`, `mcp` |
| `backend/tests/` | 33 tests (section 7) |
| `frontend/src/` | Dashboard: `App.tsx`, components (TopBar, RunTimeline, GraphCanvas, ProofPanel, CypherInspector, MemoryPanel, ChatDrawer), `lib/` (API client, run stream hook, types) |
| `Dockerfile`, `docker-compose.yml` | One image (UI + API + MCP); compose adds FalkorDB with a data volume |
| `.github/workflows/ci.yml` | Tests against FalkorDB, typecheck and build the UI, build and smoke-test the image |
| `render.yaml` | Render deploy blueprint |
| `scripts/` | `start.ps1` / `start.sh`, `package.ps1` / `package.sh`, `gen_queries_md.py` |
| `demo-repo/acme-payments-demo/` | The public demo repo: auth-service stub, three PR descriptions, PR script |
| `docs/` | Query reference, MCP client setup, deploy, demo script, blog draft, transfer guide, screenshots |

About 4,100 lines of Python (including tests) and 1,600 lines of TypeScript and CSS.

## 4. How it works

### One check, step by step

1. `POST /api/runs {tenant, pr_number, mode}` starts a background run and returns a run id. The dashboard opens
   `GET /api/runs/{id}/events` (server-sent events) and renders every event as it arrives.
2. The orchestrator opens **six MCP client sessions** to the in-process MCP server, one per agent: Scout, Scribe,
   `sim-1..3`, Judge. Every agent action below is an MCP tool call.
3. **Scout** calls `memory_open_episode` (creates an `Episode` with the PR text embedded), then
   `memory_recall_playbook` (vector search over past episodes; a playbook is reused only if the distance is at most
   `PLAYBOOK_MAX_DISTANCE`, default 0.6). It then posts hypotheses with `memory_post_hypotheses`:
   - no playbook: H1 partition, H2 chokepoint, H3 entry path (kind `sim`); H4 decision, H5 expert (kind `brain`);
   - with a playbook: only the checks that decided last time, always including the partition check;
   - LLM mode: Gemini receives the MCP tool list (read-only tools only), may call tools to investigate (up to 8
     calls), and must finish by proposing checks; `partition` and `decision` are always added. Any failure falls
     back to the default plan and emits an `llm_fallback` event.
4. **Scribe and the three Simulators run in parallel** (`asyncio.gather`). Each loops: `memory_claim` (atomic),
   work, `memory_resolve`, until nothing of its kind is open. All Simulators first try H1, so two of them lose that
   race visibly (`lock_conflict` events) and take H2 and H3.
5. A **Simulator** forks its own shadow graph (`sim_fork` → `GRAPH.COPY acme_live acme_sim_{run}_{H}`), deletes the
   endpoint and every `CALLS` edge whose `via` is that endpoint (`sim_apply_change`), measures (`sim_measure`), and
   drops the shadow graph in a `finally` block (`sim_drop`, which refuses any graph that is not a shadow graph).
   `sim_measure` runs WCC and exact betweenness on both graphs, and the direct-dependents and cascade queries on the
   live graph (in the shadow the path no longer exists). Islands are shadow components that did not exist in the
   live graph and do not contain the provider service.
6. The **Scribe** traces decisions (`brain_trace_decision`: Q1 from the cited decision plus any decision in force
   that governs the target) and records the PR author's belief as a fact ("safe to remove = true, source ADR-08",
   only if memory has no current fact). For H5 it ranks experts (`brain_find_expert`).
7. The **Judge** applies the rules (`core/verdict.py`), writes the comment (Gemini may write only the opening
   sentences), calls `action_post_verdict` (posts to GitHub when configured and closes the episode), records the
   simulation's fact (which overrides the author's), and saves a playbook (or records the reuse of one).
8. `run_finished` closes the stream; `GET /api/runs/{id}` returns the full report.

### Verdict rules

- **BLOCK**: a new island contains a tier 0 or tier 1 service, or a decision in force forbids removing the target.
- **WARN**: islands of tier 2/3 services only, or a service's exact betweenness rises 50% or more and it is in the
  shadow's top 3.
- **PASS**: neither.

### The demo outcomes (all asserted by tests)

| PR | Change | Result |
| --- | --- | --- |
| #1 | remove `/v1/token-verify` | 1 → 3 components; islands {partner-edge, partner-gateway, partner-webhooks, partner-billing} (tier 0) and {legacy-mobile-bff, mobile-push}; partner-edge cut off; ADR-08 → ADR-19 forbids it; expert @priya. **BLOCK**, 35 agent steps |
| #2 | remove `/v1/session-refresh` | island {kiosk-bff, kiosk-sync}; ADR-12 → ADR-21 forbids it. **BLOCK**, playbook from PR #1 reused, **23 steps** |
| #3 | remove `/v1/jwks` | nothing breaks; ADR-15 in force allows it. **PASS** |
| Globex #1 | remove `/v1/legacy-export` | **PASS**; Globex memory stays separate from Acme's |

### Memory model

- **Episodic**: `Episode` per check (PR, text, embedding, verdict, steps, ms).
- **Coordination**: `Hypothesis` nodes with status OPEN → CLAIMED → CONFIRMED/REJECTED and `claimed_by`.
- **Semantic**: `Fact` nodes; a new fact on the same entity and attribute sets the old one's `valid_until` and
  links `(new)-[:OVERRIDES]->(old)`.
- **Procedural**: `Playbook` with ordered `ToolStep`s and the decisive checks; reuse updates its run count and
  success rate.
- **Conversation**: `Session` → `Message` chain, messages embedded for recall; sessions link to the episodes they
  discussed.
- **Per tenant**: a separate memory graph per tenant, so isolation does not depend on a `WHERE` clause.

### Dashboard

Top bar (tenant, PR, Rules/LLM, Run, health, reset, theme); run timeline (agent steps, optional query rows,
click to inspect); Cytoscape graph (node size = live betweenness, Live/Shadow toggle, islands in red, removed calls
dashed, cascade chain highlighted, click a node for details); Proof tab (verdict, stats, playbook reuse, GitHub
result, hypotheses, decision in force, expert, blast radius, Cypher inspector with parameters and timing, the PR
comment); Memory tab (counts, facts with overridden ones struck through, playbooks, episodes); chat drawer with
saved sessions. A run can be opened by URL (`/?tenant=acme&run=<id>`), which is where the GitHub status links.

## 5. Where the build departs from the design doc, and why

| Design doc said | Built | Why |
| --- | --- | --- |
| Chokepoints with `algo.betweenness` (`samplingSize: 64` makes it exact) | Exact betweenness in Cypher with `allShortestPaths` (Q5); `algo.betweenness` kept only in the smoke test | Measured: `algo.betweenness` samples sources with replacement whatever `samplingSize` is; scores vary by seed and the sample shifts between two graphs, so before/after deltas were noise (one seed showed a jump from 11 to 51 that no path could cause). The Cypher version matches Brandes exactly in about 8 ms |
| One `DEFERRED_TO` edge per review, "edge count is the weight" | One edge per pair with a `count` property | Measured: `algo.pageRank` collapses parallel edges |
| `GRAPH.COPY` in "<5 ms"; Simulators copy in parallel | Copies queued in-process and retried across processes; 25–100 ms each | Measured: concurrent copies fail with "could not fork" (Redis allows one fork at a time) |
| 17 MCP tools including a separate "close episode" | 17 tools; closing the episode is part of `action_post_verdict` | Keeps the published tool count and puts "publish the verdict" in one action |
| FastMCP | `mcp` 2.2 `MCPServer` | FastMCP was renamed in mcp 2.x; DNS-rebinding protection had to be configured for deployed hosts |
| Seed data in YAML | Python modules | Typed, commented, no parser dependency |
| Tailwind CSS | Plain CSS with tokens (`tokens.css`) | Fewer moving parts; same look, light and dark |
| Vercel for the dashboard | The backend serves the built dashboard; Vercel remains optional | One container, one URL, no CORS setup |
| Verdict rules BLOCK/WARN/PASS | Same, plus WARN for islands of tier 2/3 services only | A cut-off low-tier service is still worth a warning |
| Seed counts "about 40 endpoints" | 29 endpoints, 42 calls | What the story needs |

## 6. Other findings worth knowing

- FalkorDB vector query `score` is a distance (0 = identical): queries sort ascending.
- After an aggregation, `ORDER BY` must reference a returned column (two queries were fixed for this).
- `shortestPath` needs both endpoints bound before the call and works inside `WITH`; `*0..5` works.
- `GRAPH.COPY` copies indexes too, including vector indexes.
- mcp 2.x enables DNS-rebinding protection when it thinks it runs on localhost; ShadowGraph allows localhost plus
  the host in `PUBLIC_URL` (`MCP_ALLOWED_HOSTS` adds more).
- fastembed retries with long sleeps when offline (about 40 s); the embedder now checks the local cache and a
  3-second reachability probe first, so offline startup falls back to the hashing embedder in about 2 s.

## 7. Verification

**Automated: 33 tests, all passing** (`cd backend && python -m pytest -q`, against FalkorDB 4.18):

| File | What it proves |
| --- | --- |
| `test_smoke.py` (7) | `GRAPH.COPY` is independent and keeps indexes; concurrent copies succeed through the queue; WCC map config and isolated nodes; exact betweenness and that `algo.betweenness` runs; PageRank ignores parallel edges; vector score is a distance; `shortestPath` in `WITH` and `*0..5` |
| `test_queries.py` (7) | Seed shape; Q1 follows `SUPERSEDED_BY` to ADR-19; PASS case has no blocker; Q2 skips replaced decisions (ADR-17 not ADR-04); expert is @priya; cascade chain; Q5 equals an independent Brandes implementation |
| `test_simulation.py` (4) | PR #1 BLOCK with the exact islands; PR #2 BLOCK; PR #3 PASS; the live graph never changes; no shadow left; `sim_drop` refuses the live graph |
| `test_lock_race.py` (2) | 150 hypotheses, 3 workers on separate connections: each claimed exactly once; 10 workers on one hypothesis: one winner |
| `test_agents.py` (6) | 17 MCP tools; `simulate_change`; full loop with 2 lock conflicts and 3 forks, then playbook reuse with fewer steps, then PASS; facts override; LLM mode without a key falls back; chat remembers; tenants are isolated |
| `test_api.py` (5) | Health, tenants, graph; run + events; chat + sessions + memory; webhook ingests a new PR and runs it; signature check and description parsing |
| `test_llm.py` (2) | With a fake Gemini client (real `google-genai` request objects): the planner calls an MCP tool, then proposes checks, and its summary lands in the comment; a failing client falls back twice (plan and comment) |

**Manual checks done in the build environment:**

- MCP over **HTTP** (`http://localhost:8000/mcp`) and **stdio** (`python -m shadowgraph mcp`) with the official
  MCP client: 17 tools listed, `simulate_change` returns BLOCK, errors come back as readable messages.
- The dashboard in **headless Chromium**: run PR #1 and #2, inspect a step, chat, memory tab, dark mode, 400 px wide
  (no horizontal overflow). No console errors.
- **The Docker image's steps in a clean environment**: `npm ci` and `npm run build` from a fresh copy, a fresh
  virtualenv with only `requirements.txt`, then the server with the image's environment variables
  (`STATIC_DIR`, `PYTHONPATH`, `FASTEMBED_CACHE_PATH`): UI served, PR #3 checked (PASS).
- **Wheels exist** for every pinned package on Linux x86-64 and ARM64 (Python 3.12, the image) and on Windows
  (Python 3.12 and 3.13).
- The demo repo script's route deletion, for all three routes (the result still compiles).

Measured here: a full check takes 0.2–0.5 s; a copy of the 25-service graph 25–100 ms; exact betweenness about 8 ms.

## 8. Not run here: check these on your machine

| Item | Why not here | Check (minutes) |
| --- | --- | --- |
| `docker compose up --build` | All container registries were blocked in the build environment | Run it; open <http://localhost:8000>; the top bar says "FalkorDB connected" and PR #1 gives BLOCK |
| Embedding model download | Hugging Face was blocked, so tests used the hashing embedder | `/api/health` shows `embedder: fastembed:...` in Docker. Either backend works; the seed re-embeds itself when it changes |
| `scripts/start.ps1`, `scripts/package.ps1` | No PowerShell in the build environment | Run them once on Windows |
| Real Gemini calls | No key or network to Google | Put `GEMINI_API_KEY` in `.env`, choose LLM, run PR #1. If the model name is retired, set `GEMINI_MODEL`; failures fall back to rules automatically and show an amber banner |
| Real GitHub posting and webhook | No GitHub token | Follow the demo repo README, set `GITHUB_TOKEN` and `GITHUB_REPO`, run PR #1; the PR gets a comment and a red status |
| FalkorDB Cloud | No account | `FALKORDB_URL=... python -m pytest -q tests/test_smoke.py` (only `test_*` graphs are touched) |

## 9. Explaining it to judges (practice these)

1. **Remove FalkorDB and what breaks?** Everything: no fork (`GRAPH.COPY`), no partition check (`algo.WCC`), no
   betweenness or cascade (`allShortestPaths`, `shortestPath`), no expert (`algo.pageRank`), no decision or episode
   search (vector indexes), no locks, facts or playbooks (the memory graph).
2. **Why can't two agents claim the same hypothesis?** The claim is one write query that matches only `OPEN`
   hypotheses and sets them to `CLAIMED`; FalkorDB runs write queries on a graph one at a time. Proven by the race
   test.
3. **Why does the cascade run on the live graph but the partition check on the copy?** The partition is the
   effect of the change, so it needs the changed graph; the cascade is the path that used to exist, which only the
   live graph still has.
4. **How does it know ADR-08 is outdated?** The `SUPERSEDED_BY` edge to ADR-19; the query follows it until a
   decision with no successor.
5. **What did you design and verify, and what did the AI write?** You chose the product, users, scope, stack and
   demo through the questionnaire, and you can show the tests that check every FalkorDB behaviour. Claude wrote
   most of the code under that direction (disclosed in the README).

## 10. Next steps

- Run section 8's checks, create the demo repo PRs, then record the video with `docs/DEMO_SCRIPT.md`.
- Deploy with `docs/DEPLOY.md` for a live link.
- Publish `docs/BLOG_DRAFT.md` after editing it in your own voice.
