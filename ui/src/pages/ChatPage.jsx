import { useState, useRef, useEffect } from "react";
import { api } from "../lib/api.js";

// Web channel into the orchestrator — same brain the email channel uses.
export default function ChatPage() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const { reply } = await api.post("/chat", { message: text, thread_key: "web" });
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <p className="hint">Ask JARVIS anything. Try “what’s AAPL trading at?”</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">{m.content}</div>
          </div>
        ))}
        {busy && <div className="msg assistant"><div className="bubble">…</div></div>}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Message JARVIS…"
          autoFocus
        />
        <button type="submit" disabled={busy}>Send</button>
      </form>
    </div>
  );
}
