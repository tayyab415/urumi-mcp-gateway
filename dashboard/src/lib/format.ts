/** Elapsed run time as m:ss. */
export const fmtElapsed = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** Live durations in whole seconds, settled ones with one decimal. */
export const fmtDuration = (ms: number, settled: boolean) =>
  settled ? `${(Math.max(0, ms) / 1000).toFixed(1)}s` : `${Math.max(0, Math.floor(ms / 1000))}s`;

/** Milliseconds for tables: "118 ms" under a second, "2.4s" above. */
export const fmtMs = (ms: number | null | undefined) => {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)}s`;
};

export const fmtInt = (n: number) => n.toLocaleString("en-US");

export const fmtPct = (num: number, den: number) => (den === 0 ? "0%" : `${((num / den) * 100).toFixed(num / den < 0.1 ? 1 : 0)}%`);

/** "just now", "12s ago", "4m ago", "3h ago", "2 days ago". */
export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const diff = Math.max(0, now - Date.parse(iso));
  const s = Math.floor(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("") || "?";

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
