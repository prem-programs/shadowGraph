import { ArrowLeft, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Empty, Loading, Mark, Note, Segmented } from "../components/ui";
import { ago, plural } from "../lib/format";
import { ha, LEVEL_WORD, type ChangeRow, type FeedStatus, type ImpactReport, type Level } from "../lib/ha";
import { go, href } from "../lib/router";
import { useRun } from "../lib/useRun";
import { Report } from "./Report";
import { RunProgress } from "./RunProgress";

type Filter = "all" | Level;

// Real changes from the week of Oct 11-18, 2026 on home-assistant/core (merged; replayed from their commits).
const REPLAYS = [
  { number: 186863, note: "Moves sensor entity classes; drops re-exports" },
  { number: 184756, note: "Removes the deprecated async_track_state_change" },
  { number: 186267, note: "Deprecates the config flow CONN_CLASS_* constants" },
];

function parsePr(input: string): number | null {
  const m = input.trim().match(/(?:pull\/)?#?(\d{3,7})\s*$/);
  return m ? Number(m[1]) : null;
}

function FeedLine({ feed, onPoll, polling }: { feed: FeedStatus | null; onPoll: () => void; polling: boolean }) {
  if (!feed) return null;
  return (
    <div className="feedline">
      <span className={`feed-dot ${feed.enabled ? "on" : ""}`} aria-hidden />
      <span>
        {feed.enabled ? `Watching open PRs every ${Math.round(feed.interval / 60)} min` : "Live feed off"}
        {feed.last_poll ? ` · last poll ${ago(feed.last_poll)}` : ""}
        {feed.running ? " · agents running" : ""}
      </span>
      <button type="button" className="btn btn-ghost btn-sm" onClick={onPoll} disabled={polling} title="Check the newest open pull requests now">
        <RefreshCw size={12} className={polling ? "spin" : ""} /> {polling ? "Polling" : "Poll now"}
      </button>
      {feed.last_error && <span className="feed-err" title={feed.last_error}>last poll failed</span>}
    </div>
  );
}

export function ChangesPage({ selected }: { selected: string | null }) {
  const [rows, setRows] = useState<ChangeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [input, setInput] = useState("");
  const [report, setReport] = useState<ImpactReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [feed, setFeed] = useState<FeedStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const [checking, setChecking] = useState<string>("");
  const run = useRun();

  const refresh = useCallback(() => {
    ha.changes().then(setRows).catch((e) => setError(String(e.message ?? e)));
    ha.feed().then(setFeed).catch(() => setFeed(null));
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (!selected) { setReport(null); return; }
    let live = true;
    setReport(null);
    setReportError(null);
    ha.change(selected).then((r) => live && setReport(r)).catch((e) => live && setReportError(String(e.message ?? e)));
    return () => { live = false; };
  }, [selected]);

  useEffect(() => {
    if (run.status === "done" && run.result) {
      const res = run.result as unknown as { report?: ImpactReport; change_id?: string };
      refresh();
      if (res.change_id) go("ha", "changes", res.change_id);
      if (res.report) setReport(res.report);
      run.clear();
      setChecking("");
    }
  }, [run, refresh]);

  const start = async (number: number) => {
    setChecking(`#${number}`);
    try {
      const { run_id } = await ha.check({ number });
      run.follow(run_id);
    } catch (e) {
      setChecking("");
      setError(String((e as Error).message ?? e));
    }
  };

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows?.length ?? 0, breaks: 0, retires: 0, review: 0, clear: 0 };
    for (const r of rows ?? []) c[r.level] = (c[r.level] ?? 0) + 1;
    return c;
  }, [rows]);
  const visible = (rows ?? []).filter((r) => filter === "all" || r.level === filter);
  const attention = visible.filter((r) => r.level !== "clear");
  const quiet = visible.filter((r) => r.level === "clear");
  const showDetail = Boolean(selected) || run.status === "running" || run.status === "error";

  return (
    <div className={`split ${showDetail ? "has-detail" : ""}`}>
      <div className="split-list">
        <div className="pane-head">
          <h1 className="pane-title">Pull requests</h1>
          <p className="pane-sub">home-assistant/core · checked read-only against every core and HACS integration</p>
        </div>
        <form className="check-form" onSubmit={(e) => {
          e.preventDefault();
          const n = parsePr(input);
          if (n) { start(n); setInput(""); } else setError("Type a PR number or paste a pull request URL");
        }}>
          <input className="input" value={input} onChange={(e) => setInput(e.target.value)} placeholder="PR number or URL"
            aria-label="Pull request to check" inputMode="numeric" />
          <button className="btn btn-primary" type="submit" disabled={run.status === "running"}><Play size={13} /> Check</button>
        </form>
        <FeedLine feed={feed} polling={polling} onPoll={async () => {
          setPolling(true);
          try { await ha.poll(); } catch (e) { setError(String((e as Error).message ?? e)); }
          setPolling(false);
          refresh();
        }} />
        {error && <Note tone="breaks">{error} <button className="linkish" onClick={() => setError(null)}>dismiss</button></Note>}
        <Segmented<Filter> label="Filter by outcome" value={filter} onChange={setFilter} options={[
          { value: "all", label: "All", count: counts.all },
          { value: "breaks", label: "Breaks", count: counts.breaks },
          { value: "retires", label: "Retires", count: counts.retires },
          { value: "review", label: "Review", count: counts.review },
          { value: "clear", label: "Clear", count: counts.clear },
        ]} />
        {rows === null && <Loading />}
        {rows && visible.length === 0 && (
          <Empty title={rows.length ? "Nothing with this outcome" : "No checks yet"}>
            Check a PR above, poll the live feed, or replay one of this week's merged changes below.
          </Empty>
        )}
        {[["Needs attention", attention], ["No API impact", quiet]].map(([label, list]) => (list as ChangeRow[]).length > 0 && (
          <div key={label as string}>
            {filter === "all" && <div className="list-head">{label as string}</div>}
            <ul className="changes">
              {(list as ChangeRow[]).map((r) => (
                <li key={r.id}>
                  <a className={`change change-${r.level} ${selected === r.id ? "sel" : ""}`} href={href("ha", "changes", r.id)}>
                    <span className="change-top">
                      <span className="mono change-num">{r.number ? `#${r.number}` : r.id}</span>
                      <Mark tone={r.level} word={LEVEL_WORD[r.level]} size="sm" />
                      <span className="change-when">{r.state === "merged" ? "merged · " : ""}{ago(r.analyzed_at)}</span>
                    </span>
                    <span className="change-title">{r.title}</span>
                    <span className="change-headline">{r.headline}</span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <div className="replays">
          <div className="replays-title">Replay a real change from this week</div>
          {REPLAYS.map((x) => (
            <button key={x.number} type="button" className="replay" onClick={() => start(x.number)} disabled={run.status === "running"}>
              <span className="mono">#{x.number}</span><span>{x.note}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="split-detail">
        <a className="back-link" href={href("ha", "changes")}><ArrowLeft size={14} /> All pull requests</a>
        {(run.status === "running" || run.status === "error") && (
          <RunProgress title={checking} events={run.events} status={run.status} error={run.error} />
        )}
        {run.status !== "running" && reportError && <Note tone="breaks">{reportError}</Note>}
        {run.status !== "running" && selected && !report && !reportError && <Loading label="Loading the report" />}
        {run.status !== "running" && report && <Report r={report} />}
        {!selected && run.status === "idle" && rows && rows.length > 0 && (
          <Empty title="Pick a pull request">
            {plural(counts.breaks, "check")} found breaking changes; {plural(counts.retires, "check")} start a retirement.
          </Empty>
        )}
      </div>
    </div>
  );
}
