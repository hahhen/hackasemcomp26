import { useCallback, useEffect, useRef, useState } from "react";
import { api, identityKey, room } from "./api";
import { Header, type View } from "./components/Header";
import { RoomEntrance } from "./components/RoomEntrance";
import { GodView, TrueView } from "./components/RoomViews";
import type { Health, Session } from "./types";

export type Run = (action: () => Promise<Session>, activity?: string) => Promise<boolean>;

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [meId, setMeId] = useState<string | null>(() => room ? localStorage.getItem(identityKey(room)) : null);
  const [view, setView] = useState<View>("true");
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(Boolean(room && meId));
  const [entryError, setEntryError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number>(0);
  const mutationVersion = useRef(0);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const resize = () => {
      if (viewport.scale === 1) document.documentElement.style.setProperty("--app-height", `${viewport.height}px`);
    };
    resize();
    viewport.addEventListener("resize", resize);
    return () => { viewport.removeEventListener("resize", resize); document.documentElement.style.removeProperty("--app-height"); };
  }, []);

  useEffect(() => { api.health().then(setHealth).catch(() => setOffline(true)); }, []);

  useEffect(() => {
    if (!room || !meId) return;
    let alive = true;
    let timer: number;
    const tick = async () => {
      const version = mutationVersion.current;
      let delay = 1500;
      try {
        const next = await api.table();
        if (!alive || version !== mutationVersion.current) { delay = 350; return; }
        if (next.chatBusy || next.searchingRecommendations || next.busy || next.cartPendingMembers?.length) delay = 350;
        if (!next.members.some((m) => m.id === meId && !m.removed)) {
          localStorage.removeItem(identityKey(room)); setMeId(null); setSession(null);
          setEntryError("Sua participação terminou. Entre novamente para voltar à mesa."); return;
        }
        setSession(next); setOffline(false);
      } catch (e) {
        if (alive) { setOffline(true); setEntryError(e instanceof Error ? e.message : "Não consegui abrir a mesa."); }
      } finally {
        if (alive) { setLoading(false); timer = window.setTimeout(tick, delay); }
      }
    };
    void tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [meId]);

  useEffect(() => {
    if (session?.status !== "ORDER_SUBMITTED") return;
    const timer = setInterval(() => api.track().then(setSession).catch(() => {}), 6000);
    return () => clearInterval(timer);
  }, [session?.status]);

  const notify = useCallback((message: string) => {
    setToast(message); clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 5000);
  }, []);

  const run: Run = useCallback(async (action) => {
    mutationVersion.current += 1;
    try { setSession(await action()); return true; }
    catch (e) { notify(e instanceof Error ? e.message : "Algo deu errado."); return false; }
    finally { mutationVersion.current += 1; }
  }, [notify]);

  if (loading) return <div className="splash"><p>Abrindo sua mesa…</p></div>;
  const me = session?.members.find((m) => m.id === meId && !m.removed);
  if (!session || !me) return <RoomEntrance initialCode={room} error={entryError} />;

  return <div className="app">
    <Header health={health} offline={offline} room={session.roomId} view={view} onView={setView} member={me} notify={notify} session={session} run={run} />
    {view === "true" ? <TrueView session={session} member={me} run={run} /> : <GodView session={session} run={run} />}
    {toast && <div className="toast" role="alert" onClick={() => setToast(null)}>{toast}</div>}
  </div>;
}
