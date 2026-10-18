import { Copy } from "lucide-react";
import { agentColor, ms } from "../lib/format";
import type { RunEvent } from "../lib/types";

interface Props {
  selected: RunEvent | null;
  queries: RunEvent[];
}

export function CypherInspector({ selected, queries }: Props) {
  if (!selected) {
    return <p className="muted small" style={{ margin: 0 }}>Click a timeline row to see the Cypher it ran, with parameters and timing.</p>;
  }
  if (!queries.length) {
    return <p className="muted small" style={{ margin: 0 }}>“{selected.summary ?? selected.type}” ran no query of its own.</p>;
  }
  return (
    <div>
      {queries.map((q) => (
        <div className="query-item" key={q.seq}>
          <div className="query-meta">
            <span style={{ color: agentColor(q.agent), fontWeight: 600 }}>{q.agent}</span>
            <span>{q.label ?? q.tool}</span>
            <span className="spacer" style={{ flex: 1 }} />
            <span className="mono">{q.graph}</span>
            <span className="chip">{ms(q.ms)}</span>
            {q.rows !== undefined && <span className="chip">{q.rows} rows</span>}
            <button className="btn btn-ghost" style={{ height: 22, padding: "0 4px" }} aria-label="Copy query"
                    onClick={() => { void navigator.clipboard?.writeText(q.cypher ?? ""); }}>
              <Copy size={12} />
            </button>
          </div>
          <pre className="code">{q.cypher}</pre>
          {q.params && Object.keys(q.params).length > 0 && (
            <details style={{ marginTop: 4 }}>
              <summary className="small">parameters</summary>
              <pre className="code">{JSON.stringify(q.params, null, 2)}</pre>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
