import type { Run } from "../App";
import { useRef, useState } from "react";
import { api } from "../api";
import type { Member, Session } from "../types";
import { Composer } from "./Composer";
import { GroupChat } from "./GroupChat";
import { OrderDock } from "./OrderDock";
import { RestaurantBar } from "./RestaurantBar";

interface Props {
  member: Member;
  session: Session;
  run: Run;
  plain?: boolean;
}

/**
 * One simulated phone. It is just the conversation: what the table is ordering
 * shows up in the floating dock above the messages, and nothing else here is an
 * "ordering control" - people ask in the chat and the agent does the rest.
 */
export function DeviceFrame({ member, session, run, plain }: Props) {
  const [activity, setActivity] = useState<string | null>(null);
  const activityId = useRef(0);
  const runWithActivity: Run = async (action, label) => {
    if (!label) return run(action);
    const id = ++activityId.current;
    setActivity(label);
    try { return await run(action); }
    finally { if (id === activityId.current) setActivity(null); }
  };
  return (
    <article className={`device ${member.isHost ? "is-host" : ""} ${session.chat.some((message) => message.kind === "user") ? "" : "is-welcoming"}`}>
      <div className="welcome-backdrop" aria-hidden="true" />
      {!plain && <header className="device-bar">
        <span className="device-avatar" aria-hidden>
          {member.name.trim().charAt(0).toUpperCase()}
        </span>
        <strong>{member.name}</strong>
        {member.isHost && <span className="badge">host</span>}

      </header>}

      <RestaurantBar session={session} meId={member.id} run={run} />

      <div className="device-body">
        <GroupChat session={session} meId={member.id} activity={activity} />
      </div>

      <footer className="device-foot">
        <OrderDock session={session} meId={member.id} run={runWithActivity} compact />
        <Composer
          compact
          placeholder={plain ? "O que você quer comer?" : `Mensagem como ${member.name.split(" ")[0]}…`}
          onSend={(text, images) => run(() => api.chat(member.id, text, images))}
        />
      </footer>
    </article>
  );
}
