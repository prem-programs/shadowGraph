import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Empty, Ext, Loading, Mark, Note, Stat } from "../components/ui";
import { date, fileName, n, plural, shortDate } from "../lib/format";
import { CONTEXT_WORD, USE_WORD, ha, useTone, type RetirementCard, type SymbolUser } from "../lib/ha";
import { href } from "../lib/router";
import { DraftPanel } from "./DraftPanel";

const STATUS: Record<RetirementCard["status"], { tone: "clear" | "retires" | "breaks"; word: string }> = {
  ready: { tone: "clear", word: "Ready" },
  migrating: { tone: "retires", word: "Migrating" },
  overdue: { tone: "breaks", word: "Overdue" },
};

function Users({ card }: { card: RetirementCard }) {
  const [users, setUsers] = useState<SymbolUser[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ symbol: string; integration: string } | null>(null);
  useEffect(() => {
    ha.retirementUsers(card.symbols).then(setUsers).catch((e) => setErr(String(e.message ?? e)));
  }, [card.symbols]);
  if (err) return <Note tone="breaks">{err}</Note>;
  if (!users) return <Loading label="Loading users" />;
  if (!users.length) return <p className="muted">No scanned integration uses {card.names.length > 1 ? "these names" : "this name"}. It can go.</p>;
  return (
    <div className="ret-users">
      <table className="sites">
        <thead><tr><th>Integration</th><th>Where</th><th>When it runs</th><th>On removal</th><th></th></tr></thead>
        <tbody>
          {users.map((u) => {
            const top = u.uses[0];
            return (
              <tr key={u.id}>
                <td>
                  <div>{u.name}</div>
                  <div className="mono muted small">{u.kind === "core" ? `core · ${u.domain}` : u.repo}</div>
                </td>
                <td><Ext href={top.url} className="mono">{fileName(top.file)}:{top.line}</Ext>
                  {u.uses.length > 1 && <span className="muted small"> +{u.uses.length - 1}</span>}</td>
                <td className="mono">{CONTEXT_WORD[top.context]}</td>
                <td><Mark tone={useTone(u.if_removed)} word={USE_WORD[u.if_removed]} size="sm" /></td>
                <td>
                  {u.if_removed !== "safe" && top.symbol && (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft({ symbol: top.symbol!, integration: u.id })}>
                      <Wrench size={12} /> Draft fix
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {draft && <DraftPanel symbol={draft.symbol} integration={draft.integration} onClose={() => setDraft(null)} />}
    </div>
  );
}

function CardRow({ c }: { c: RetirementCard }) {
  const [open, setOpen] = useState(false);
  const st = STATUS[c.status];
  return (
    <li className={`ret ${open ? "open" : ""}`}>
      <button type="button" className="ret-main" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="crow-caret" aria-hidden>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span className="ret-name">
          <span className="mono ret-family">{c.family}</span>
          <span className="mono muted small">{c.module}</span>
        </span>
        <span className="ret-count">
          <span className={`mono ret-num ${c.blocking ? "" : "zero"}`}>{n(c.blocking)}</span>
          <span className="muted small">still {c.blocking === 1 ? "uses it" : "use it"}</span>
        </span>
        <span className="ret-split mono small muted">{n(c.users_external)} custom · {n(c.users_core)} core</span>
        <span className="ret-status"><Mark tone={st.tone} word={st.word} size="sm" /></span>
      </button>
      {open && (
        <div className="ret-detail">
          <dl className="facts">
            <div><dt>Condition</dt><dd>{c.condition}: <strong>{c.ready ? "met" : `${plural(c.blocking, "integration")} left`}</strong></dd></div>
            {c.replacements.length > 0 && <div><dt>Use instead</dt><dd className="mono">{c.replacements.join(", ")}</dd></div>}
            {!c.replacements.length && <div><dt>Use instead</dt><dd>Nothing: core deprecates it without a replacement.</dd></div>}
            <div><dt>Deprecated</dt><dd>{c.deprecated_in ? `since ${c.deprecated_in}` : "–"} · <Ext href={`https://github.com/home-assistant/core/blob/dev/${c.path}#L${c.line}`} className="mono">{fileName(c.path)}:{c.line}</Ext></dd></div>
            {c.posts.length > 0 && <div><dt>Announced</dt><dd>{c.posts.map((p) => <Ext key={p.id} href={p.url}>{p.title}</Ext>)}</dd></div>}
            {c.names.length > 1 && <div><dt>Names</dt><dd className="mono small">{c.names.join(", ")}</dd></div>}
          </dl>
          <Users card={c} />
        </div>
      )}
    </li>
  );
}

export function RetirementsPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof ha.retirements>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { ha.retirements().then(setData).catch((e) => setErr(String(e.message ?? e))); }, []);
  const groups = useMemo(() => {
    const g = new Map<string, RetirementCard[]>();
    for (const c of data?.cards ?? []) {
      const key = c.breaks_in ? c.breaks_in.split(".").slice(0, 2).join(".") : "no date";
      g.set(key, [...(g.get(key) ?? []), c]);
    }
    return [...g.entries()];
  }, [data]);
  if (err) return <div className="page"><Note tone="breaks">{err}</Note></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const blocking = data.cards.reduce((a, c) => a + c.blocking, 0);
  const overdue = data.cards.filter((c) => (c.days_left ?? 0) < 0).length;
  return (
    <div className="page">
      <div className="pane-head">
        <h1 className="pane-title">Retirements</h1>
        <p className="pane-sub">Every deprecated name in core, the release it stops working in, and who still uses it. A retirement is done when its condition is met: no integration imports it at load or run time.</p>
      </div>
      <div className="stats">
        <Stat value={n(data.totals.families)} label="retirements in progress" sub={`${n(data.totals.names)} names`} />
        <Stat tone="retires" value={n(blocking)} label="integration migrations left" />
        <Stat tone="clear" value={n(data.totals.ready)} label="ready to remove now" />
        <Stat tone={overdue ? "breaks" : "plain"} value={n(overdue)} label="past their removal release" sub={overdue ? "still in core" : undefined} />
      </div>
      {groups.length === 0 && <Empty title="Nothing is deprecated" />}
      {groups.map(([version, cards]) => {
        const first = cards[0];
        const days = first.days_left;
        return (
          <section key={version} className="ret-group">
            <header className="ret-group-head">
              <span className="mono ret-version">{version}</span>
              <span className="muted">{first.release_date ? `${shortDate(first.release_date)} ${new Date(first.release_date).getFullYear()}` : "no removal release set"}</span>
              {days !== null && <span className={`ret-days ${days < 0 ? "late" : ""}`}>{days < 0 ? `${-days} days ago` : `in ${days} days`}</span>}
            </header>
            <ul className="rets">{cards.map((c) => <CardRow key={c.id} c={c} />)}</ul>
          </section>
        );
      })}
      <p className="muted small">Release dates follow Home Assistant's schedule (first Wednesday of the month) and are expectations. Snapshot of {date(new Date().toISOString())}; see <a href={href("ha", "about")}>About the data</a>.</p>
    </div>
  );
}
