import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { Ext, Mark } from "../components/ui";
import { date, fileName, plural } from "../lib/format";
import { CONTEXT_WORD, USE_WORD, useTone, type Consumer, type UseLevel } from "../lib/ha";
import { DraftPanel } from "./DraftPanel";

const ORDER: UseLevel[] = ["breaks", "may_break", "warns", "review", "safe"];
const GROUP_TITLE: Record<UseLevel, string> = {
  breaks: "Break", may_break: "May break", warns: "Will log warnings", review: "Need a look", safe: "Unaffected",
};

function ConsumerRow({ c }: { c: Consumer }) {
  const [open, setOpen] = useState(false);
  const [draftFor, setDraftFor] = useState<string | null>(null);
  const top = c.uses[0];
  const draftable = c.level !== "safe" && (top.how === "removed" || top.how === "deprecated");
  return (
    <li className={`crow ${open ? "open" : ""}`}>
      <button type="button" className="crow-main" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="crow-caret" aria-hidden>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span className="crow-name">
          <span className="crow-title">{c.name}</span>
          <span className="crow-repo mono">{c.kind === "core" ? `core · ${c.domain}` : c.repo}</span>
        </span>
        <span className="crow-effect">{c.effect}</span>
        <span className="crow-where mono">{fileName(top.file)}:{top.line}</span>
        <span className="crow-when mono" title="last commit">{c.kind === "external" ? date(c.last_commit) : ""}</span>
      </button>
      {open && (
        <div className="crow-detail">
          <table className="sites">
            <thead>
              <tr><th>Name</th><th>Where</th><th>When it runs</th><th>Effect</th></tr>
            </thead>
            <tbody>
              {c.uses.map((u, i) => (
                <tr key={i}>
                  <td className="mono">{u.symbol.split(".").slice(-1)[0]}</td>
                  <td><Ext href={u.url} className="mono">{u.file}:{u.line}</Ext></td>
                  <td className="mono">{CONTEXT_WORD[u.context]}</td>
                  <td><Mark tone={useTone(u.level)} size="sm" /> {u.effect}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="crow-actions">
            <Ext href={c.url}>{c.kind === "external" ? "Repository" : "Integration in core"}</Ext>
            {c.min_ha && <span className="muted">declares Home Assistant ≥ {c.min_ha}</span>}
            {c.kind === "core" && c.fixed_in_pr && <span className="muted">updated in this change</span>}
            {draftable && (
              <button type="button" className="btn btn-sm" onClick={() => setDraftFor(top.symbol)}>
                <Wrench size={13} /> Draft a fix
              </button>
            )}
          </div>
          {draftFor && <DraftPanel symbol={draftFor} integration={c.id} onClose={() => setDraftFor(null)} />}
        </div>
      )}
    </li>
  );
}

export function Consumers({ consumers, emptyText }: { consumers: Consumer[]; emptyText: string }) {
  const [showSafe, setShowSafe] = useState(false);
  const groups = useMemo(() => {
    const g = new Map<UseLevel, Consumer[]>();
    for (const c of consumers) g.set(c.level, [...(g.get(c.level) ?? []), c]);
    return ORDER.filter((l) => g.has(l)).map((l) => [l, g.get(l)!] as const);
  }, [consumers]);
  if (!consumers.length) return <p className="muted">{emptyText}</p>;
  return (
    <div className="consumers">
      {groups.map(([level, list]) => {
        const hidden = level === "safe" && !showSafe;
        return (
          <div key={level} className="cgroup">
            <div className="cgroup-head">
              <Mark tone={useTone(level)} word={USE_WORD[level]} size="sm" />
              <span className="cgroup-title">{GROUP_TITLE[level]}</span>
              <span className="cgroup-count mono">
                {plural(list.filter((c) => c.kind === "external").length, "custom integration")}
                {list.some((c) => c.kind === "core") && ` · ${list.filter((c) => c.kind === "core").length} core`}
              </span>
              {level === "safe" && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowSafe((v) => !v)}>
                  {showSafe ? "Hide" : "Show"}
                </button>
              )}
            </div>
            {!hidden && (
              <ul className="crows">
                <li className="crow-header" aria-hidden>
                  <span /><span>Integration</span><span>What happens</span><span>Where</span><span>Updated</span>
                </li>
                {list.map((c) => <ConsumerRow key={c.id} c={c} />)}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
