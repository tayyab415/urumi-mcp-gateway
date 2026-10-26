import { useMemo, useState } from "react";
import { useLocation } from "react-router";
import { ArrowUpRight, FileText, PanelRightClose, PanelRightOpen, Plus } from "lucide-react";
import { useChat } from "@/state/chat";
import { AgentGlyph } from "@/components/ui/AgentGlyph";
import { Chip } from "@/components/ui/Chip";
import { Composer } from "./Composer";
import { Thread } from "./Thread";
import styles from "./DockedAssistant.module.css";

const PAGE_NAMES: Record<string, string> = { "/": "Overview", "/servers": "Servers", "/team": "Team", "/connect": "Connect", "/audit": "Audit log", "/permissions": "Permissions", "/stores": "Stores" };

const SUGGESTIONS: Record<string, string[]> = {
  "/": ["How many orders came in today?", "Which products are low on stock at the supplier?"],
  "/servers": ["List the products in the store", "Quote shipping for the latest order"],
  "/audit": ["Show me today's orders with their totals", "Which supplier items are out of stock?"],
  "/team": ["List the five most recent orders", "What does the supplier charge for training balls?"],
  "/connect": ["List the products in the store", "Show today's orders"],
  "/permissions": ["Read order #1 and quote shipping for it", "List the supplier catalog"],
  "/stores": ["Create a product called Training Ball for 25", "List orders placed today"],
};

/** A page-aware assistant beside any page. Same task cards as the Chat page, with a chip that names what it can see. */
export function DockedAssistant() {
  const { turns, activeTurn, send, stop, meta } = useChat();
  const location = useLocation();
  const [open, setOpen] = useState(() => localStorage.getItem("urumi.dock") !== "closed");
  const [withPage, setWithPage] = useState(true);
  const page = PAGE_NAMES[location.pathname] ?? "this page";
  const dockTurns = useMemo(() => turns.filter((t) => t.source === "dock"), [turns]);
  const toggle = () => { localStorage.setItem("urumi.dock", open ? "closed" : "open"); setOpen(!open); };
  const context = withPage ? `Context: the user is looking at the ${page} page of the Urumi dashboard.` : undefined;
  const ask = (text: string) => void send(text, { context, source: "dock" });

  if (!open) {
    return (
      <div className={styles.collapsed}>
        <button type="button" className={styles.toggle} onClick={toggle} aria-label="Open assistant" title="Open assistant"><PanelRightOpen /></button>
        <AgentGlyph size={16} />
      </div>
    );
  }
  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <AgentGlyph size={16} />
        <span className={styles.name}>Assistant</span>
        <span className={styles.model}>{meta?.model_label ?? "…"}</span>
        <button type="button" className={styles.toggle} onClick={toggle} aria-label="Hide assistant" title="Hide assistant"><PanelRightClose /></button>
      </div>
      <div className={styles.chips}>
        <Chip icon={<FileText />} selected={withPage} onClick={() => setWithPage((v) => !v)} aria-label={withPage ? "Stop sharing this page" : "Share this page"}>{withPage ? "This page" : `Add ${page.toLowerCase()}`}</Chip>
        {!withPage && <Chip icon={<Plus />} onClick={() => setWithPage(true)}>Add context</Chip>}
      </div>
      <div className={styles.body}>
        {dockTurns.length === 0 ? (
          <div className={styles.empty}>
            {(SUGGESTIONS[location.pathname] ?? SUGGESTIONS["/"]!).map((s) => (
              <button key={s} type="button" className={styles.suggestion} onClick={() => ask(s)}>{s}<ArrowUpRight /></button>
            ))}
          </div>
        ) : (
          <Thread turns={dockTurns} compact modelLabel={meta?.model_label ?? "model"} />
        )}
      </div>
      <div className={styles.footer}>
        <Composer compact onSend={ask} onStop={stop} busy={Boolean(activeTurn)} waiting={activeTurn?.status === "waiting"} placeholder="Ask about this page" />
      </div>
    </div>
  );
}
