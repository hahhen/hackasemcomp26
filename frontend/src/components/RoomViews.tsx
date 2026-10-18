import { useState } from "react";
import type { Run } from "../App";
import { api } from "../api";
import type { Member, Session } from "../types";
import { DeviceFrame } from "./DeviceFrame";
import { Timeline } from "./Timeline";

export function TrueView({ session, member, run }: { session: Session; member: Member; run: Run }) {
  return <main className="true-view"><DeviceFrame member={member} session={session} run={run} plain /></main>;
}

export function GodView({ session, run }: { session: Session; run: Run }) {
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const host = session.members.find((m) => m.id === session.hostMemberId);
  const friends = session.members.filter((m) => !m.removed && !m.isHost);
  const add = async () => {
    if (!name.trim() || adding) return;
    setAdding(true);
    if (await run(() => api.addMember({ name: name.trim() }))) setName("");
    setAdding(false);
  };
  return <main className="god-view">
    <section className="god-host">{host && <DeviceFrame member={host} session={session} run={run} />}</section>
    <section className="god-inspector">
      <div className="god-toolbar"><div><h2>Celulares da mesa</h2><p className="muted small">Inspecione sugestões e converse como qualquer participante.</p></div>
        <form onSubmit={(e) => { e.preventDefault(); void add(); }}>
          <input aria-label="Nome do novo membro" placeholder="Nome do novo membro" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} disabled={adding || session.busy} />
          <button className="primary small" disabled={!name.trim() || adding || session.busy || session.status !== "COLLECTING_PREFERENCES"}>+ Adicionar membro</button>
        </form>
      </div>
      <div className="god-phones">{friends.length ? friends.map((member) => <DeviceFrame key={member.id} member={member} session={session} run={run} />) : <div className="empty-phones"><strong>A mesa está esperando a galera.</strong><p>Compartilhe o convite ou adicione um membro para abrir o celular dele aqui.</p></div>}</div>
      <section className="god-logs"><Timeline events={session.events} live={session.chatBusy || session.searchingRecommendations || session.busy} /></section>
    </section>
  </main>;
}
