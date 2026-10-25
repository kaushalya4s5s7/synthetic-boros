"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useLive } from "./Live";
import { ago } from "@/lib/format";

const NAV = [
  { href: "/overview", label: "Overview" },
  { href: "/offers", label: "Offers" },
  { href: "/position", label: "Position" },
  { href: "/activity", label: "Activity" },
  { href: "/settings", label: "Settings" },
  { href: "/guide", label: "Guide" },
];

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { state, health, error, now } = useLive();
  const env = (health?.trading_env || "testnet").toUpperCase();
  const src = (health?.funding_source || state?.market.funding_source || "mainnet").toUpperCase();
  const running = state?.bot.mode === "running";
  const lastTick = state?.bot.last_tick_ts ?? health?.last_tick_ts ?? null;
  const override = state?.market.override_apr;

  return (
    <>
      <header className="topbar">
        <Link href="/" className="logo">Fixed Funding</Link>
        <div className="topbar-right">
          <span className="pill hide-sm" title="Orders go to the trading venue; funding for decisions is read from the live market">
            {env} · funding from {src}
          </span>
          {override !== null && override !== undefined && (
            <span className="pill soft" title="Funding input is overridden on the Settings page">Rehearsal {override}%</span>
          )}
          {state?.account && (
            <span className="pill" title="Your Delta wallet: equity, and what is free for new trades">
              Wallet ${state.account.equity.toFixed(2)} · free ${state.account.available.toFixed(2)}
            </span>
          )}
          <span className={`pill ${running ? "soft" : ""}`}>
            <span className={`dot ${running ? "on" : ""}`} />
            {health ? (running ? "Bot running" : "Bot paused") : "Bot offline"}
          </span>
          <span className="caption hide-sm">tick {ago(lastTick, now)}</span>
        </div>
      </header>
      <div className="layout">
        <nav className="rail" aria-label="Main">
          <div className="rail-group">Console</div>
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={path === n.href ? "active" : ""}>
              {n.label}
            </Link>
          ))}
        </nav>
        <main className="main">
          <div className="page">
            {!health && error && (
              <div className="banner" role="alert">
                <strong>Bot API unreachable.</strong> The console can’t reach the backend. Start it with{" "}
                <code>uvicorn main:app --port 8000</code> from the repo root. Retrying every 5 seconds.
              </div>
            )}
            {state?.bot.stale && (
              <div className="banner" role="alert">
                <strong>Market data is stale.</strong> The bot has frozen all actions until fresh prices arrive.
              </div>
            )}
            {children}
          </div>
        </main>
      </div>
    </>
  );
}
