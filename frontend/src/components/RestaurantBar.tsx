import type { Run } from "../App";
import { api } from "../api";
import { money } from "../format";
import type { Session } from "../types";
import { useEffect, useState } from "react";

interface Props {
  session: Session;
  meId: string | null;
  run: Run;
}

/** The floating dropdown on top of the chat: which restaurant is the table's. */
export function RestaurantBar({ session, meId, run }: Props) {
  const isHost = meId === session.hostMemberId;
  const open = session.status === "COLLECTING_PREFERENCES";
  const options = session.merchantOptions;
  const selected = options.find((o) => o.merchantId === session.selectedMerchantId);
  const addressFailed = Boolean(session.recommendError?.includes("listAddresses"));
  const [addresses, setAddresses] = useState<Awaited<ReturnType<typeof api.previousAddresses>>>([]);
  const [loadingAddress, setLoadingAddress] = useState(false);
  const [addressError, setAddressError] = useState("");
  useEffect(() => {
    let alive = true;
    if (!addressFailed || !isHost || !meId) { setAddresses([]); return; }
    api.previousAddresses(meId).then((rows) => { if (alive) setAddresses(rows); })
      .catch(() => { if (alive) setAddressError("Não consegui carregar os endereços anteriores."); });
    return () => { alive = false; };
  }, [addressFailed, isHost, meId]);

  if (!options.length) {
    if (session.searchingRecommendations) {
      return <div className="dock-pill muted" role="status">🔎 {session.recommendPhase || "procurando restaurantes que atendem a mesa…"}</div>;
    }
    if (session.recommendError) return <div className="dock-pill warn address-recovery">
      <span>⚠ {session.recommendError}</span>
      {isHost && addressFailed && addresses.length > 0 && <>
        <strong>Podemos usar um endereço já salvo nesta instalação:</strong>
        {addresses.map((address) => <div className="address-choice" key={address.addressId}>
          <span><strong>{address.alias}</strong>{address.label && <span> · {address.label}</span>}</span>
          <button className="primary small" disabled={loadingAddress || session.busy || session.chatBusy || !open} onClick={async () => {
            setLoadingAddress(true);
            try { await run(() => api.reuseAddress(meId!, address.sourceRoom, address.addressId)); }
            finally { setLoadingAddress(false); }
          }}>Usar este endereço</button>
        </div>)}
        <small>Confirme que é o local de entrega e a conta iFood corretos. O endereço será validado pelo iFood ao criar o carrinho.</small>
      </>}
      <button className="link" disabled={loadingAddress || session.busy || session.chatBusy || !open} onClick={() => run(() => api.retrySearch())}>Buscar novamente</button>
      {addressError && <small>{addressError}</small>}
    </div>;
    return null;
  }

  return (
    <div className="restaurant-bar">
      {session.recommendError && <div className="dock-pill warn">Não consegui atualizar. Mantive as opções anteriores para os mesmos pedidos. {session.recommendError}</div>}
      {selected?.logoUrl && <img className="restaurant-logo" src={selected.logoUrl} alt="" />}
      <div className="restaurant-text">
        <span className="dock-label">
          {options.length > 1 ? `${options.length} restaurantes com opções` : "Restaurante da mesa"}
        </span>
        {open && isHost ? (
          <select
            disabled={session.busy || session.searchingRecommendations || session.chatBusy}
            value={session.selectedMerchantId ?? ""}
            onChange={(e) => run(() => api.selectMerchant(e.target.value, meId))}
            aria-label="Escolher restaurante"
          >
            {options.map((o) => (
              <option key={o.merchantId} value={o.merchantId}>
                {o.name} — frete {money(o.fee)} · mín {money(o.minimumOrderAmount)}
                {o.alternativeMembers?.length ? " · com sugestões similares" : ""}
                {o.requestedCount ? ` · atende ${o.covers}/${o.requestedCount}` : ""}
                {o.rating ? ` · ${o.rating}★` : ""}
                {o.distanceKm ? ` · ${o.distanceKm} km` : ""}
              </option>
            ))}
          </select>
        ) : (
          <strong>
            {selected?.name ?? "—"}
            {open && <span className="muted small"> · só o host troca</span>}
          </strong>
        )}
      </div>
      {open && selected && (
        <span className="restaurant-meta muted small">
          frete {money(selected.fee)} · mín {money(selected.minimumOrderAmount)}
          {selected.eta ? ` · ${selected.eta}` : ""}
        </span>
      )}
      {selected?.alternativeMembers?.length ? <span className="restaurant-meta muted small">Sugestões similares para {selected.alternativeMembers.join(", ")}; confira os cartões antes de adicionar.</span> : null}
      {selected?.missingMembers?.length ? <span className="restaurant-meta warning">Ainda sem opção para {selected.missingMembers.join(", ")}. Confira outro restaurante ou ajuste os desejos.</span> : null}
      {selected?.searchIncomplete && <span className="restaurant-meta muted small">Busca parcial: algumas consultas ao iFood falharam.</span>}
      {isHost && (selected?.missingMembers?.length || selected?.searchIncomplete) ? <button className="link" disabled={session.busy || session.searchingRecommendations || session.chatBusy || !open} onClick={() => run(() => api.retrySearch())}>Buscar novamente</button> : null}
      {session.searchingRecommendations && (
        <span className="restaurant-meta muted small" role="status">
          🔎 {session.recommendPhase || "procurando de novo…"}
        </span>
      )}
    </div>
  );
}
