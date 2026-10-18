import { Ext, Section, Stat } from "../components/ui";
import { date, n } from "../lib/format";
import type { Overview } from "../lib/ha";

export function AboutPage({ o }: { o: Overview | null }) {
  return (
    <div className="page prose">
      <div className="pane-head">
        <h1 className="pane-title">About the data</h1>
        <p className="pane-sub">What ShadowGraph knows about Home Assistant, where it came from, and what it cannot see.</p>
      </div>
      {o?.loaded && (
        <div className="stats">
          <Stat value={n(o.core_integrations)} label="core integrations" />
          <Stat value={n(o.external_integrations)} label="HACS custom integrations" sub={`${n(o.external_repos)} repositories`} />
          <Stat value={n(o.uses)} label="import sites" />
          <Stat value={n(o.symbols)} label="core names in use" />
        </div>
      )}
      <Section title="Sources">
        <ul>
          <li><Ext href="https://github.com/home-assistant/core">home-assistant/core</Ext> at commit <span className="mono">{o?.core_sha?.slice(0, 10)}</span> ({date(o?.core_date)}), plus the thirteen monthly releases from 2025.10 to 2026.10 for history.</li>
          <li>Every integration in the <Ext href="https://github.com/hacs/default">HACS default list</Ext>, cloned at its latest commit (Python files and manifests only).</li>
          <li>The <Ext href="https://developers.home-assistant.io/blog">developer blog</Ext> (deprecation announcements) and the <Ext href="https://github.com/home-assistant/architecture">architecture decisions</Ext>.</li>
          <li>CODEOWNERS and each integration's manifest (owners and dependencies).</li>
        </ul>
      </Section>
      <Section title="How a use is classified">
        <ul>
          <li><strong>on import</strong>: module level or a class body; a missing name fails the import, so the integration (or that platform) does not load.</li>
          <li><strong>at run time</strong>: inside a function; fails when that code path runs.</li>
          <li><strong>conditional</strong>: inside a module-level <span className="mono">if</span>, often a version check; may fail.</li>
          <li><strong>has fallback</strong>: inside <span className="mono">try/except ImportError</span> (or similar); keeps working.</li>
          <li><strong>type hint</strong>: annotations only; no runtime effect, because annotations are lazy in Python 3.14.</li>
        </ul>
        <p>Whether an integration "fails to set up" or "its sensor platform fails to load" comes from following its own imports from <span className="mono">__init__.py</span> and from each platform file.</p>
      </Section>
      <Section title="Correctness checks">
        <ul>
          <li>Names are resolved statically, following re-exports, aliases, star imports and base classes. When that cannot prove an answer (a third-party base class, a module-level <span className="mono">__getattr__</span>), the name is left out instead of guessed.</li>
          <li>Self-test: every one of the core integrations' imports resolves in core itself (0 false "missing").</li>
          <li>Each agent run recomputes the breakage inside a shadow copy of the graph and compares it with the engine's count.</li>
        </ul>
      </Section>
      <Section title="What it cannot see">
        <ul>
          <li>Custom integrations outside the HACS default list, and code that is never imported (dead files still count).</li>
          <li>Attribute access on instances (for example <span className="mono">entity.some_removed_method()</span>); only names reached through imports are tracked.</li>
          <li>How many people run each integration: install counts are not in this snapshot.</li>
          <li>Future release dates are expectations from Home Assistant's monthly schedule.</li>
        </ul>
      </Section>
      <p className="muted small">Read-only by design: ShadowGraph never posts comments, statuses or pull requests to Home Assistant or to any custom integration.</p>
    </div>
  );
}
