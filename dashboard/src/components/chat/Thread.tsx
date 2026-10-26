import { useEffect, useRef } from "react";
import { useNavigate } from "react-router";
import { TextShimmer } from "@/arc/text-shimmer";
import { useChat, type Turn } from "@/state/chat";
import { useToast } from "@/components/ui/Toast";
import { TaskCard } from "@/components/task-card/TaskCard";
import { Markdown } from "@/components/ui/Markdown";
import { cx } from "@/lib/cx";
import styles from "./Thread.module.css";

export function Thread({ turns, compact, modelLabel }: { turns: Turn[]; compact?: boolean; modelLabel: string }) {
  const { decide, send, conversationId, meta } = useChat();
  const toast = useToast();
  const navigate = useNavigate();
  const bottom = useRef<HTMLDivElement>(null);
  const lastSignature = turns.map((t) => `${t.id}:${t.status}:${t.steps.length}:${t.text.length}`).join("|");
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [lastSignature]);
  const anthropic = /claude|anthropic/i.test(meta?.model ?? "");

  const onDecide = (turnId: string, stepId: string, decision: "approve" | "deny") => {
    decide(turnId, stepId, decision).catch((e: unknown) => toast(e instanceof Error ? e.message : "Could not send the decision", "danger"));
  };

  return (
    <div className={cx(styles.thread, compact && styles.compact)}>
      {turns.map((turn) => {
        const hasTools = turn.steps.some((s) => s.name !== "plan");
        const thinking = turn.status === "running" && !hasTools && !turn.text;
        return (
          <div key={turn.id} className={styles.turn}>
            <div className={styles.userRow}><div className={styles.bubble}>{turn.prompt}</div></div>
            {hasTools ? (
              <TaskCard
                turn={turn}
                modelLabel={modelLabel}
                modelIsAnthropic={anthropic}
                compact={compact}
                onApprove={(stepId) => onDecide(turn.id, stepId, "approve")}
                onDeny={(stepId) => onDecide(turn.id, stepId, "deny")}
                onReplay={() => void send(turn.prompt, { source: turn.source })}
                onOpenRun={conversationId ? () => navigate(`/audit?chat_id=${encodeURIComponent(conversationId)}`) : undefined}
              />
            ) : thinking ? (
              <div className={styles.thinking}><TextShimmer duration={1.2}>Thinking</TextShimmer></div>
            ) : null}
            {turn.text && <div className={cx(styles.reply, hasTools && styles.cardReply)}><Markdown text={turn.text} /></div>}
            {!hasTools && turn.error && <div className={styles.error} role="alert">{turn.error}</div>}
            {!hasTools && turn.status === "stopped" && !turn.text && <div className={styles.error} style={{ color: "var(--text-muted)" }}>Stopped.</div>}
          </div>
        );
      })}
      <div ref={bottom} />
    </div>
  );
}
