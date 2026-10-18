import { useMemo, useState } from "react";
import { Ext, Mark, Section, Stat, SymbolName } from "../components/ui";
import { date, ms, n, plural, shortDate } from "../lib/format";
import { LEVEL_WORD, type ImpactReport } from "../lib/ha";
import { href } from "../lib/router";
import { Consumers } from "./Consumers";
import { HowWeChecked } from "./HowWeChecked";

function daysUntil(d?: string): number | null {
  if (!d) return null;
  const t = Date.parse(`${d}T12:00:00Z`);
  return Number.isNaN(t) ? null : Math.round((t - Date.now()) / 86400000);
}

function Meta({ r }: { r: ImpactReport }) {
  const c = r.change;
  const when = c.state === "merged" ? `merged ${date(c.merged_at)}` : c.state === "open" ? "open" : c.state;
  return (
    <div className="report-meta mono">
      {c.number ? <span>#{c.number}</span> : <span>planned change</span>}
      <span>{when}</span>
      {c.author && <span>{c.author}</span>}
      <span>{plural(r.files.total, "file")}</span>
      {c.url && <Ext href={c.url}>GitHub</Ext>}
    </div>
  );
}

function Numbers({ r }: { r: ImpactReport }) {
  const v = r.verdict;
  const removed = r.api.removed.filter((s) => !s.consequence_of).length;
  const days = daysUntil(r.next_release.date);
  if (v.level === "retires") {
    const dep = r.api.deprecated;
    const deadline = dep.map((d) => d.dep_breaks_in).filter(Boolean).sort()[0];
    return (
      <div className="stats">
        <Stat tone="retires" value={n(v.counts.warns)} label="integrations still use it" />
        <Stat value={n(dep.length)} label={dep.length === 1 ? "name deprecated" : "names deprecated"} />
        <Stat value={deadline ? deadline.split(".").slice(0, 2).join(".") : "–"} label="stops working in" />
        <Stat value={n(r.reach.core + r.reach.external)} label="import from these modules" />
      </div>
    );
  }
  return (
    <div className="stats">
      <Stat tone={v.counts.external_breaks ? "breaks" : "plain"} value={n(v.counts.external_breaks)}
        label="custom integrations break" />
      <Stat tone={v.counts.core_breaks ? "breaks" : "plain"} value={n(v.counts.core_breaks)} label="core integrations break" />
      <Stat value={n(removed)} label="used names removed" sub={r.api.removed_unused_count ? `+${r.api.removed_unused_count} unused` : undefined} />
      <Stat value={r.next_release.version || "–"} label="ships in"
        sub={r.next_release.date ? `${shortDate(r.next_release.date)}${days !== null ? ` · ${days} days` : ""}` : undefined} />
    </div>
  );
}

function ApiChanges({ r }: { r: ImpactReport }) {
  const [showUnused, setShowUnused] = useState(false);
  const removed = r.api.removed.filter((s) => !s.consequence_of);
  const nothing = !removed.length && !r.api.deprecated.length && !r.api.signature.length;
  return (
    <>
      {nothing && (
        <p className="muted">
          No name that anyone imports changed. {r.api.checked ? `${n(r.api.checked)} used names in the changed modules were compared before and after.` : "No core Python module changed."}
        </p>
      )}
      <ul className="api">
        {removed.map((s) => (
          <li key={s.id} className="api-row">
            <span className="api-op api-op-del">removed</span>
            <SymbolName id={s.id} name={s.name} href={href("ha", "name", s.id)} />
            <span className="api-note">
              {s.reexport ? `re-export of ${s.origin}${s.in_all ? "" : ", never listed in __all__"}` : s.kind}
            </span>
            <span className="api-users mono">{n(s.users_external)} custom · {n(s.users_core)} core</span>
          </li>
        ))}
        {r.api.deprecated.map((s) => (
          <li key={s.id} className="api-row">
            <span className="api-op api-op-dep">deprecated</span>
            <SymbolName id={s.id} name={s.name} href={href("ha", "name", s.id)} />
            <span className="api-note">
              {s.dep_breaks_in ? `stops working in ${s.dep_breaks_in}` : "no removal date"}
              {s.dep_replacement ? ` · use ${s.dep_replacement}` : ""}
            </span>
            <span className="api-users mono">{n(s.users_external)} custom · {n(s.users_core)} core</span>
          </li>
        ))}
        {r.api.signature.map((s) => (
          <li key={s.id} className="api-row">
            <span className="api-op api-op-sig">signature</span>
            <SymbolName id={s.id} name={s.name} href={href("ha", "name", s.id)} />
            <span className="api-note">{(s.changes ?? []).join("; ")}</span>
            <span className="api-users mono">{n(s.users_external)} custom · {n(s.users_core)} core</span>
          </li>
        ))}
      </ul>
      {r.api.removed_unused_count > 0 && (
        <div className="api-unused">
          <button type="button" className="linkish" onClick={() => setShowUnused((v) => !v)}>
            {plural(r.api.removed_unused_count, "other name")} removed that no scanned integration imports
          </button>
          {showUnused && (
            <ul className="mono small">{r.api.removed_unused.map((x) => <li key={x}>{x}</li>)}</ul>
          )}
        </div>
      )}
    </>
  );
}

function Timeline({ r }: { r: ImpactReport }) {
  const points = useMemo(() => {
    const pts: { key: string; label: string; date: string; note: string; tone: string }[] = [
      { key: "now", label: "Today", date: new Date().toISOString().slice(0, 10), note: r.change.state === "open" ? "pull request open" : "change on the dev branch", tone: "plain" },
    ];
    if (r.next_release.version) {
      const breaks = r.verdict.counts.external_breaks;
      pts.push({
        key: r.next_release.version, label: r.next_release.version, date: r.next_release.date,
        note: breaks && r.verdict.level === "breaks" ? `${plural(breaks, "custom integration")} stop loading` :
          r.verdict.level === "retires" ? "deprecation warnings start" : "next release", tone: r.verdict.level === "breaks" ? "breaks" : "plain",
      });
    }
    const seen = new Set(pts.map((p) => p.key));
    for (const d of r.api.deprecated) {
      const v = d.dep_breaks_in?.split(".").slice(0, 2).join(".");
      if (v && !seen.has(v)) {
        seen.add(v);
        const [y, m] = v.split(".").map(Number);
        const first = new Date(Date.UTC(y, m - 1, 1));
        first.setUTCDate(1 + ((3 - first.getUTCDay() + 7) % 7));
        pts.push({ key: v, label: v, date: first.toISOString().slice(0, 10), note: `${d.name} stops working`, tone: "retires" });
      }
    }
    return pts;
  }, [r]);
  return (
    <ol className="timeline">
      {points.map((p) => (
        <li key={p.key} className={`tl tl-${p.tone}`}>
          <span className="tl-dot" aria-hidden />
          <span className="tl-label mono">{p.label}</span>
          <span className="tl-date">{shortDate(p.date)}</span>
          <span className="tl-note">{p.note}</span>
        </li>
      ))}
    </ol>
  );
}

function Context({ r }: { r: ImpactReport }) {
  const big = r.touched.filter((t) => t.dependents_core + t.dependents_external > 0);
  return (
    <div className="context-grid">
      <div>
        <h3>Announced</h3>
        {r.posts.length ? (
          <ul className="plain-list">
            {r.posts.map((p) => (
              <li key={p.id}>
                <Ext href={p.url}>{p.title}</Ext>
                <div className="muted small">{date(p.date)} · developer blog{p.author ? ` · ${p.author}` : ""}</div>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No developer-blog post names these symbols.</p>}
      </div>
      <div>
        <h3>Reviewers</h3>
        {r.owners.length ? (
          <ul className="plain-list mono">{r.owners.slice(0, 8).map((o) => <li key={o}>@{o}</li>)}</ul>
        ) : <p className="muted">No code owners for the touched paths.</p>}
        <p className="muted small">From CODEOWNERS and integration manifests.</p>
      </div>
      <div>
        <h3>Reach</h3>
        <p>
          <span className="mono">{n(r.reach.core)}</span> core and <span className="mono">{n(r.reach.external)}</span> custom
          integrations import from the modules this change touches.
        </p>
        {big.length > 0 && (
          <ul className="plain-list small">
            {big.slice(0, 4).map((t) => (
              <li key={t.domain}><span className="mono">{t.domain}</span> is a manifest dependency of {n(t.dependents_core + t.dependents_external)} integrations</li>
            ))}
          </ul>
        )}
        {r.cascade.length > 0 && (
          <p className="small"><strong>{plural(r.cascade.length, "more integration")}</strong> depend on one that would fail to set up.</p>
        )}
      </div>
    </div>
  );
}

export function Report({ r }: { r: ImpactReport }) {
  const v = r.verdict;
  const breaking = r.consumers.filter((c) => c.level !== "safe").length;
  return (
    <article className="report">
      <Meta r={r} />
      <h1 className="report-title">{r.change.title}</h1>
      {r.change.title_is_commit && <p className="muted small">Title is the latest commit subject (no GitHub API access configured).</p>}
      <div className={`verdict verdict-${v.level}`}>
        <Mark tone={v.level} word={LEVEL_WORD[v.level]} />
        <p className="verdict-headline">{v.headline}</p>
        {v.reasons.length > 0 && <ul className="verdict-reasons">{v.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
      </div>
      <Numbers r={r} />
      <Section title="Who is affected" aside={r.consumers.length ? <span className="muted mono">{plural(breaking, "integration")} need action</span> : undefined}>
        <Consumers consumers={r.consumers}
          emptyText="Nobody we scanned imports anything this change removes or deprecates." />
      </Section>
      <Section title="What changes in the API" aside={<span className="muted mono">{r.modules.length} core modules</span>}>
        <ApiChanges r={r} />
      </Section>
      <Section title="When">
        <Timeline r={r} />
        <p className="muted small">Home Assistant ships x.y.0 on the first Wednesday of the month; future dates are expected, not announced.</p>
      </Section>
      <Section title="Context">
        <Context r={r} />
      </Section>
      <HowWeChecked r={r} />
      <footer className="report-foot muted small">
        Checked in {ms(r.ms)} · base <span className="mono">{r.change.base_sha?.slice(0, 10)}</span> · read-only: nothing was posted to GitHub.
      </footer>
    </article>
  );
}
