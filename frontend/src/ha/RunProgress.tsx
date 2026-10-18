import { agentClass, ms } from "../lib/format";
import type { RunEvent } from "../lib/types";

const SHOW = new Set([
  "run_started", "fetch", "change_opened", "api_diff", "verdict", "episode_opened", "playbook_miss", "playbook_reused",
  "plan", "hypotheses_posted", "lock_claimed", "lock_conflict", "fork_created", "change_applied", "hypothesis_resolved",
  "fact_recorded", "playbook_saved", "playbook_updated", "report_saved", "error", "run_finished",
]);

export function RunProgress({ title, events, status, error }: { title: string; events: RunEvent[]; status: string; error: string | null }) {
  const shown = events.filter((e) => SHOW.has(e.type) && e.summary);
  const started = events[0]?.ts;
  const last = events[events.length - 1]?.ts;
  const steps = events.filter((e) => e.type === "tool_call").length;
  return (
    <div className="progress">
      <div className="progress-head">
        <span className={`progress-state ${status}`}>{status === "running" ? "Checking" : status === "error" ? "Failed" : "Done"}</span>
        <span className="progress-title">{title}</span>
        <span className="mono muted">{steps} tool calls · {started && last ? ms(last - started) : "–"}</span>
      </div>
      {error && <p className="note note-breaks">{error}</p>}
      <ol className="progress-log">
        {shown.map((e) => (
          <li key={e.seq} className={`plog plog-${e.type}`}>
            <span className={`agent ${agentClass(e.agent)}`}>{e.agent ?? ""}</span>
            <span className="plog-text">{e.summary}</span>
            <span className="plog-t mono">{started ? `+${((e.ts - started) / 1000).toFixed(2)}s` : ""}</span>
          </li>
        ))}
        {status === "running" && <li className="plog plog-wait"><span className="dots" aria-hidden><i /><i /><i /></span></li>}
      </ol>
    </div>
  );
}
