import { useState } from "react";
import { CopyButton, Loading, Mark, Note, Section } from "../components/ui";
import { API_BASE } from "../lib/api";
import { n } from "../lib/format";
import { ha, type SymbolDetail } from "../lib/ha";

const TOOLS: [string, string][] = [
  ["ha_can_remove", "Before deleting or renaming a core name: is it safe today, and if not, who breaks (file:line)."],
  ["ha_check_edits", "Before committing: send the new content of the files you changed; get the impact report."],
  ["ha_who_uses", "Every core and custom integration that imports a name, with how it uses it."],
  ["ha_check_pr", "Impact report for any open or merged pull request on home-assistant/core."],
  ["ha_retirements", "Deprecated names, their removal release and the users left."],
  ["ha_draft_migration", "A patch for one integration that uses a removed or deprecated name (a draft, never posted)."],
  ["ha_search", "Find core names by partial name, sorted by how many integrations use them."],
];

const RULE = `Before you remove, rename or change the signature of anything under homeassistant/,
call the ShadowGraph tool ha_can_remove (or ha_check_edits with your new file contents).
If it reports integrations that would break, do not remove the name: deprecate it with a
removal version instead, and include the list of affected integrations in the PR description.`;

function TryIt() {
  const [q, setQ] = useState("homeassistant.components.sensor.EntityCategory");
  const [busy, setBusy] = useState(false);
  const [d, setD] = useState<SymbolDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="tryit">
      <form className="check-form" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true); setErr(null); setD(null);
        try { setD(await ha.symbol(q.trim())); } catch (x) { setErr((x as Error).message); }
        setBusy(false);
      }}>
        <input className="input mono" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Fully qualified name" />
        <button className="btn btn-primary" type="submit" disabled={busy}>ha_can_remove</button>
      </form>
      {busy && <Loading label="Asking the graph" />}
      {err && <Note tone="breaks">{err}</Note>}
      {d && (
        <div className="tryit-out">
          <Mark tone={d.can_remove ? "clear" : "breaks"} word={d.can_remove ? "can_remove: true" : "can_remove: false"} />
          <p>{d.can_remove ? "Safe to remove: no scanned integration would break." :
            `${n(d.summary.would_break)} integrations would break (${n(d.summary.would_break_external)} custom), e.g. ` +
            d.users.filter((u) => u.if_removed === "breaks").slice(0, 3).map((u) => `${u.name} (${u.uses[0]?.file.split("/").pop()}:${u.uses[0]?.line})`).join(", ") + "."}</p>
        </div>
      )}
    </div>
  );
}

export function ConnectPage() {
  const base = (API_BASE || window.location.origin).replace(/\/$/, "");
  const url = `${base}/mcp`;
  const claude = `claude mcp add --transport http shadowgraph ${url}`;
  const cursor = JSON.stringify({ mcpServers: { shadowgraph: { url } } }, null, 2);
  const stdio = JSON.stringify({ mcpServers: { shadowgraph: { command: "python", args: ["-m", "shadowgraph", "mcp"], env: { PYTHONPATH: "backend" } } } }, null, 2);
  return (
    <div className="page">
      <div className="pane-head">
        <h1 className="pane-title">Connect a coding agent</h1>
        <p className="pane-sub">AI coding agents refactor fast and cannot see who depends on the code outside the repository. Give them a pre-flight check: before deleting or renaming a core name, the agent asks ShadowGraph who would break.</p>
      </div>
      <Section title="1. Add the MCP server">
        <div className="snippets">
          <div className="snippet"><div className="snippet-head"><strong>Claude Code</strong><CopyButton text={claude} /></div><pre className="code">{claude}</pre></div>
          <div className="snippet"><div className="snippet-head"><strong>Cursor</strong> <span className="muted small">.cursor/mcp.json</span><CopyButton text={cursor} /></div><pre className="code">{cursor}</pre></div>
          <div className="snippet"><div className="snippet-head"><strong>Any client, stdio</strong> <span className="muted small">from the repo root</span><CopyButton text={stdio} /></div><pre className="code">{stdio}</pre></div>
        </div>
      </Section>
      <Section title="2. Tell the agent when to use it" aside={<CopyButton text={RULE} label="Copy rule" />}>
        <p className="muted">Put this in CLAUDE.md, AGENTS.md or your Cursor rules.</p>
        <pre className="code">{RULE}</pre>
      </Section>
      <Section title="3. What the agent gets">
        <table className="table">
          <tbody>{TOOLS.map(([t, d]) => <tr key={t}><td className="mono">{t}</td><td>{d}</td></tr>)}</tbody>
        </table>
        <p className="muted small">All tools are read-only. ShadowGraph never comments on, pushes to or opens pull requests in any repository.</p>
      </Section>
      <Section title="Try it here">
        <TryIt />
      </Section>
    </div>
  );
}
