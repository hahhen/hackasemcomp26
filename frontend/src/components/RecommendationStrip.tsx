import type { Run } from "../App";
import { api } from "../api";
import { money } from "../format";
import { useRef } from "react";
import type { Member, Recommendation, Session } from "../types";

interface Props {
  session: Session;
  meId: string | null;
  run: Run;

}

function Card({ member, recommendation, session, meId, run }: Props & { member: Member; recommendation: Recommendation }) {
  const isHost = meId === session.hostMemberId;
  const open = session.status === "COLLECTING_PREFERENCES";
  const item = member.assignedItem?.itemId === recommendation.itemId ? member.assignedItem : null;
  const mine = meId === member.id;

  const mayAdd = open && !session.busy && !session.searchingRecommendations && !session.chatBusy && (isHost || mine);
  const price = item ? item.price : recommendation.price;

  return (
    <div className={`reco-card ${item ? "in-cart" : ""} ${mine ? "mine" : ""}`}>
      {recommendation.imageUrl ? (
        <img src={recommendation.imageUrl} alt="" />
      ) : (
        <div className="reco-ph">🍽</div>
      )}
      <div className="reco-text">
        <span className="reco-who">{member.name}</span>
        <strong>{item ? item.name : recommendation.name}</strong>
        {recommendation.alternativeQuery && <span className="reco-alternative">Sugestão similar</span>}
        {recommendation.note && <span className="reco-description">{recommendation.note}</span>}
        <span className="reco-description">{recommendation.description || "O restaurante não informou a descrição."}</span>
        <span className="reco-price">
          {money(price)}
          {recommendation.overBudget && <span className="muted small"> · acima do teto</span>}
        </span>
      </div>
      {item ? (
        <button
          className="add-btn done"
          title={open ? "tirar do carrinho" : "no carrinho"}
          disabled={!mayAdd}
          onClick={() => run(() => api.removeFromCart(member.id, meId))}
        >
          ✓
        </button>
      ) : (
        <button
          className="add-btn"
          title={mayAdd ? (member.assignedItem ? "trocar meu prato por este" : "adicionar ao carrinho") : "aguarde a atualização das sugestões"}
          aria-label={`Adicionar ${recommendation.name} ao carrinho de ${member.name}`}
          disabled={!mayAdd}
          onClick={() => run(() => api.addToCart(member.id, meId, recommendation.itemId))}
        >
          +
        </button>
      )}
    </div>
  );
}

/** All matching dishes for the viewer, horizontally scrollable. */
export function RecommendationStrip({ session, meId, run }: Props) {
  const strip = useRef<HTMLDivElement>(null);
  if (session.status !== "COLLECTING_PREFERENCES") return null;
  if (!session.merchantOptions.length) return null;

  const member = session.members.find((m) => !m.removed && m.id === meId);
  const suggestions = session.recommendations.filter((r) => r.memberId === meId);
  if (!member) return null;
  if (!suggestions.length) return <section className="suggestions warning" role="status">
    {member.desire || member.restrictions.length ? <>Ainda não encontrei um prato ou similar para seu pedido neste restaurante, respeitando seus limites. O host pode trocar a loja, ou você pode ajustar seu desejo no chat.</> : <>Conte no chat o que você quer comer para receber sugestões.</>}
  </section>;

  return (
    <section className="suggestions" aria-label={`Sugestões para ${member.name}`}>
      <div className="suggestions-heading">
        <span>{suggestions.length} {suggestions.length === 1 ? "opção para você" : "opções para você"}</span>
        {suggestions.length > 1 && <div className="suggestion-arrows">
          <button type="button" aria-label="Sugestões anteriores" onClick={() => strip.current?.scrollBy({ left: -290, behavior: "smooth" })}>←</button>
          <button type="button" aria-label="Próximas sugestões" onClick={() => strip.current?.scrollBy({ left: 290, behavior: "smooth" })}>→</button>
        </div>}
      </div>
      {member.maxBudget != null && <div className="muted small">Até {money(member.maxBudget)} por prato · frete à parte</div>}
      <div ref={strip} className="reco-strip" tabIndex={0} aria-label="Deslize para ver os pratos">
        {suggestions.map((recommendation) => (
          <Card key={recommendation.itemId} session={session} meId={meId} run={run} member={member} recommendation={recommendation} />
        ))}
      </div>
    </section>
  );
}
