import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 20 }}>
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 500, letterSpacing: "-0.015em", lineHeight: 1.2 }}>{title}</h1>
        {subtitle && <p style={{ color: "var(--text-muted)", marginTop: 4 }}>{subtitle}</p>}
      </div>
      {actions && <div style={{ display: "flex", gap: 8, alignItems: "center", flex: "none" }}>{actions}</div>}
    </div>
  );
}

export const Section = ({ title, children, right }: { title: ReactNode; children: ReactNode; right?: ReactNode }) => (
  <section style={{ marginTop: 24 }}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
      <h2 style={{ fontSize: 15, fontWeight: 500 }}>{title}</h2>
      {right}
    </div>
    {children}
  </section>
);
