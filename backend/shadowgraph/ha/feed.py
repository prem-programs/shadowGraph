"""Live feed: watch open pull requests on home-assistant/core and check each new or updated one.

Read-only. Every check is stored as a Change node; changes that remove or deprecate something people use
are handed to the agent team for a full run (one at a time). Nothing is posted to GitHub.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

from . import service

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger("shadowgraph.ha.feed")
ESCALATE = {"breaks", "retires", "review"}


async def poll_once(rt: "Runtime", batch: int | None = None, escalate: bool = True) -> dict[str, Any]:
    st = service.state(rt)
    feed = st.feed
    batch = batch or rt.settings.ha_feed_batch
    src = service.get_source(rt)
    checked: list[dict[str, Any]] = []
    try:
        prs = await src.list_open(limit=max(batch * 3, 30))
        feed["open_seen"] = len(prs)
        todo = [p for p in prs if feed["seen"].get(p["number"]) != (p.get("merge_sha") or p.get("head_sha"))][:batch]
        for p in todo:
            run = f"feed-{p['number']}"
            try:
                r = await service.analyze_pr(rt, p["number"], run=run, agent="feed", save=True)
                feed["seen"][p["number"]] = p.get("merge_sha") or p.get("head_sha")
                checked.append({"number": p["number"], "level": r["verdict"]["level"],
                                "headline": r["verdict"]["headline"]})
                if escalate and r["verdict"]["level"] in ESCALATE:
                    feed["queue"].append(p["number"])
            except Exception as exc:  # one bad PR must not stop the feed
                log.warning("feed: PR #%s failed: %s", p["number"], exc)
                checked.append({"number": p["number"], "error": str(exc).split("\n")[0]})
                feed["seen"][p["number"]] = p.get("merge_sha") or p.get("head_sha")
            finally:
                rt.hub.forget(run)
        feed["last_error"] = None
    except Exception as exc:
        feed["last_error"] = str(exc).split("\n")[0]
        log.warning("feed poll failed: %s", exc)
    feed["last_poll"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    feed["polls"] += 1
    feed["last_checked"] = checked
    await _drain(rt)
    return {"checked": checked, "error": feed["last_error"], "source": src.name}


async def _drain(rt: "Runtime") -> None:
    """Run the agent team on escalated PRs, one at a time (shadow copies fork the database process)."""
    from .agents import start_change_run

    feed = service.state(rt).feed
    if feed.get("running") or not feed["queue"]:
        return
    number = feed["queue"].pop(0)
    record = start_change_run(rt, number)
    feed["running"] = record.run_id

    async def wait() -> None:
        try:
            if record.task:
                await record.task
        finally:
            feed["running"] = None
            await _drain(rt)

    asyncio.create_task(wait())


async def loop(rt: "Runtime") -> None:
    st = service.state(rt)
    st.feed["enabled"] = True
    log.info("HA feed on: every %ss, %s PRs per poll", rt.settings.ha_feed_interval, rt.settings.ha_feed_batch)
    while True:
        try:
            await rt.ensure_ready()
            if st.loaded:
                await poll_once(rt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            st.feed["last_error"] = str(exc)
            log.warning("feed loop: %s", exc)
        await asyncio.sleep(rt.settings.ha_feed_interval)
