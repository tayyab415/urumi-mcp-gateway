import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Card.module.css";

export function Card({ padded, floating, className, ...rest }: HTMLAttributes<HTMLDivElement> & { padded?: boolean; floating?: boolean }) {
  return <div className={cx(styles.card, padded && styles.padded, floating && styles.floating, className)} {...rest} />;
}

export function CardHeader({ title, subtitle, children, className }: { title: ReactNode; subtitle?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div className={cx(styles.header, className)}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className={styles.title}>{title}</div>
        {subtitle && <div className={styles.subtitle}>{subtitle}</div>}
      </div>
      {children}
    </div>
  );
}

export const Divider = ({ className }: { className?: string }) => <hr className={cx(styles.divider, className)} />;
