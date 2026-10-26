import { motion } from "motion/react";
import { snap } from "@/lib/motion";
import { cx } from "@/lib/cx";
import styles from "./Switch.module.css";

export function Switch({ checked, onChange, disabled, label, className }: { checked: boolean; onChange: (next: boolean) => void; disabled?: boolean; label: string; className?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={cx(styles.switch, className)}
      onClick={() => onChange(!checked)}
    >
      <motion.span className={styles.thumb} animate={{ x: checked ? 16 : 0 }} transition={snap} initial={false} />
    </button>
  );
}
