import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router";
import { MotionConfig } from "motion/react";
import { AuthProvider, useAuth } from "@/state/auth";
import { ChatProvider } from "@/state/chat";
import { ToastProvider } from "@/components/ui/Toast";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { AppShell } from "@/components/shell/AppShell";
import { DockedAssistant } from "@/components/chat/DockedAssistant";
import { LoginPage } from "@/pages/Login";
import { AcceptInvitePage } from "@/pages/AcceptInvite";
import { OverviewPage } from "@/pages/Overview";
import { ServersPage } from "@/pages/Servers";
import { ChatPage } from "@/pages/Chat";
import { TeamPage } from "@/pages/Team";
import { ConnectPage } from "@/pages/Connect";
import { AuditPage } from "@/pages/Audit";
import { PermissionsPage } from "@/pages/Permissions";
import { StoresPage } from "@/pages/Stores";
import { LabPage } from "@/pages/Lab";

function Protected() {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  const docked = location.pathname !== "/chat" && location.pathname !== "/lab";
  return (
    <ChatProvider>
      <AppShell dock={docked ? <ErrorBoundary resetKey={location.pathname}><DockedAssistant /></ErrorBoundary> : undefined}>
        <ErrorBoundary resetKey={location.pathname}><Outlet /></ErrorBoundary>
      </AppShell>
    </ChatProvider>
  );
}

function AdminOnly() {
  const { me } = useAuth();
  return me?.role === "admin" ? <Outlet /> : <Navigate to="/chat" replace />;
}

function Home() {
  const { me } = useAuth();
  return me?.role === "admin" ? <OverviewPage /> : <Navigate to="/chat" replace />;
}

export function App() {
  return (
    <MotionConfig reducedMotion="user">
      <AuthProvider>
        <ToastProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/accept-invite" element={<AcceptInvitePage />} />
              <Route element={<Protected />}>
                <Route index element={<Home />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/connect" element={<ConnectPage />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/lab" element={<LabPage />} />
                {/* Any signed-in person may create stores; the gateway checks owner and limits. */}
                <Route path="/stores" element={<StoresPage />} />
                <Route element={<AdminOnly />}>
                  <Route path="/servers" element={<ServersPage />} />
                  <Route path="/team" element={<TeamPage />} />
                  <Route path="/permissions" element={<PermissionsPage />} />
                </Route>
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </ToastProvider>
      </AuthProvider>
    </MotionConfig>
  );
}
