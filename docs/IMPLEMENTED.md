# What was implemented in ShadowGraph

**Know who you'll break before you merge, whether a person or an AI agent wrote the change.**

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), entered in all three tracks:
1. *Agents that act on connected data* (multi-agent simulation via isolated `GRAPH.COPY` shadow graphs)
2. *Agent memory and coordination* (atomic hypothesis locking, episodes, facts, and reusable playbooks)
3. *Company brain* (unified graph uniting code, people, ADRs, announcements, releases, and downstream dependents)

---

# Part 1: Experience 1 — The Live "War-Room" & GitHub PR Guardrail (Acme Payments)

## 1.1 Overview & Architecture
Rather than maintaining a disconnected checklist of features, ShadowGraph grounds its multi-agent capabilities in an interactive live war room on a microservice architecture (`acme` tenant, `demo-repo/acme-payments-demo/`):
* **Opening a Risky Pull Request**: An engineer opens a PR on GitHub—e.g. removing `/v1/token-verify`, `/v1/session-refresh`, or `/v1/jwks` from `auth_service/main.py` using templates in `pr-bodies/` (`1-remove-token-verify.md`, `2-remove-session-refresh.md`, `3-remove-jwks.md`).
* **Watching the Agents Think Live** (`backend/shadowgraph/events.py` + `api.py`): Server-Sent Events (SSE) stream every step in real time across a 3-panel war room:
  - **Left Feed (Agent Coordination)**: Scout, Scribe, Simulator, and Judge wake up, lock hypotheses atomically via FalkorDB write locks, and recall past incident playbooks.
  - **Center Canvas (Live vs. Shadow Graph Diff)**: Displays the healthy live graph (`org_live`) alongside an instant clone (`org_sim_{id}`). Severed services light up in red and bottleneck nodes expand visually.
  - **Right Panel (Proof & Lineage)**: Displays active vs. outdated ADRs (`[:SUPERSEDED_BY]`), the true Subject Matter Expert via PageRank, and query execution latency.
* **Automated GitHub PR Comment** (`backend/shadowgraph/core/comment.py` + `core/verdict.py`): Posts a rich Markdown verdict directly onto the GitHub PR (`core/github.py`), blocking bad merges with mathematical proof.
* **Interactive Co-Pilot Chat & Tenant Switching** (`backend/shadowgraph/core/chat.py`): Switch between isolated company tenants (`acme` via `seed_acme.py` and `globex` via `seed_globex.py`) and converse with the Company Brain with persistent session memory.
* **Graph Algorithms**:
  - `algo.WCC` (Weakly Connected Components): detects service mesh network partitions (isolating Tier 0 services).
  - Exact betweenness centrality with `allShortestPaths` (Brandes algorithm) to find critical architectural chokepoints.
  - PageRank (`algo.pageRank`) on the code-review network to rank the single most qualified reviewer.
  - Vector similarity search (`score` distance) on architectural decisions (ADRs) with `SUPERSEDED_BY` chains.
* Full test suite: **33 passing automated tests** in `tests/test_smoke.py`, `tests/test_queries.py`, `tests/test_simulation.py`, `tests/test_lock_race.py`, `tests/test_agents.py`, `tests/test_api.py`, `tests/test_llm.py`.

---

# Part 2: Experience 2 — Real-World Codebase Mode: The Home Assistant Explorer (ha/)

## 2.1 The Problem at Open-Source Scale (Why Both Exist)
Synthetic or microservice demo data alone is not enough to prove real-world viability. Experience 2 exists *specifically* to prove that ShadowGraph scales to an enormous, unmanaged, real-world open-source ecosystem:
* **`home-assistant/core`**: ~130 merged pull requests a day, 1,510 core integrations.
* **All 3,304 custom integrations in HACS** (Home Assistant Community Store) spanning 3,303 external GitHub repositories.
* **The Blindness of Core CI**: Core CI tests only pass/fail code inside `home-assistant/core`. An internal refactor in core can break dozens of downstream custom integrations without failing a single core test.
* **Visualizing Broken Integrations**: Select any core component or service and simulate breaking or deprecating it. The UI maps the blast radius across dependent integrations (`ha/impact.py`).
* **Asking the Codebase Brain**: Through natural language (`ha/ask.py`), query component ownership, API surface (`ha/surface.py`), service call usage (`ha/usage.py`), or migration paths (`ha/migrate.py`), grounded in real parsed Python ASTs (`data/ha_snapshot.json.gz`).

## 2.2 What was built for Home Assistant

| Path | What it does |
| --- | --- |
| `backend/shadowgraph/ha/pyparse.py` | Parses modern Python syntax (PEP 758, PEP 695) newer than standard interpreters; rewrites and safely parses AST without executing untrusted code. |
| `ha/surface.py` | Computes a module's public importable surface: definitions, re-exports, aliases, type aliases, deprecations (`DeprecatedConstant`, `@deprecated_class`, etc.) and a `Resolver` that tracks star imports and base classes. |
| `ha/usage.py` | Scans every use of a Home Assistant symbol with line and context (`load`, `runtime`, `conditional`, `shimmed`, `type`), filtering local variable shadowing. |
| `ha/importer.py` | Builds the ecosystem snapshot: Core dev plus 13 monthly release tags, all HACS repos, manifests (`DEPENDS_ON`), CODEOWNERS, developer blog posts, and architecture ADRs. |
| `ha/load.py` | Hydrates the snapshot into the FalkorDB graph (`ha_live`), vector indexes on posts/ADRs, and streams `ha_uses.jsonl.gz`. |
| `ha/sources.py` | Change sources: `GitSource` (local shallow clone via `refs/pull/N/merge`) and `GitHubSource` (live REST API with automatic rate-limit management and 401 recovery). |
| `ha/impact.py` | Blast-radius report: AST diff of changed modules, affected downstream integrations, effect classification, cascade through manifest dependencies, and release schedule dates. |
| `ha/migrate.py` | Draft patches (unified `.diff` files for moved imports, migration instructions for changed signatures). |
| `ha/feed.py` | Background live feed polling open pull requests on `home-assistant/core` and escalating breaking changes to autonomous agents. |
| `ha/ask.py` | Conversational Q&A querying the live graph, developer blog, and past check episodes. |
| `ha/mcp_tools.py` | 13 MCP tools enabling external coding agents (Claude Code, Cursor) to run pre-flight checks before modifying core code. |
| `scripts/fetch_ha_sources.py` | Parallel cloning and AST extraction pipeline for 3,304 repositories. |
| `data/ha_snapshot.json.gz` | Compressed ecosystem knowledge graph (5.5 MB compressed; 76 MB uncompressed). |

## 2.3 Verified Real-World Findings
* **PR #186863**: Moving sensor entity classes out of `sensor/__init__.py` breaks **32 custom integrations on 2026.11** (e.g., `AlexxIT/SonoffLAN`, `BottlecapDave/HomeAssistant-OctopusEnergy`) because they imported `StateType` and `EntityCategory` at module level.
* **PR #184756**: Removing deprecated `async_track_state_change` breaks **11 custom integrations on 2026.11** (e.g., `Geek-MD/HA_Daily_Counter` at `counter.py:7`).
* **PR #186267**: Deprecating `CONN_CLASS_*` flags starts the retirement clock across **343 integrations** with a target deletion date of 2027.5 (May 2027).

---

# Part 3: Deployments, Real-World Edge Cases & Production Fixes

## 3.1 GitHub Codespaces Docker-in-Docker Network Fix
* **Problem**: In GitHub Codespaces, Docker runs nested inside an Azure container. The nested Linux kernel's `iptables FORWARD` chain drops inter-container bridge packets, causing `redis.exceptions.TimeoutError: Timeout connecting to server` when the app container attempts to connect to `redis://falkordb:6379`.
* **Fix**: Configured both services in `docker-compose.yml` with `network_mode: host` and connected over `redis://127.0.0.1:6379`. Bypassed the virtual bridge entirely; latency dropped to <0.5 ms over local Linux loopback with zero packet drops.
* **Resilience**: Added a 15-second retry loop in `backend/shadowgraph/db.py` during client initialization.

## 3.2 Railway & FalkorDB Cloud Memory Optimization
* **Problem**: When deploying on Railway and connecting to FalkorDB Cloud Free Tier, the database threw:
  `OutOfMemoryError: command not allowed when used memory > 'maxmemory'`
  because the free cloud instance has a strict 25 MB memory cap, while the Home Assistant knowledge graph with 4,814 integrations and 100,000+ edges requires ~150 MB.
* **Fix**: Documented running `falkordb/falkordb:latest` directly as an internal Docker service on Railway with `REDIS_ARGS="--bind 0.0.0.0 --protected-mode no"`, unlocking Railway's native 8 GB RAM and 0 ms private network routing (`redis://falkordb.railway.internal:6379`).

## 3.3 GitHub REST API 401 Unauthorized Graceful Fallback
* **Problem**: When an invalid, expired, or malformed `GITHUB_TOKEN` is supplied, GitHub returns `401 Unauthorized (Bad credentials)` and rejects requests even for public repositories like `home-assistant/core`.
* **Fix**: In `backend/shadowgraph/ha/sources.py`, added proactive error handling: if a `401 Unauthorized` is returned, ShadowGraph automatically discards the invalid `Authorization` header and retries anonymously, allowing public PR checks to proceed uninterrupted.

## 3.4 Decoupling Admin Token from Public Feed Polling
* **Problem**: The dashboard's "Poll now" button triggers `POST /api/ha/feed/poll`. When `ADMIN_TOKEN` was set, the backend rejected the click with `X-Admin-Token required` because the frontend UI had no mechanism to send administrative headers.
* **Fix**: Removed the admin token check from `ha_feed_poll`, keeping `ADMIN_TOKEN` strictly for destructive endpoints like `/api/admin/reset/{tenant}`.

## 3.5 Comprehensive Public-Facing FAQ
* Added a 16-question accordion FAQ to `README.md` addressing questions that first-time developers and evaluators encounter:
  1. Data authenticity (100% verified real GitHub repositories).
  2. Snapshot architecture & Git commit `19b2bae`.
  3. Real AST analysis vs. `GRAPH.COPY` shadow graph simulation.
  4. Release countdown calculation (CalVer + First Wednesday rule).
  5. PR batch queues and 130+ daily merges on Home Assistant.
  6. Automatic `.diff` patch generation vs. signature change notices.
  7. Adherence to Home Assistant's strict AI policy (read-only advisory mode).
  8. Why Core CI passes while downstream code breaks.
  9. AST context classification (load time, runtime, conditional, shimmed, type hints).
  10. Explanation of the 4 triage verdicts (`Breaks`, `Retires`, `Review`, `Clear`).
  11. Multi-repo and enterprise microservice support (Acme Payments).
  12. Model Context Protocol (MCP) coding agent integration.
  13. The Retirements board and automated deprecation conditions.
  14. The "Broken by core" catalog.
  15. Zero untrusted code execution (pure AST parsing).

---

# Verification Summary

1. **46 Automated Tests Passing**:
   - 33 tests on Acme Payments, Brandes betweenness, PageRank, WCC, atomic locks, simulation.
   - 13 tests on Home Assistant miniature fixture with Git commits, release tags, and HACS mock integrations.
2. **Live Cloud Deployments Tested**:
   - GitHub Codespaces: `https://curly-palm-tree-wrq59x4vwvj439x4x-8000.app.github.dev` (healthy with FastEmbed, LLM Mode, and 3 workspaces).
   - Railway: Live container deployment with FalkorDB and Gemini 2.5 Flash.
3. **MCP Compatibility**:
   - Streamable HTTP `/mcp` and `stdio` compatible with Claude Code, Cursor, and custom agent frameworks.
