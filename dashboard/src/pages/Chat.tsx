import { useMemo } from "react";
import { useChat } from "@/state/chat";
import { useAuth } from "@/state/auth";
import { Chip } from "@/components/ui/Chip";
import { Button } from "@/components/ui/Button";
import { Composer } from "@/components/chat/Composer";
import { Thread } from "@/components/chat/Thread";
import styles from "./Chat.module.css";

const SUGGESTIONS = [
  "What orders came in today?",
  "List the products in the store",
  "Create a product called Training Ball for 25",
  "Quote shipping for the most recent order",
  "Which supplier items are out of stock?",
];

export function ChatPage() {
  const { me } = useAuth();
  const { turns, activeTurn, send, stop, reset, meta, conversationId } = useChat();
  const pageTurns = useMemo(() => turns.filter((t) => t.source === "page"), [turns]);
  const ask = (text: string) => void send(text, { source: "page" });
  return (
    <div className={styles.page}>
      <div className={styles.top}>
        <span>{meta ? `${meta.model_label} · acting as ${me?.email}` : "Connecting to the chat service…"}</span>
        {conversationId && <Button variant="link" onClick={reset}>New conversation</Button>}
      </div>
      <div className={styles.body}>
        {pageTurns.length === 0 ? (
          <div className={styles.empty}>
            <div className={styles.emptyTitle}>Ask about your store</div>
            <p>Every tool call goes through the gateway as you, and lands in the audit log. Changes stop for your approval first.</p>
            <div className={styles.suggestions}>
              {SUGGESTIONS.map((s) => <Chip key={s} onClick={() => ask(s)}>{s}</Chip>)}
            </div>
          </div>
        ) : (
          <Thread turns={pageTurns} modelLabel={meta?.model_label ?? "model"} />
        )}
      </div>
      <div className={styles.composer}>
        <Composer onSend={ask} onStop={stop} busy={Boolean(activeTurn)} waiting={activeTurn?.status === "waiting"} autoFocus />
      </div>
    </div>
  );
}
