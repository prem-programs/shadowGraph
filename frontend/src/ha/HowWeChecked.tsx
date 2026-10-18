import { useContext } from "react";
import { Mark } from "../components/ui";
import { ThemeContext } from "../lib/theme";
import { agentClass, ms, n } from "../lib/format";
import type { ImpactReport } from "../lib/ha";
import { ImpactGraph } from "./ImpactGraph";

const CHECK_TEXT: Record<string, string> = {
  consumers: "Who loses a name they import",
  cascade: "Failures spreading through manifest dependencies",
  deadline: "Users of newly deprecated names",
  announced: "Announcements and decisions",
  reviewers: "Who should review",
};

export function HowWeChecked({ r }: { r: ImpactReport }) {
  const theme = useContext(ThemeContext);
  const s = r.agent_stats;
  return (
    <details className="how">
      <summary>
        <span className="how-title">How this was checked</span>
        <span className="how-sub muted">
          {s ? `Scout, Scribe, Simulators and Judge · ${n(s.steps)} MCP tool calls · ${ms(s.ms)} · ${n(s.forks)} shadow graph copies`
            : "Static analysis of both trees and a graph query (no agent run stored)"}
        </span>
      </summary>
      <div className="how-body">
        <ol className="how-steps">
          <li><strong>Read the change, read-only.</strong> Base and result trees come from GitHub's test-merge ref (or the squash commit for merged PRs). Nothing is posted back.</li>
          <li><strong>Diffed the API.</strong> Every name someone imports from the changed modules was resolved in both trees, following re-exports, aliases and base classes ({n(r.api.checked)} names checked{r.api.unknown ? `, ${r.api.unknown} undecidable and left out` : ""}).</li>
          <li><strong>Found the users.</strong> {n(r.reach.core)} core and {n(r.reach.external)} HACS integrations import from these modules; each use is classified by when it runs (import, function body, try/except fallback, type hint).</li>
          {s && <li><strong>Simulated in shadow graphs.</strong> Each simulator copied the live graph with <span className="mono">GRAPH.COPY</span> (fastest {ms(s.copy_ms)}), applied the change, measured and dropped the copy.</li>}
        </ol>
        {r.cross_check && (
          <p className={`cross ${r.cross_check.agree ? "cross-ok" : "cross-bad"}`}>
            {r.cross_check.agree ? "Engine and shadow simulation agree" : "Engine and shadow simulation disagree"}:
            {" "}<span className="mono">{r.cross_check.engine_external_breaks}</span> vs <span className="mono">{r.cross_check.shadow_external_breaks}</span> custom integrations break.
          </p>
        )}
        {r.playbook_reused && (
          <p className="muted small">Reused the playbook "{r.playbook_reused.name}" from a similar earlier check (distance {r.playbook_reused.distance}).</p>
        )}
        {r.hypotheses && r.hypotheses.length > 0 && (
          <table className="hyps">
            <thead><tr><th></th><th>Check</th><th>Agent</th><th>Result</th><th>Copy</th></tr></thead>
            <tbody>
              {r.hypotheses.map((h) => (
                <tr key={h.label}>
                  <td className="mono">{h.label}</td>
                  <td>{CHECK_TEXT[h.check] ?? h.check}</td>
                  <td><span className={`agent ${agentClass(h.agent)}`}>{h.agent ?? "–"}</span></td>
                  <td><Mark tone={h.status === "CONFIRMED" ? "retires" : h.status === "REJECTED" ? "clear" : "plain"} size="sm"
                    word={h.status === "CONFIRMED" ? "found" : h.status === "REJECTED" ? "none" : h.status.toLowerCase()} /> {h.summary}</td>
                  <td className="mono">{h.copy_ms ? ms(h.copy_ms) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {(r.consumers.length > 0 || r.api.removed.length > 0) && <ImpactGraph r={r} themeKey={theme} />}
      </div>
    </details>
  );
}
