import { useEffect } from "react";
import { AnimatePresence, animate, motion, useMotionValue } from "motion/react";
import { C, SPIN_MS, fade, snap } from "@/lib/motion";
import { useReducedFlag } from "@/lib/reduced";
import { AgentGlyph } from "./AgentGlyph";

export type OrbState = "running" | "paused" | "waiting" | "done" | "failed" | "unknown" | "idle" | "stopped" | "checking";

const R = 10;
const CIRC = 2 * Math.PI * R;

/** The status orb: a track ring with a rotating white arc while running. Green ring and check when done, a blue dot when the user is needed.
 *  The rotation is a motion value so the done ring continues from the arc's current angle instead of snapping. */
export function StatusOrb({ state, size = 22, glyph = true, className }: { state: OrbState; size?: number; glyph?: boolean; className?: string }) {
  const rotate = useMotionValue(0);
  const reduced = useReducedFlag();
  const spinning = state === "running" || state === "checking";

  useEffect(() => {
    if (!spinning || reduced) return;
    const controls = animate(rotate, rotate.get() + 360, { duration: SPIN_MS / 1000, ease: "linear", repeat: Infinity });
    return () => controls.stop(); // keeps the current angle for paused and done
  }, [spinning, reduced, rotate]);

  const trackColor = state === "failed" ? C.danger : state === "unknown" ? C.warning : "var(--border)";
  const arc =
    state === "done" ? { strokeDasharray: `${CIRC} ${CIRC}`, stroke: C.success, opacity: 1 }
    : spinning || state === "paused" ? { strokeDasharray: `${CIRC / 4} ${CIRC}`, stroke: C.text1, opacity: reduced && spinning ? [0.5, 1, 0.5] : 1 }
    : { strokeDasharray: `${CIRC / 4} ${CIRC}`, stroke: C.text1, opacity: 0 };

  return (
    <div className={className} style={{ position: "relative", width: size, height: size, flex: "none" }} aria-hidden>
      <svg viewBox="0 0 22 22" width={size} height={size} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
        <circle cx="11" cy="11" r={R} fill={glyph ? "var(--orb-fill)" : "transparent"} strokeWidth="1.5" style={{ stroke: trackColor, transition: "stroke 200ms" }} />
        <motion.circle
          cx="11" cy="11" r={R} fill="none" strokeWidth="1.5" strokeLinecap="round"
          style={{ rotate, transformOrigin: "11px 11px" }}
          initial={false}
          animate={arc}
          transition={{ ...snap, strokeDasharray: { duration: 0.2, ease: "easeOut" }, opacity: reduced && spinning ? { duration: 1.6, repeat: Infinity, ease: "easeInOut" } : fade }}
        />
      </svg>
      <AnimatePresence initial={false} mode="popLayout">
        {state === "done" ? (
          <motion.svg key="check" viewBox="0 0 22 22" width={size} height={size} style={{ position: "absolute", inset: 0 }}
            initial={{ scale: reduced ? 1 : 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ opacity: 0, transition: fade }} transition={{ ...snap, delay: 0.1 }}>
            <path d="M7.3 11.2l2.4 2.4 5-5.2" fill="none" stroke={C.success} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </motion.svg>
        ) : state === "failed" ? (
          <motion.svg key="x" viewBox="0 0 22 22" width={size} height={size} style={{ position: "absolute", inset: 0 }}
            initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ opacity: 0, transition: fade }} transition={snap}>
            <path d="M8 8l6 6M14 8l-6 6" fill="none" stroke={C.danger} strokeWidth="1.6" strokeLinecap="round" />
          </motion.svg>
        ) : state === "unknown" ? (
          <motion.svg key="q" viewBox="0 0 22 22" width={size} height={size} style={{ position: "absolute", inset: 0 }}
            initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ opacity: 0, transition: fade }} transition={snap}>
            <path d="M8.6 9a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .8-1 1.5M11 15.6h.01" fill="none" stroke={C.warning} strokeWidth="1.6" strokeLinecap="round" />
          </motion.svg>
        ) : glyph ? (
          <motion.div key="glyph" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", opacity: state === "idle" || state === "stopped" ? 0.55 : 1 }}
            initial={{ opacity: 0 }} animate={{ opacity: state === "idle" || state === "stopped" ? 0.55 : 1 }} exit={{ opacity: 0, scale: 0.8, transition: fade }}>
            <AgentGlyph size={Math.round(size / 2)} />
          </motion.div>
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {state === "waiting" && (
          <motion.span
            style={{ position: "absolute", right: -1, top: -1, width: 7, height: 7, borderRadius: "50%", background: "var(--accent)", boxShadow: "0 0 0 2px var(--surface)" }}
            initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }} transition={snap}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
