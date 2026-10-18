import { useEffect, useRef, useState } from "react";
import type { Health, Member } from "../types";
import type { Session } from "../types";
import type { Run } from "../App";
import { Members } from "./Members";

export type View = "true" | "god";
interface Props {
  health: Health | null; offline: boolean; room: string; view: View;
  onView: (view: View) => void; member: Member; notify: (message: string) => void;
  session: Session; run: Run;
}

export function Header({ health, offline, room, view, onView, member, notify, session, run }: Props) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
      if (event.key === "Tab") {
        const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>("button, a[href]") || []);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", keydown); trigger.current?.focus(); };
  }, [open]);
  const copyLink = async () => {
    const link = new URL(window.location.pathname, window.location.origin);
    link.searchParams.set("mesa", room);
    try { await navigator.clipboard.writeText(link.href); notify("Convite copiado!"); }
    catch { notify(`Link do convite: ${link.href}`); }
    setOpen(false);
  };
  return <>
    <header className="header minimal-header">
      <button ref={trigger} className="nav-trigger" aria-label="Abrir menu" aria-expanded={open} aria-controls="room-sidebar" onClick={() => setOpen(true)}>☰</button>
      <strong className="header-brand">iFood Mesa</strong>
      <div className="header-identity"><span>{member.name}</span><small>{member.isHost ? "host" : "membro"}</small></div>
      <Members session={session} member={member} run={run} />
    </header>
    {open && <div className="sidebar-overlay" onClick={() => setOpen(false)}>
      <aside ref={panel} id="room-sidebar" className="room-sidebar" role="dialog" aria-modal="true" aria-label="Menu da mesa" tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        <div className="sidebar-heading"><strong>iFood Mesa</strong><button aria-label="Fechar menu" onClick={() => setOpen(false)}>×</button></div>
        <div className="sidebar-account"><strong>{member.name}</strong><span>{member.isHost ? "Host da mesa" : "Membro da mesa"}</span></div>
        <p className="dock-label">Visualização</p>
        <nav className="sidebar-navigation" aria-label="Modo de visualização">
          <button aria-pressed={view === "true"} onClick={() => { onView("true"); setOpen(false); }}>True mode <small>Sua conversa e seus pedidos</small></button>
          <button aria-pressed={view === "god"} onClick={() => { onView("god"); setOpen(false); }}>God mode <small>Todos os celulares e logs</small></button>
        </nav>
        <p className="dock-label">Sua mesa</p>
        <div className="sidebar-code"><span>Código do convite</span><strong>{room}</strong></div>
        <button className="primary" onClick={copyLink}>Copiar convite 🔗</button>
        <a className="sidebar-link" href={window.location.pathname}>Criar ou entrar em outra mesa →</a>
        <div className="sidebar-status">{offline ? "Sem conexão com o servidor" : health?.mode === "live" ? "iFood em modo real · disponibilidade depende das consultas" : "MCP simulado"}</div>
      </aside>
    </div>}
  </>;
}
