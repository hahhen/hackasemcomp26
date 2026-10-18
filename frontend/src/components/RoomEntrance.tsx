import { useState } from "react";
import { api, identityKey } from "../api";

export function RoomEntrance({ initialCode, error }: { initialCode: string; error: string | null }) {
  const [action, setAction] = useState<"create" | "join">(initialCode ? "join" : "create");
  const [name, setName] = useState("");
  const [code, setCode] = useState(initialCode);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy || !name.trim()) return;
    setBusy(true); setFailure(null);
    try {
      let enteredCode = code.trim();
      if (enteredCode.includes("?")) enteredCode = new URL(enteredCode, window.location.origin).searchParams.get("mesa") || "";
      const entry = action === "create" ? await api.createRoom(name.trim()) : await api.enterRoom(enteredCode.toUpperCase(), name.trim());
      localStorage.setItem(identityKey(entry.session.roomId), entry.memberId);
      const target = new URL(window.location.pathname, window.location.origin);
      target.searchParams.set("mesa", entry.session.roomId);
      window.location.assign(target.href);
    } catch (e) { setFailure(e instanceof Error ? e.message : "Não consegui entrar na mesa."); setBusy(false); }
  };

  return <main className="entrance">
    <div className="entrance-brand"><span className="brand-dot">i</span> iFood Mesa</div>
    <div className="entrance-layout">
      <section className="entrance-intro">
        <span className="eyebrow">CADA UM ESCOLHE. TODO MUNDO PEDE JUNTO.</span>
        <h1>Uma mesa para<br />todos os gostos.</h1>
        <p>Convide a galera, diga o que está com vontade de comer e encontre um restaurante que combine com todo mundo.</p>
        <div className="entrance-steps"><span>01 · Crie a mesa</span><span>02 · Compartilhe o convite</span><span>03 · Escolham juntos</span></div>
      </section>
      <section className="entry-card">
        <nav className="entry-tabs" aria-label="Acessar uma mesa">
          <button aria-pressed={action === "create"} onClick={() => setAction("create")}>Criar mesa</button>
          <button aria-pressed={action === "join"} onClick={() => setAction("join")}>Entrar na mesa</button>
        </nav>
        <h2>{action === "create" ? "Puxe uma cadeira." : "Seu lugar está aqui."}</h2>
        <p className="muted">{action === "create" ? "Você será o host. Seus amigos entram pelo link ou código." : "Entre como membro usando o convite de quem criou a mesa."}</p>
        <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <label>Seu nome<input autoComplete="given-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} required placeholder="Como você quer ser chamado?" disabled={busy} /></label>
          {action === "join" && <label>Código ou link do convite<input value={code} onChange={(e) => setCode(e.target.value)} required placeholder="Ex.: A1B2C3D4" disabled={busy} /></label>}
          {(failure || error) && <div className="entry-error" role="alert">{failure || error}</div>}
          <button className="primary big" disabled={busy || !name.trim() || (action === "join" && !code.trim())}>{busy ? "Preparando sua mesa…" : action === "create" ? "Criar minha mesa →" : "Entrar como membro →"}</button>
        </form>
      </section>
    </div>
  </main>;
}
