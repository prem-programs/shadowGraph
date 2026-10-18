import type { RunEvent } from "./types";

const AGENT_COLORS: Record<string, string> = {
  scout: "var(--agent-scout)",
  scribe: "var(--agent-scribe)",
  judge: "var(--agent-judge)",
  orchestrator: "var(--muted)",
  chat: "var(--agent-chat)",
  external: "var(--agent-external)",
};

export function agentColor(agent?: string): string {
  if (!agent) return "var(--muted)";
  if (agent.startsWith("sim")) return "var(--agent-sim)";
  return AGENT_COLORS[agent] ?? "var(--muted)";
}

/** Events shown as timeline rows by default; query-level events are shown when "Queries" is on. */
export const STEP_TYPES = new Set([
  "run_started", "episode_opened", "playbook_miss", "playbook_reused", "plan", "hypotheses_posted",
  "lock_claimed", "lock_conflict", "fork_created", "change_applied", "measurement", "decision_traced",
  "expert_found", "hypothesis_resolved", "fact_recorded", "verdict", "github_posted", "github_skipped",
  "playbook_saved", "playbook_updated", "llm_fallback", "llm_text", "error", "run_finished",
]);

export function tone(e: RunEvent): "bad" | "warn" | "good" | "accent" | "plain" {
  if (e.type === "error" || (e.type === "run_finished" && e.status === "error")) return "bad";
  if (e.type === "verdict") return e.verdict === "BLOCK" ? "bad" : e.verdict === "WARN" ? "warn" : "good";
  if (e.type === "lock_conflict" || e.type === "llm_fallback") return "warn";
  if (e.type === "playbook_reused" || e.type === "fork_created") return "accent";
  if (e.type === "hypothesis_resolved") return e.status === "CONFIRMED" ? "bad" : "good";
  return "plain";
}

export function ms(value?: number | null): string {
  if (value === undefined || value === null) return "–";
  return value < 10 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`;
}

export function shortGraph(name?: string): string {
  return name ?? "";
}
