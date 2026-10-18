import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Ext, Loading, Mark, Note, Section, Stat, SymbolName } from "../components/ui";
import { date, fileName, n } from "../lib/format";
import { CONTEXT_WORD, USE_WORD, ha, useTone, type SymbolDetail } from "../lib/ha";
import { go, href } from "../lib/router";
import { DraftPanel } from "./DraftPanel";

export function NameSearch({ autoFocus = false }: { autoFocus?: boolean }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Awaited<ReturnType<typeof ha.search>>>([]);
  useEffect(() => {
    if (q.trim().length < 3) { setHits([]); return; }
    const t = setTimeout(() => ha.search(q.trim()).then(setHits).catch(() => setHits([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="namesearch">
      <label className="namesearch-box">
        <Search size={14} aria-hidden />
        <input className="input bare" value={q} onChange={(e) => setQ(e.target.value)} autoFocus={autoFocus}
          placeholder="Who uses… e.g. EntityCategory, async_track_state_change, CONN_CLASS" aria-label="Search core names" />
      </label>
      {hits.length > 0 && (
        <ul className="namesearch-hits">
          {hits.map((h) => (
            <li key={h.id}>
              <button type="button" onClick={() => { setQ(""); setHits([]); go("ha", "name", h.id); }}>
                <SymbolName id={h.id} name={h.name} />
                <span className="mono muted small">{n(h.users_core + h.users_external)} users{h.state === "missing" ? " · removed" : h.deprecated ? " · deprecated" : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function NamePage({ id }: { id: string }) {
  const [d, setD] = useState<SymbolDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  useEffect(() => {
    setD(null); setErr(null);
    ha.symbol(id).then(setD).catch((e) => setErr(String(e.message ?? e)));
  }, [id]);
  if (err) return <div className="page"><NameSearch /><Note tone="breaks">{err}</Note></div>;
  if (!d) return <div className="page"><Loading /></div>;
  const s = d.symbol;
  const missing = s.state === "missing";
  const tone = missing ? "breaks" : d.can_remove ? "clear" : "retires";
  const word = missing ? "Removed" : d.can_remove ? "Safe to remove" : "In use";
  return (
    <div className="page">
      <NameSearch />
      <div className="report-meta mono">
        <span>{s.state === "missing" ? "removed name" : String(s.kind || "name")}</span>
        {s.path && Number(s.line) > 0 && s.state !== "missing" && (
          <Ext href={`https://github.com/home-assistant/core/blob/dev/${s.path}#L${s.line}`}>{fileName(String(s.path))}:{String(s.line)}</Ext>
        )}
      </div>
      <h1 className="report-title"><SymbolName id={s.id} name={String(s.name)} /></h1>
      <div className={`verdict verdict-${tone}`}>
        <Mark tone={tone} word={word} />
        <p className="verdict-headline">
          {missing ? `No longer exists in core${s.removed_in ? ` (removed in ${s.removed_in})` : ""}; ${n(d.summary.would_break_external)} custom integrations still import it in a way that fails.`
            : d.can_remove ? "Removing it today breaks no integration we scanned: every user has a fallback or only uses it as a type hint."
              : `Removing it today breaks ${n(d.summary.would_break)} integrations (${n(d.summary.would_break_external)} custom).`}
        </p>
        {s.deprecated && <ul className="verdict-reasons"><li>Deprecated{s.deprecated_in ? ` since ${s.deprecated_in}` : ""}{s.breaks_in ? `, stops working in ${s.breaks_in}` : ""}{s.replacement ? `; use ${s.replacement}` : ""}.</li></ul>}
      </div>
      <div className="stats">
        <Stat value={n(d.summary.external)} label="custom integrations use it" />
        <Stat value={n(d.summary.core)} label="core integrations use it" />
        <Stat tone={d.summary.would_break ? "breaks" : "clear"} value={n(d.summary.would_break)} label="would break on removal" />
        <Stat value={n(d.summary.fallback_or_type_only)} label="have a fallback or type-hint use" />
      </div>
      {d.alternatives.length > 0 && (
        <p className="muted">Also exported by: {d.alternatives.map((a, i) => (
          <span key={a.id}>{i > 0 && ", "}<a href={href("ha", "name", a.id)} className="mono">{a.module}</a></span>
        ))}</p>
      )}
      <Section title="Users">
        <table className="table">
          <thead><tr><th>Integration</th><th>Where</th><th>When it runs</th><th>If removed</th><th>Updated</th><th></th></tr></thead>
          <tbody>
            {d.users.map((u) => (
              <tr key={u.id}>
                <td><div>{u.name}</div><div className="mono muted small">{u.kind === "core" ? `core · ${u.domain}` : u.repo}</div></td>
                <td><Ext href={u.uses[0]?.url ?? u.url} className="mono">{fileName(u.uses[0]?.file ?? "")}:{u.uses[0]?.line}</Ext>
                  {u.uses.length > 1 && <span className="muted small"> +{u.uses.length - 1}</span>}</td>
                <td className="mono">{CONTEXT_WORD[u.uses[0]?.context ?? u.context]}</td>
                <td><Mark tone={useTone(u.if_removed)} word={USE_WORD[u.if_removed]} size="sm" /></td>
                <td className="mono small">{u.kind === "external" ? date(u.last_commit) : ""}</td>
                <td>{u.if_removed !== "safe" && u.kind === "external" && (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(u.id)}>Draft fix</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {draft && <DraftPanel symbol={s.id} integration={draft} onClose={() => setDraft(null)} />}
      </Section>
      {d.posts.length > 0 && (
        <Section title="Announced">
          <ul className="plain-list">{d.posts.map((p) => <li key={p.id}><Ext href={p.url}>{p.title}</Ext> <span className="muted small">{date(p.date)}</span></li>)}</ul>
        </Section>
      )}
    </div>
  );
}
