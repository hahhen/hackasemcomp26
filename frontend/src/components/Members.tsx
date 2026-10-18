import { useEffect, useRef, useState } from "react";
import type { Run } from "../App";
import { api } from "../api";
import { money } from "../format";
import type { Member, Session } from "../types";

function orderSummary(member: Member): string {
  if (member.assignedItem) return member.assignedItem.name;
  const summary = member.desire.trim()
    .replace(/^(?:(?:eu\s+)?(?:quero|queria|gostaria de|vou querer)|i\s+(?:want|would like)|i'd like)\s+/i, "")
    .replace(/^(?:comer\s+)?(?:um(?:a)?|an?|some)\s+/i, "")
    .replace(/[.!]+$/, "").trim();
  return summary || "Ainda não pediu";
}

export function Members({ session, member, run }: { session: Session; member: Member; run: Run }) {
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const members = session.members.filter((m) => !m.removed);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); setMenu(null); }
      if (event.key !== "Tab") return;
      const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>("button:not(:disabled)") || []);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); trigger.current?.focus(); };
  }, [open]);
  return <>
    <button ref={trigger} className="members-trigger" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>Membros <span>{members.length}</span></button>
    {open && <div className="members-overlay" onClick={() => { setOpen(false); setMenu(null); }}>
      <section ref={panel} className="members-panel" role="dialog" aria-modal="true" aria-label="Membros da mesa" tabIndex={-1} onClick={(event) => { event.stopPropagation(); setMenu(null); }}>
        <div className="members-heading"><h2>Membros da mesa</h2><button className="icon-small" aria-label="Fechar membros" onClick={() => setOpen(false)}>×</button></div>
        <ul className="members-list">{members.map((person) => <li key={person.id}>
          <span className="device-avatar" aria-hidden>{person.name.trim().charAt(0).toUpperCase()}</span>
          <div className="member-order"><strong>{person.name}{person.id === member.id ? " (você)" : ""} {person.isHost && <span className="badge">host</span>}</strong><p>{orderSummary(person)}</p>{person.maxBudget != null && <small>Até {money(person.maxBudget)} por prato</small>}</div>
          {member.isHost && !person.isHost && <div className="member-options" onClick={(event) => event.stopPropagation()}>
            <button className="icon-small" aria-label={`Opções de ${person.name}`} aria-expanded={menu === person.id} aria-controls={`member-options-${person.id}`} onClick={() => setMenu(menu === person.id ? null : person.id)}>⋯</button>
            {menu === person.id && <div id={`member-options-${person.id}`} className="member-options-menu"><button disabled={removing || session.status !== "COLLECTING_PREFERENCES"} onClick={async () => { setRemoving(true); try { if (await run(() => api.deleteMember(person.id, member.id))) setMenu(null); } finally { setRemoving(false); } }}>Remover membro</button></div>}
          </div>}
        </li>)}</ul>
      </section>
    </div>}
  </>;
}
