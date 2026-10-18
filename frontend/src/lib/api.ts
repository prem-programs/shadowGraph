import type {
  ChatMessage, ChatReply, GraphData, Health, MemoryOverview, RunEvent, RunRecord, RunSummary, SessionSummary, Tenant,
} from "./types";

// Same origin by default (the backend serves the built dashboard). Set VITE_API_URL when hosting it elsewhere.
export const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  tenants: () => request<Tenant[]>("/api/tenants"),
  graph: (tenant: string) => request<GraphData>(`/api/graph/${encodeURIComponent(tenant)}`),
  startRun: (tenant: string, pr_number: number, mode: string) =>
    request<{ run_id: string }>("/api/runs", { method: "POST", body: JSON.stringify({ tenant, pr_number, mode }) }),
  run: (id: string) => request<RunRecord>(`/api/runs/${encodeURIComponent(id)}`),
  runs: (tenant: string) => request<RunSummary[]>(`/api/runs?tenant=${encodeURIComponent(tenant)}`),
  memory: (tenant: string) => request<MemoryOverview>(`/api/memory/${encodeURIComponent(tenant)}`),
  chat: (tenant: string, message: string, session_id: string | null, mode: string) =>
    request<ChatReply>("/api/chat", { method: "POST", body: JSON.stringify({ tenant, message, session_id, mode }) }),
  sessions: (tenant: string) => request<SessionSummary[]>(`/api/sessions/${encodeURIComponent(tenant)}`),
  session: (tenant: string, id: string) =>
    request<ChatMessage[]>(`/api/sessions/${encodeURIComponent(tenant)}/${encodeURIComponent(id)}`),
  reset: (tenant: string) => request<{ ok: boolean }>(`/api/admin/reset/${encodeURIComponent(tenant)}`, { method: "POST" }),
};

/** Stream a run's events. Calls onEvent for each, onDone when the run finishes. Returns a stop function. */
export function streamRun(runId: string, onEvent: (e: RunEvent) => void, onDone: () => void): () => void {
  const source = new EventSource(`${API_BASE}/api/runs/${encodeURIComponent(runId)}/events`);
  let finished = false;
  source.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data) as RunEvent;
      onEvent(event);
      if (event.type === "run_finished") {
        finished = true;
        source.close();
        onDone();
      }
    } catch {
      /* ignore malformed */
    }
  };
  source.onerror = () => {
    if (!finished) {
      source.close();
      onDone();
    }
  };
  return () => source.close();
}

export const storage = {
  get(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string | null) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {
      /* private mode */
    }
  },
};
