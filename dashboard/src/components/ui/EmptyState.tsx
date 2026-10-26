import type { ReactNode } from "react";

/** One line of muted text and, at most, one secondary button. No illustrations. */
export function EmptyState({ children, action, inline }: { children: ReactNode; action?: ReactNode; inline?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: inline ? "row" : "column", alignItems: "center", justifyContent: inline ? "space-between" : "center", gap: 12, padding: inline ? "12px 0" : "32px 16px", color: "var(--text-muted)", fontSize: "var(--text-sm)" }}>
      <span>{children}</span>
      {action}
    </div>
  );
}
