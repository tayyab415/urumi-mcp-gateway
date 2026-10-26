/** Motion presets for the dashboard. All of them come from Arc's tokens; the
 *  numbers are the reference clip's timings converted to real time (x1.35). */
import { motionTokens as t } from "@/lib/motion-tokens";

type Bezier = [number, number, number, number];
const bez = (e: readonly number[]): Bezier => [e[0]!, e[1]!, e[2]!, e[3]!];

export const settle = t.spring.smooth; // card size, rows, lists, panel heights
export const morph = t.spring.morph; // widths that follow new text
export const snap = t.spring.snappy; // segment fill, presses, pops
export const fade = { duration: t.duration.fast, ease: bez(t.ease.standard) } as const;
export const enterEase = bez(t.ease.enter);
export const standardEase = bez(t.ease.standard);
export const exitEase = bez(t.ease.exit);

/** Text swap: the new label rises from a soft blur, the old one lifts out faster. */
export const rise = {
  initial: { opacity: 0, y: "0.3em", filter: `blur(${t.blur.soft}px)` },
  animate: { opacity: 1, y: "0em", filter: "blur(0px)", transition: { duration: t.duration.standard, ease: enterEase } },
  exit: { opacity: 0, y: "-0.24em", filter: `blur(${t.blur.subtle}px)`, transition: { duration: t.duration.fast, ease: standardEase } },
} as const;

/** Smaller version for 14 px rows: 8 px of travel and a 2 px blur, as measured in the clip. */
export const roll = {
  initial: { y: 8, opacity: 0, filter: "blur(2px)" },
  animate: { y: 0, opacity: 1, filter: "blur(0px)" },
  exit: { y: -8, opacity: 0, filter: "blur(2px)" },
} as const;

export const SPIN_MS = 900; // one turn of the orb arc, real time
export const SHIMMER = { sweepMs: 570, periodMs: 2400, delayMs: 570 } as const;
export const ROW_H = 28;
export const MAX_ROWS = 5;
export const TASK_ROW = 38;

/** Hex copies for motion color tweens: motion cannot interpolate CSS variables.
 *  Values are Arc's dark theme with the blue accent. */
export const C = {
  surface: "#191919",
  surfaceHover: "#242424",
  border: "#2b2b2b",
  accent: "#6ba3ff",
  accentHover: "#639aee",
  accentPressed: "#5a8bd8",
  text1: "#f2f2f2",
  text2: "#b4b4b4",
  text3: "#8c8c8c",
  success: "#4cd676",
  danger: "#ff736d",
  warning: "#ffb90e",
  onAccent: "#0b0b0b",
} as const;

export const stagger = t.stagger;
export const blur = t.blur;
export const durations = t.duration;
