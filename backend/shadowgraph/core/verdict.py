"""Verdict rules. The graph decides; nothing here reads model output."""
from __future__ import annotations

from typing import Any


def decide(measure: dict[str, Any] | None, decision: dict[str, Any] | None) -> dict[str, Any]:
    """BLOCK: a new island holds a tier 0/1 service, or a decision in force forbids the removal.
    WARN: islands of tier 2/3 services only, or a chokepoint's betweenness rises 50%+ into the top 3.
    PASS: neither."""
    reasons: list[dict[str, Any]] = []
    level = "PASS"

    if measure:
        for island in measure.get("blocking_islands", []):
            reasons.append({"level": "BLOCK", "check": "partition",
                            "text": f"Cuts off {len(island['ids'])} services ({', '.join(island['ids'])}); "
                                    f"best tier {island['best_tier']}"})
        minor = [c for c in measure.get("islands", []) if c not in measure.get("blocking_islands", [])]
        for island in minor:
            reasons.append({"level": "WARN", "check": "partition",
                            "text": f"Cuts off low-tier services ({', '.join(island['ids'])})"})
        for c in measure.get("rising_chokepoints", []):
            reasons.append({"level": "WARN", "check": "chokepoint",
                            "text": f"{c['service']} betweenness rises {c['pct']}% ({c['live']} to {c['shadow']})"})
    if decision:
        for d in decision.get("blockers", []):
            reasons.append({"level": "BLOCK", "check": "decision",
                            "text": f"{d['adr']} is in force and forbids this removal: {d['summary']}"})

    if any(r["level"] == "BLOCK" for r in reasons):
        level = "BLOCK"
    elif any(r["level"] == "WARN" for r in reasons):
        level = "WARN"

    if level == "BLOCK":
        parts = []
        if measure and measure.get("blocking_islands"):
            n = len(measure["islands"])
            parts.append(f"{n} island{'s' if n != 1 else ''}, one with a tier "
                         f"{min(c['best_tier'] for c in measure['blocking_islands'])} service")
        if decision and decision.get("blockers"):
            parts.append(f"{decision['blockers'][0]['adr']} forbids it")
        headline = "; ".join(parts)
    elif level == "WARN":
        headline = reasons[0]["text"]
    else:
        headline = "No partition, no chokepoint jump, no decision in force against it"
    return {"verdict": level, "headline": headline, "reasons": reasons}
