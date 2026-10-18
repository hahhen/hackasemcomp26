import type { Health, Session } from "./types";

export const room = (new URLSearchParams(window.location.search).get("mesa") ?? "").trim().toUpperCase();
export const identityKey = (code: string) => `mesa:member:${code}`;

const base = `/api/mesas/${encodeURIComponent(room)}`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = `Erro ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the generic message */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T = Session>(path: string, body?: unknown) =>
  request<T>(`${base}${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });

/** Every action that changes the table carries who is doing it. */
export const api = {
  transcribe: (audio: string) => request<{ text: string }>("/api/transcribe", { method: "POST", body: JSON.stringify({ audio }) }),
  createRoom: (name: string) => request<{ session: Session; memberId: string }>("/api/rooms", {
    method: "POST", body: JSON.stringify({ name }),
  }),
  enterRoom: (code: string, name: string) => request<{ session: Session; memberId: string }>(`/api/rooms/${encodeURIComponent(code)}/join`, {
    method: "POST", body: JSON.stringify({ name }),
  }),
  health: () => request<Health>("/api/health"),
  table: () => request<Session>(`/api/rooms/${encodeURIComponent(room)}`),
  track: () => request<Session>(`${base}/track`),
  retrySearch: () => post("/recommend"),
  previousAddresses: (authorId: string) => request<{ sourceRoom: string; alias: string; label: string; addressId: string }[]>(`${base}/addresses/previous?authorId=${encodeURIComponent(authorId)}`),
  reuseAddress: (authorId: string, sourceRoom: string, addressId: string) => post("/addresses/reuse", { authorId, sourceRoom, addressId }),
  addMember: (m: { name: string }) => post("/members", m),
  deleteMember: (id: string, authorId: string) => request<Session>(`${base}/members/${id}`, {
    method: "DELETE", body: JSON.stringify({ authorId }),
  }),

  /** Chat is asynchronous: the reply arrives via polling. */
  chat: (authorId: string | null, message: string, images: string[]) =>
    post("/chat", { authorId, message, images }),

  selectMerchant: (merchantId: string, authorId: string | null) =>
    request<Session>(`${base}/merchant`, {
      method: "PATCH",
      body: JSON.stringify({ merchantId, authorId }),
    }),

  /** "+" / "-" on somebody's recommendation card. */
  addToCart: (memberId: string, authorId: string | null, itemId: string) =>
    post(`/members/${memberId}/cart`, { authorId, itemId }),
  removeFromCart: (memberId: string, authorId: string | null) =>
    request<Session>(`${base}/members/${memberId}/cart`, {
      method: "DELETE",
      body: JSON.stringify({ authorId }),
    }),
  /** The host closes the cart; that is what creates the iFood cart. */
  finishCart: (authorId: string | null) => post("/cart/finish", { authorId }),

  submit: (authorId: string | null, document?: string) => post("/submit", { authorId, document }),

  /** A device confirms its own Pix, so author and target are the same member. */
  pay: (memberId: string) => post(`/members/${memberId}/pay`, { authorId: memberId }),
  cover: (memberId: string, authorId: string | null) => post(`/members/${memberId}/cover`, { authorId }),
  drop: (memberId: string, authorId: string | null) => post(`/members/${memberId}/drop`, { authorId }),
};
