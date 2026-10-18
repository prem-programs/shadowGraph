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
async def rt():
    runtime = Runtime()
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
