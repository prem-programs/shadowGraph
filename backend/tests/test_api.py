"""HTTP API, in-process (no server needed)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import httpx

from shadowgraph.api import app
from shadowgraph.core.github import verify_signature
from shadowgraph.core.ingest import parse_body


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_tenants_graph(fresh):
    async with _client() as c:
        h = (await c.get("/api/health")).json()
        assert h["ok"] is True and "acme" in h["tenants"]
        tenants = (await c.get("/api/tenants")).json()
        assert [p["number"] for p in tenants[0]["prs"]] == [1, 2, 3]
        g = (await c.get("/api/graph/acme")).json()
        assert len(g["services"]) == 25 and len(g["calls"]) == 42
        assert (await c.get("/api/graph/nope")).status_code == 404


async def test_run_and_events(fresh):
    async with _client() as c:
        r = await c.post("/api/runs", json={"tenant": "acme", "pr_number": 1})
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        for _ in range(100):
            state = (await c.get(f"/api/runs/{run_id}")).json()
            if state["status"] != "running":
                break
            await asyncio.sleep(0.1)
        assert state["status"] == "done" and state["result"]["verdict"]["verdict"] == "BLOCK"
        assert (await c.post("/api/runs", json={"tenant": "acme", "pr_number": 999})).status_code == 404
        runs = (await c.get("/api/runs?tenant=acme")).json()
        assert runs[0]["run_id"] == run_id
    types = [e["type"] for e in fresh.hub.history(run_id)]
    assert types[0] == "run_started" and types[-1] == "run_finished"


async def test_chat_and_memory(fresh):
    async with _client() as c:
        r = (await c.post("/api/chat", json={"tenant": "acme", "message": "what is our retry policy?"})).json()
        assert "ADR-17" in r["sources"]
        sessions = (await c.get("/api/sessions/acme")).json()
        assert sessions[0]["id"] == r["session_id"]
        mem = (await c.get("/api/memory/acme")).json()
        assert mem["counts"]["sessions"] == 1


async def test_webhook_ingests_a_new_pr(fresh):
    payload = {"action": "opened", "pull_request": {
        "number": 42, "title": "Drop old refresh route", "state": "open", "merged": False,
        "body": "Cleanup\n\nRemoves: auth-service:/v1/session-refresh\nCites: ADR-12", "user": {"login": "tom"}}}
    async with _client() as c:
        r = (await c.post("/api/webhooks/github", content=json.dumps(payload),
                          headers={"X-GitHub-Event": "pull_request", "content-type": "application/json"})).json()
        assert r["ingested"]["linked_targets"] == ["auth-service:/v1/session-refresh"] and r["run"]
        assert (await c.post("/api/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"})).json()["pong"]


def test_signature_and_parsing():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, sig) and not verify_signature("s3cret", body, "sha256=bad")
    assert parse_body("x\nRemoves: /v1/token-verify\nCites: ADR-08 and ADR-19") == {
        "removes": ["/v1/token-verify"], "cites": ["ADR-08", "ADR-19"]}
