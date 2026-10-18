# ShadowGraph backend

FastAPI + MCP server + agents. See the project README one level up.

    pip install -r requirements-dev.txt
    python -m shadowgraph serve      # API, /mcp and the built dashboard on :8000
    python -m shadowgraph mcp        # MCP over stdio for Cursor / Claude Desktop
    python -m shadowgraph run acme 1 # check PR #1 from the terminal
    python -m pytest -q              # needs FalkorDB on FALKORDB_URL (default redis://localhost:6379)
