import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { CopyButton, Diff, Ext, Loading, Note } from "../components/ui";
import { ha, type Draft } from "../lib/ha";

export function DraftPanel({ symbol, integration, onClose }: { symbol: string; integration: string; onClose: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setDraft(null);
    setError(null);
    ha.draft(symbol, integration).then((d) => live && setDraft(d)).catch((e) => live && setError(String(e.message ?? e)));
    return () => { live = false; };
  }, [symbol, integration]);
  return (
    <div className="draft">
      <div className="draft-head">
        <strong>Draft fix</strong>
        <span className="muted">for {integration.replace(/^hacs:/, "")}</span>
        <button type="button" className="btn btn-ghost btn-sm draft-close" onClick={onClose} aria-label="Close draft"><X size={14} /></button>
      </div>
      {!draft && !error && <Loading label="Fetching the file and drafting a patch" />}
      {error && <Note tone="breaks">{error}</Note>}
      {draft && (
        <>
          {draft.notes.length > 0 && (
            <ul className="draft-notes">{draft.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          )}
          {draft.patches.length === 0 && <Note>No automatic patch for this one; the notes above say what to change.</Note>}
          {draft.patches.map((p) => (
            <div key={p.file} className="draft-patch">
              <div className="draft-patch-head">
                <Ext href={p.url} className="mono">{p.file}</Ext>
                <CopyButton text={p.diff} label="Copy patch" />
              </div>
              <Diff text={p.diff} />
            </div>
          ))}
          <p className="draft-disclaimer">{draft.disclaimer}</p>
        </>
      )}
    </div>
  );
}
