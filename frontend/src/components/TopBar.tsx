import { Moon, Play, RotateCcw, Sun, SunMoon } from "lucide-react";
import type { Health, Tenant } from "../lib/types";

export type Theme = "system" | "light" | "dark";

interface Props {
  health: Health | null;
  healthError: string | null;
  tenants: Tenant[];
  tenant: string;
  onTenant: (t: string) => void;
  pr: number | null;
  onPr: (n: number) => void;
  mode: "rules" | "llm";
  onMode: (m: "rules" | "llm") => void;
  running: boolean;
  onRun: () => void;
  onReset: () => void;
  theme: Theme;
  onTheme: (t: Theme) => void;
}

const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };

export function TopBar(p: Props) {
  const prs = p.tenants.find((t) => t.id === p.tenant)?.prs ?? [];
  const ThemeIcon = p.theme === "light" ? Sun : p.theme === "dark" ? Moon : SunMoon;
  return (
    <header className="topbar">
      <div className="brand">
        <img src="/favicon.svg" alt="" />
        ShadowGraph
      </div>
      <label className="field">
        <span className="sr-only">Tenant</span>
        <select className="select" value={p.tenant} onChange={(e) => p.onTenant(e.target.value)} aria-label="Tenant">
          {p.tenants.map((t) => <option key={t.id} value={t.id}>{t.company}</option>)}
        </select>
      </label>
      <label className="field">
        <span className="sr-only">Pull request</span>
        <select className="select" value={p.pr ?? ""} onChange={(e) => p.onPr(Number(e.target.value))}
                aria-label="Pull request" disabled={!prs.length}>
          {prs.map((pr) => <option key={pr.number} value={pr.number}>PR #{pr.number}: {pr.title}</option>)}
        </select>
      </label>
      <div className="seg" role="group" aria-label="Mode">
        <button aria-pressed={p.mode === "rules"} onClick={() => p.onMode("rules")}>Rules</button>
        <button aria-pressed={p.mode === "llm"} onClick={() => p.onMode("llm")} disabled={!p.health?.llm}
                title={p.health?.llm ? `Gemini plans the checks and writes the summary (${p.health.llm_model})`
                  : "Set GEMINI_API_KEY to enable"}>LLM</button>
      </div>
      <button className="btn btn-primary" onClick={p.onRun} disabled={p.running || p.pr === null || !p.health?.ok}>
        <Play size={14} /> {p.running ? "Running…" : "Run check"}
      </button>
      <div className="grow" />
      <div className="health" title={p.healthError ?? p.health?.falkordb ?? ""}>
        <span className={`status-dot ${p.health?.ok ? "ok" : "bad"}`} />
        {p.health?.ok ? "FalkorDB connected" : p.healthError ? "FalkorDB unreachable" : "Connecting…"}
        {p.health?.github ? <span className="chip">GitHub: {p.health.github}</span>
          : p.health?.ok ? <span className="chip" title="Set GITHUB_TOKEN and GITHUB_REPO">GitHub off</span> : null}
      </div>
      <button className="btn btn-ghost" onClick={p.onReset} title="Rebuild this tenant's graph and clear its memory">
        <RotateCcw size={14} /> Reset demo
      </button>
      <button className="btn btn-ghost" onClick={() => p.onTheme(NEXT[p.theme])} aria-label={`Theme: ${p.theme}`}
              title={`Theme: ${p.theme}`}>
        <ThemeIcon size={15} />
      </button>
    </header>
  );
}
