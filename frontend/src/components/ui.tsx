import { Check, Copy, ExternalLink } from "lucide-react";
import { useState, type ReactNode } from "react";
import { splitName } from "../lib/format";

export type Tone = "breaks" | "retires" | "review" | "clear" | "plain";

/** Outcome marker: a shape AND a word, so colour is never the only signal. */
export function Mark({ tone, word, size = "md" }: { tone: Tone; word?: string; size?: "sm" | "md" }) {
  return (
    <span className={`mark mark-${tone} mark-${size}`}>
      <span className="mark-shape" aria-hidden />
      {word && <span className="mark-word">{word}</span>}
    </span>
  );
}

export function Stat({ value, label, tone = "plain", sub }: { value: ReactNode; label: ReactNode; tone?: Tone; sub?: ReactNode }) {
  return (
    <div className={`stat stat-${tone}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Section({ title, aside, children, id }: { title: ReactNode; aside?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="section" id={id}>
      <header className="section-head">
        <h2>{title}</h2>
        {aside && <div className="section-aside">{aside}</div>}
      </header>
      {children}
    </section>
  );
}

export function SymbolName({ id, name, href, stacked }: { id: string; name?: string; href?: string; stacked?: boolean }) {
  const [mod0, leaf] = splitName(id, name);
  const mod = stacked ? mod0.replace(/\.$/, "") : mod0;
  const body = (
    <>
      <span className="sym-mod">{mod}</span>
      <span className="sym-leaf">{leaf}</span>
    </>
  );
  const cls = `sym ${stacked ? "stacked" : ""}`;
  return href ? <a className={cls} href={href}>{body}</a> : <span className={cls}>{body}</span>;
}

export function Ext({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a className={`ext ${className ?? ""}`} href={href} target="_blank" rel="noreferrer">
      {children}
      <ExternalLink size={11} aria-hidden className="ext-icon" />
    </a>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch { /* clipboard blocked */ }
      }}
    >
      {done ? <Check size={13} /> : <Copy size={13} />} {done ? "Copied" : label}
    </button>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="loading"><span className="dots" aria-hidden><i /><i /><i /></span>{label}</div>;
}

export function Note({ tone = "plain", children }: { tone?: Tone; children: ReactNode }) {
  return <div className={`note note-${tone}`}>{children}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode; count?: number }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value}
          className={`seg-item ${o.value === value ? "on" : ""}`} onClick={() => onChange(o.value)}>
          {o.label}{o.count !== undefined && <span className="seg-count">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Diff({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <pre className="diff">
      {lines.map((l, i) => {
        const cls = l.startsWith("+++") || l.startsWith("---") ? "diff-file" : l.startsWith("@@") ? "diff-hunk"
          : l.startsWith("+") ? "diff-add" : l.startsWith("-") ? "diff-del" : "";
        return <span key={i} className={`diff-line ${cls}`}>{l || " "}{"\n"}</span>;
      })}
    </pre>
  );
}
