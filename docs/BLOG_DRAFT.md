---
title: "A green refactor that breaks 32 projects: checking Home Assistant PRs against every custom integration"
tags: falkordb, graphdb, ai, opensource
---

# A green refactor that breaks 32 projects

*Draft for Dev.to / Hashnode. Edit it into your own voice, add a screenshot or GIF, and the repo link.*

On October 16, 2026, Home Assistant merged
[#186863](https://github.com/home-assistant/core/pull/186863), "Move sensor entity classes to entity module". A
tidy refactor: the classes moved into a new file, the package re-exports what it should, every test passed.

It also stopped importing ten names into `homeassistant.components.sensor` that the sensor package never meant to
export: `StateType`, `RestoreEntity`, `EntityCategory`, `Entity`, even `datetime`. Thirty-two custom
integrations import exactly those names from exactly that module. When 2026.11 ships on November 4, their sensor
platforms (and in four cases the whole integration) will fail to load, unless someone fixes them first.

Nobody in core could have seen it. Core's CI tests core. The 32 integrations live in other repositories.

This is the oldest problem in platform engineering, with a law named after it: *with enough users, every
observable behaviour of your API will be depended on by somebody.* Every company with an internal SDK, a shared
library or a public API has a version of it. And AI coding agents, which refactor quickly and confidently, will
make more of these "harmless" changes, not fewer.

For Graph Hacks (WeMakeDevs × FalkorDB) I built **ShadowGraph**: know who you'll break before you merge, whether
a person or an agent wrote the change.

## The graph: who imports what, and when that code runs

Home Assistant is a good proving ground because its consumers are public: the
[HACS default list](https://github.com/hacs/default) names 3,303 repositories of custom integrations. ShadowGraph
reads all of them plus core (1,510 integrations) with static analysis, and stores every import of a Home Assistant
name in FalkorDB: 562,444 import sites.

The important detail is *when* each use runs, because that decides what a removal does:

| where the name is used | if it disappears |
|---|---|
| module level, or a class body | the import fails: the integration (or that platform) doesn't load |
| inside a function | fails when that function runs |
| inside `try/except ImportError` | the author has a fallback; nothing breaks |
| type annotations only | nothing; annotations are lazy in Python 3.14 |

Following each integration's own imports from `__init__.py` and from each platform file turns that into
sentences a maintainer understands: "the integration fails to set up" or "its sensor platform fails to load".

The rest is graph: `(Integration)-[:USES {sites, context}]->(Symbol)`, `DEPENDS_ON` from manifests (a failure
spreads to integrations that depend on it), `CODEOWNER`, releases, developer-blog posts linked to the names they
mention, and architecture decisions with `SUPERSEDED_BY`.

## A change, end to end

For a pull request, ShadowGraph reads both trees (GitHub's test-merge ref for open PRs, the squash commit for
merged ones), diffs the API of every changed module name by name, and asks the graph one question: who uses what
disappeared, where, and in what context? On the full graph that takes under a second.

Then a small team of agents re-checks it, all through MCP tools. Three simulators race for hypotheses (locks in a
memory graph, so no two do the same work). Each copies the live graph with `GRAPH.COPY`, applies the change in its
own shadow copy, and measures: who loses an import, and how failures spread through manifest dependencies. The
judge compares the shadow count with the engine's (32 and 32) before filing the report, and memory keeps what was
learned, so the next similar change runs only the checks that mattered.

## What one week looked like

- #186863: breaks **32** custom integrations.
- [#184756](https://github.com/home-assistant/core/pull/184756), "Remove deprecated `async_track_state_change`":
  breaks **11**, all at import time, even though the developer blog announced the change in 2024.
- [#186267](https://github.com/home-assistant/core/pull/186267) deprecates the `CONN_CLASS_*` constants:
  **343** integrations still use them, with removal planned for 2027.5.
- Across the snapshot, 59 custom integrations stop loading in 2026.11 unless fixed first; 95 already fail today.

Most PRs are quiet: in the same week 62% touched one integration that nothing else depends on. The live feed checks
those in a second and says so. The point is to catch the few that matter, every week.

## For people, and for agents

The dashboard shows each user with the exact `file:line`, and drafts a fix (usually "import it from where it
actually lives"), parsed and re-checked before it is shown. It runs **read-only**: Home Assistant's AI policy treats
bot comments as spam, so nothing is ever posted; a person decides what to send.

For coding agents, the same checks are MCP tools. Put one line in `CLAUDE.md`, *"before you remove or rename
anything under homeassistant/, call ha_can_remove"*, and the agent asks before it deletes. For
`homeassistant.components.sensor.EntityCategory` the answer today is: no, 7 integrations would break, here they are.

## What I learned about FalkorDB along the way

- `GRAPH.COPY` forks the server process, so copies must be queued; on this graph (about 266,000 edges and 15,000 nodes) a copy takes
  about 1.4 seconds, so the agents fork only for the hypotheses that need a shadow.
- `algo.betweenness` samples sources, so before/after deltas are noise; for the services sample I compute exact
  betweenness with `allShortestPaths` instead.
- Vector search returns a *distance*, and a graph edge (`SUPERSEDED_BY`) beats similarity when the most similar
  document is the one that was replaced.

ShadowGraph is open source: [github.com/prem-programs/shadowGraph](https://github.com/prem-programs/shadowGraph).
