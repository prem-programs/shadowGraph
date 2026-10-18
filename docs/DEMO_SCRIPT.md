# Demo video script (2:30)

Before recording: press **Reset demo** in the dashboard (or `POST /api/admin/reset/acme`), make sure the three PRs
exist in the demo repo, and open the GitHub PR in a second tab. Numbers marked N come from your take: read them off
the screen.

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:15 | GitHub PR #1: the ShadowGraph check is red, the comment is there | "This PR would have cut off our partner API. ShadowGraph caught it by running it on a copy of the company first." |
| 0:15–0:50 | Dashboard, PR #1, press Run. Point at the Decision card: ADR-08 struck through → ADR-19; the expert card | "The author cites ADR-08. The graph knows ADR-08 was replaced by ADR-19, and PageRank says Priya is who auth engineers defer to." |
| 0:50–1:35 | Timeline: the two amber lock conflicts, three "Copied acme_live" rows; graph switches to Shadow: red islands, dashed cut calls; verdict BLOCK; tick "Queries" and click the measurement row to show `algo.WCC` | "Three simulator agents race for hypotheses. The locks live in FalkorDB, so no two do the same work. Each forks the live graph in N ms, removes the endpoint and measures: one connected company becomes three. Blocked, with proof." |
| 1:35–2:10 | Memory tab: the fact overridden; the playbook. Pick PR #2, Run: "Playbook reused … N steps instead of M" | "It remembers. The old belief is overridden by evidence, and the investigation becomes a playbook. The next similar PR takes N steps instead of M, and still re-runs the simulation." |
| 2:10–2:30 | Chat: "Did we see a token-verify removal before?" → answer with PR #1 and ADR-19 chips. Switch tenant to Globex: memory empty. 5 s of Cursor calling `simulate_change` | "Same tools from Cursor over MCP, with separate memory per company. ShadowGraph: see the blast radius before you merge." |

Recording tips: OBS at 1080p, browser zoom 90%, dark or light theme consistently, close other tabs.
