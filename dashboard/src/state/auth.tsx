import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, UNAUTHORIZED_EVENT, api, type Me } from "@/lib/api";

interface Auth {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** The public demo: sign in as a person picked from the list. */
  loginAs: (userId: number) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

/** Demo builds (scripts/dev-tunnel.sh) sign in on their own so a shared link opens straight on the dashboard.
 *  Off unless the build sets VITE_DEMO_LOGIN="email:password". Signing out turns it off for the tab. */
const DEMO_LOGIN = (import.meta.env.VITE_DEMO_LOGIN as string | undefined)?.split(/:(.*)/s);
const SIGNED_OUT = "urumi.signedOut";

const Ctx = createContext<Auth>({ me: null, loading: true, login: async () => {}, loginAs: async () => {}, logout: async () => {}, refresh: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.auth.me());
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null);
      else throw e;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      const whoAmI = () => api.auth.me().catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      });
      try {
        let current = await whoAmI();
        if (!current && DEMO_LOGIN && !sessionStorage.getItem(SIGNED_OUT)) {
          await api.auth.login(DEMO_LOGIN[0] ?? "", DEMO_LOGIN[1] ?? "");
          current = await whoAmI();
        }
        setMe(current);
      } catch {
        setMe(null);
      } finally {
        setLoading(false);
      }
    })();
    const onUnauthorized = () => setMe(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    await api.auth.login(email, password);
    sessionStorage.removeItem(SIGNED_OUT);
    await refresh();
  }, [refresh]);

  const loginAs = useCallback(async (userId: number) => {
    await api.auth.demoLogin(userId);
    sessionStorage.removeItem(SIGNED_OUT);
    await refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    sessionStorage.setItem(SIGNED_OUT, "1");
    try { await api.auth.logout(); } finally { setMe(null); }
  }, []);

  const value = useMemo(() => ({ me, loading, login, loginAs, logout, refresh }), [me, loading, login, loginAs, logout, refresh]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
