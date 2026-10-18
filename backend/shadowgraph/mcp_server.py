"""ShadowGraph MCP server: tools over the company graph, the shadow graphs, agent memory and the
Home Assistant workspace (ha_* tools, see shadowgraph/ha/mcp_tools.py).

Used three ways: in-process by the agents, over streamable HTTP at /mcp (mounted by the API),
and over stdio for Cursor / Claude Desktop:  python -m shadowgraph.mcp_server
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .core import brain, comment, github, memory, sim, verdict
from .events import EXTERNAL_RUN
from .runtime import Runtime, get_runtime

log = logging.getLogger("shadowgraph.mcp")

server = MCPServer(
    name="shadowgraph",
    title="ShadowGraph",
    version="1.0.0",
    instructions=(
        "ShadowGraph checks what a code change would break before it merges. The company is a FalkorDB graph "
        "of services, endpoints, PRs, decision records, chat threads and engineers. Typical use: call "
        "simulate_change with a pr_number (or an endpoint_id like 'auth-service:/v1/token-verify') to get a "
        "BLOCK / WARN / PASS verdict with proof. brain_* tools read the company graph, sim_* tools work on "
        "short-lived copies of it, memory_* tools read and write agent memory. tenant defaults to 'acme'. "
        "For the real Home Assistant codebase (home-assistant/core and its HACS custom integrations) use the "
        "ha_* tools: before deleting or renaming a core name call ha_can_remove; before committing a change call "
        "ha_check_edits with the new file contents; ha_who_uses lists every user with file:line. All read-only."
    ),
)

Fn = Callable[[Runtime, str], Awaitable[dict[str, Any]]]


async def _call(name: str, tenant: str, run_id: str, agent: str, args: dict[str, Any], fn: Fn) -> dict[str, Any]:
    rt = get_runtime()
    await rt.ensure_ready()
    if tenant not in rt.settings.tenants:
        raise ToolError(f"unknown tenant '{tenant}'; known: {', '.join(rt.settings.tenants)}")
    emit_run = run_id or EXTERNAL_RUN
    rt.hub.emit(emit_run, "tool_call", agent=agent or "external", tool=name, params=args)
    try:
        return await fn(rt, emit_run)
    except ToolError:
        raise
    except (brain.NotFound, sim.SimError, ValueError, PermissionError, LookupError) as exc:
        rt.hub.emit(emit_run, "error", agent=agent, tool=name, summary=str(exc))
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # unexpected: log it, but give the caller a readable message
        log.exception("tool %s failed", name)
        rt.hub.emit(emit_run, "error", agent=agent, tool=name, summary=f"{type(exc).__name__}: {exc}")
        raise ToolError(f"{name} failed: {type(exc).__name__}: {exc}") from exc


# ------------------------------------------------------------------ brain_* (company graph, read-only)

@server.tool(description="Trace the decision records behind a PR: the decision it cites, the decision now in force "
                         "(following SUPERSEDED_BY), any decision that forbids the change, the chat thread and author.")
async def brain_trace_decision(pr_number: int, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("brain_trace_decision", tenant, run_id, agent, {"pr_number": pr_number},
                       lambda rt, er: brain.trace_decision(rt, tenant, pr_number, agent=agent, emit_run=er))


@server.tool(description="Semantic search over decision records that are still in force (replaced ones are "
                         "filtered out). Returns the 5 closest with sources.")
async def brain_search_decisions(question: str, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("brain_search_decisions", tenant, run_id, agent, {"question": question},
                       lambda rt, er: brain.search_decisions(rt, tenant, question, agent=agent, emit_run=er))


@server.tool(description="Rank the engineers to ask about a service: PageRank over who defers to whom, "
                         "multiplied by commits to that service.")
async def brain_find_expert(service_id: str, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("brain_find_expert", tenant, run_id, agent, {"service_id": service_id},
                       lambda rt, er: brain.find_expert(rt, tenant, service_id, agent=agent, emit_run=er))


# ------------------------------------------------------------------ sim_* (shadow graphs)

@server.tool(description="Copy the live company graph into a new shadow graph with GRAPH.COPY. Returns the shadow "
                         "graph's name and the copy time. Always call sim_drop when done.")
async def sim_fork(hyp_id: str = "adhoc", tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    run_key = run_id or f"ext{uuid.uuid4().hex[:6]}"
    return await _call("sim_fork", tenant, run_id, agent, {"hyp_id": hyp_id},
                       lambda rt, er: sim.fork(rt, tenant, run_key, hyp_id, agent=agent, emit_run=er))


@server.tool(description="Apply a change inside a shadow graph. change_type 'remove_endpoint' needs endpoint_id "
                         "(e.g. 'auth-service:/v1/token-verify'); 'isolate_service' needs service_id.")
async def sim_apply_change(shadow: str, change_type: str = "remove_endpoint", endpoint_id: str = "",
                           service_id: str = "", tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    change = {"type": change_type, "endpoint_id": endpoint_id or None, "service_id": service_id or None}
    return await _call("sim_apply_change", tenant, run_id, agent, {"shadow": shadow, **change},
                       lambda rt, er: sim.apply_change(rt, tenant, shadow, change, agent=agent, emit_run=er))


@server.tool(description="Measure a shadow graph against the live graph: connected components (algo.WCC), new "
                         "islands, betweenness changes (algo.betweenness), direct dependents and cascade chains.")
async def sim_measure(shadow: str, endpoint_id: str = "", service_id: str = "", tenant: str = "acme",
                      run_id: str = "", agent: str = "") -> dict:
    return await _call("sim_measure", tenant, run_id, agent,
                       {"shadow": shadow, "endpoint_id": endpoint_id, "service_id": service_id},
                       lambda rt, er: sim.measure(rt, tenant, shadow, endpoint_id=endpoint_id or None,
                                                  service_id=service_id or None, agent=agent, emit_run=er))


@server.tool(description="Delete a shadow graph. Refuses anything that is not a shadow graph.")
async def sim_drop(shadow: str, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("sim_drop", tenant, run_id, agent, {"shadow": shadow},
                       lambda rt, er: sim.drop(rt, tenant, shadow, agent=agent, emit_run=er))


async def _simulate(rt: Runtime, er: str, tenant: str, pr_number: int, endpoint_id: str, agent: str) -> dict:
    if pr_number:
        pr = await brain.pr_context(rt, tenant, pr_number, agent=agent, emit_run=er, tool="simulate_change")
        if not pr["removals"]:
            raise ValueError(f"PR #{pr_number} removes nothing that ShadowGraph can simulate")
        endpoint_id = pr["removals"][0]["target"]
    if not endpoint_id:
        raise ValueError("give pr_number or endpoint_id")
    run_key = f"ext{uuid.uuid4().hex[:6]}"
    start = time.perf_counter()
    forked = await sim.fork(rt, tenant, run_key, "oneshot", agent=agent, emit_run=er)
    try:
        await sim.apply_change(rt, tenant, forked["shadow"], {"type": "remove_endpoint", "endpoint_id": endpoint_id},
                               agent=agent, emit_run=er)
        measured = await sim.measure(rt, tenant, forked["shadow"], endpoint_id=endpoint_id, agent=agent, emit_run=er)
    finally:
        await sim.drop(rt, tenant, forked["shadow"], agent=agent, emit_run=er)
    decision = await brain.trace_decision(rt, tenant, pr_number, agent=agent, emit_run=er) if pr_number else None
    expert = await brain.find_expert(rt, tenant, endpoint_id.split(":", 1)[0], agent=agent, emit_run=er)
    v = verdict.decide(measured, decision)
    return {"verdict": v["verdict"], "headline": v["headline"], "reasons": v["reasons"], "endpoint_id": endpoint_id,
            "copy_ms": forked["copy_ms"], "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "measure": measured, "decisions_in_force": (decision or {}).get("decisions_in_force", []),
            "experts": expert["experts"]}


@server.tool(description="One call: simulate a PR (or removing one endpoint) on a copy of the company graph and "
                         "return BLOCK / WARN / PASS with the islands, cascade chains, decisions and expert.")
async def simulate_change(pr_number: int = 0, endpoint_id: str = "", tenant: str = "acme", run_id: str = "",
                          agent: str = "") -> dict:
    return await _call("simulate_change", tenant, run_id, agent, {"pr_number": pr_number, "endpoint_id": endpoint_id},
                       lambda rt, er: _simulate(rt, er, tenant, pr_number, endpoint_id, agent or "external"))


# ------------------------------------------------------------------ memory_* (agent memory graph)

async def _open(rt: Runtime, er: str, tenant: str, pr_number: int, agent: str) -> dict:
    pr = await brain.pr_context(rt, tenant, pr_number, agent=agent, emit_run=er, tool="memory_open_episode")
    opened = await memory.open_episode(rt, tenant, pr, er, agent=agent, emit_run=er)
    return {**opened, "pr": pr}


@server.tool(description="Open an episode (episodic memory) for a PR. Returns episode_id, the PR context and the "
                         "change target.")
async def memory_open_episode(pr_number: int, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("memory_open_episode", tenant, run_id, agent, {"pr_number": pr_number},
                       lambda rt, er: _open(rt, er, tenant, pr_number, agent))


@server.tool(description="Post hypotheses for an episode so agents can claim them. checks: any of partition, "
                         "chokepoint, entry_path, decision, expert. target_id like 'auth-service:/v1/token-verify'.")
async def memory_post_hypotheses(episode_id: str, checks: list[str], target_id: str, tenant: str = "acme",
                                 run_id: str = "", agent: str = "") -> dict:
    provider, _, route = target_id.partition(":")
    target = {"target": target_id, "provider": provider, "route": route or None}
    return await _call("memory_post_hypotheses", tenant, run_id, agent,
                       {"episode_id": episode_id, "checks": checks, "target_id": target_id},
                       lambda rt, er: memory.post_hypotheses(rt, tenant, episode_id, checks, target, agent=agent,
                                                             emit_run=er))


@server.tool(description="Atomically claim a hypothesis (the lock). Tries `prefer` first; if another agent holds it, "
                         "reports the conflict and claims the best open hypothesis of `kind` (sim or brain). "
                         "claimed is null when nothing is left.")
async def memory_claim(episode_id: str, kind: str, prefer: str = "", tenant: str = "acme", run_id: str = "",
                       agent: str = "") -> dict:
    return await _call("memory_claim", tenant, run_id, agent, {"episode_id": episode_id, "kind": kind, "prefer": prefer},
                       lambda rt, er: memory.claim(rt, tenant, episode_id, agent or "external", kind, prefer or None,
                                                   emit_run=er))


@server.tool(description="Resolve a hypothesis you hold as CONFIRMED or REJECTED, with a result object.")
async def memory_resolve(hyp_id: str, status: str, result: dict, tenant: str = "acme", run_id: str = "",
                         agent: str = "") -> dict:
    return await _call("memory_resolve", tenant, run_id, agent, {"hyp_id": hyp_id, "status": status},
                       lambda rt, er: memory.resolve(rt, tenant, hyp_id, agent or "external", status, result,
                                                     emit_run=er))


@server.tool(description="Record a fact (semantic memory). If a still-valid fact exists for the same entity and "
                         "attribute, the new one overrides it. if_absent=true only records when none exists.")
async def memory_record_fact(entity: str, attribute: str, value: bool | int | float | str, source: str,
                             episode_id: str = "", if_absent: bool = False, tenant: str = "acme", run_id: str = "",
                             agent: str = "") -> dict:
    return await _call("memory_record_fact", tenant, run_id, agent,
                       {"entity": entity, "attribute": attribute, "value": value, "source": source},
                       lambda rt, er: memory.record_fact(rt, tenant, entity, attribute, value, source,
                                                         episode_id=episode_id or None, agent=agent,
                                                         if_absent=if_absent, emit_run=er))


@server.tool(description="Find a playbook (procedural memory) from the most similar past episode. Returns null when "
                         "nothing is close enough.")
async def memory_recall_playbook(text: str, exclude_episode: str = "", tenant: str = "acme", run_id: str = "",
                                 agent: str = "") -> dict:
    return await _call("memory_recall_playbook", tenant, run_id, agent, {"text": text},
                       lambda rt, er: memory.recall_playbook(rt, tenant, text, exclude_episode=exclude_episode,
                                                             agent=agent, emit_run=er))


@server.tool(description="Save the steps that decided an episode as a playbook, or (reuse_of=playbook id) record "
                         "that a playbook was reused and update its success rate.")
async def memory_save_playbook(episode_id: str, name: str = "", pattern: str = "", decisive: list[str] | None = None,
                               steps: list[dict] | None = None, reuse_of: str = "", ok: bool = True,
                               tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("memory_save_playbook", tenant, run_id, agent, {"episode_id": episode_id, "reuse_of": reuse_of},
                       lambda rt, er: memory.save_playbook(rt, tenant, episode_id, name=name, pattern=pattern,
                                                           decisive=decisive, steps=steps, reuse_of=reuse_of or None,
                                                           ok=ok, agent=agent, emit_run=er))


@server.tool(description="Recall earlier chat messages and closed episodes similar to a question "
                         "(e.g. 'did we see this before?').")
async def memory_recall_conversation(question: str, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("memory_recall_conversation", tenant, run_id, agent, {"question": question},
                       lambda rt, er: memory.recall_conversation(rt, tenant, question, agent=agent, emit_run=er))


# ------------------------------------------------------------------ action_*

async def _post(rt: Runtime, er: str, tenant: str, episode_id: str, pr_number: int, report: dict, summary: str,
                steps: int, ms: float, agent: str) -> dict:
    body = comment.render(report, summary or None)
    v = report["verdict"]
    target_url = f"{rt.settings.public_url.rstrip('/')}/?tenant={tenant}&run={report.get('run_id', '')}"
    posted = (await github.post_verdict(rt.settings, pr_number, v["verdict"], v["headline"], body, target_url)
              if tenant == rt.settings.webhook_tenant else
              {"posted": False, "reason": f"GitHub posting is set up for tenant '{rt.settings.webhook_tenant}' only"})
    await memory.close_episode(rt, tenant, episode_id, v["verdict"], v["headline"], steps, ms, agent=agent, emit_run=er)
    rt.hub.emit(er, "github_posted" if posted.get("posted") else "github_skipped", agent=agent,
                tool="action_post_verdict",
                summary=(f"Posted {v['verdict']} to PR #{pr_number} ({posted.get('status', 'no status')})"
                         if posted.get("posted") else f"Not posted to GitHub: {posted.get('reason')}"),
                url=posted.get("comment_url"))
    return {"github": posted, "comment": body}


@server.tool(description="Publish a verdict: render the PR comment, post it to GitHub (comment + commit status) "
                         "when configured, and close the episode with the verdict.")
async def action_post_verdict(episode_id: str, pr_number: int, report: dict, summary: str = "", steps: int = 0,
                              ms: float = 0.0, tenant: str = "acme", run_id: str = "", agent: str = "") -> dict:
    return await _call("action_post_verdict", tenant, run_id, agent,
                       {"episode_id": episode_id, "pr_number": pr_number, "verdict": report.get("verdict", {}).get("verdict")},
                       lambda rt, er: _post(rt, er, tenant, episode_id, pr_number, report, summary, steps, ms, agent))


def main() -> None:
    logging.basicConfig(level=logging.WARNING)  # stdout carries the MCP protocol; logs go to stderr
    server.run("stdio")


if __name__ == "__main__":
    main()


# Home Assistant workspace tools (registered on the same server)
from .ha import mcp_tools as _ha_tools  # noqa: E402,F401
