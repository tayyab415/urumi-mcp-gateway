import { FilePen, FilePlus, FileText, List, Package, Search, Sparkles, SquareTerminal, Truck, type LucideProps } from "lucide-react";
import type { StepKind } from "@/lib/labels";

const ICONS: Record<StepKind, React.ComponentType<LucideProps>> = {
  search: Search, read: FileText, edit: FilePen, create: FilePlus, truck: Truck, package: Package, shell: SquareTerminal, plan: Sparkles, list: List,
};

export function StepIcon({ kind, size = 16, ...rest }: LucideProps & { kind: StepKind }) {
  const Icon = ICONS[kind] ?? SquareTerminal;
  return <Icon size={size} strokeWidth={1.75} aria-hidden {...rest} />;
}
