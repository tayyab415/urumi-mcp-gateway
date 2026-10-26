import { motion, LayoutGroup } from "motion/react";
import { useId, type ReactNode } from "react";
import { morph } from "@/lib/motion";
import { cx } from "@/lib/cx";
import styles from "./Tabs.module.css";

export interface TabItem<T extends string> { value: T; label: ReactNode; count?: number }

/** A neutral segmented tab bar. The selected pill is a shared layout highlight, never blue. */
export function Tabs<T extends string>({ items, value, onChange, className, ariaLabel }: { items: TabItem<T>[]; value: T; onChange: (v: T) => void; className?: string; ariaLabel?: string }) {
  const id = useId();
  return (
    <LayoutGroup id={id}>
      <div className={cx(styles.tabs, className)} role="tablist" aria-label={ariaLabel}>
        {items.map((item) => (
          <button key={item.value} type="button" role="tab" aria-selected={item.value === value} className={styles.tab} onClick={() => onChange(item.value)}>
            {item.value === value && <motion.span layoutId="tab-highlight" className={styles.highlight} transition={morph} />}
            <span style={{ position: "relative" }}>{item.label}</span>
            {item.count !== undefined && <span className={styles.count} style={{ position: "relative" }}>{item.count}</span>}
          </button>
        ))}
      </div>
    </LayoutGroup>
  );
}
