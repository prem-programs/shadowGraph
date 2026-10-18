---
title: "A Green Refactor That Broke 32 Projects: How We Built ShadowGraph with FalkorDB and AI Agents"
published: true
tags: falkordb, graphdb, ai, opensource, devops, python
canonical_url: https://github.com/prem-programs/shadowGraph
cover_image: https://raw.githubusercontent.com/prem-programs/shadowGraph/main/docs/img/ha-report.png
---

# A Green Refactor That Broke 32 Projects: How We Built ShadowGraph with FalkorDB and AI Agents

On Thursday, October 16, 2026, [Home Assistant](https://github.com/home-assistant/core) merged pull request [#186863](https://github.com/home-assistant/core/pull/186863): *"Move sensor entity classes to entity module"*.

It was a clean, textbook refactor:
- The internal classes moved to their own tidy submodule.
- The package re-exported what was needed.
- Unit tests, linting, and core CI were **100% green**.

Yet on November 4, when Home Assistant 2026.11 ships, **32 custom integrations will fail to load or crash on startup**.

Why? The refactor stopped re-exporting ten internal utility symbols from `homeassistant.components.sensor`—names like `StateType`, `RestoreEntity`, `EntityCategory`, `HomeAssistantError`, and even standard library imports like `datetime` and `dataclass`. Thirty-two separate open-source integration repositories were importing those names directly from that sensor module.

Nobody in core CI saw it. Core CI tests core. The 32 integrations live across 3,303 external repositories.

This is **Hyrum's Law** in raw daylight:
> *"With a sufficient number of users of an API, it does not matter what you promise in the contract: all observable behaviours of your system will be depended on by somebody."*

As AI coding agents like Claude Code, Cursor, and Codex write and refactor code at superhuman speed, these "harmless" refactors are going to explode.

To solve this, I built **[ShadowGraph](https://github.com/prem-programs/shadowGraph)** for the **WeMakeDevs × FalkorDB Graph Hacks Hackathon**: an autonomous pull-request merge gate and blast-radius simulator that tells you who you will break before you merge—whether a human or an AI agent authored the change.

---

## The Blind Spot of Modern CI

In large engineering organizations and massive open-source ecosystems, architecture naturally mirrors a platform model:
- **The Core / Platform Team**: Maintains internal libraries, microservices, and foundational SDKs.
- **Consumer Teams / External Maintainers**: Build services and integrations on top of the platform.

```
+-----------------------------------------------------------+
|                    Core Platform (CI Green)               |
|            [PR #186863: Refactor sensor module]           |
+-----------------------------+-----------------------------+
                              |
                     BREAKS DOWNSTREAM
                              |
       +----------------------+----------------------+
       |                      |                      |
       v                      v                      v
[Integration A]        [Integration B]        [Integration C]
(Aquatlantis Ori)      (SunPower Monitor)     (Smart Thermostat)
  * CRASHES *            * CRASHES *            * CRASHES *
```

When platform teams change code:
1. They test their own repository.
2. They cannot see or run CI for all 3,000 downstream repos.
3. Breaking changes slip through silently, only discovered in production by angry users.

---

## Modeling 562,444 Import Sites in FalkorDB

To know the blast radius of a change, you need a graph. Relational tables choke when navigating dynamic dependency chains, and static text greps generate thousands of false alarms.

We chose **[FalkorDB](https://www.falkordb.com/)**, an ultra-low latency graph database running on Redis, capable of executing Cypher queries at in-memory speed.

### The Home Assistant Proving Ground
Home Assistant is the world's largest real-world proving ground:
- **1,510 core integrations** in `home-assistant/core`.
- **3,303 external custom integration repositories** registered in the [HACS default list](https://github.com/hacs/default).

Using Python AST static analysis, ShadowGraph indexed every single import of a Home Assistant symbol across all 4,814 integrations:
- **4,814 Integration Nodes**
- **6,562 Symbol Nodes**
- **562,444 USES relationships**

```
(:Integration {id: "hacs:aquatlantis", name: "Aquatlantis Ori"})
   -[:USES {file: "sensor.py", line: 9, context: "load"}]->
(:Symbol {id: "homeassistant.components.sensor.StateType"})
```

### Context Awareness: Eliminating False Positives
The critical insight is *context*. A missing import doesn't always break code:

| Import Context | Where It Appears | Blast Radius if Name Disappears |
|---|---|---|
| **`load`** | Module-level or class body | **CRITICAL**: The module fails to import; integration fails to set up. |
| **`runtime`** | Inside a function body | **DEFERRED**: Works on startup; fails only when that function executes. |
| **`shimmed`** | Inside `try...except ImportError` | **SAFE**: Maintainer wrote a fallback; zero breakage. |
| **`type`** | Typing annotations only | **SAFE**: Under Python 3.14 lazy annotations, causes zero runtime failure. |

By following internal import paths from `__init__.py` and each platform file (`sensor.py`, `climate.py`), ShadowGraph can tell you:
> *"Aquatlantis Ori will fail to set up entirely, but SmartAir will only fail when its climate platform loads."*

---

## How It Works: Counterfactual Simulation with `GRAPH.COPY`

When a PR arrives, checking it statically against a database is only step one. ShadowGraph performs a **counterfactual simulation** in isolated memory:

```
[Live Graph: ha_live] 
         |
         | FalkorDB GRAPH.COPY (~100ms)
         v
[Shadow Graph: ha_shadow_pr186863]
         |
         | 1. Apply PR AST diff (delete removed symbols)
         | 2. Measure WCC partitions & broken USES edges
         | 3. Propagate failure through DEPENDS_ON manifests
         v
[Judge Agent Verification] ---> Verdict: BLOCK (32 broken integrations)
```

1. **Diffing the AST**: ShadowGraph reads the pull request diff (or squash commit), extracting exact symbol additions, modifications, re-exports, and removals.
2. **Instant Graph Cloning (`GRAPH.COPY`)**: FalkorDB clones the entire multi-gigabyte live graph into an isolated shadow copy in ~100ms.
3. **Multi-Agent Simulation Race**:
   A swarm of autonomous agents coordinates through locks in a shared memory graph:
   - **Scout Agent**: Generates hypotheses (e.g., *H1: Module re-export dropped*, *H2: Cascade failure in dependents*).
   - **Simulator Agents**: Acquire locks on hypotheses, execute counterfactual Cypher queries on their shadow graphs, and measure broken components.
   - **Judge Agent**: Compares shadow simulation results against static engine analysis (e.g., `32 vs 32`), confirming proof before generating reports.
   - **Scribe Agent**: Records verified outcomes into persistent memory playbooks. If a similar PR is tested later, the agent recalls the playbook and reaches a verdict in 23 steps instead of 35.

---

## Real Discoveries from One Week of Production PRs

Running ShadowGraph against Home Assistant's real merged PRs from October 11–18, 2026 revealed astonishing findings:

1. **PR #186863 (The Sensor Entity Refactor)**:
   - **Result**: Breaks **32 custom integrations** on 2026.11.
   - Custom integrations like *Aquatlantis Ori*, *SunPower Monitor*, and *Buderus ProWork* will fail on startup.
2. **PR #184756 (Removing deprecated `async_track_state_change`)**:
   - **Result**: Breaks **11 custom integrations**.
   - Even though this deprecation was announced on the developer blog in 2024, 11 maintained integrations still import it at the module level.
3. **PR #186267 (Deprecating `CONN_CLASS_*` constants)**:
   - **Result**: **343 integrations** currently depend on these constants. Scheduled for removal in 2027.5.
4. **Across the entire ecosystem**:
   - **59 custom integrations** will stop loading in Home Assistant 2026.11 unless patched.
   - **95 integrations** are already broken today because they reference previously removed core APIs.

---

## Automated Draft Fixes: Don't Just Complain, Fix It

ShadowGraph doesn't just block PRs; it creates one-click draft migration patches for downstream maintainers.

Because ShadowGraph knows where the symbol originally moved, it automatically synthesizes the clean fix:

```diff
# custom_components/aquatlantis/sensor.py
- from homeassistant.components.sensor import SensorEntity, StateType
+ from homeassistant.components.sensor import SensorEntity
+ from homeassistant.helpers.typing import StateType
```

Maintainers can copy the two-line diff directly from the dashboard or via API.

> **Respecting Open Source AI Policy**: Home Assistant's [AI policy](https://github.com/home-assistant/architecture/blob/master/AI_POLICY.md) strictly prohibits bot comments and automated spam. ShadowGraph operates **read-only by design** on public repositories. It never posts unsolicited comments or opens automated PRs. It gives humans and platform maintainers the intelligence they need to make decisions.

---

## AI Agent Pre-Flight via MCP (Model Context Protocol)

ShadowGraph exposes 17 native tools over **Model Context Protocol (MCP)**, connecting directly to **Claude Code**, **Cursor**, **Windsurf**, and IDE agents.

By adding a single instruction to your project's `CLAUDE.md` or `.cursorrules`:
```markdown
Before deleting or renaming any symbol in homeassistant/, call ha_can_remove.
```

Your AI assistant can query ShadowGraph before touching code:

```text
User: "Can we delete homeassistant.components.sensor.EntityCategory?"

Claude Code: [Calls MCP Tool: ha_can_remove(symbol="homeassistant.components.sensor.EntityCategory")]
ShadowGraph: {
  "can_remove": false,
  "blocking_integrations": 7,
  "affected": ["aquatlantis", "solaredge_modbus", "daikin_residential", ...]
}

Claude: "No. Removing EntityCategory will break 7 custom integrations that import it at module load time. We must maintain a deprecated alias."
```

Instead of AI agents hallucinating that a refactor is safe because local tests pass, they have full visibility into the external blast radius.

---

## Key Lessons Learned with FalkorDB

1. **`GRAPH.COPY` is a Game Changer for Counterfactual AI**:
   Testing hypotheses on live data risks state corruption. Cloning graphs on demand enables speculative counterfactual reasoning without affecting production state.
2. **Exact Algorithms Beat Sampling for Blast Radius**:
   Approximation algorithms (like sampling betweenness centrality) generate noisy deltas. Computing exact shortest paths (`allShortestPaths`) in FalkorDB was fast enough (sub-second) and eliminated ghost warnings.
3. **Vector Embeddings + Structural Lineage = Reliable Memory**:
   Semantic similarity (via fastembed `all-MiniLM-L6-v2`) helps agents retrieve relevant past incidents, but combining vector distance with explicit graph relationships (`SUPERSEDED_BY`) prevents agents from citing obsolete architectural decisions.

---

## Try It Out

ShadowGraph is 100% open source under the MIT License. You can run the entire system locally with Docker in under two minutes:

```bash
git clone https://github.com/prem-programs/shadowGraph.git
cd shadowGraph
copy .env.example .env    # Linux/macOS: cp .env.example .env
docker compose up --build
```

- Open the dashboard at `http://localhost:8000`
- Explore the FalkorDB Browser at `http://localhost:3000`
- Check out the repository on GitHub: **[github.com/prem-programs/shadowGraph](https://github.com/prem-programs/shadowGraph)**

---

*Built for Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB). Entered in Track 01 (Acts on connected data), Track 02 (Memory and coordination), and Track 03 (Company brain).*
