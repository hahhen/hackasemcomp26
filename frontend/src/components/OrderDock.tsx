import type { Run } from "../App";
import type { Session } from "../types";
import { CartBar } from "./CartBar";
import { RecommendationStrip } from "./RecommendationStrip";

interface Props {
  session: Session;
  meId: string | null;
  run: Run;
  /** A phone screen shows only the owner's card, in a tighter layout. */
  compact?: boolean;
}

/**
 * Above the composer: the viewer's suggested dish with a "+", and the shared cart.
 */
export function OrderDock({ session, meId, run, compact }: Props) {
  const empty =
    !session.merchantOptions.length &&
    !session.mcpCart.cartId &&
    !session.searchingRecommendations &&
    !session.lastError;
  if (empty) return null;

  return (
    <div className={`order-dock ${compact ? "compact" : ""}`}>
      <RecommendationStrip session={session} meId={meId} run={run} />
      <CartBar session={session} meId={meId} run={run} />
    </div>
  );
}
