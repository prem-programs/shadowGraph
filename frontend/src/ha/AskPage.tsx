import { ArrowUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Ext } from "../components/ui";
import { storage } from "../lib/api";
import { ha } from "../lib/ha";

interface Msg { role: "user" | "assistant"; text: string; sources?: { label: string; url: string }[] }

const SUGGESTIONS = [
  "Who still uses async_track_state_change?",
  "Is CONN_CLASS_CLOUD_POLL safe to remove?",
  "What uses EntityCategory from homeassistant.components.sensor?",
  "Was the RestoreState last_states removal announced?",
];

function render(text: string) {
  return text.split(/(`[^`]+`)/g).map((p, i) => (p.startsWith("`") ? <code key={i}>{p.slice(1, -1)}</code> : <span key={i}>{p}</span>));
}

export function AskPage() {
  const [session, setSession] = useState<string | null>(() => storage.get("sg.ha.session"));
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);

  const send = async (q: string) => {
    const message = q.trim();
    if (!message || busy) return;
    setMsgs((m) => [...m, { role: "user", text: message }]);
    setText("");
    setBusy(true);
    try {
      const r = await ha.ask(message, session);
      setSession(r.session_id);
      storage.set("sg.ha.session", r.session_id);
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, sources: r.sources }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `Could not answer: ${(e as Error).message}` }]);
    }
    setBusy(false);
  };

  return (
    <div className="page ask">
      <div className="pane-head">
        <h1 className="pane-title">Ask</h1>
        <p className="pane-sub">Answers come from the graph (names, users, deprecations), the developer blog, architecture decisions and earlier checks, with sources. Conversations are kept in this workspace's memory.</p>
      </div>
      <div className="ask-thread">
        {msgs.length === 0 && (
          <div className="ask-suggest">
            {SUGGESTIONS.map((s) => <button key={s} type="button" className="replay" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg msg-${m.role}`}>
            <div className="msg-text">{render(m.text)}</div>
            {m.sources && m.sources.length > 0 && (
              <ul className="msg-sources">
                {m.sources.map((s) => (
                  <li key={s.url}>{s.url.startsWith("#") ? <a href={s.url}>{s.label}</a> : <Ext href={s.url}>{s.label}</Ext>}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {busy && <div className="msg msg-assistant"><span className="dots" aria-hidden><i /><i /><i /></span></div>}
        <div ref={end} />
      </div>
      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about a core name, a deprecation or a past check" aria-label="Question" />
        <button className="btn btn-primary" type="submit" disabled={busy || !text.trim()} aria-label="Send"><ArrowUp size={14} /></button>
        {session && <button type="button" className="btn btn-ghost" onClick={() => { setSession(null); storage.set("sg.ha.session", null); setMsgs([]); }}>New</button>}
      </form>
    </div>
  );
}
