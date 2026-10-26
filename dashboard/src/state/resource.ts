import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | null;
  error: string | null;
  loading: boolean; // first load only
  refreshing: boolean;
  reload: () => Promise<void>;
  setData: (updater: T | ((prev: T | null) => T | null)) => void;
}

/** Fetch once, keep the last good value, optionally poll while the tab is visible. */
export function useResource<T>(fetcher: () => Promise<T>, deps: unknown[], options: { interval?: number; enabled?: boolean } = {}): Resource<T> {
  const { interval = 0, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const reload = useCallback(async () => {
    if (!enabled) return;
    setRefreshing(true);
    try {
      const next = await fetchRef.current();
      if (alive.current) { setData(next); setError(null); }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (alive.current) { setLoading(false); setRefreshing(false); }
    }
  }, [enabled]);

  useEffect(() => {
    alive.current = true;
    if (enabled) { setLoading((l) => l || data === null); void reload(); }
    return () => { alive.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  useEffect(() => {
    if (!interval || !enabled) return;
    const tick = () => { if (document.visibilityState === "visible") void reload(); };
    const t = setInterval(tick, interval);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", tick); };
  }, [interval, enabled, reload]);

  return { data, error, loading, refreshing, reload, setData: (u) => setData((prev) => (typeof u === "function" ? (u as (p: T | null) => T | null)(prev) : u)) };
}
