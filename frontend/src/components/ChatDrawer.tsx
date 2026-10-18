import { ChevronDown, ChevronUp, MessageSquare, Plus, Send } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, storage } from "../lib/api";
import type { ChatMessage, SessionSummary } from "../lib/types";

interface Props {
  tenant: string;
  mode: string;
  onAnswered: () => void;
}

const SUGGESTIONS = ["Did we see a token-verify removal before?", "What is our retry policy?", "Who knows auth-service best?"];

export function ChatDrawer({ tenant, mode, onAnswered }: Props) {
  const key = `sg-session-${tenant}`;
  const [open, setOpen] = useState(() => storage.get("sg-chat-open") !== "0");
  const [sessionId, setSessionId] = useState<string | null>(() => storage.get(key));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const saved = storage.get(key);
    setSessionId(saved);
    setMessages([]);
    api.sessions(tenant).then(setSessions).catch(() => setSessions([]));
    if (saved) {
      api.session(tenant, saved).then(setMessages).catch(() => { setSessionId(null); storage.set(key, null); });
    }
  }, [tenant, key]);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [messages.length]);

  async function send(message: string) {
    const q = message.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", text: q }]);
    setText("");
    try {
      const reply = await api.chat(tenant, q, sessionId, mode);
      setSessionId(reply.session_id);
      storage.set(key, reply.session_id);
      setMessages((m) => [...m, { role: "assistant", text: reply.answer, sources: reply.sources }]);
      api.sessions(tenant).then(setSessions).catch(() => undefined);
      onAnswered();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(text);
  }

  function pickSession(id: string) {
    if (!id) return;
    setSessionId(id);
    storage.set(key, id);
    api.session(tenant, id).then(setMessages).catch(() => setMessages([]));
  }

  function newSession() {
    setSessionId(null);
    storage.set(key, null);
    setMessages([]);
  }

  return (
    <section className="chat" aria-label="Chat">
      <div className="chat-bar">
        <MessageSquare size={15} />
        <strong>Chat</strong>
        <span className="muted small">answers from past episodes, earlier chats and decisions in force · saved per session</span>
        <span style={{ flex: 1 }} />
        {open && sessions.length > 0 && (
          <select className="select" value={sessionId ?? ""} onChange={(e) => pickSession(e.target.value)} aria-label="Session">
            <option value="">Current: new session</option>
            {sessions.map((s) => <option key={s.id} value={s.id}>{s.title} ({s.messages})</option>)}
          </select>
        )}
        {open && <button className="btn btn-ghost" onClick={newSession}><Plus size={14} /> New</button>}
        <button className="btn btn-ghost" onClick={() => { setOpen(!open); storage.set("sg-chat-open", open ? "0" : "1"); }}
                aria-expanded={open}>
          {open ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
        </button>
      </div>
      {open && (
        <div className="chat-body">
          {messages.length > 0 ? (
            <div className="messages">
              {messages.map((m, i) => (
                <div key={m.id ?? i} className={`msg ${m.role}`}>
                  {m.text}
                  {m.sources && m.sources.length > 0 && (
                    <div className="src">{m.sources.map((s) => <span key={s} className="chip accent">{s}</span>)}</div>
                  )}
                </div>
              ))}
              <div ref={endRef} />
            </div>
          ) : (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {SUGGESTIONS.map((s) => <button key={s} className="btn" onClick={() => void send(s)}>{s}</button>)}
            </div>
          )}
          {error && <div className="banner bad" style={{ margin: 0 }}>{error}</div>}
          <form className="chat-form" onSubmit={onSubmit}>
            <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about services, decisions or past checks…"
                   aria-label="Message" maxLength={2000} />
            <button className="btn btn-primary" type="submit" disabled={busy || !text.trim()}>
              <Send size={14} /> {busy ? "…" : "Send"}
            </button>
          </form>
        </div>
      )}
    </section>
  );
}
