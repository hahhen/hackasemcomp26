export type Restriction =
  | "VEGETARIAN"
  | "VEGAN"
  | "LAC_FREE"
  | "GLUTEN_FREE"
  | "SUGAR_FREE"
  | "ORGANIC";

export type Status =
  | "COLLECTING_PREFERENCES"
  | "WAITING_PAYMENTS"
  | "SUBMITTING"
  | "ORDER_SUBMITTED";

export interface Recommendation {
  memberId: string;
  itemId: string;
  name: string;
  price: number;
  description: string;
  imageUrl: string | null;
  overBudget: boolean;
  note: string | null;
  alternativeQuery: string | null;
}

export interface MerchantOption {
  missingMembers: string[];
  requestedCount: number;
  searchIncomplete: boolean;
  alternativeMembers: string[];
  merchantId: string;
  name: string;
  logoUrl: string | null;
  fee: number;
  minimumOrderAmount: number;
  rating: number;
  distanceKm: number | null;
  eta: string | null;
  covers: number;
}

export interface Quota {
  item: number;
  deliveryShare: number;
  discountShare: number;
  total: number;
}

export interface AssignedItem {
  itemId: string;
  name: string;
  merchantId: string;
  unitPrice: number;
  optionsPrice: number;
  /** unitPrice + optionsPrice, computed by the backend. */
  price: number;
  optionLabels: string[];
  customerNote: string;
  imageUrl: string | null;
  warnings: string[];
}

export interface Member {
  id: string;
  name: string;
  isHost: boolean;
  desire: string;
  restrictions: Restriction[];
  maxBudget: number | null;
  assignedItem: AssignedItem | null;
  amountToPay: number;
  quota: Quota | null;
  hasPaid: boolean;
  coveredByHost: boolean;
  removed: boolean;
}

export interface HostAddress {
  addressId: string;
  alias: string;
  label: string;
}

export interface McpCart {
  cartId: string | null;
  merchantId: string | null;
  merchantName: string | null;
  merchantLogoUrl: string | null;
  minimumOrderAmount: number;
  subtotal: number;
  deliveryFee: number;
  voucherCode: string | null;
  voucherDiscount: number;
  totalAmount: number;
  estimatedMinutes: string | null;
  orderId: string | null;
  verificationCode: string | null;
  orderStatus: string | null;
  simulated: boolean;
}

export interface TableEvent {
  ts: number;
  kind: "info" | "tool" | "warn" | "error" | "ok";
  text: string;
}

export interface ChatMessage {
  id: string;
  kind: "user" | "agent" | "system";
  /** Member id of whoever spoke; null for the agent. */
  authorId: string | null;
  authorName: string;
  text: string;
  ts: number;
  images: string[];
}

export interface Session {
  roomId: string;
  status: Status;
  mode: "mock" | "live";
  hostAddress: HostAddress | null;
  members: Member[];
  mcpCart: McpCart;
  events: TableEvent[];
  chat: ChatMessage[];
  lastError: string | null;
  recommendError: string | null;
  busy: boolean;
  /** True while the agent is answering, so every open screen can show dots. */
  chatBusy: boolean;
  hostMemberId: string | null;
  paidCount: number;
  activeCount: number;
  allPaid: boolean;

  /** True while restaurants for the whole table are being searched. */
  searchingRecommendations: boolean;
  recommendPhase: string | null;
  merchantOptions: MerchantOption[];
  selectedMerchantId: string | null;
  /** Each person's suggested dish at the selected restaurant. */
  recommendations: Recommendation[];
  cartSubtotal: number;
  cartIsFull: boolean;
}

export interface Health {
  ok: boolean;
  mode: "mock" | "live";
  realOrdersEnabled: boolean;
  defaultRoom: string;
  environment: string;
  model: string | null;
  /** False when MESA_CHAT_LLM=false: the chat answers without a model. */
  chatLlm: boolean;
}
