export const Kbd = ({ children }: { children: string }) => (
  <kbd style={{ display: "inline-block", padding: "1px 6px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--surface)", color: "var(--text-muted)", fontSize: 11, lineHeight: "16px" }}>{children}</kbd>
);
