"""Command line:  python -m shadowgraph [serve|seed|reset|run|mcp|ha-import]"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "ha-import":  # its own argument parser
        from .ha.importer import main as ha_import

        ha_import(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="shadowgraph", epilog="also: ha-import (build the Home Assistant snapshot)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="run the API, MCP over HTTP and the dashboard")
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    sub.add_parser("seed", help="create any missing tenant graphs")
    p = sub.add_parser("reset", help="rebuild a tenant's live graph and clear its memory")
    p.add_argument("tenant")
    p = sub.add_parser("run", help="check one PR from the terminal")
    p.add_argument("tenant")
    p.add_argument("pr", type=int)
    p.add_argument("--mode", default="rules", choices=["rules", "llm"])
    p.add_argument("--json", action="store_true")
    sub.add_parser("mcp", help="MCP server over stdio (for Cursor / Claude Desktop)")
    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("shadowgraph.api:app", host=args.host, port=args.port, proxy_headers=True,
                    forwarded_allow_ips="*")
    elif args.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
    else:
        asyncio.run(_async(args))


async def _async(args) -> None:
    from .graph.seed import reset_tenant
    from .runtime import get_runtime

    rt = get_runtime()
    try:
        if args.cmd == "seed":
            await rt.ensure_ready()
            print("ready:", ", ".join(await rt.db.list_graphs()))
        elif args.cmd == "reset":
            await rt.ensure_ready()
            await reset_tenant(rt, args.tenant)
            print(f"reset {args.tenant}")
        elif args.cmd == "run":
            from .agents.orchestrator import run_pr

            res = await run_pr(rt, args.tenant, args.pr, args.mode)
            if args.json:
                print(json.dumps(res, default=str, indent=2))
            else:
                print(res["comment"])
                print(f"\n{res['verdict']['verdict']} · {res['steps']} steps · {res['ms']} ms")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        await rt.db.close()


if __name__ == "__main__":
    main()
