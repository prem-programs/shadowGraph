# Using ShadowGraph from Cursor or Claude Desktop

ShadowGraph's MCP server exposes 17 tools. Start the app first (`docker compose up` or
`python -m shadowgraph serve`); FalkorDB must be running.

Good first prompts:

- "Use shadowgraph to check PR 1 in tenant acme. Is it safe to merge?" (calls `simulate_change`)
- "Which decision is in force for PR 2?" (`brain_trace_decision`)
- "What happens if auth-service:/v1/jwks is removed?" (`simulate_change` with `endpoint_id`)

Tool calls from these clients appear in the server log; they use tenant `acme` unless you name another.

## Cursor (HTTP, simplest)

Create `.cursor/mcp.json` in any project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "shadowgraph": { "url": "http://localhost:8000/mcp" }
  }
}
```

Then Cursor Settings → MCP: `shadowgraph` should show 17 tools.

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
with the URL can run checks and write to agent memory, so keep the demo data synthetic (it is).
