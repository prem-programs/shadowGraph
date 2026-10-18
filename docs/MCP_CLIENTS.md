# Using ShadowGraph from Claude Code, Cursor or Claude Desktop

ShadowGraph's MCP server exposes 30 tools. Start the app first (`docker compose up` or
`python -m shadowgraph serve`); FalkorDB must be running.

## The pre-flight check (Home Assistant)

The point: a coding agent working in a clone of home-assistant/core asks who would break **before** it deletes,
renames or re-signs a core name. All of these are read-only.

| Tool | When |
|---|---|
| `ha_can_remove(symbol)` | before deleting or renaming a name: yes/no, who breaks with `file:line`, deprecation status, where else the name lives |
| `ha_check_edits(files, title)` | before committing: pass the full new content of each edited file (`content: null` deletes it) |
| `ha_who_uses(symbol)` | every user and how it uses the name |
| `ha_check_pr(pr_number)` | the impact report for an open or merged PR |
| `ha_retirements(filter)` | deprecated names, removal release, users left |
| `ha_draft_migration(symbol, integration_id)` | a unified diff for one user (never posted) |
| `ha_search(query)` | find names by part of the name |

Put this in the repo's `CLAUDE.md`, `AGENTS.md` or Cursor rules:

```text
Before you remove, rename or change the signature of anything under homeassistant/,
call the ShadowGraph tool ha_can_remove (or ha_check_edits with your new file contents).
If it reports integrations that would break, do not remove the name: deprecate it with a
removal version instead, and include the list of affected integrations in the PR description.
```

Good prompts: *"Can I delete `homeassistant.components.sensor.EntityCategory`?"*, *"Who still uses
`CONN_CLASS_CLOUD_POLL`, and what would a migration look like for one of them?"*, *"Check PR 186863."*

**Claude Code** (one command, HTTP):

```bash
claude mcp add --transport http shadowgraph http://localhost:8000/mcp
```

## The services sample (Acme)

- "Use shadowgraph to check PR 1 in tenant acme. Is it safe to merge?" (calls `simulate_change`)
- "Which decision is in force for PR 2?" (`brain_trace_decision`)
- "What happens if auth-service:/v1/jwks is removed?" (`simulate_change` with `endpoint_id`)

Tool calls from these clients appear in the server log; the sample tools use tenant `acme` unless you name another.

## Cursor (HTTP, simplest)

Create `.cursor/mcp.json` in any project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "shadowgraph": { "url": "http://localhost:8000/mcp" }
  }
}
```

Then Cursor Settings → MCP: `shadowgraph` should show 30 tools.

## Claude Desktop

Claude Desktop starts local servers over stdio. Edit `claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`; macOS: `~/Library/Application Support/Claude/`), then
restart Claude Desktop. Pick one option.

**A. Through Docker** (no Python needed on the machine; replace the path with your project folder):

```json
{
  "mcpServers": {
    "shadowgraph": {
      "command": "docker",
      "args": ["compose", "-f", "C:\\Users\\you\\shadowgraph\\docker-compose.yml", "exec", "-T", "app",
               "python", "-m", "shadowgraph", "mcp"]
    }
  }
}
```

**B. With the local virtualenv** (after `pip install -r backend/requirements.txt` in `backend\.venv`):

```json
{
  "mcpServers": {
    "shadowgraph": {
      "command": "C:\\Users\\you\\shadowgraph\\backend\\.venv\\Scripts\\python.exe",
      "args": ["-m", "shadowgraph", "mcp"],
      "env": {
        "PYTHONPATH": "C:\\Users\\you\\shadowgraph\\backend",
        "FALKORDB_URL": "redis://localhost:6379"
      }
    }
  }
}
```

**C. Bridge to the HTTP endpoint** (needs Node): `"command": "npx", "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"]`.

## A deployed server

`/mcp` accepts requests whose Host is localhost or the host in `PUBLIC_URL`. Add other hostnames with
`MCP_ALLOWED_HOSTS` (comma separated, or `*`). The endpoint has no authentication: on a public deployment anyone
with the URL can run checks and write to agent memory. The Home Assistant data is public and the tools are
read-only toward GitHub, but put an authenticating proxy in front of a public deployment if memory matters.
