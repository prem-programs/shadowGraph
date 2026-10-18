import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { ServicesPage } from "./acme/ServicesPage";
import { AboutPage } from "./ha/AboutPage";
import { AskPage } from "./ha/AskPage";
import { BrokenPage } from "./ha/BrokenPage";
import { ChangesPage } from "./ha/ChangesPage";
import { ConnectPage } from "./ha/ConnectPage";
import { NamePage, NameSearch } from "./ha/NamePage";
import { RetirementsPage } from "./ha/RetirementsPage";
import { api } from "./lib/api";
import { n, shortDate } from "./lib/format";
import { ha, type Overview } from "./lib/ha";
import { go, href, useRoute } from "./lib/router";
import { ThemeContext, useTheme } from "./lib/theme";
import type { Tenant } from "./lib/types";

function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden className="logo">
      <circle cx="8" cy="11" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="14" cy="11" r="5.2" fill="currentColor" opacity="0.28" />
      <circle cx="14" cy="11" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="2 2" />
    </svg>
  );
}

const NAV: { key: string; label: string; count?: (o: Overview) => number | undefined }[] = [
  { key: "changes", label: "Pull requests", count: (o) => o.changes },
  { key: "retirements", label: "Retirements", count: (o) => o.retirements },
  { key: "broken", label: "Broken by core", count: (o) => (o.breaks_next ?? 0) + (o.already_broken ?? 0) },
  { key: "names", label: "Who uses…" },
  { key: "ask", label: "Ask" },
  { key: "agents", label: "Connect an agent" },
  { key: "about", label: "About the data" },
];

export default function App() {
  const route = useRoute();
  const theme = useTheme();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const workspace = route[0] || "ha";
  const page = route[1] || (workspace === "ha" ? "changes" : "");

  useEffect(() => {
    ha.overview().then(setOverview).catch(() => setOverview({ loaded: false }));
    api.tenants().then(setTenants).catch(() => setTenants([]));
  }, []);
  useEffect(() => {
    if (!route.length) go("ha", "changes");
  }, [route.length]);
  useEffect(() => {
    const titles: Record<string, string> = {
      changes: "Pull requests", retirements: "Retirements", broken: "Broken by core",
      names: "Who uses", name: "Who uses", ask: "Ask", agents: "Connect an agent", about: "About the data"
    };
    document.title = workspace === "ha" ? `${titles[page] ?? "ShadowGraph"} · ShadowGraph` : "Services sample · ShadowGraph";
  }, [workspace, page]);

  const haOff = overview && !overview.loaded;
  let body;
  if (workspace !== "ha") body = <ServicesPage tenant={workspace} />;
  else if (haOff) body = (
    <div className="page"><div className="pane-head"><h1 className="pane-title">The Home Assistant snapshot is not loaded</h1>
      <p className="pane-sub">Build it with <span className="mono">python -m shadowgraph ha-import</span> (see docs/HOME_ASSISTANT.md) or put <span className="mono">data/ha_snapshot.json.gz</span> in place, then restart.</p></div></div>
  );
  else if (page === "changes") body = <ChangesPage selected={route[2] ?? null} />;
  else if (page === "retirements") body = <RetirementsPage />;
  else if (page === "broken") body = <BrokenPage />;
  else if (page === "name" && route[2]) body = <NamePage id={route.slice(2).join("/")} />;
  else if (page === "names") body = (
    <div className="page"><div className="pane-head"><h1 className="pane-title">Who uses…</h1>
      <p className="pane-sub">Any core name: who imports it, where, and what would happen if it went away.</p></div><NameSearch autoFocus /></div>
  );
  else if (page === "ask") body = <AskPage />;
  else if (page === "agents") body = <ConnectPage />;
  else if (page === "about") body = <AboutPage o={overview} />;
  else body = <ChangesPage selected={null} />;

  const navKey = page === "name" ? "names" : page;
  const services = tenants.filter((t) => t.id !== "ha");
  return (
    <ThemeContext.Provider value={theme.effective}>
      <div className="app">
        <header className="topbar">
          <a className="brand" href={href("ha", "changes")}><Logo /><span>ShadowGraph</span></a>
          <label className="ws">
            <span className="sr-only">Workspace</span>
            <select value={workspace} onChange={(e) => (e.target.value === "ha" ? go("ha", "changes") : go(e.target.value))}>
              <option value="ha">home-assistant/core</option>
              {services.map((t) => <option key={t.id} value={t.id}>{t.company} (sample)</option>)}
            </select>
          </label>
          {workspace === "ha" && overview?.loaded && (
            <span className="topmeta mono">
              snapshot {overview.core_sha?.slice(0, 7)} · {shortDate(overview.core_date)} · {n((overview.core_integrations ?? 0) + (overview.external_integrations ?? 0))} integrations · read-only
            </span>
          )}
          <span className="spacer" />
          <button type="button" className="btn btn-ghost btn-icon" onClick={theme.cycle} aria-label="Switch light or dark theme" title="Theme">
            {theme.effective === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </header>
        <div className={`body ${workspace === "ha" ? "with-nav" : ""}`}>
          {workspace === "ha" && (
            <nav className="nav" aria-label="Sections">
              {NAV.map((item) => {
                const c = overview?.loaded && item.count ? item.count(overview) : undefined;
                return (
                  <a key={item.key} href={href("ha", item.key)} className={`nav-item ${navKey === item.key ? "on" : ""}`}
                    aria-current={navKey === item.key ? "page" : undefined}>
                    <span>{item.label}</span>
                    {c !== undefined && <span className="nav-count mono">{n(c)}</span>}
                  </a>
                );
              })}
              {overview?.loaded && overview.next_release && (
                <div className="nav-foot">
                  <div className="muted small">Next release</div>
                  <div className="mono">{overview.next_release} · {shortDate(overview.next_release_date)}</div>
                </div>
              )}
            </nav>
          )}
          <main className="main">{body}</main>
        </div>
      </div>
    </ThemeContext.Provider>
  );
}
