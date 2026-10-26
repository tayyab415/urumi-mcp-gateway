import type { HTMLAttributes } from "react";
import { cx } from "@/lib/cx";
import styles from "./Table.module.css";

export const Table = ({ className, ...rest }: HTMLAttributes<HTMLTableElement>) => <table className={cx(styles.table, className)} {...rest} />;
export const tableStyles = styles;
