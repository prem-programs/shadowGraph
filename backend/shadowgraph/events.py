"""In-process event hub: every agent step becomes an event that the dashboard streams."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, AsyncIterator

TERMINAL_TYPES = {"run_finished"}
EXTERNAL_RUN = "external"  # tool calls from Cursor / Claude Desktop land here


def _clean(value: Any, depth: int = 0) -> Any:
    """Make params JSON-friendly and short: vectors become a short note."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: _clean(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 32 and all(isinstance(x, (int, float)) for x in value):
            return f"[{len(value)} floats]"
        return [_clean(v, depth + 1) for v in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 600:
            return value[:600] + "…"
        return value
    return str(value)


class EventHub:
    def __init__(self, keep_per_run: int = 2000) -> None:
        self._events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._seq: dict[str, int] = defaultdict(int)
        self._keep = keep_per_run

    def emit(self, run_id: str | None, type: str, **fields: Any) -> dict[str, Any]:
        run = run_id or EXTERNAL_RUN
        self._seq[run] += 1
        event = {"seq": self._seq[run], "ts": int(time.time() * 1000), "run_id": run, "type": type}
        for key, value in fields.items():
            if value is not None:
                event[key] = _clean(value)
        log = self._events[run]
        log.append(event)
        if len(log) > self._keep:
            del log[: len(log) - self._keep]
        for queue in list(self._subscribers[run]):
            queue.put_nowait(event)
        return event

    def forget(self, run_id: str) -> None:
        if not self._subscribers.get(run_id):
            self._events.pop(run_id, None)
            self._seq.pop(run_id, None)
            self._subscribers.pop(run_id, None)

    def history(self, run_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(run_id, []))

    def finished(self, run_id: str) -> bool:
        return any(e["type"] in TERMINAL_TYPES for e in self._events.get(run_id, []))

    async def subscribe(self, run_id: str, heartbeat: float = 15.0) -> AsyncIterator[dict[str, Any] | None]:
        """Yield past events, then live ones, until the run finishes. None = heartbeat."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            seen = 0
            for event in self.history(run_id):
                seen = event["seq"]
                yield event
                if event["type"] in TERMINAL_TYPES:
                    return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if event["seq"] <= seen:
                    continue
                seen = event["seq"]
                yield event
                if event["type"] in TERMINAL_TYPES:
                    return
        finally:
            self._subscribers[run_id].discard(queue)
