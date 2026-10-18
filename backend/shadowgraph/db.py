"""Thin async wrapper over FalkorDB: graph naming, timing, and one event per query."""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from falkordb.asyncio import FalkorDB

from .events import EventHub

Rows = list[dict[str, Any]]
_SAFE = re.compile(r"[^a-zA-Z0-9_]")
# Optional prefix for every graph name (the tests use "test_" so they never touch real demo graphs).
PREFIX = re.sub(r"[^a-zA-Z0-9_]", "_", os.environ.get("GRAPH_PREFIX", ""))


def safe_name(part: str) -> str:
    return _SAFE.sub("_", part)[:48]


def live_graph(tenant: str) -> str:
    return f"{PREFIX}{safe_name(tenant)}_live"


def memory_graph(tenant: str) -> str:
    return f"{PREFIX}{safe_name(tenant)}_memory"


def sim_prefix(tenant: str) -> str:
    return f"{PREFIX}{safe_name(tenant)}_sim_"


def sim_graph(tenant: str, run_id: str, hyp_id: str) -> str:
    return f"{sim_prefix(tenant)}{safe_name(run_id)}_{safe_name(hyp_id)}"


def _py(value: Any) -> Any:
    """Convert FalkorDB result objects into plain Python values."""
    if isinstance(value, list):
        return [_py(v) for v in value]
    if isinstance(value, dict):
        return {k: _py(v) for k, v in value.items()}
    cls = type(value).__name__
    if cls == "Node":
        out = dict(getattr(value, "properties", {}) or {})
        out["_labels"] = list(getattr(value, "labels", []) or [])
        return out
    if cls == "Edge":
        out = dict(getattr(value, "properties", {}) or {})
        out["_type"] = getattr(value, "relation", None)
        return out
    if cls == "Path":
        return {"nodes": [_py(n) for n in value.nodes()], "edges": [_py(e) for e in value.edges()]}
    return value


def _one_line(cypher: str) -> str:
    return " ".join(line.strip() for line in cypher.strip().splitlines() if line.strip())


class GraphDB:
    def __init__(self, url: str, hub: EventHub) -> None:
        self.url = url
        self.hub = hub
        last_exc: Exception | None = None
        for attempt in range(1, 16):
            try:
                self.client = FalkorDB.from_url(url)
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(1)
        else:
            raise RuntimeError(f"Could not connect to FalkorDB at {url}: {last_exc}")
        self._copy_lock = asyncio.Lock()

    async def ping(self) -> bool:
        try:
            await self.client.list_graphs()
            return True
        except Exception:
            return False

    async def list_graphs(self) -> list[str]:
        names = await self.client.list_graphs()
        return [n.decode() if isinstance(n, bytes) else n for n in names]

    async def exists(self, name: str) -> bool:
        return name in await self.list_graphs()

    async def query(
        self,
        graph: str,
        cypher: str,
        params: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        agent: str | None = None,
        tool: str | None = None,
        label: str | None = None,
        emit: bool = True,
        read_only: bool = False,
    ) -> Rows:
        g = self.client.select_graph(graph)
        start = time.perf_counter()
        try:
            result = await (g.ro_query(cypher, params or {}) if read_only else g.query(cypher, params or {}))
        except Exception as exc:  # record the failed query, then re-raise
            ms = round((time.perf_counter() - start) * 1000, 2)
            if emit:
                self.hub.emit(run_id, "error", agent=agent, tool=tool, graph=graph, label=label,
                              cypher=_one_line(cypher), params=params or {}, ms=ms, summary=str(exc))
            raise
        ms = round((time.perf_counter() - start) * 1000, 2)
        header = [h[1] if isinstance(h, (list, tuple)) else h for h in (result.header or [])]
        header = [h.decode() if isinstance(h, bytes) else h for h in header]
        rows = [dict(zip(header, (_py(v) for v in row))) for row in (result.result_set or [])]
        if emit and run_id is not None:
            self.hub.emit(run_id, "cypher", agent=agent, tool=tool, graph=graph, label=label,
                          cypher=_one_line(cypher), params=params or {}, ms=ms, rows=len(rows))
        return rows

    async def copy(self, src: str, dst: str, *, run_id: str | None = None, agent: str | None = None,
                   tool: str | None = None) -> float:
        """GRAPH.COPY forks the FalkorDB server process, and Redis allows one fork at a time, so concurrent
        copies fail with 'could not fork'. Copies are queued in this process and retried across processes.
        The reported time is the copy itself, not the wait."""
        last_error: Exception | None = None
        for attempt in range(8):
            async with self._copy_lock:
                start = time.perf_counter()
                try:
                    await self.client.select_graph(src).copy(dst)
                    ms = round((time.perf_counter() - start) * 1000, 2)
                    break
                except Exception as exc:
                    if "fork" not in str(exc).lower():
                        raise
                    last_error = exc
            await asyncio.sleep(0.05 * (attempt + 1))
        else:
            raise RuntimeError(f"GRAPH.COPY kept failing: {last_error}")
        if run_id is not None:
            self.hub.emit(run_id, "cypher", agent=agent, tool=tool, graph=src,
                          cypher=f"GRAPH.COPY {src} {dst}", params={}, ms=ms, rows=0)
        return ms

    async def delete(self, name: str, *, run_id: str | None = None, agent: str | None = None,
                     tool: str | None = None) -> bool:
        try:
            await self.client.select_graph(name).delete()
        except Exception:
            return False
        if run_id is not None:
            self.hub.emit(run_id, "cypher", agent=agent, tool=tool, graph=name,
                          cypher=f"GRAPH.DELETE {name}", params={}, ms=0, rows=0)
        return True

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            pass
