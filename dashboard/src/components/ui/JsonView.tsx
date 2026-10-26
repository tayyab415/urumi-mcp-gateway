/** A small pretty-printer for tool arguments and results. Enough for review; not a full viewer. */
export function JsonView({ value, maxHeight = 240 }: { value: unknown; maxHeight?: number }) {
  let text: string;
  if (typeof value === "string") {
    try { text = JSON.stringify(JSON.parse(value), null, 2); } catch { text = value; }
  } else {
    text = JSON.stringify(value, null, 2) ?? "";
  }
  return (
    <pre style={{ maxHeight, overflow: "auto", margin: 0, padding: "10px 12px", borderRadius: 12, border: "1px solid var(--border)", background: "var(--background)", color: "var(--text-secondary)", fontSize: 12.5, lineHeight: "19px" }}>{text || "(empty)"}</pre>
  );
}
