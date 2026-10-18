import type { MemoryOverview } from "../lib/types";

export function MemoryPanel({ memory, tenantName }: { memory: MemoryOverview | null; tenantName: string }) {
  if (!memory) return <div className="empty">Loading memory…</div>;
  const c = memory.counts;
  return (
    <div>
      <div className="section">
        <h3>{tenantName} memory</h3>
        <div className="counts">
          <div><strong>{c.episodes ?? 0}</strong><span className="small muted">episodes</span></div>
          <div><strong>{c.facts ?? 0}</strong><span className="small muted">facts</span></div>
          <div><strong>{c.playbooks ?? 0}</strong><span className="small muted">playbooks</span></div>
          <div><strong>{c.sessions ?? 0}</strong><span className="small muted">chats</span></div>
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>Each tenant has its own memory graph; nothing here is visible to other tenants.</p>
      </div>
      <div className="section">
        <h3>Facts (semantic)</h3>
        {memory.facts.length === 0 ? <p className="muted small" style={{ margin: 0 }}>No facts yet.</p> : (
          <ul className="facts">
            {memory.facts.map((f) => (
              <li key={f.id} className={f.valid_until ? "struck" : ""}>
                <div className="mono small">{f.entity}</div>
                <div>{f.attribute} = <strong>{String(f.value)}</strong></div>
                <div className="small muted">{f.source}{f.overrides ? " · overrides an older fact" : ""}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="section">
        <h3>Playbooks (procedural)</h3>
        {memory.playbooks.length === 0 ? <p className="muted small" style={{ margin: 0 }}>No playbooks yet.</p> :
          memory.playbooks.map((p) => (
            <div key={p.id} style={{ marginBottom: 6 }}>
              <div>{p.name}</div>
              <div className="small muted">{p.steps} steps · used {p.executions}× · success {Math.round(p.success_rate * 100)}% ·
                checks: {(p.decisive ?? []).join(", ")}</div>
            </div>
          ))}
      </div>
      <div className="section">
        <h3>Episodes (episodic)</h3>
        {memory.episodes.length === 0 ? <p className="muted small" style={{ margin: 0 }}>No episodes yet.</p> :
          memory.episodes.map((e) => (
            <div key={e.id} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 4 }}>
              <span>PR #{e.pr_number} {e.title}</span>
              <span className={`chip ${e.verdict === "BLOCK" ? "bad" : e.verdict === "PASS" ? "good" : e.verdict ? "warn" : ""}`}>
                {e.verdict ?? e.status}{e.steps ? ` · ${e.steps} steps` : ""}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}
