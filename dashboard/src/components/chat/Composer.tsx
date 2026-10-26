import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, Square } from "lucide-react";
import { fade, snap } from "@/lib/motion";
import { cx } from "@/lib/cx";
import styles from "./Composer.module.css";

export function Composer({ onSend, onStop, busy, waiting, placeholder = "Ask about your store", compact, autoFocus }: { onSend: (text: string) => void; onStop: () => void; busy: boolean; waiting: boolean; placeholder?: string; compact?: boolean; autoFocus?: boolean }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(160, el.scrollHeight)}px`;
  }, [text]);
  const submit = () => {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  };
  return (
    <div className={cx(styles.wrap, compact && styles.compact, waiting && styles.dimmed)}>
      <textarea ref={ref} rows={1} className={styles.textarea} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} placeholder={waiting ? "Waiting for your decision" : placeholder} disabled={waiting} autoFocus={autoFocus} aria-label="Message" />
      <AnimatePresence initial={false} mode="popLayout">
        {busy && !waiting ? (
          <motion.button key="stop" type="button" className={cx(styles.send, styles.stop)} onClick={onStop} aria-label="Stop" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0, transition: fade }} transition={snap} whileTap={{ scale: 0.94 }}>
            <Square fill="currentColor" strokeWidth={0} style={{ width: 12, height: 12 }} />
          </motion.button>
        ) : (
          <motion.button key="send" type="button" className={styles.send} onClick={submit} disabled={!text.trim() || waiting} aria-label="Send" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0, transition: fade }} transition={snap} whileTap={{ scale: 0.94 }}>
            <ArrowUp strokeWidth={2} />
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}
