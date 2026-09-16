import type { Message } from "../lib/protocol";

export function Conversation({
  messages,
  partial,
  phase,
}: {
  messages: Message[];
  partial: string;
  phase: string;
}) {
  const showPartial = phase === "listening" && partial;
  return (
    <section className="glass conversation" aria-label="Conversation">
      <h3>Conversation</h3>
      {messages.length === 0 && !showPartial && (
        <p className="empty">“Ask Mysa anything.”</p>
      )}
      {showPartial && (
        <div className="msg user">
          <span className="who">You</span>
          <p className="partial-text">“{partial}”</p>
        </div>
      )}
      {messages.map((m, i) => (
        <div key={i} className={`msg ${m.role}`}>
          <span className="who">{m.role === "user" ? "You" : "Mysa"}</span>
          <p>
            {m.text}
            {!m.final && <span className="caret" aria-hidden="true" />}
          </p>
        </div>
      ))}
    </section>
  );
}
