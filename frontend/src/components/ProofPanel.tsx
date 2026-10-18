import { CircleAlert, CircleCheck, ExternalLink, OctagonX } from "lucide-react";
import { ms } from "../lib/format";
import type { RunEvent, RunResult } from "../lib/types";
import type { RunStatus } from "../lib/useRun";
import { CypherInspector } from "./CypherInspector";

interface Props {
  status: RunStatus;
  result: RunResult | null;
  selected: RunEvent | null;
  queries: RunEvent[];
  liveHypotheses: { label: string; check: string; status: string; agent?: string }[];
}

const ICONS = { BLOCK: OctagonX, WARN: CircleAlert, PASS: CircleCheck };

export function ProofPanel({ status, result, selected, queries, liveHypotheses }: Props) {
  const r = result?.report;
  const v = r?.verdict;
  const Icon = v ? ICONS[v.verdict] : null;
  const hyps = r?.hypotheses ?? liveHypotheses;
  return (
    <div>
      <div className="section">
        {v && Icon ? (
          <div className={`verdict ${v.verdict}`}>
            <div className="word"><Icon size={20} aria-hidden="true" /> {v.verdict}</div>
            <div className="headline">{v.headline}</div>
            <div className="stats">
              <div>Agent steps <strong>{result!.steps}</strong></div>
              <div>Total <strong>{ms(result!.ms)}</strong></div>
              <div>Shadow copies <strong>{r!.stats.forks}</strong></div>
              <div>Fastest copy <strong>{ms(r!.stats.copy_ms)}</strong></div>
            </div>
            {result!.playbook_reused && (
              <div className="small" style={{ marginTop: 8 }}>
                <span className="chip accent">Playbook reused</span>{" "}
                from PR #{result!.playbook_reused.from_pr}: {result!.steps} steps
                {result!.playbook_reused.from_steps ? ` instead of ${result!.playbook_reused.from_steps}` : ""}
              </div>
            )}
            <div className="small" style={{ marginTop: 8 }}>
              {result!.github.posted ? (
                <a href={result!.github.comment_url} target="_blank" rel="noreferrer">
                  Posted to GitHub ({result!.github.status}) <ExternalLink size={11} />
                </a>
              ) : <span className="muted">Not posted to GitHub: {result!.github.reason}</span>}
            </div>
          </div>
        ) : (
          <div className="verdict">
            <div className="word">{status === "running" ? "Checking…" : status === "error" ? "Run failed" : "No verdict yet"}</div>
            <div className="headline muted">
              {status === "running" ? "Agents are investigating on shadow copies of the graph."
                : "The verdict, its proof and the queries behind it appear here."}
            </div>
          </div>
        )}
      </div>

      {hyps.length > 0 && (
        <div className="section">
          <h3>Hypotheses</h3>
          <ul className="hyps">
            {hyps.map((h) => (
              <li key={h.label}>
                <span className="lbl">{h.label}</span>
                <span>{h.check.replace("_", " ")}{h.agent ? <span className="muted"> · {h.agent}</span> : null}</span>
                <span className={`chip ${h.status === "CONFIRMED" ? "bad" : h.status === "REJECTED" ? "good" : ""}`}>
                  {h.status.toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {r?.decision && r.decision.decisions_in_force.length > 0 && (
        <div className="section">
          <h3>Decision in force</h3>
          {r.decision.decisions_in_force.map((d) => (
            <div key={d.adr} className="kv" style={{ marginBottom: 6 }}>
              <div>
                {d.cited && d.cited !== d.adr && <><span className="struck">{d.cited}</span>{" → "}</>}
                <strong>{d.adr}</strong> {d.title}
                {d.blocks_removal && <span className="chip bad" style={{ marginLeft: 6 }}>forbids removal</span>}
              </div>
              <div className="small muted">{d.summary}</div>
              {d.discussion && (
                <div className="small">
                  <a href={d.discussion} target="_blank" rel="noreferrer">{d.channel ?? "discussion"}</a>
                  {d.author ? <span className="muted"> · by @{d.author}</span> : null}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {r?.expert && r.expert.experts.length > 0 && (
        <div className="section">
          <h3>Who should review</h3>
          <ul className="experts">
            {r.expert.experts.map((e, i) => (
              <li key={e.engineer}>
                <span>{i === 0 ? <strong>@{e.engineer}</strong> : `@${e.engineer}`} <span className="muted small">{e.name}</span></span>
                <span className="muted small mono">PR {e.authority.toFixed(3)} × {e.commits}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {r?.measure && (r.measure.cascade.length > 0 || r.measure.direct_dependents.length > 0) && (
        <div className="section">
          <h3>Blast radius</h3>
          <div className="small">Connected groups: {r.measure.components_live} → <strong>{r.measure.components_shadow}</strong></div>
          {r.measure.cascade.map((c) => (
            <div key={c.chain.join()} className="small mono" style={{ marginTop: 4 }}>{c.chain.join(" → ")}</div>
          ))}
          {r.measure.direct_dependents.length > 0 && (
            <div className="small muted" style={{ marginTop: 4 }}>
              Direct callers: {r.measure.direct_dependents.map((d) => `${d.service} (tier ${d.tier})`).join(", ")}
            </div>
          )}
        </div>
      )}

      <div className="section">
        <h3>Cypher inspector</h3>
        <CypherInspector selected={selected} queries={queries} />
      </div>

      {result?.comment && (
        <div className="section">
          <details>
            <summary>PR comment (markdown, as posted)</summary>
            <pre className="code" style={{ maxHeight: 360, marginTop: 8 }}>{result.comment}</pre>
          </details>
        </div>
      )}
    </div>
  );
}
