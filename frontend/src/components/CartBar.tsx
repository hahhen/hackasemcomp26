import { useState } from "react";
import type { Run } from "../App";
import { api } from "../api";
import { money } from "../format";
import type { Session } from "../types";

interface Props {
  session: Session;
  meId: string | null;
  run: Run;
}

function Totals({ session }: { session: Session }) {
  const cart = session.mcpCart;
  return (
    <>
      <div className="quota-line">
        <span>Itens</span>
        <span>{money(session.cartSubtotal)}</span>
      </div>
      <div className="quota-line">
        <span>Entrega{cart.estimatedMinutes ? ` (${cart.estimatedMinutes})` : ""}</span>
        <span>{money(cart.deliveryFee)}</span>
      </div>
      {cart.voucherDiscount > 0 && (
        <div className="quota-line discount">
          <span>Cupom {cart.voucherCode ?? ""}</span>
          <span>− {money(cart.voucherDiscount)}</span>
        </div>
      )}
      <div className="quota-total">
        <span>Total</span>
        <strong>{money(cart.totalAmount || session.cartSubtotal + cart.deliveryFee)}</strong>
      </div>
    </>
  );
}

/** The shared cart: everybody sees it, only the host closes it, everybody pays. */
export function CartBar({ session, meId, run }: Props) {
  const [open, setOpen] = useState(false);
  const isHost = meId === session.hostMemberId;
  const cart = session.mcpCart;
  const collecting = session.status === "COLLECTING_PREFERENCES";
  const paying = session.status === "WAITING_PAYMENTS" || session.status === "SUBMITTING";

  const active = session.members.filter((m) => !m.removed);
  const lines = active.filter((m) => m.assignedItem);
  const missing = active.filter((m) => !m.assignedItem);
  if (!lines.length && !cart.cartId) return null;

  const short = Boolean(cart.cartId) && session.cartSubtotal < cart.minimumOrderAmount;
  const pct = session.activeCount ? (session.paidCount / session.activeCount) * 100 : 0;

  return (
    <div className={`cart-bar ${open ? "is-open" : ""}`}>
      <button className="cart-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        🛒 {lines.length} {lines.length === 1 ? "item" : "itens"} · {money(session.cartSubtotal)}
        {paying && ` · ${session.paidCount}/${session.activeCount} pagos`}
        {collecting && missing.length > 0 && ` · faltam ${missing.length}`}
        <span className="chev">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="cart-panel">
          <div className="cart-panel-heading"><strong>O pedido da mesa</strong><button className="icon-small" aria-label="Fechar carrinho" onClick={() => setOpen(false)}>×</button></div>
          <div className="cart-items">
          {lines.map((member) => (
            <div key={member.id} className="cart-line">
              <span className="cart-who">{member.name}</span>
              <span className="cart-item">{member.assignedItem?.name}</span>
              <span className="cart-price">{money(member.assignedItem?.price ?? 0)}</span>
              {collecting && (isHost || meId === member.id) && (
                <button
                  className="icon-small"
                  title="tirar do carrinho"
                  onClick={() => run(() => api.removeFromCart(member.id, meId))}
                >
                  ×
                </button>
              )}
              {paying &&
                (member.hasPaid ? (
                  <span className="badge badge-ok">{member.coveredByHost ? "coberto" : "pago ✓"}</span>
                ) : (
                  <span className="pay-inline">
                    {(isHost || meId === member.id) && (
                      <button className="primary small" onClick={() => run(() => api.pay(member.id))}>
                        pagar {money(member.amountToPay)}
                      </button>
                    )}
                    {isHost && (
                      <>
                        <button className="link" onClick={() => run(() => api.cover(member.id, meId))}>
                          cobrir
                        </button>
                        <button
                          className="link danger"
                          onClick={() =>
                            window.confirm(`Tirar ${member.name} do pedido?`) &&
                            run(() => api.drop(member.id, meId))
                          }
                        >
                          remover
                        </button>
                      </>
                    )}
                  </span>
                ))}
            </div>
          ))}
          </div>
          <div className="cart-actions">
          <Totals session={session} />

          {collecting && (
            <>
              {missing.length > 0 && (
                <div className="muted small">faltam: {missing.map((m) => m.name).join(", ")}</div>
              )}
              {short && (
                <div className="warning">
                  ⚠ abaixo do pedido mínimo de {money(cart.minimumOrderAmount)}
                </div>
              )}
              {isHost ? (
                <button
                  className="primary"
                  disabled={missing.length > 0 || session.busy || session.chatBusy || session.searchingRecommendations || short}
                  onClick={() => run(() => api.finishCart(meId))}
                >
                  {session.busy ? "fechando…" : "Fechar carrinho e dividir"}
                </button>
              ) : (
                <span className="muted small">o host fecha o carrinho quando estiver tudo aí</span>
              )}
            </>
          )}

          {paying && (
            <>
              <div className="progress-track">
                <div
                  className={`progress-fill ${session.allPaid ? "full" : ""}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="muted small">
                {session.allPaid
                  ? "todo mundo pagou — o pedido foi enviado"
                  : `${session.paidCount}/${session.activeCount} pagos`}
              </div>
              {isHost && !session.allPaid && (
                <button
                  className="primary"
                  disabled={!session.allPaid || session.busy}
                  onClick={() => run(() => api.submit(meId))}
                >
                  Enviar pedido (espera todos)
                </button>
              )}
            </>
          )}
          </div>
        </div>
      )}
    </div>
  );
}
