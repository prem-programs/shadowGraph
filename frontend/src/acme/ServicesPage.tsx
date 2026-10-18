import { Play, RotateCcw } from "lucide-react";
import { useContext, useEffect, useMemo, useState } from "react";
import { GraphCanvas, type Overlay } from "../components/GraphCanvas";
import { Empty, Mark, Note, Section, Stat, type Tone } from "../components/ui";
import { api } from "../lib/api";
import { ms, n } from "../lib/format";
import { ThemeContext } from "../lib/theme";
import type { GraphData, Tenant, Verdict } from "../lib/types";
import { useRun } from "../lib/useRun";
import { RunProgress } from "../ha/RunProgress";

const TONE: Record<Verdict, Tone> = { BLOCK: "breaks", WARN: "retires", PASS: "clear" };
const WORD: Record<Verdict, string> = { BLOCK: "Breaks", WARN: "Review", PASS: "Clear" };

/** The synthetic services company (endpoints between microservices): same engine, a different kind of API. */
export function ServicesPage({ tenant }: { tenant: string }) {
  const theme = useContext(ThemeContext);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [view, setView] = useState<"live" | "shadow">("live");
  const [pr, setPr] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = useRun();
  useEffect(() => {
    api.tenants().then(setTenants).catch((e) => setErr(String(e.message ?? e)));
    api.graph(tenant).then(setGraph).catch(() => setGraph(null));
    run.clear();
    setPr(null);
    setView("live");
  }, [tenant]); // eslint-disable-line react-hooks/exhaustive-deps
  const t = tenants.find((x) => x.id === tenant);
  const report = run.result?.report ?? null;
  const overlay = useMemo<Overlay>(() => {
    const removed: [string, string][] = [];
    let islands: string[][] = [];
    for (const e of run.events) {
      if (e.type === "change_applied" && e.edges) for (const edge of e.edges) removed.push(edge);
      if (e.type === "measurement" && e.islands) islands = e.islands;
    }
    return { removed, islands, chains: report?.measure?.cascade.map((c) => c.chain) ?? [] };
  }, [run.events, report]);
  useEffect(() => { if (run.status === "done") setView("shadow"); }, [run.status]);

  return (
    <div className="split has-detail services">
      <div className="split-list">
        <div className="pane-head">
          <h1 className="pane-title">{t?.company ?? tenant}</h1>
          <p className="pane-sub">Sample company: microservices calling each other's HTTP endpoints. Same engine, a different kind of API: removing an endpoint instead of a Python name.</p>
        </div>
        {err && <Note tone="breaks">{err}</Note>}
        <ul className="changes">
          {(t?.prs ?? []).map((p) => (
            <li key={p.number}>
              <button type="button" className={`change ${pr === p.number ? "sel" : ""}`} onClick={() => { setPr(p.number); run.start(tenant, p.number, "rules"); }}
                disabled={run.status === "running"}>
                <span className="change-top"><span className="mono change-num">#{p.number}</span><span className="change-when">{p.author}</span></span>
                <span className="change-title">{p.title}</span>
                <span className="change-headline"><Play size={11} /> Run the check</span>
              </button>
            </li>
          ))}
        </ul>
        <button type="button" className="btn btn-ghost btn-sm" onClick={async () => { await api.reset(tenant); run.clear(); setPr(null); setView("live"); api.tenants().then(setTenants); }}>
          <RotateCcw size={12} /> Reset sample memory
        </button>
      </div>
      <div className="split-detail">
        {run.status === "idle" && <Empty title="Pick a pull request">Each check copies the service graph into shadow graphs, removes the endpoint and measures what is cut off.</Empty>}
        {(run.status === "running" || run.status === "error") && <RunProgress title={`#${pr}`} events={run.events} status={run.status} error={run.error} />}
        {report && run.status === "done" && (
          <article className="report">
            <div className="report-meta mono"><span>#{report.pr.number}</span><span>{report.pr.author}</span><span>{report.target.target}</span></div>
            <h1 className="report-title">{report.pr.title}</h1>
            <div className={`verdict verdict-${TONE[report.verdict.verdict]}`}>
              <Mark tone={TONE[report.verdict.verdict]} word={WORD[report.verdict.verdict]} />
              <p className="verdict-headline">{report.verdict.headline}</p>
              {report.verdict.reasons.length > 0 && <ul className="verdict-reasons">{report.verdict.reasons.map((r) => <li key={r.text}>{r.text}</li>)}</ul>}
            </div>
            <div className="stats">
              <Stat tone={report.measure?.islands.length ? "breaks" : "plain"} value={n(report.measure?.islands.length ?? 0)} label="groups of services cut off" />
              <Stat value={n(report.measure?.entries_cut_off.length ?? 0)} label="tier 0 entry points lose their path" />
              <Stat value={n(report.stats.forks)} label="shadow copies" sub={`fastest ${ms(report.stats.copy_ms)}`} />
              <Stat value={ms(report.stats.ms)} label="total" sub={`${n(report.stats.steps)} agent steps`} />
            </div>
            <Section title="Who is affected">
              {report.measure?.islands.length ? (
                <ul className="plain-list">
                  {report.measure.islands.map((i) => <li key={i.id}><span className="mono">{i.ids.join(", ")}</span> <span className="muted small">best tier {i.best_tier}</span></li>)}
                </ul>
              ) : <p className="muted">No service loses its path.</p>}
            </Section>
            {report.decision && report.decision.decisions_in_force.length > 0 && (
              <Section title="Decisions in force">
                <ul className="plain-list">
                  {report.decision.decisions_in_force.map((d) => (
                    <li key={d.adr}><strong className="mono">{d.adr}</strong> {d.title}{d.blocks_removal ? " · forbids this removal" : ""}
                      {d.cited && d.cited !== d.adr && <div className="muted small">The PR cites {d.cited}, which {d.adr} replaced.</div>}</li>
                  ))}
                </ul>
              </Section>
            )}
            {report.expert && report.expert.experts.length > 0 && (
              <Section title="Reviewers">
                <ul className="plain-list mono">{report.expert.experts.map((e) => <li key={e.engineer}>@{e.engineer} <span className="muted small">{e.channel}</span></li>)}</ul>
              </Section>
            )}
          </article>
        )}
        <div className="services-graph">
          <GraphCanvas graph={graph} view={view} onView={setView} shadowAvailable={overlay.removed.length > 0 || overlay.islands.length > 0}
            overlay={overlay} highlight={[]} measure={report?.measure ?? null} themeKey={theme} />
        </div>
      </div>
    </div>
  );
}
