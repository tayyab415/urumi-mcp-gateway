import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CircleAlert, CircleCheck, CircleX, Info } from "lucide-react";
import { fade, settle } from "@/lib/motion";
import styles from "./Toast.module.css";

type Tone = "success" | "danger" | "warning" | "neutral";
interface Toast { id: number; text: string; tone: Tone; action?: ReactNode }
const Ctx = createContext<(text: string, tone?: Tone, action?: ReactNode) => void>(() => {});

/** Toasts are for background work only; foreground actions confirm in place. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string, tone: Tone = "neutral", action?: ReactNode) => {
    const id = ++seq.current;
    setItems((xs) => [...xs.slice(-3), { id, text, tone, action }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5000);
  }, []);
  const icons = useMemo(() => ({ success: CircleCheck, danger: CircleX, warning: CircleAlert, neutral: Info }), []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className={styles.stack} aria-live="polite">
        <AnimatePresence initial={false}>
          {items.map((t) => {
            const Icon = icons[t.tone];
            return (
              <motion.div key={t.id} className={styles.toast} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: fade }} transition={settle}>
                <Icon className={styles[t.tone]} />
                <span className={styles.text}>{t.text}</span>
                {t.action}
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
