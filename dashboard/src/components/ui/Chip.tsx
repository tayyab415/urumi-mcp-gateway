import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Chip.module.css";

export function Chip({ selected, icon, children, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean; icon?: ReactNode; children: ReactNode }) {
  return (
    <button type="button" className={cx(styles.chip, className)} aria-pressed={selected} {...rest}>
      {icon}
      {children}
    </button>
  );
}

export const StaticChip = ({ icon, children, className }: { icon?: ReactNode; children: ReactNode; className?: string }) => (
  <span className={cx(styles.chip, styles.static, className)}>{icon}{children}</span>
);
