import { useEffect, useMemo, useRef, useState } from "react";
import { agentColor, ms, STEP_TYPES, tone } from "../lib/format";
import type { RunEvent, RunSummary } from "../lib/types";
import type { RunStatus } from "../lib/useRun";

interface Props {
  events: RunEvent[];
  status: RunStatus;
  selected: number | null;
  onSelect: (seq: number) => void;
  recent: RunSummary[];
  onOpenRun: (id: string) => void;
  error: string | null;
}

export function RunTimeline({ events, status, selected, onSelect, recent, onOpenRun, error }: Props) {
  const [showQueries, setShowQueries] = useState(false);
  const [hover, setHover] = useState(false);
  const endRef = useRef<HTMLLIElement | null>(null);
  const t0 = events[0]?.ts ?? 0;
  const rows = useMemo(
    () => events.filter((e) => STEP_TYPES.has(e.type) || (showQueries && (e.type === "cypher" || e.type === "tool_call"))),
    [events, showQueries],
  );

  useEffect(() => {
    if (!hover) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [rows.length, hover]);

  return (
    <section className="panel" aria-label="Run timeline">
      <div className="panel-head">
        <h2>Run timeline</h2>
        {status === "running" && <span className="chip accent">live</span>}
        <span className="spacer" />
        <label className="small muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={showQueries} onChange={(e) => setShowQueries(e.target.checked)} />
          Queries
        </label>
      </div>
      {error && <div className="banner bad">{error}</div>}
      <div className="panel-body" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        {rows.length === 0 ? (
          <div className="empty">
            <strong>No run yet</strong>
            Pick a PR and press Run check. Every agent step and every Cypher query will stream here.
            {recent.length > 0 && (
              <div style={{ marginTop: 16, textAlign: "left" }}>
                <div className="small muted" style={{ marginBottom: 6 }}>Recent runs</div>
                {recent.slice(0, 6).map((r) => (
                  <button key={r.run_id} className="btn btn-ghost" style={{ width: "100%", justifyContent: "space-between" }}
                          onClick={() => onOpenRun(r.run_id)}>
                    <span>PR #{r.pr_number}</span>
                    <span className={`chip ${r.verdict === "BLOCK" ? "bad" : r.verdict === "PASS" ? "good" : r.verdict ? "warn" : ""}`}>
                      {r.verdict ?? r.status}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <ul className="timeline" role="listbox" aria-label="Events">
            {rows.map((e) => {
              const isQuery = e.type === "cypher" || e.type === "tool_call";
              return (
                <li key={e.seq} role="option" aria-selected={selected === e.seq}
                    className={`event ${tone(e)} ${isQuery ? "query" : ""}`} onClick={() => onSelect(e.seq)}>
                  <span className="dot" style={isQuery ? { background: "var(--border-strong)" } : undefined} />
                  <span>
                    {e.agent && <span className="who" style={{ color: agentColor(e.agent) }}>{e.agent}</span>}
                    <span className="what">
                      {e.type === "tool_call" ? `→ ${e.tool}`
                        : e.type === "cypher" ? `${e.label ?? e.tool ?? "query"} · ${e.rows ?? 0} rows`
                        : e.summary ?? e.type}
                    </span>
                  </span>
                  <span className="when">{e.type === "cypher" ? ms(e.ms) : `+${((e.ts - t0) / 1000).toFixed(2)}s`}</span>
                </li>
              );
            })}
            <li ref={endRef} aria-hidden="true" />
          </ul>
        )}
      </div>
    </section>
  );
}
