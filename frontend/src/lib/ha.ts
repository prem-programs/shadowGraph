// Types and API calls for the Home Assistant workspace (/api/ha/*). Mirrors backend/shadowgraph/ha.
import { API_BASE } from "./api";

export type Level = "breaks" | "retires" | "review" | "clear";
export type UseLevel = "breaks" | "may_break" | "warns" | "review" | "safe";
export type Context = "load" | "runtime" | "conditional" | "shimmed" | "type";

export interface Overview {
  loaded: boolean;
  snapshot?: string;
  core_repo?: string;
  core_sha?: string;
  core_date?: string;
  generated_at?: string;
  core_integrations?: number;
  external_integrations?: number;
  external_repos?: number;
  uses?: number;
  symbols?: number;
  deprecated?: number;
  retirements?: number;
  changes?: number;
  next_release?: string;
  next_release_date?: string;
  already_broken?: number;
  breaks_next?: number;
  source?: string | null;
  token?: boolean;
  feed?: { enabled: boolean; last_poll: string | null; last_error: string | null; polls: number; queued: number };
}

export interface ChangeRow {
  id: string;
  number: number;
  title: string;
  author: string;
  url: string;
  state: string;
  level: Level;
  headline: string;
  analyzed_at: number;
  files: number;
  breaks: number;
  warns: number;
  source: string;
}

export interface Use {
  symbol: string;
  how: "removed" | "deprecated" | "signature";
  file: string;
  line: number;
  context: Context;
  level: UseLevel;
  effect: string;
  url: string;
}

export interface Consumer {
  id: string;
  kind: "core" | "external";
  name: string;
  domain: string;
  repo: string;
  url: string;
  last_commit: string;
  min_ha: string;
  uses: Use[];
  level: UseLevel;
  effect: string;
  fixed_in_pr: boolean | null;
  symbols: string[];
}

export interface ApiSymbol {
  id: string;
  module: string;
  name: string;
  users_core: number;
  users_external: number;
  kind?: string;
  reexport?: boolean;
  in_all?: boolean;
  private?: boolean;
  origin?: string | null;
  consequence_of?: string;
  changes?: string[];
  dep_breaks_in?: string | null;
  dep_replacement?: string | null;
  dep_how?: string;
}

export interface Hypothesis {
  label: string;
  check: string;
  status: string;
  summary: string;
  agent: string | null;
  copy_ms: number | null;
}

export interface ImpactReport {
  change: {
    number: number | null; title: string; author: string; url: string; state: string; base_sha: string;
    head_sha: string; merged_at: string; updated_at: string; source: string; title_is_commit: boolean;
  };
  files: { total: number; core_python: number; tests: number; list: string[] };
  modules: string[];
  domains: string[];
  api: {
    removed: ApiSymbol[]; deprecated: ApiSymbol[]; signature: ApiSymbol[]; restored: ApiSymbol[];
    unknown: number; added: number; removed_unused: string[]; removed_unused_count: number; checked: number;
  };
  consumers: Consumer[];
  cascade: { id: string; name: string; kind: string; repo: string; because: string[]; hops: number }[];
  touched: { domain: string; name: string; integration_type: string; dependents_core: number; dependents_external: number; owners: string[] }[];
  reach: { core: number; external: number };
  posts: { id: string; title: string; date: string; url: string; author: string; author_handle: string; summary: string; symbols: string[] }[];
  owners: string[];
  history: { id: string; removed_in: string; users_external: number }[];
  next_release: { version: string; date: string };
  verdict: {
    level: Level; headline: string; reasons: string[];
    counts: { external_breaks: number; external_may_break: number; core_breaks: number; warns: number; consumers: number };
  };
  ms: number;
  checks?: Record<string, { status: string; summary: string; agent: string; label: string; copy_ms?: number; measure?: Record<string, unknown>; data?: Record<string, unknown> }>;
  agent_stats?: { forks: number; copy_ms: number | null; steps: number; ms: number };
  cross_check?: { engine_external_breaks: number; shadow_external_breaks: number; agree: boolean };
  hypotheses?: Hypothesis[];
  playbook_reused?: { playbook: string; name: string; from_pr: number; distance: number } | null;
}

export interface SymbolUser {
  id: string; kind: "core" | "external"; name: string; domain: string; repo: string; url: string;
  last_commit: string; min_ha: string; context: Context; n: number; if_removed: UseLevel; effect: string;
  uses: { file: string; line: number; context: Context; if_removed: UseLevel; effect: string; url: string; symbol?: string }[];
}

export interface SymbolDetail {
  symbol: Record<string, string | number | boolean | null> & { id: string; module: string; name: string; state: string };
  posts: { id: string; title: string; date: string; url: string; author: string }[];
  users: SymbolUser[];
  summary: { core: number; external: number; would_break: number; would_break_external: number; fallback_or_type_only: number };
  can_remove: boolean;
  alternatives: { id: string; module: string; reexport: boolean; users: number }[];
}

export interface RetirementCard {
  id: string; status: "ready" | "migrating" | "overdue"; family: string; module: string; how: string;
  names: string[]; symbols: string[]; replacements: string[]; breaks_in: string; release_date: string;
  days_left: number | null; deprecated_in: string; path: string; line: number; users_core: number;
  users_external: number; blocking: number; blocking_external: number; condition: string; ready: boolean;
  posts: { id: string; title: string; url: string; date: string; author: string }[];
}

export interface BrokenItem {
  integration: { id: string; name: string; repo: string; url: string; last_commit: string; min_ha: string };
  symbol: string; removed_in: string; never_existed: boolean; released: boolean; file: string; line: number;
  context: Context; effect: string; url: string;
}

export interface Draft {
  symbol: string;
  integration: { id: string; name: string; kind: string; repo: string; url: string };
  plan: { kind: string; new_module: string | null; new_expr: string | null };
  patches: { file: string; diff: string; url: string }[];
  notes: string[];
  posted: boolean;
  disclaimer: string;
}

export interface FeedStatus {
  enabled: boolean; last_poll: string | null; last_error: string | null; polls: number; open_seen?: number;
  last_checked?: { number: number; level?: Level; headline?: string; error?: string }[]; running: string | null;
  queued: number[]; interval: number;
}

export interface AskReply {
  session_id: string;
  answer: string;
  sources: { label: string; url: string }[];
  mode: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init, headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

export const ha = {
  overview: () => req<Overview>("/api/ha/overview"),
  changes: () => req<ChangeRow[]>("/api/ha/changes"),
  change: (id: string) => req<ImpactReport>(`/api/ha/changes/${enc(id)}`),
  check: (body: { number?: number; url?: string; sha?: string }) =>
    req<{ run_id: string }>("/api/ha/changes", { method: "POST", body: JSON.stringify(body) }),
  symbol: (id: string) => req<SymbolDetail>(`/api/ha/symbols/${id.split("/").map(enc).join("/")}`),
  search: (q: string) => req<{ id: string; module: string; name: string; state: string; deprecated: boolean; breaks_in: string; removed_in: string; users_core: number; users_external: number }[]>(`/api/ha/symbols?q=${enc(q)}`),
  retirements: () => req<{ next_release: { version: string; date: string }; cards: RetirementCard[]; totals: { families: number; names: number; ready: number } }>("/api/ha/retirements"),
  retirementUsers: (symbols: string[]) =>
    req<SymbolUser[]>("/api/ha/retirements/users", { method: "POST", body: JSON.stringify({ symbols }) }),
  broken: () => req<{ next_release: { version: string; date: string }; items: BrokenItem[] }>("/api/ha/broken"),
  draft: (symbol: string, integration: string) =>
    req<Draft>("/api/ha/drafts", { method: "POST", body: JSON.stringify({ symbol, integration }) }),
  feed: () => req<FeedStatus>("/api/ha/feed"),
  poll: () => req<{ checked: unknown[]; error: string | null }>("/api/ha/feed/poll", { method: "POST" }),
  ask: (message: string, session_id: string | null) =>
    req<AskReply>("/api/chat", { method: "POST", body: JSON.stringify({ tenant: "ha", message, session_id }) }),
};

export const LEVEL_WORD: Record<Level, string> = { breaks: "Breaks", retires: "Retires", review: "Review", clear: "Clear" };
export const USE_WORD: Record<UseLevel, string> = {
  breaks: "Breaks", may_break: "May break", warns: "Warns", review: "Review", safe: "Unaffected",
};
export const CONTEXT_WORD: Record<Context, string> = {
  load: "on import", runtime: "at run time", conditional: "conditional", shimmed: "has fallback", type: "type hint",
};
export function useTone(l: UseLevel): Level {
  return l === "breaks" ? "breaks" : l === "may_break" || l === "warns" ? "retires" : l === "review" ? "review" : "clear";
}
