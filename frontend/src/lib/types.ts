export type Verdict = "BLOCK" | "WARN" | "PASS";

export interface Health {
  ok: boolean;
  falkordb: string;
  tenants: string[];
  embedder: string | null;
  llm: boolean;
  llm_model: string | null;
  github: string | null;
  default_mode: "rules" | "llm";
}

export interface PRItem {
  number: number;
  title: string;
  author: string | null;
}

export interface Tenant {
  id: string;
  company: string;
  prs: PRItem[];
}

export interface ServiceNode {
  id: string;
  tier: number;
  description: string;
  team: string | null;
  betweenness: number;
}

export interface CallEdge {
  source: string;
  target: string;
  via: string;
  p99_ms: number;
  rpm: number;
}

export interface GraphData {
  tenant: string;
  services: ServiceNode[];
  calls: CallEdge[];
}

export interface RunEvent {
  seq: number;
  ts: number;
  run_id: string;
  type: string;
  agent?: string;
  tool?: string;
  graph?: string;
  label?: string;
  cypher?: string;
  params?: Record<string, unknown>;
  ms?: number;
  rows?: number;
  summary?: string;
  nodes?: string[];
  edges?: [string, string][];
  islands?: string[][];
  hypothesis?: string;
  status?: string;
  verdict?: Verdict;
  url?: string;
  mode?: string;
  steps?: number;
}

export interface Island {
  id: number;
  size: number;
  ids: string[];
  best_tier: number;
}

export interface Measure {
  shadow: string;
  provider: string | null;
  components_live: number;
  components_shadow: number;
  islands: Island[];
  blocking_islands: Island[];
  betweenness: { service: string; tier: number; live: number; shadow: number; delta: number; pct: number }[];
  rising_chokepoints: { service: string; pct: number; live: number; shadow: number }[];
  direct_dependents: { service: string; tier: number }[];
  cascade: { entry: string; breaks_at: string; hops: number; chain: string[] }[];
  entries_cut_off: string[];
}

export interface DecisionInForce {
  adr: string;
  title: string;
  summary: string;
  blocks_removal: boolean;
  discussion: string | null;
  channel: string | null;
  author: string | null;
  chain: string[];
  reasons: string[];
  cited: string | null;
  cited_title: string | null;
}

export interface Expert {
  engineer: string;
  name: string;
  timezone: string;
  authority: number;
  commits: number;
  expertise: number;
  channel: string | null;
}

export interface Report {
  run_id: string;
  tenant: string;
  pr: { number: number; title: string; author: string; cites: string[] };
  target: { target: string; route: string; provider: string; change: string };
  verdict: { verdict: Verdict; headline: string; reasons: { level: string; check: string; text: string }[] };
  measure: Measure | null;
  decision: { decisions_in_force: DecisionInForce[]; blockers: DecisionInForce[] } | null;
  expert: { service_id: string; experts: Expert[] } | null;
  hypotheses: { label: string; check: string; status: string; summary: string; agent: string | null }[];
  stats: { forks: number; copy_ms: number | null; steps: number; ms: number };
}

export interface RunResult {
  run_id: string;
  tenant: string;
  pr_number: number;
  mode: string;
  verdict: Report["verdict"];
  report: Report;
  github: { posted: boolean; reason?: string; comment_url?: string; status?: string };
  comment: string;
  playbook_reused: { playbook: string; name: string; from_pr: number; from_steps: number | null; from_ms: number | null; distance: number } | null;
  steps: number;
  ms: number;
}

export interface RunRecord {
  run_id: string;
  tenant: string;
  pr_number: number;
  mode: string;
  status: "running" | "done" | "error";
  started_at: number;
  result: RunResult | null;
  error: string | null;
}

export interface RunSummary {
  run_id: string;
  tenant: string;
  pr_number: number;
  mode: string;
  status: string;
  started_at: number;
  verdict: Verdict | null;
  steps: number | null;
  ms: number | null;
}

export interface MemoryOverview {
  counts: { episodes?: number; facts?: number; playbooks?: number; sessions?: number; hypotheses?: number };
  facts: {
    id: string; entity: string; attribute: string; value: unknown; source: string; agent: string | null;
    valid_from: number; valid_until: number | null; overrides: string | null;
  }[];
  playbooks: { id: string; name: string; executions: number; success_rate: number; decisive: string[]; steps: number }[];
  episodes: { id: string; pr_number: number; title: string; status: string; verdict: Verdict | null; steps: number | null; ms: number | null }[];
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  text: string;
  sources?: string[];
  ts?: number;
}

export interface ChatReply {
  session_id: string;
  answer: string;
  sources: string[];
  mode: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  started_at: number;
  messages: number;
}
