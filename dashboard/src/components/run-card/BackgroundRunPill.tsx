import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { AnimatePresence, motion } from "motion/react";
import { useChat } from "@/state/chat";
import { fade, settle } from "@/lib/motion";
import { useToast } from "@/components/ui/Toast";
import { AgentRunCard } from "./AgentRunCard";
import { runFromTurn } from "./fromTurn";

/** The collapsed pill for a chat run that is still going while the user is on another page.
 *  It expands into the full run card in place; Approve and Deny there call the real endpoint. */
export function BackgroundRunPill() {
  const { turns, activeTurn, decide, stop, meta, conversationId } = useChat();
  const navigate = useNavigate();
  const toast = useToast();
  const [expanded, setExpanded] = useState(false);
  const [lingering, setLingering] = useState<string | null>(null);
  const last = turns[turns.length - 1];
  const pageTurn = activeTurn?.source === "page" ? activeTurn : null;

  // Keep the pill for a while after a page run finishes, so the done ring is seen.
  useEffect(() => {
    if (!last || last.source !== "page" || !(last.status === "done" || last.status === "failed" || last.status === "stopped")) return;
    setLingering(last.id);
    const t = setTimeout(() => setLingering(null), 12000);
    return () => clearTimeout(t);
  }, [last?.id, last?.status, last?.source]);

  const turn = pageTurn ?? (lingering && last?.id === lingering ? last : null);
  const run = useMemo(() => (turn ? runFromTurn(turn, meta?.model_label ?? "model") : null), [turn, meta?.model_label]);
  const waiting = turn?.steps.find((s) => s.status === "waiting");

  useEffect(() => { if (!turn) setExpanded(false); }, [turn]);

  if (!turn || !run) return null;
  const onDecide = (d: "approve" | "deny") => { if (waiting) decide(turn.id, waiting.id, d).catch((e: unknown) => toast(e instanceof Error ? e.message : "Could not send the decision", "danger")); };
  return (
    <AnimatePresence>
      <motion.div key={turn.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: fade }} transition={settle} style={{ position: "fixed", left: "calc(var(--rail-width) + 24px)", bottom: 20, zIndex: 30 }}>
        <AgentRunCard
          run={run}
          expanded={expanded}
          floating
          onToggle={() => setExpanded((v) => !v)}
          onStop={stop}
          onApprove={() => onDecide("approve")}
          onDeny={() => onDecide("deny")}
          onOpenRun={() => navigate(conversationId ? `/audit?chat_id=${encodeURIComponent(conversationId)}` : "/chat")}
        />
      </motion.div>
    </AnimatePresence>
  );
}
