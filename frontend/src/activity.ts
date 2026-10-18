import type { Session } from "./types";

/** Describe an actual operation; labels are never a simulated thinking timer. */
export function activityLabel(session: Session, meId: string | null): string | null {
  if (session.busy) return session.busyPhase || "Preparando o pedido da mesa…";
  if (session.cartPendingMembers?.length) {
    return meId && session.cartPendingMembers.includes(meId)
      ? "Conferindo seu prato e adicionando ao carrinho…"
      : "Adicionando pratos ao carrinho da mesa…";
  }
  if (session.searchingRecommendations) {
    return session.recommendPhase || "Procurando o restaurante certo para a mesa…";
  }
  if (session.chatBusy) return session.chatPhase || "Entendendo os pedidos da mesa…";
  return null;
}
