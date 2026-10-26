import { useSyncExternalStore } from "react";
import { useReducedMotion } from "motion/react";

const subscribe = () => () => {};
/** Hydration-safe reduced-motion flag: false on the first paint, then the real preference. */
export function useReducedFlag(): boolean {
  const hydrated = useSyncExternalStore(subscribe, () => true, () => false);
  return Boolean(useReducedMotion()) && hydrated;
}
