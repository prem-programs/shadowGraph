# The Home Assistant workspace

ShadowGraph's main demo runs on a real, busy open-source codebase instead of invented data:
[home-assistant/core](https://github.com/home-assistant/core) (about 130 merged pull requests a day) and every
custom integration in the [HACS default list](https://github.com/hacs/default), which builds on core's Python API
from 3,303 other repositories.

That pairing works like a large company. Core is the platform team. Custom integrations are the consumer
teams in other repositories. Core's CI only tests core, so a refactor that is green there can still break
code nobody in core can see. ShadowGraph makes that visible before the merge.

**Read-only by design.** Home Assistant's [AI policy](https://github.com/home-assistant/architecture/blob/master/AI_POLICY.md)
treats automated comments as spam. ShadowGraph never posts comments, statuses or pull requests to Home
Assistant or to any custom integration. Migration patches are drafts that you copy yourself.

## What it found in one real week (Oct 11-18, 2026)

These are reproducible: replay them from the Pull requests page.

| Change | What ShadowGraph reports |
|---|---|
| [#186863](https://github.com/home-assistant/core/pull/186863) "Move sensor entity classes to entity module" (merged Oct 16) | **Breaks 32 custom integrations on 2026.11.** The refactor stopped importing 10 names into `homeassistant.components.sensor` that custom integrations import from there: `StateType`, `RestoreEntity`, `EntityCategory`, `HomeAssistantError`, `UnitOfTemperature`, `CONF_UNIT_OF_MEASUREMENT`, `Entity`, even `datetime`, `dataclass` and `cached_property`. Core CI is green, because no core code imports them that way. |
| [#184756](https://github.com/home-assistant/core/pull/184756) "Remove deprecated async_track_state_change from event" (merged Oct 12) | **Breaks 11 custom integrations on 2026.11.** A planned removal, announced on the developer blog in 2024, still catches 11 custom integrations, all of which import it at module level. |
| [#186267](https://github.com/home-assistant/core/pull/186267) "Deprecate config flow connection class constants" (merged Oct 14) | **Starts retiring `CONN_CLASS_*` (6 names): 343 integrations still use them;** removal planned for 2027.5 (May 5, 2027). |

Across the whole snapshot:
- **59** custom integrations stop loading (fully or one platform) in 2026.11 unless they are fixed first;
  36 of them were updated since July, so these are maintained projects, not abandoned ones.
- **95** already fail on the current release because they import names core has removed.
- **17** retirements are in progress (32 deprecated names). 10 are ready to remove today. One,
  `DeviceEntry.suggested_area`, is past its planned removal release.
- In the same week, 62% of merged PRs touched one integration nothing depends on. The live feed checks
  those in a second and stays quiet; the interesting ones are rare but real, and they show up every week.

## How it works

1. **Snapshot** (`python -m shadowgraph ha-import`, see below). Static analysis only; no third-party code
   runs. For every core integration and every HACS integration, every import of a Home Assistant name is
   recorded with its file, line and *context*:

   | context | meaning | if the name disappears |
   |---|---|---|
   | load | module level or a class body | the import fails: the integration or that platform does not load |
   | runtime | inside a function | fails when that code runs |
   | conditional | inside a module-level `if` (often a version check) | may fail |
   | shimmed | inside `try/except ImportError` (or similar) | keeps working |
   | type | annotations only | no effect (annotations are lazy in Python 3.14) |

   Each integration's own import graph is followed from `__init__.py` and from each platform file, so a
   failure is described precisely ("the integration fails to set up" or "its sensor platform fails to load").
   Names are resolved through re-exports, aliases, star imports and base classes. When static analysis cannot
   prove an answer (a third-party base class, a module-level `__getattr__`), the name is marked *unknown* and
   left out instead of guessed. Self-test: every import in core's own 1,510 integrations resolves (zero false
   "missing").
2. **Graph** (FalkorDB graph `ha_live`): `Integration`, `Module`, `Symbol`, `Person`, `Release`, `Post`
   (developer blog), `Decision` (ADRs); `USES {sites, context}`, `DEPENDS_ON` (manifests), `CODEOWNER`,
   `REMOVED_IN` / `DEPRECATED_IN` / `BREAKS_IN`, `MENTIONS`, `SUPERSEDED_BY`.
3. **A change** (an open PR, a merged PR, or an agent's planned edit). Both trees are read (GitHub's test-merge
   ref `refs/pull/N/merge` for open PRs, the squash commit for merged ones), the API of every changed module is
   diffed name by name, and the graph answers who uses what disappeared, where, and with what effect.
   Failures spread through manifest `dependencies`, release dates follow Home Assistant's monthly schedule.
4. **Agents** (Scout, Scribe, three Simulators, Judge) run the same check through MCP tools. Each simulator
   copies the graph with `GRAPH.COPY`, applies the change in the copy, measures and drops it. The Judge compares
   the shadow count with the engine's count and stores the report; memory keeps episodes, facts ("is
   `X` safe to remove?") and playbooks, so the next similar change runs only the checks that mattered.

## Using it

```bash
# 1. FalkorDB
docker compose up -d falkordb
# 2. the app (the snapshot in data/ is loaded automatically on first start, about 20 s)
cd backend && pip install -r requirements.txt && python -m shadowgraph serve
# open http://localhost:8000
```

Where pull requests come from (`HA_SOURCE`):

| source | needs | notes |
|---|---|---|
| `github` (default when no clone) | nothing; `GITHUB_TOKEN` recommended | 60 API requests/hour without a token, 5,000 with one (read-only use). Real PR titles. |
| `git` | `HA_CLONE` = a clone of home-assistant/core | No API at all. Open PRs via `git ls-remote refs/pull/*/merge`; titles are the latest commit subject. PR refs are fetched into a separate store next to the clone (`<clone>-prs.git`), borrowing the clone's objects. |

Live feed: `HA_FEED=1` checks the newest open PRs every `HA_FEED_INTERVAL` seconds (`HA_FEED_BATCH` per poll),
and hands any PR that breaks, retires or needs review to the agent team. Or press **Poll now**.

MCP tools for coding agents: see the **Connect an agent** page, or `docs/MCP_CLIENTS.md`.

## Rebuilding the snapshot

```bash
python scripts/fetch_ha_sources.py --dir ../ha-sources --build
```

This clones home-assistant/core (dev since two weeks ago, plus 13 monthly release tags at depth 1), hacs/default,
every listed integration (Python files and manifests only), the developer blog and the architecture ADRs, then
writes `data/ha_snapshot.json.gz` (about 6 MB). Expect about 3 GB of disk and 15 to 25 minutes on a good
connection. `--limit 50` makes a quick partial snapshot for testing (write it elsewhere with `--out`).

## Limits (said plainly)

- Only integrations in the HACS default list are covered; private and unlisted ones are not.
- Dead code counts: a file that is never imported still counts as a user.
- Attribute access on instances (`entity.some_removed_method()`) is not tracked; only names reached through
  imports, plus `Class.member` on imported classes.
- Install counts are not in the snapshot, so "how many people" is not shown. (Home Assistant publishes
  [analytics](https://analytics.home-assistant.io) per custom integration; adding them is a small task.)
- Future release dates are expectations from the schedule (first Wednesday of the month), not announcements.
- A "breaks in 2026.11" claim assumes neither side changes before the release.
