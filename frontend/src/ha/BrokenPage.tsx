import { useEffect, useMemo, useState } from "react";
import { Ext, Loading, Note, Segmented, Stat, SymbolName } from "../components/ui";
import { date, fileName, n, shortDate } from "../lib/format";
import { ha, type BrokenItem } from "../lib/ha";
import { href } from "../lib/router";

type Tab = "next" | "now";

interface Row {
  integration: BrokenItem["integration"];
  items: BrokenItem[];
}

function group(items: BrokenItem[]): Row[] {
  const m = new Map<string, Row>();
  for (const it of items) {
    const r = m.get(it.integration.id) ?? { integration: it.integration, items: [] };
    r.items.push(it);
    m.set(it.integration.id, r);
  }
  return [...m.values()].sort((a, b) => (b.integration.last_commit || "").localeCompare(a.integration.last_commit || ""));
}

export function BrokenPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof ha.broken>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("next");
  useEffect(() => { ha.broken().then(setData).catch((e) => setErr(String(e.message ?? e))); }, []);
  const next = useMemo(() => group((data?.items ?? []).filter((i) => !i.released)), [data]);
  const now = useMemo(() => group((data?.items ?? []).filter((i) => i.released)), [data]);
  if (err) return <div className="page"><Note tone="breaks">{err}</Note></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const rows = tab === "next" ? next : now;
  const recent = (tab === "next" ? next : now).filter((r) => (r.integration.last_commit || "") >= "2026-07-01").length;
  return (
    <div className="page">
      <div className="pane-head">
        <h1 className="pane-title">Broken by core</h1>
        <p className="pane-sub">Custom integrations that import a name core no longer has, at a place that runs on import or at run time. Fallbacks and type hints are excluded.</p>
      </div>
      <div className="stats">
        <Stat tone="breaks" value={n(next.length)} label={`stop loading in ${data.next_release.version}`} sub={data.next_release.date ? `${shortDate(data.next_release.date)} · unless fixed first` : undefined} />
        <Stat tone="breaks" value={n(now.length)} label="already fail on the current release" />
        <Stat value={n(recent)} label="of these were updated since July" sub="maintained, not abandoned" />
      </div>
      <Segmented<Tab> label="Which group" value={tab} onChange={setTab} options={[
        { value: "next", label: `Break in ${data.next_release.version}`, count: next.length },
        { value: "now", label: "Already broken", count: now.length },
      ]} />
      <table className="table broken">
        <thead>
          <tr><th>Integration</th><th>Imports</th><th>Removed in</th><th>Where</th><th>What happens</th><th>Updated</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const it = r.items[0];
            return (
              <tr key={r.integration.id}>
                <td>
                  <div>{r.integration.name}</div>
                  <Ext href={r.integration.url} className="mono muted small">{r.integration.repo}</Ext>
                </td>
                <td>
                  <SymbolName id={it.symbol} href={href("ha", "name", it.symbol)} stacked />
                  {r.items.length > 1 && <div className="muted small">+{r.items.length - 1} more</div>}
                </td>
                <td className="mono">{it.removed_in || (it.never_existed ? "before 2025.10" : "–")}</td>
                <td><Ext href={it.url} className="mono">{fileName(it.file)}:{it.line}</Ext></td>
                <td>{it.effect}</td>
                <td className="mono small">{date(r.integration.last_commit)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
