import type { SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cx } from "@/lib/cx";
import styles from "./Select.module.css";

export function Select({ className, size, ...rest }: Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> & { size?: "md" | "sm" }) {
  return (
    <span className={cx(styles.wrap, size === "sm" && styles.sm, className)}>
      <select className={styles.select} {...rest} />
      <ChevronDown className={styles.chevron} />
    </span>
  );
}
