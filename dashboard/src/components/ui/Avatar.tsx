import { initials } from "@/lib/format";

export function Avatar({ name, size = 32 }: { name: string; size?: number }) {
  return (
    <span aria-hidden style={{ width: size, height: size, borderRadius: "50%", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text-secondary)", display: "inline-grid", placeItems: "center", fontSize: size <= 28 ? 11 : 12, fontWeight: 500, flex: "none", letterSpacing: 0 }}>
      {initials(name)}
    </span>
  );
}
