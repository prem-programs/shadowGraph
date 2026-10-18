"""Builds the PR comment. Facts come from the graph; an LLM may only add the opening summary."""
from __future__ import annotations

from typing import Any

MARKER = "<!-- shadowgraph:verdict -->"
ICON = {"BLOCK": "🛑", "WARN": "⚠️", "PASS": "✅"}


def template_summary(report: dict[str, Any]) -> str:
    v = report["verdict"]
    pr = report["pr"]
    target = report.get("target", {}).get("route") or "this change"
    if v["verdict"] == "BLOCK":
        return (f"Merging this PR would break production. ShadowGraph removed `{target}` in a copy of the "
                f"company graph and measured the result: {v['headline']}.")
    if v["verdict"] == "WARN":
        return f"This PR is probably safe but carries risk: {v['headline']}."
    return f"ShadowGraph removed `{target}` in a copy of the company graph and found nothing that breaks."


def render(report: dict[str, Any], summary: str | None = None) -> str:
    v = report["verdict"]
    lines = [MARKER, f"## {ICON.get(v['verdict'], '')} ShadowGraph: **{v['verdict']}**", "",
             summary or template_summary(report), ""]
    m = report.get("measure") or {}
    if m:
        lines += ["### Blast radius (simulated on a copy of the live graph)", "",
                  "| Check | Live | After this PR |", "| --- | --- | --- |",
                  f"| Connected groups of services (WCC) | {m.get('components_live')} | {m.get('components_shadow')} |"]
        for island in m.get("islands", []):
            lines.append(f"| Cut off (best tier {island['best_tier']}) | connected | {', '.join(island['ids'])} |")
        if m.get("entries_cut_off"):
            lines.append(f"| Tier 0 entry points cut off from `{m.get('provider')}` | 0 | "
                         f"{', '.join(m['entries_cut_off'])} |")
        lines.append("")
        for c in m.get("cascade", [])[:3]:
            lines.append(f"- Cascade: {' → '.join(c['chain'])}")
        if m.get("direct_dependents"):
            lines.append("- Direct callers of the removed endpoint: "
                         + ", ".join(f"{d['service']} (tier {d['tier']})" for d in m["direct_dependents"]))
        lines.append("")
    d = report.get("decision") or {}
    if d.get("decisions_in_force"):
        lines += ["### Decisions", ""]
        for item in d["decisions_in_force"]:
            chain = " → ".join(item["chain"]) if item.get("chain") else item["adr"]
            cited = (f"The PR cites **{item['cited']}**, which was replaced: {chain}. "
                     if item.get("cited") and item["cited"] != item["adr"] else "")
            link = f" ([discussion]({item['discussion']}))" if item.get("discussion") else ""
            lines.append(f"- {cited}In force: **{item['adr']}** {item['title']}{link}")
        lines.append("")
    experts = (report.get("expert") or {}).get("experts") or []
    if experts:
        top = experts[0]
        lines += [f"### Reviewer", "", f"Ask **@{top['engineer']}** ({top['name']}, {top['channel']}): "
                  f"highest PageRank authority × commits to `{report['expert']['service_id']}`.", ""]
    stats = report.get("stats") or {}
    lines += ["<details><summary>Proof: queries and timings</summary>", "",
              f"- Shadow copies (GRAPH.COPY): {stats.get('forks', 0)}, fastest {stats.get('copy_ms', '?')} ms",
              f"- Agent steps: {stats.get('steps', '?')}; total {stats.get('ms', '?')} ms",
              f"- Hypotheses: " + ", ".join(f"{h['label']} {h['check']} {h['status'].lower()}"
                                             for h in report.get("hypotheses", [])),
              "", "</details>", "", f"<sub>Run `{report.get('run_id')}` · tenant `{report.get('tenant')}`</sub>"]
    return "\n".join(lines)
