"""Tests run against a real FalkorDB (FALKORDB_URL, default redis://localhost:6379).

They use graphs prefixed 'test_' and the hashing embedder, so they never touch your demo graphs and need no
model download. If FalkorDB is not reachable, every test is skipped with a clear message.
"""
from __future__ import annotations

import os

os.environ["GRAPH_PREFIX"] = "test_"
os.environ.setdefault("EMBED_BACKEND", "hash")
os.environ["GITHUB_TOKEN"] = ""
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402

from shadowgraph.runtime import Runtime, set_runtime  # noqa: E402


@pytest.fixture(scope="session")
def ha_fixture(tmp_path_factory):
    """A miniature Home Assistant (git repo + HACS folder) and the snapshot built from it."""
    from shadowgraph.ha.importer import build, save
    from tests.ha_fixture import build as build_fixture

    tmp = tmp_path_factory.mktemp("ha")
    fx = build_fixture(tmp)
    snap = build(fx["core"], fx["hacs"], None, None, "origin/dev", ["2026.9.0", "2026.10.0"])
    path = str(tmp / "ha_snapshot.json.gz")
    save(snap, path)
    return {**fx, "snapshot": path, "snap": snap}


@pytest.fixture(scope="session")
async def rt(ha_fixture):
    from shadowgraph.config import Settings

    cfg = Settings(tenants=("ha", "acme", "globex"), ha_snapshot=ha_fixture["snapshot"], ha_clone=ha_fixture["core"],
                   ha_source="git", github_token="", gemini_api_key="")
    runtime = Runtime(cfg)
    if not await runtime.db.ping():
        pytest.skip(f"FalkorDB not reachable at {runtime.settings.falkordb_url} (start it: docker compose up falkordb)")
    set_runtime(runtime)
    await runtime.ensure_ready()
    yield runtime
    for name in await runtime.db.list_graphs():
        if name.startswith("test_"):
            await runtime.db.delete(name)
    await runtime.db.close()


@pytest.fixture
async def fresh(rt):
    """A freshly reset acme tenant (live graph rebuilt, memory empty)."""
    from shadowgraph.graph.seed import reset_tenant

    await reset_tenant(rt, "acme")
    return rt
