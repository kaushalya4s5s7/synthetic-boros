"use client";
// Polls /api/state and /api/health every POLL_MS and shares them with every page.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Health, type State } from "@/lib/api";
import { POLL_MS } from "@/lib/config";

interface LiveCtx {
  state: State | null;
  health: Health | null;
  error: string | null;
  loading: boolean;
  now: number;
  refresh: () => Promise<void>;
  toast: (msg: string) => void;
}

const Ctx = createContext<LiveCtx | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h);
      try {
        setState(await api.state());
        setError(null);
      } catch (e) {
        // health answered but state failed (e.g. key not whitelisted): keep health, report the error
        setError((e as Error).message);
      }
    } catch (e) {
      setError((e as Error).message);
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    const clock = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => {
      clearInterval(id);
      clearInterval(clock);
    };
  }, [refresh]);

  const toast = useCallback((m: string) => {
    setMsg(m);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(null), 4000);
  }, []);

  return (
    <Ctx.Provider value={{ state, health, error, loading, now, refresh, toast }}>
      {children}
      {msg && <div className="toast" role="status">{msg}</div>}
    </Ctx.Provider>
  );
}

export function useLive(): LiveCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useLive outside LiveProvider");
  return c;
}

/** Fetch a resource once and again whenever the shared state ticks. */
export function usePolled<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const { state } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const tick = state?.bot.last_tick_ts ?? 0;
  useEffect(() => {
    let alive = true;
    load()
      .then((d) => alive && (setData(d), setErr(null)))
      .catch((e) => alive && setErr((e as Error).message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);
  return { data, err, setData };
}
