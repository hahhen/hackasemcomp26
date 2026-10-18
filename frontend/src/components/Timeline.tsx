import { useEffect, useRef } from "react";
import type { TableEvent } from "../types";

const ICON: Record<TableEvent["kind"], string> = {
  info: "•",
  tool: "🔧",
  warn: "⚠",
  error: "✕",
  ok: "✓",
};

export function Timeline({ events, live = false }: { events: TableEvent[]; live?: boolean }) {
  const end = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (live) end.current?.scrollIntoView({ block: "nearest" });
  }, [events.length, live]);

  return (
    <details className="timeline" open={live}>
      <summary>
        {live ? "Agente trabalhando…" : "O que o agente fez"} <span className="muted">({events.length} passos)</span>
      </summary>
      <ul>
        {events.map((e, i) => (
          <li key={i} className={`ev ${e.kind}`}>
            <span className="ev-icon">{ICON[e.kind]}</span>
            {e.text}
          </li>
        ))}
        <li ref={end} />
      </ul>
    </details>
  );
}
