"use client";
import { useEffect, useState } from "react";
import { api, type State } from "@/lib/api";

// Live market numbers under the hero. Hidden entirely when the API is down: the landing never shows fake data.
export function LiveStrip() {
  const [s, setS] = useState<State | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.state().then((d) => alive && setS(d)).catch(() => alive && setS(null));
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!s) return null;
  const m = s.market;
  const best = s.decision?.best;
  return (
    <div className="lp-live">
      <span className="lp-live-dot" />
      {m.funding_now_apr != null && <span>Live · funding now <b>{m.funding_now_apr.toFixed(2)}%</b></span>}
      {m.funding_avg24_apr != null && <span>24h avg <b>{m.funding_avg24_apr.toFixed(2)}%</b></span>}
      {best && best.fixed_apr != null && <span>best fixed <b>{best.fixed_apr.toFixed(2)}%</b></span>}
      <span>{s.decision?.action === "enter" ? "offer available" : "no trade right now"}</span>
    </div>
  );
}
