"""auth-service (demo stub). Part of the synthetic 'Acme Payments' company used by ShadowGraph.

The three demo pull requests each delete one of the deprecated routes below.
"""
from fastapi import FastAPI

app = FastAPI(title="auth-service")


@app.post("/v1/token-verify")  # deprecated: partner-gateway and legacy-mobile-bff still call it (ADR-19)
def token_verify_v1(token: str) -> dict:
    return {"valid": bool(token), "version": 1}


@app.post("/v2/token-verify")
def token_verify_v2(token: str) -> dict:
    return {"valid": bool(token), "version": 2, "keys": "served in token metadata"}


@app.post("/v1/session-refresh")  # deprecated: kiosk-bff still calls it (ADR-21)
def session_refresh_v1(session_id: str) -> dict:
    return {"session_id": session_id, "refreshed": True, "version": 1}


@app.post("/v2/session-refresh")
def session_refresh_v2(session_id: str) -> dict:
    return {"session_id": session_id, "refreshed": True, "version": 2}


@app.get("/v1/jwks")  # unused since keys moved to v2 token metadata (ADR-15)
def jwks_v1() -> dict:
    return {"keys": []}
