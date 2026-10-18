# Demo video script (2:45)

Before recording: `docker compose up`, open <http://localhost:8000>, replay #186863, #184756 and #186267 once
(Pull requests page, "Replay a real change") so they are in the list, and have Claude Code or Cursor connected to
`http://localhost:8000/mcp`. Numbers below are from the snapshot in `data/` (Oct 18, 2026); if you rebuild it,
read the new ones off the screen.

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | GitHub: PR #186863 in home-assistant/core, all checks green, merged | "This is Home Assistant, one of the busiest open-source projects: 130 merged PRs a day. This refactor passed every check and merged on Thursday." |
| 0:20–0:55 | ShadowGraph, Pull requests → #186863. The verdict line, the 4 numbers, then the first rows of *Who is affected* | "It breaks 32 custom integrations when 2026.11 ships on November 4. Core's CI can't see them: they live in 3,303 other repositories. ShadowGraph can, down to the file and line: Aquatlantis Ori imports `StateType` from the sensor module, so its sensor platform will fail to load." |
| 0:55–1:20 | Expand a row, press **Draft a fix**: the two-line diff | "And here is the fix for that maintainer: import it from where it actually lives. A draft. ShadowGraph never posts to anyone's repository; Home Assistant's policy treats bot comments as spam, so we respect that." |
| 1:20–1:45 | Open *How this was checked*: hypotheses H1–H5, the agents, "Engine and shadow simulation agree: 32 vs 32", the small graph | "Under the hood, agents check every change. Three simulators copy the FalkorDB graph, apply the change in their own shadow, and measure. The judge compares their count with the engine's before filing the report." |
| 1:45–2:05 | Retirements → 2027.5 → CONN_CLASS_* (343 still use it) | "It also runs retirements. Core just deprecated six constants; 343 integrations still use them. The condition for removal is executable: remove when nobody imports them. Today: 343 to go." |
| 2:05–2:30 | Claude Code: "Can I delete `homeassistant.components.sensor.EntityCategory`?" → it calls `ha_can_remove` → "No: 7 integrations would break…" | "And it's a pre-flight for AI agents. Before Claude deletes a name, it asks ShadowGraph who would break. Agents refactor fast; now they refactor with the blast radius in view." |
| 2:30–2:45 | Broken by core: 59 / 95 | "59 integrations stop loading next month, 95 are broken today. Every platform team has this problem with their internal consumers. ShadowGraph: know who you'll break before you merge." |

Recording tips: 1080p, browser zoom 90%, one theme throughout, close other tabs. The Ask page ("Who still uses
async_track_state_change?") is a good 10-second extra if you have time.
