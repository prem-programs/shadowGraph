"""FastAPI app: dashboard API, server-sent events, GitHub webhook, MCP over HTTP at /mcp, and the built UI."""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .agents.orchestrator import start_run
from .core import brain, chat, github, ingest, memory
from .ha import ask as ha_ask
from .ha import feed as ha_feed
from .ha import service as ha
from .db import live_graph
from .graph import queries as Q
from .graph.seed import TENANT_MODULES, reset_tenant
from .mcp_server import server as mcp_server
from .runtime import get_runtime

log = logging.getLogger("shadowgraph.api")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")


def _mcp_security() -> TransportSecuritySettings:
    extra = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if "*" in extra:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "host.docker.internal:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    public = urlparse(get_runtime().settings.public_url)
    if public.hostname and public.hostname not in ("localhost", "127.0.0.1"):
        hosts += [public.hostname, f"{public.hostname}:*"]
        origins += [f"{public.scheme}://{public.hostname}", f"{public.scheme}://{public.hostname}:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts + extra,
                                     allowed_origins=origins + [f"https://{h}" for h in extra])


mcp_http = mcp_server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                          transport_security=_mcp_security())


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = get_runtime()
    rt.cleanup_shadows = True
    try:
        await rt.ensure_ready()
        log.info("FalkorDB ready at %s; tenants %s; embedder %s", rt.settings.falkordb_url,
                 ", ".join(rt.settings.tenants), rt.embedder.name)
    except Exception as exc:  # keep serving; /api/health reports it and requests retry
        log.error("FalkorDB not ready yet: %s", exc)
    feed_task = None
    if rt.settings.ha_feed and "ha" in rt.settings.tenants:
        import asyncio

        feed_task = asyncio.create_task(ha_feed.loop(rt))
    async with mcp_server.session_manager.run():
        yield
    if feed_task:
        feed_task.cancel()
    await rt.db.close()


app = FastAPI(title="ShadowGraph", version="1.0.0", lifespan=lifespan)
app.router.routes.extend(mcp_http.routes)  # POST/GET /mcp
_origins = list(get_runtime().settings.cors_origins)
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"],
                   allow_credentials="*" not in _origins)


async def _ready():
    rt = get_runtime()
    try:
        await rt.ensure_ready()
    except Exception as exc:
        raise HTTPException(503, f"FalkorDB is not reachable at {rt.settings.falkordb_url}: {exc}") from exc
    return rt


def _tenant(rt, tenant: str) -> str:
    if tenant == "ha" and "ha" in rt.settings.tenants:
        return tenant
    if tenant not in rt.settings.tenants or tenant not in TENANT_MODULES:
        raise HTTPException(404, f"unknown tenant '{tenant}'")
    return tenant


class RunRequest(BaseModel):
    tenant: str = "acme"
    pr_number: int | None = None
    pr_url: str | None = None
    mode: str | None = None


class ChatRequest(BaseModel):
    tenant: str = "acme"
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    mode: str | None = None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    rt = get_runtime()
    ok = await rt.db.ping()
    return {"ok": ok, "falkordb": rt.settings.falkordb_url.split("@")[-1], "tenants": list(rt.settings.tenants),
            "embedder": rt.embedder.name if ok else None, "llm": rt.settings.llm_available,
            "llm_model": rt.settings.gemini_model if rt.settings.llm_available else None,
            "github": rt.settings.github_repo if rt.settings.github_enabled else None,
            "default_mode": rt.settings.default_mode}


@app.get("/api/tenants")
async def tenants() -> list[dict[str, Any]]:
    rt = await _ready()
    out = []
    for t in rt.settings.tenants:
        if t == "ha":
            out.append({"id": "ha", "company": "Home Assistant", "kind": "codebase",
                        "loaded": ha.state(rt).loaded, "prs": []})
            continue
        if t not in TENANT_MODULES:
            continue
        prs = await rt.db.query(live_graph(t), Q.PR_LIST, emit=False, read_only=True)
        out.append({"id": t, "company": TENANT_MODULES[t].COMPANY, "kind": "services", "prs": prs})
    return out


@app.get("/api/graph/{tenant}")
async def graph(tenant: str) -> dict[str, Any]:
    rt = await _ready()
    _tenant(rt, tenant)
    g = live_graph(tenant)
    services = await rt.db.query(g, Q.GRAPH_SERVICES, emit=False, read_only=True)
    calls = await rt.db.query(g, Q.GRAPH_CALLS, emit=False, read_only=True)
    bc = {r["service"]: round(float(r["score"]), 3)
          for r in await rt.db.query(g, Q.Q5_BETWEENNESS, emit=False, read_only=True)}
    for s in services:
        s["betweenness"] = bc.get(s["id"], 0.0)
    return {"tenant": tenant, "services": services, "calls": calls}


@app.post("/api/runs")
async def create_run(req: RunRequest) -> dict[str, Any]:
    rt = await _ready()
    tenant = _tenant(rt, req.tenant)
    number = req.pr_number
    if number is None and req.pr_url:
        tail = req.pr_url.rstrip("/").split("/")[-1]
        number = int(tail) if tail.isdigit() else None
    if number is None:
        raise HTTPException(422, "give pr_number or a pr_url ending in the PR number")
    try:
        await brain.pr_context(rt, tenant, number)
    except brain.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    record = start_run(rt, tenant, number, req.mode or rt.settings.default_mode)
    return {"run_id": record.run_id, "tenant": tenant, "pr_number": number, "mode": record.mode}


@app.get("/api/runs")
async def list_runs(tenant: str | None = None) -> list[dict[str, Any]]:
    rt = get_runtime()
    runs = [r for r in rt.runs.values() if tenant is None or r.tenant == tenant]
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return [{"run_id": r.run_id, "tenant": r.tenant, "pr_number": r.pr_number, "mode": r.mode, "status": r.status,
             "started_at": r.started_at, "verdict": (r.result or {}).get("verdict", {}).get("verdict"),
             "steps": (r.result or {}).get("steps"), "ms": (r.result or {}).get("ms")} for r in runs[:30]]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    rt = get_runtime()
    record = rt.runs.get(run_id)
    if not record:
        raise HTTPException(404, "unknown run")
    return record.public()


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    rt = get_runtime()
    if run_id not in rt.runs and not rt.hub.history(run_id):
        raise HTTPException(404, "unknown run")

    async def stream():
        async for event in rt.hub.subscribe(run_id):
            if await request.is_disconnected():
                break
            if event is None:
                continue
            yield {"event": "message", "data": json.dumps(event, default=str)}

    return EventSourceResponse(stream(), ping=15)


@app.post("/api/chat")
async def post_chat(req: ChatRequest) -> dict[str, Any]:
    rt = await _ready()
    tenant = _tenant(rt, req.tenant)
    try:
        if tenant == "ha":
            return await ha_ask.answer(rt, req.session_id, req.message, req.mode or rt.settings.default_mode)
        return await chat.answer(rt, tenant, req.session_id, req.message, req.mode or rt.settings.default_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/sessions/{tenant}")
async def list_sessions(tenant: str) -> list[dict[str, Any]]:
    rt = await _ready()
    return await memory.sessions(rt, _tenant(rt, tenant))


@app.get("/api/sessions/{tenant}/{session_id}")
async def get_session(tenant: str, session_id: str) -> list[dict[str, Any]]:
    rt = await _ready()
    return await memory.session_messages(rt, _tenant(rt, tenant), session_id)


@app.get("/api/memory/{tenant}")
async def memory_overview(tenant: str) -> dict[str, Any]:
    rt = await _ready()
    return await memory.overview(rt, _tenant(rt, tenant))


@app.post("/api/webhooks/github")
async def github_webhook(request: Request, x_github_event: str | None = Header(default=None),
                         x_hub_signature_256: str | None = Header(default=None)) -> JSONResponse:
    rt = await _ready()
    body = await request.body()
    if not github.verify_signature(rt.settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(401, "bad webhook signature")
    if x_github_event == "ping":
        return JSONResponse({"ok": True, "pong": True})
    if x_github_event != "pull_request":
        return JSONResponse({"ok": True, "ignored": x_github_event})
    payload = json.loads(body or b"{}")
    action = payload.get("action")
    tenant = rt.settings.webhook_tenant
    ingested = await ingest.ingest_pull_request(rt, tenant, payload)
    if action not in ("opened", "synchronize", "reopened", "edited") or ingested["status"] != "open":
        return JSONResponse({"ok": True, "ingested": ingested, "run": None})
    try:
        pr = await brain.pr_context(rt, tenant, ingested["number"])
    except brain.NotFound:
        return JSONResponse({"ok": True, "ingested": ingested, "run": None})
    if not pr["removals"]:
        return JSONResponse({"ok": True, "ingested": ingested, "run": None,
                             "note": "PR removes nothing ShadowGraph knows; add 'Removes: /v1/route' to its description"})
    record = start_run(rt, tenant, ingested["number"], rt.settings.default_mode)
    return JSONResponse({"ok": True, "ingested": ingested, "run": record.run_id})


@app.post("/api/admin/reset/{tenant}")
async def admin_reset(tenant: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    rt = await _ready()
    token = os.environ.get("ADMIN_TOKEN", "")
    if token and x_admin_token != token:
        raise HTTPException(401, "X-Admin-Token required")
    await reset_tenant(rt, _tenant(rt, tenant))
    return {"ok": True, "tenant": tenant}


# ---------------------------------------------------------------- Home Assistant workspace (read-only)

def _ha_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ha.NotAvailable):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    return HTTPException(502, f"{type(exc).__name__}: {exc}")


class HAChangeRequest(BaseModel):
    number: int | None = None
    url: str | None = None
    sha: str | None = None
    agents: bool = True


class HAPreflightFile(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str | None = None


class HAPreflight(BaseModel):
    title: str = "Planned change"
    files: list[HAPreflightFile] = Field(min_length=1, max_length=40)


class HADraftRequest(BaseModel):
    symbol: str
    integration: str


class HASymbols(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=60)


@app.get("/api/ha/overview")
async def ha_overview() -> dict[str, Any]:
    rt = await _ready()
    return await ha.overview(rt)


@app.get("/api/ha/changes")
async def ha_changes(limit: int = 100) -> list[dict[str, Any]]:
    rt = await _ready()
    try:
        return await ha.list_changes(rt, min(max(limit, 1), 300))
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.post("/api/ha/changes")
async def ha_check_change(req: HAChangeRequest) -> dict[str, Any]:
    rt = await _ready()
    number = req.number
    if number is None and req.url:
        m = __import__("re").search(r"/pull/(\d+)", req.url)
        number = int(m.group(1)) if m else None
    if number is None and not req.sha:
        raise HTTPException(422, "give a PR number, a PR url or a commit sha")
    if not ha.state(rt).loaded:
        raise HTTPException(409, "the Home Assistant snapshot is not loaded")
    from .ha.agents import start_change_run

    record = start_change_run(rt, number or 0, req.sha or "")
    return {"run_id": record.run_id, "number": number, "sha": req.sha}


@app.get("/api/ha/changes/{change_id}")
async def ha_change(change_id: str) -> dict[str, Any]:
    rt = await _ready()
    try:
        r = await ha.get_change(rt, change_id)
    except Exception as exc:
        raise _ha_error(exc) from exc
    if r is None:
        raise HTTPException(404, f"no check stored for {change_id}")
    return r


@app.post("/api/ha/preflight")
async def ha_preflight(req: HAPreflight) -> dict[str, Any]:
    rt = await _ready()
    try:
        return await ha.analyze_edits(rt, {f.path.lstrip("/"): f.content for f in req.files}, req.title)
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.get("/api/ha/symbols")
async def ha_symbols(q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    rt = await _ready()
    try:
        return await ha.search_symbols(rt, q, min(max(limit, 1), 50))
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.get("/api/ha/symbols/{symbol_id:path}")
async def ha_symbol(symbol_id: str) -> dict[str, Any]:
    rt = await _ready()
    try:
        d = await ha.symbol_detail(rt, symbol_id)
    except Exception as exc:
        raise _ha_error(exc) from exc
    if d is None:
        raise HTTPException(404, f"nobody we scanned imports {symbol_id}")
    return d


@app.get("/api/ha/retirements")
async def ha_retirements() -> dict[str, Any]:
    rt = await _ready()
    try:
        return await ha.retirements(rt)
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.post("/api/ha/retirements/users")
async def ha_retirement_users(req: HASymbols) -> list[dict[str, Any]]:
    rt = await _ready()
    try:
        return await ha.retirement_users(rt, req.symbols)
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.get("/api/ha/broken")
async def ha_broken() -> dict[str, Any]:
    rt = await _ready()
    try:
        return await ha.already_broken(rt)
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.post("/api/ha/drafts")
async def ha_draft(req: HADraftRequest) -> dict[str, Any]:
    rt = await _ready()
    try:
        return await ha.draft_migration(rt, req.symbol, req.integration)
    except Exception as exc:
        raise _ha_error(exc) from exc


@app.get("/api/ha/feed")
async def ha_feed_status() -> dict[str, Any]:
    rt = await _ready()
    f = ha.state(rt).feed
    return {k: f.get(k) for k in ("enabled", "last_poll", "last_error", "polls", "open_seen", "last_checked",
                                  "running")} | {"queued": list(f["queue"]), "interval": rt.settings.ha_feed_interval}


@app.post("/api/ha/feed/poll")
async def ha_feed_poll(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    rt = await _ready()
    token = os.environ.get("ADMIN_TOKEN", "")
    if token and x_admin_token != token:
        raise HTTPException(401, "X-Admin-Token required")
    try:
        return await ha_feed.poll_once(rt)
    except Exception as exc:
        raise _ha_error(exc) from exc


# ---------------------------------------------------------------- the built dashboard (single-container mode)

def _static_dir() -> Path | None:
    configured = get_runtime().settings.static_dir
    candidates = [Path(configured)] if configured else []
    candidates += [Path(__file__).resolve().parents[2] / "frontend" / "dist", Path("/app/static")]
    return next((p for p in candidates if (p / "index.html").is_file()), None)


_static = _static_dir()
if _static:
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("api/", "mcp")):
            raise HTTPException(404)
        file = (_static / path).resolve()
        if path and file.is_file() and _static in file.parents:
            return FileResponse(file)
        return FileResponse(_static / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "ShadowGraph API", "docs": "/docs", "health": "/api/health",
                "ui": "build the frontend (cd frontend && npm run build) or run it with npm run dev"}
