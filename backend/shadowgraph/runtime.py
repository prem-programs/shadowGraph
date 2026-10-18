"""One shared runtime per process: settings, event hub, FalkorDB client, embedder, run registry."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings, settings as default_settings
from .db import GraphDB
from .embed import Embedder
from .events import EventHub

log = logging.getLogger("shadowgraph")


@dataclass
class RunRecord:
    run_id: str
    tenant: str
    pr_number: int
    mode: str
    status: str = "running"  # running | done | error
    started_at: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "tenant": self.tenant, "pr_number": self.pr_number, "mode": self.mode,
            "status": self.status, "started_at": self.started_at, "result": self.result, "error": self.error,
        }


class Runtime:
    def __init__(self, cfg: Settings | None = None, embedder: Embedder | None = None) -> None:
        self.settings = cfg or default_settings
        self.hub = EventHub()
        self.db = GraphDB(self.settings.falkordb_url, self.hub)
        self._embedder = embedder
        self.runs: dict[str, RunRecord] = {}
        self._ready = False
        self._checked_at = 0.0
        self._lock = asyncio.Lock()
        self.cleanup_shadows = False  # the API server turns this on at startup
        self.recheck_seconds = 30.0

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(self.settings.embed_backend, self.settings.embed_model)
            log.info("embedder: %s", self._embedder.name)
        return self._embedder

    async def ensure_ready(self) -> None:
        """Connect and seed any tenant graph that is missing or stale. Safe to call often: after the first
        call it re-checks at most every `recheck_seconds` (e.g. FalkorDB restarted without its data volume)."""
        if self._ready and time.monotonic() - self._checked_at < self.recheck_seconds:
            return
        async with self._lock:
            if self._ready and time.monotonic() - self._checked_at < self.recheck_seconds:
                return
            from .graph.seed import ensure_seeded

            first = not self._ready
            await ensure_seeded(self, drop_shadows=first and self.cleanup_shadows)
            self._ready = True
            self._checked_at = time.monotonic()

    def remember_run(self, record: "RunRecord", keep: int = 200) -> None:
        self.runs[record.run_id] = record
        if len(self.runs) > keep:
            for old in sorted(self.runs.values(), key=lambda r: r.started_at)[: len(self.runs) - keep]:
                if old.status != "running":
                    self.runs.pop(old.run_id, None)
                    self.hub.forget(old.run_id)

    def mark_stale(self) -> None:
        self._ready = False


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime


def set_runtime(rt: Runtime) -> None:
    global _runtime
    _runtime = rt
