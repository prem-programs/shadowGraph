"""Counterfactual simulation: fork the live graph, apply a change to the copy, measure what breaks."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import live_graph, sim_graph, sim_prefix
from ..graph import queries as Q

if TYPE_CHECKING:
    from ..runtime import Runtime


class SimError(ValueError):
    pass


def _check_shadow(tenant: str, shadow: str) -> None:
    if not shadow.startswith(sim_prefix(tenant)):
        raise SimError(f"'{shadow}' is not a shadow graph of tenant '{tenant}'; refusing to touch it")


async def fork(rt: "Runtime", tenant: str, run_id: str, hyp_id: str, *, agent: str | None = None,
               emit_run: str | None = None) -> dict[str, Any]:
    src = live_graph(tenant)
    if not await rt.db.exists(src):
        raise SimError(f"tenant '{tenant}' has no live graph")
    dst = sim_graph(tenant, run_id, hyp_id)
    await rt.db.delete(dst)  # a leftover from a crashed run
    ms = await rt.db.copy(src, dst, run_id=emit_run, agent=agent, tool="sim_fork")
    rt.hub.emit(emit_run, "fork_created", agent=agent, tool="sim_fork", graph=dst, ms=ms,
                summary=f"Copied {src} into {dst} in {ms} ms")
    return {"shadow": dst, "source": src, "copy_ms": ms}


async def apply_change(rt: "Runtime", tenant: str, shadow: str, change: dict[str, Any], *,
                       agent: str | None = None, emit_run: str | None = None) -> dict[str, Any]:
    _check_shadow(tenant, shadow)
    kind = change.get("type", "remove_endpoint")
    kw = {"run_id": emit_run, "agent": agent, "tool": "sim_apply_change"}
    if kind == "remove_endpoint":
        endpoint_id = change.get("endpoint_id")
        if not endpoint_id:
            raise SimError("remove_endpoint needs endpoint_id")
        removed = await rt.db.query(shadow, Q.CALLS_VIA, {"endpoint_id": endpoint_id}, **kw, read_only=True)
        await rt.db.query(shadow, Q.Q3_DELETE_CALLS_VIA, {"endpoint_id": endpoint_id}, **kw)
        ep = await rt.db.query(shadow, Q.Q3_DELETE_ENDPOINT, {"endpoint_id": endpoint_id}, **kw)
        result = {"type": kind, "endpoint_id": endpoint_id, "removed_calls": removed,
                  "removed_endpoints": ep[0]["removed_endpoints"] if ep else 0}
    elif kind == "isolate_service":
        service_id = change.get("service_id")
        if not service_id:
            raise SimError("isolate_service needs service_id")
        removed = await rt.db.query(shadow, Q.CALLS_TOUCHING, {"service_id": service_id}, **kw, read_only=True)
        await rt.db.query(shadow, Q.Q3_ISOLATE_SERVICE, {"service_id": service_id}, **kw)
        result = {"type": kind, "service_id": service_id, "removed_calls": removed, "removed_endpoints": 0}
    else:
        raise SimError(f"unknown change type '{kind}' (use remove_endpoint or isolate_service)")
    rt.hub.emit(emit_run, "change_applied", agent=agent, tool="sim_apply_change", graph=shadow,
                summary=f"Removed {len(result['removed_calls'])} calls in {shadow}",
                edges=[[c["source"], c["target"]] for c in result["removed_calls"]])
    return result


def _components(rows: list[dict]) -> list[dict]:
    return [{"id": r["componentId"], "size": r["services"], "ids": sorted(r["ids"]), "best_tier": r["best_tier"]}
            for r in rows]


async def measure(rt: "Runtime", tenant: str, shadow: str, *, endpoint_id: str | None = None,
                  service_id: str | None = None, agent: str | None = None,
                  emit_run: str | None = None) -> dict[str, Any]:
    """Compare the live graph with the shadow: partitions, chokepoints, broken dependents, cascade."""
    _check_shadow(tenant, shadow)
    live = live_graph(tenant)
    kw = {"run_id": emit_run, "agent": agent, "tool": "sim_measure", "read_only": True}

    live_wcc = _components(await rt.db.query(live, Q.Q4_WCC, label="partitions (live)", **kw))
    sim_wcc = _components(await rt.db.query(shadow, Q.Q4_WCC, label="partitions (shadow)", **kw))
    tiers = {r["service"]: r["tier"] for r in await rt.db.query(live, Q.SERVICE_TIERS, emit=False, read_only=True)}
    live_scores = dict.fromkeys(tiers, 0.0)
    live_scores.update({r["service"]: float(r["score"]) for r in
                        await rt.db.query(live, Q.Q5_BETWEENNESS, label="chokepoints (live)", **kw)})
    sim_scores = dict.fromkeys(tiers, 0.0)
    sim_scores.update({r["service"]: float(r["score"]) for r in
                       await rt.db.query(shadow, Q.Q5_BETWEENNESS, label="chokepoints (shadow)", **kw)})

    provider = None
    direct: list[dict] = []
    cascade: list[dict] = []
    if endpoint_id:
        provider = endpoint_id.split(":", 1)[0]
        direct = await rt.db.query(live, Q.Q6_DIRECT_DEPENDENTS, {"endpoint_id": endpoint_id},
                                   label="direct dependents (live)", **kw)
        cascade = await rt.db.query(live, Q.Q6_CASCADE, {"endpoint_id": endpoint_id},
                                    label="cascade chains (live)", **kw)
        for row in cascade:
            row["chain"] = list(row["chain"]) + [provider]
    elif service_id:
        provider = service_id

    # Islands = shadow components that did not exist in the live graph and do not hold the provider.
    live_sets = {frozenset(c["ids"]) for c in live_wcc}
    anchor = provider if provider else (sim_wcc[0]["ids"][0] if sim_wcc and sim_wcc[0]["ids"] else None)
    islands = [c for c in sim_wcc if frozenset(c["ids"]) not in live_sets and anchor not in c["ids"]]
    if service_id:  # an isolated service is its own island
        islands = [c for c in sim_wcc if frozenset(c["ids"]) not in live_sets]

    sim_rank = sorted(sim_scores, key=lambda svc: sim_scores[svc], reverse=True)
    changes = []
    for svc in sim_rank:
        before, after = live_scores[svc], sim_scores[svc]
        delta = after - before
        pct = (delta / before * 100.0) if before > 0 else (100.0 if after > 0 else 0.0)
        changes.append({"service": svc, "tier": tiers.get(svc), "live": round(before, 3),
                        "shadow": round(after, 3), "delta": round(delta, 3), "pct": round(pct, 1)})
    changes.sort(key=lambda c: abs(c["delta"]), reverse=True)
    rising = [c for c in changes if c["delta"] > 0 and c["pct"] >= 50 and c["service"] in sim_rank[:3]
              and sim_scores[c["service"]] > 0]

    def component_of(ids: list[dict], svc: str) -> int | None:
        return next((c["id"] for c in ids if svc in c["ids"]), None)

    entries_cut = []
    if provider:
        entries = sorted(svc for svc, tier in tiers.items() if tier == 0)
        for entry in entries:
            if entry == provider:
                continue
            same_live = component_of(live_wcc, entry) == component_of(live_wcc, provider)
            same_sim = component_of(sim_wcc, entry) == component_of(sim_wcc, provider)
            if same_live and not same_sim:
                entries_cut.append(entry)

    result = {
        "shadow": shadow, "provider": provider, "endpoint_id": endpoint_id, "service_id": service_id,
        "components_live": len(live_wcc), "components_shadow": len(sim_wcc),
        "islands": islands, "blocking_islands": [c for c in islands if c["best_tier"] is not None and c["best_tier"] <= 1],
        "betweenness": changes[:8], "rising_chokepoints": rising,
        "direct_dependents": direct, "cascade": cascade, "entries_cut_off": entries_cut,
    }
    island_ids = sorted({s for c in islands for s in c["ids"]})
    rt.hub.emit(emit_run, "measurement", agent=agent, tool="sim_measure", graph=shadow,
                summary=f"{len(sim_wcc)} components in the shadow (live: {len(live_wcc)}); "
                        f"{len(islands)} new island(s)", nodes=island_ids,
                islands=[c["ids"] for c in islands])
    return result


async def drop(rt: "Runtime", tenant: str, shadow: str, *, agent: str | None = None,
               emit_run: str | None = None) -> dict[str, Any]:
    _check_shadow(tenant, shadow)
    ok = await rt.db.delete(shadow, run_id=emit_run, agent=agent, tool="sim_drop")
    return {"shadow": shadow, "deleted": ok}
