import { useEffect, useRef, useState } from "react";
import type { ChatMessage, Session } from "../types";
import { Rich } from "./Rich";

/** Stable colour per person, so the same friend is the same colour everywhere. */
export function authorColor(name: string): string {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return `hsl(${hash} 55% 34%)`;
}

function Bubble({ message, meId }: { message: ChatMessage; meId: string | null }) {
  if (message.kind === "system") {
    return <div className="sysmsg">{message.text}</div>;
  }
  if (message.kind === "agent") {
    return (
      <div className="msg theirs">
        <span className="author agent-author">Agente</span>
        <div className="bubble agent">
          <Rich text={message.text} />
        </div>
      </div>
    );
  }

  const mine = message.authorId === meId;
  return (
    <div className={`msg ${mine ? "mine" : "theirs"}`}>
      {!mine && (
        <span className="author" style={{ color: authorColor(message.authorName) }}>
          {message.authorName}
        </span>
      )}
      <div className="bubble user">
        {message.images.map((src, i) => (
          <img key={i} src={src} alt="" className="attach" />
        ))}
        {message.text && <Rich text={message.text} />}
      </div>
    </div>
  );
}

interface Props {
  session: Session;
  /** Whose screen this is: their own messages go on the right. */
  meId: string | null;
}

export function GroupChat({ session, meId }: Props) {
  const end = useRef<HTMLDivElement>(null);
  const hasMessage = session.chat.some((message) => message.kind === "user");
  const [welcome, setWelcome] = useState(!hasMessage);
  const host = session.members.find((member) => member.isHost);

  useEffect(() => {
    if (!hasMessage) { setWelcome(true); return; }
    const timer = window.setTimeout(() => setWelcome(false), 700);
    return () => window.clearTimeout(timer);
  }, [hasMessage]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [session.chat.length, session.chatBusy]);

  return (
    <div className="group-chat">
      {welcome && <section className={`chat-welcome ${hasMessage ? "leaving" : ""}`} aria-label="Boas-vindas à mesa">
        <div className="welcome-content">
          <h1>O que irão pedir,<br />mesa de <span>{host?.name || "amigos"}</span>?</h1>
        </div>
      </section>}
      {session.chat.map((message) => (
        <Bubble key={message.id} message={message} meId={meId} />
      ))}
      {session.chatBusy && (
        <div className="msg theirs">
          <span className="author agent-author">Agente</span>
          <div className="bubble agent typing" aria-label="agente digitando">
            <span />
            <span />
            <span />
          </div>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
