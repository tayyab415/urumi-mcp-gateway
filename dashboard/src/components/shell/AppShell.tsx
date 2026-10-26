import { useId, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";
import { LayoutGroup, motion } from "motion/react";
import { Activity, FileText, House, KeyRound, Link2, LogOut, MessageSquare, Server, ShieldCheck, Store, Users } from "lucide-react";
import { useAuth } from "@/state/auth";
import { morph } from "@/lib/motion";
import { cx } from "@/lib/cx";
import { AgentGlyph } from "@/components/ui/AgentGlyph";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { BackgroundRunPill } from "@/components/run-card/BackgroundRunPill";
import styles from "./AppShell.module.css";

interface NavItem { to: string; label: string; icon: typeof House; end?: boolean }
const ADMIN_NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: House, end: true },
  { to: "/servers", label: "Servers", icon: Server },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/team", label: "Team", icon: Users },
  { to: "/connect", label: "Connect", icon: Link2 },
  { to: "/audit", label: "Audit log", icon: FileText },
  { to: "/permissions", label: "Permissions", icon: ShieldCheck },
  { to: "/stores", label: "Stores", icon: Store },
  { to: "/lab", label: "Design lab", icon: Activity },
];
const MEMBER_NAV: NavItem[] = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/stores", label: "Stores", icon: Store },
  { to: "/connect", label: "Connect", icon: KeyRound },
  { to: "/audit", label: "Audit log", icon: FileText },
];

export function AppShell({ children, dock }: { children: ReactNode; dock?: ReactNode }) {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const groupId = useId();
  const nav = me?.role === "admin" ? ADMIN_NAV : MEMBER_NAV;
  return (
    <div className={styles.shell}>
      <aside className={styles.rail}>
        <div className={styles.brand}>
          <AgentGlyph size={20} />
          <span>Urumi</span>
        </div>
        <LayoutGroup id={groupId}>
          <nav className={styles.nav} aria-label="Pages">
            {nav.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => cx(styles.item, isActive && styles.active)}>
                {({ isActive }) => (
                  <>
                    {isActive && <motion.span layoutId="nav-fill" className={styles.fill} transition={morph} />}
                    <Icon />
                    <span>{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </LayoutGroup>
        <div className={styles.spacer} />
        {location.pathname !== "/chat" && <div className={styles.pill}><BackgroundRunPill /></div>}
        {me && (
          <div className={styles.user}>
            <Avatar name={me.name} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className={styles.userName}>{me.name}</div>
              <div className={styles.userRole}>{me.role}</div>
            </div>
            <Button variant="ghost" size="sm" icon={<LogOut />} aria-label="Sign out" title="Sign out" onClick={() => void logout().then(() => navigate("/login"))} />
          </div>
        )}
      </aside>
      <div className={styles.main}>
        <main className={styles.content}>
          <div className={styles.contentInner}>{children}</div>
        </main>
        {dock && <aside className={styles.dock}>{dock}</aside>}
      </div>
    </div>
  );
}
