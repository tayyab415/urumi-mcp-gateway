import { cx } from "@/lib/cx";
import styles from "./Skeleton.module.css";

export function Skeleton({ width = "100%", height = 12, circle, className, style }: { width?: number | string; height?: number; circle?: boolean; className?: string; style?: React.CSSProperties }) {
  return <span aria-hidden className={cx(styles.bar, circle && styles.circle, className)} style={{ width, height, ...style }} />;
}

export function SkeletonRows({ rows = 3, height = 28 }: { rows?: number; height?: number }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, height }}>
          <Skeleton width={16} height={16} circle />
          <Skeleton width={`${55 - i * 9}%`} />
          <Skeleton width={32} style={{ marginLeft: "auto" }} />
        </div>
      ))}
    </div>
  );
}
