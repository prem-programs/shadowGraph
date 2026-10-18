import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatDrawer } from "./components/ChatDrawer";
import { GraphCanvas, type Overlay } from "./components/GraphCanvas";
import { MemoryPanel } from "./components/MemoryPanel";
import { ProofPanel } from "./components/ProofPanel";
import { RunTimeline } from "./components/RunTimeline";
import { type Theme, TopBar } from "./components/TopBar";
import { api, storage } from "./lib/api";
import { STEP_TYPES } from "./lib/format";
import type { GraphData, Health, MemoryOverview, RunEvent, RunSummary, Tenant } from "./lib/types";
import { useRun } from "./lib/useRun";

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  storage.set("sg-theme", theme === "system" ? null : theme);
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenant] = useState(params.get("tenant") ?? storage.get("sg-tenant") ?? "acme");
  const [pr, setPr] = useState<number | null>(null);
  const [mode, setMode] = useState<"rules" | "llm">("rules");
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [memory, setMemory] = useState<MemoryOverview | null>(null);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [view, setView] = useState<"live" | "shadow">("live");
  const [tab, setTab] = useState<"proof" | "memory">("proof");
  const [selected, setSelected] = useState<number | null>(null);
  const [theme, setTheme] = useState<Theme>(() => (storage.get("sg-theme") as Theme | null) ?? "system");
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  const run = useRun();

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(mq.matches);
    mq?.addEventListener("change", onChange);
    return () => mq?.removeEventListener("change", onChange);
  }, []);

  const loadHealth = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h);
      setHealthError(h.ok ? null : "FalkorDB is not reachable");
      if (h.ok) {
        const t = await api.tenants();
        setTenants(t);
      }
    } catch (err) {
      setHealth(null);
      setHealthError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadHealth();
    const id = window.setInterval(() => { void loadHealth(); }, 15000);
    return () => window.clearInterval(id);
  }, [loadHealth]);

  useEffect(() => { if (health) setMode((m) => (m === "llm" && !health.llm ? "rules" : m)); }, [health]);

  useEffect(() => {
    if (!tenants.length) return;
    if (!tenants.some((t) => t.id === tenant)) { setTenant(tenants[0].id); return; }
    const prs = tenants.find((t) => t.id === tenant)?.prs ?? [];
    setPr((cur) => (cur !== null && prs.some((p) => p.number === cur) ? cur : prs[0]?.number ?? null));
  }, [tenants, tenant]);

  const refreshTenantData = useCallback(() => {
    api.memory(tenant).then(setMemory).catch(() => setMemory(null));
    api.runs(tenant).then(setRecent).catch(() => setRecent([]));
  }, [tenant]);

  useEffect(() => {
    if (!health?.ok) return;
    storage.set("sg-tenant", tenant);
    setGraph(null);
    api.graph(tenant).then(setGraph).catch(() => setGraph(null));
    refreshTenantData();
  }, [tenant, health?.ok, refreshTenantData]);

  // open a run from the URL (the GitHub status link points here)
  useEffect(() => {
    const id = params.get("run");
    if (id) run.follow(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (run.status === "done" || run.status === "error") refreshTenantData();
  }, [run.status, refreshTenantData]);

  useEffect(() => {
    if (run.result?.report.measure) setView("shadow");
  }, [run.result]);

  const startRun = async () => {
    if (pr === null) return;
    setSelected(null);
    setView("live");
    setTab("proof");
    const id = await run.start(tenant, pr, mode);
    if (id) {
      const url = new URL(window.location.href);
      url.searchParams.set("tenant", tenant);
      url.searchParams.set("run", id);
      window.history.replaceState(null, "", url);
    }
  };

  const reset = async () => {
    if (!window.confirm(`Rebuild ${tenant}'s company graph and clear its memory? Use this between demo takes.`)) return;
    try {
      await api.reset(tenant);
    } catch (err) {
      window.alert(`Reset failed: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    run.clear();
    setSelected(null);
    setView("live");
    api.graph(tenant).then(setGraph).catch(() => setGraph(null));
    refreshTenantData();
  };

  const overlay = useMemo<Overlay>(() => {
    const removed: [string, string][] = [];
    let islands: string[][] = [];
    for (const e of run.events) {
      if (e.type === "change_applied" && e.edges) for (const edge of e.edges) removed.push(edge);
      if (e.type === "measurement" && e.islands) islands = e.islands;
    }
    const chains = run.result?.report.measure?.cascade.map((c) => c.chain) ?? [];
    return { removed, islands, chains };
  }, [run.events, run.result]);

  const selectedEvent = run.events.find((e) => e.seq === selected) ?? null;
  const queries = useMemo(() => {
    if (!selectedEvent) return [] as RunEvent[];
    if (selectedEvent.cypher) return [selectedEvent];
    // the queries this step ran: same agent and tool, after that agent's previous step
    const idx = run.events.indexOf(selectedEvent);
    const out: RunEvent[] = [];
    for (let i = idx - 1; i >= 0; i--) {
      const e = run.events[i];
      if (e.agent !== selectedEvent.agent) continue;
      if (STEP_TYPES.has(e.type)) break;
      if (e.type === "cypher" && (!selectedEvent.tool || e.tool === selectedEvent.tool)) out.unshift(e);
    }
    return out;
  }, [selectedEvent, run.events]);

  const liveHypotheses = useMemo(() => {
    const map = new Map<string, { label: string; check: string; status: string; agent?: string }>();
    for (const e of run.events) {
      if (e.type === "hypotheses_posted") {
        for (const h of (e as unknown as { hypotheses?: { label: string; check: string }[] }).hypotheses ?? []) {
          map.set(h.label, { label: h.label, check: h.check, status: "OPEN" });
        }
      }
      if (e.type === "lock_claimed" && e.hypothesis && map.has(e.hypothesis)) {
        map.set(e.hypothesis, { ...map.get(e.hypothesis)!, status: "CLAIMED", agent: e.agent });
      }
      if (e.type === "hypothesis_resolved" && e.hypothesis && map.has(e.hypothesis)) {
        map.set(e.hypothesis, { ...map.get(e.hypothesis)!, status: e.status ?? "DONE", agent: e.agent });
      }
    }
    return [...map.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [run.events]);

  const highlight = selectedEvent?.nodes ?? [];
  const tenantName = tenants.find((t) => t.id === tenant)?.company ?? tenant;
  const themeKey = theme === "system" ? (systemDark ? "dark" : "light") : theme;

  return (
    <div className="app">
      <TopBar health={health} healthError={healthError} tenants={tenants} tenant={tenant}
              onTenant={(t) => { setTenant(t); run.clear(); setSelected(null); setView("live"); }}
              pr={pr} onPr={setPr} mode={mode} onMode={setMode} running={run.status === "running"} onRun={startRun}
              onReset={reset} theme={theme} onTheme={setTheme} />
      <main className="main">
        <RunTimeline events={run.events} status={run.status} selected={selected} onSelect={setSelected}
                     recent={recent} onOpenRun={(id) => { setSelected(null); run.follow(id); }} error={run.error} />
        <GraphCanvas graph={graph} view={view} onView={setView} shadowAvailable={overlay.removed.length > 0 || overlay.islands.length > 0}
                     overlay={overlay} highlight={highlight} measure={run.result?.report.measure ?? null} themeKey={themeKey} />
        <section className="panel" aria-label="Proof and memory">
          <div className="panel-head">
            <div className="tabs" role="tablist">
              <button role="tab" aria-selected={tab === "proof"} onClick={() => setTab("proof")}>Proof</button>
              <button role="tab" aria-selected={tab === "memory"} onClick={() => setTab("memory")}>Memory</button>
            </div>
          </div>
          {run.events.some((e) => e.type === "llm_fallback") && tab === "proof" && (
            <div className="banner warn">
              {run.events.filter((e) => e.type === "llm_fallback").map((e) => e.summary).join(" · ")}
            </div>
          )}
          <div className="panel-body">
            {tab === "proof"
              ? <ProofPanel status={run.status} result={run.result} selected={selectedEvent} queries={queries}
                            liveHypotheses={liveHypotheses} />
              : <MemoryPanel memory={memory} tenantName={tenantName} />}
          </div>
        </section>
      </main>
      <ChatDrawer tenant={tenant} mode={mode} onAnswered={refreshTenantData} />
    </div>
  );
}
