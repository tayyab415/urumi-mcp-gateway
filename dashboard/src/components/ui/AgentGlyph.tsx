import type { SVGProps } from "react";

/** A 12-ray starburst in one warm coral. It never moves; only the ring around it does. */
export function AgentGlyph({ size = 11, ...rest }: SVGProps<SVGSVGElement> & { size?: number }) {
  const rays = Array.from({ length: 12 }, (_, i) => (i * 360) / 12);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden fill="none" stroke="var(--glyph-warm)" strokeWidth="2.6" strokeLinecap="round" {...rest}>
      {rays.map((deg) => (
        <line key={deg} x1="12" y1="2.5" x2="12" y2="8.5" transform={`rotate(${deg} 12 12)`} />
      ))}
    </svg>
  );
}
