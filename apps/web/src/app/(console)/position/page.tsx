"use client";
import Link from "next/link";
import { useState } from "react";
import { api, type Leg } from "@/lib/api";
import { useLive, usePolled } from "@/components/Live";
import { FundingChart } from "@/components/FundingChart";
import { Confirm, Empty, Loading, PageHead } from "@/components/ui";
import { ATTENTION, STATUS_LABEL, date, pct, price, signedPct, usd } from "@/lib/format";

function legName(l: Leg): string {
  const s = l.symbol;
  if (s.startsWith("P-")) return "Short put";
  if (s.startsWith("C-")) return "Long call";
  return l.size < 0 ? "Short perp" : "Long perp";
}

function LegCard({ l }: { l: Leg }) {
  const h = l.health_pct;
  const optionLong = l.symbol.startsWith("C-") && l.size > 0;
  return (
    <div className="leg">
      <div className="name">{legName(l)}</div>
      <div className="sym">{l.symbol}</div>
      <div className="size">{l.size > 0 ? `+${l.size}` : l.size}</div>
      <div className="caption">entry {price(l.entry_price)} · mark {price(l.mark)}</div>
      {optionLong ? (
        <p className="caption" style={{ marginTop: 10 }}>Paid in full. No margin, can’t be liquidated.</p>
      ) : (
        <>
          <div className={`bar ${h !== null && h < 70 ? "low" : ""}`} aria-label={`margin health ${h ?? 0}%`}>
            <span style={{ width: `${Math.max(0, Math.min(100, h ?? 0))}%` }} />
          </div>
          <div className="kv" style={{ fontSize: 13 }}>
            <span className="k">Margin health</span><span className="v">{h === null ? "—" : `${h.toFixed(0)}%`}</span>
            <span className="k">Margin</span><span className="v">{usd(l.margin)}</span>
            <span className="k">{l.symbol.startsWith("P-") ? "Liquidates if put price hits" : "Liquidates if BTC hits"}</span>
            <span className="v">{price(l.liquidation_price)}{l.distance_pct !== null ? ` (${signedPct(l.distance_pct, 1)})` : ""}</span>
          </div>
        </>
      )}
    </div>
  );
}

export default function PositionPage() {
  const { state, loading, refresh, toast } = useLive();
  const { data: funding } = usePolled(() => api.funding(168));
  const [closing, setClosing] = useState(false);

  if (loading) return <Loading />;
  if (!state) return null;
  const pos = state.position;
  const running = state.bot.mode === "running";

  if (!pos) {
    return (
      <div className="stack">
        <PageHead title="Position" support="Your open fixed-vs-floating position, its hedge, margin and why the bot is doing what it does." />
        <Empty title="No open position">
          <span>The bot is {running ? "scanning" : "paused"}. <Link href="/offers">Review today’s offer</Link>.</span>
        </Empty>
        {funding && <div className="panel"><h2 className="section-title">Funding, last 7 days</h2><FundingChart data={funding} /></div>}
      </div>
    );
  }

  const attention = ATTENTION.has(pos.status);
  // Synthetic forward (long call + short put, same strike) has delta exactly +1 per contract; it cancels the short perp
  // only when the three sizes match.
  const size = (p: string) => pos.legs.find((l) => l.symbol.startsWith(p))?.size ?? 0;
  const perpSize = pos.legs.find((l) => !l.symbol.startsWith("C-") && !l.symbol.startsWith("P-"))?.size ?? 0;
  const matched = size("C-") > 0 && size("C-") === -size("P-") && size("C-") === -perpSize;

  return (
    <div className="stack">
      <PageHead
        title={`${STATUS_LABEL[pos.status] ?? pos.status}`}
        support={pos.status_reason}
      >
        {running ? (
          <button className="btn" onClick={async () => { await api.pause(); toast("Bot paused: no new entries, risk checks keep running"); refresh(); }}>
            Pause bot
          </button>
        ) : (
          <button className="btn" onClick={async () => { await api.start(state.bot.auto_enter); toast("Bot running"); refresh(); }}>
            Resume bot
          </button>
        )}
        <button className="btn primary" onClick={() => setClosing(true)}>Close all</button>
      </PageHead>

      <div className={`offer ${attention ? "attention" : ""}`}>
        <div className="offer-body">
          <div className="kicker" style={{ marginBottom: 14 }}>
            BTC · {date(pos.expiry_ts)} · strike {price(pos.strike)} · {pos.days_left.toFixed(1)} days left
          </div>
          <p className="statement">
            You locked <span className="num">{pct(pos.fixed_apr)}</span>. Funding now{" "}
            <span className="num">{pct(pos.floating_apr)}</span>.{" "}
            <span className="faint">Spread {signedPct(pos.spread_apr)} / yr.</span>
          </p>
        </div>
        <div className="field-dots">
          <div className="stats four">
            <div className="stat"><div className="fig blue">{usd(pos.earned_usd, true)}</div><div className="lbl">Earned so far (funding {usd(pos.funding_usd, true)} − fees {usd(pos.fees_usd)})</div></div>
            <div className="stat"><div className="fig">{usd(pos.projected_usd, true)}</div><div className="lbl">Projected at expiry</div></div>
            <div className="stat"><div className="fig">{usd(pos.worst_case_usd, true)}</div><div className="lbl">Worst case (2y history, with exit rule)</div></div>
            <div className="stat"><div className="fig">{usd(pos.reserve_usd)}</div><div className="lbl">Reserve for margin top-ups</div></div>
          </div>
        </div>
      </div>

      <div className="panel">
        <h2 className="section-title">Funding vs your fixed rate</h2>
        {funding ? <FundingChart data={{ ...funding, fixed_apr: funding.fixed_apr ?? pos.fixed_apr }} /> : <Loading />}
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <h2 className="section-title">Hedge and margin</h2>
          {matched ? (
            <span className="chip ok">Hedged: net BTC exposure 0</span>
          ) : (
            <span className="chip critical">Leg sizes don’t match: bot is unwinding</span>
          )}
        </div>
        <div className="legs">
          {pos.legs.map((l) => <LegCard key={l.symbol} l={l} />)}
        </div>
        <p className="caption" style={{ marginTop: 8 }}>
          The bot tops up a leg from the reserve when its margin health falls below 50%, warns at 70%, and closes half of
          all three legs (staying hedged) if the reserve runs out.
        </p>
      </div>

      <div className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 className="section-title">Why the bot did what it did</h2>
          <Link href="/activity">All activity</Link>
        </div>
        <RecentEvents />
      </div>

      {closing && (
        <Confirm
          title="Close all three legs?"
          confirmLabel="Close position"
          onClose={() => setClosing(false)}
          onConfirm={async () => {
            const r = await api.close();
            if (!r.ok) throw new Error(r.message);
            toast(r.message || "Position closed");
            await refresh();
          }}
        >
          <p className="muted" style={{ margin: 0 }}>
            Reduce-only orders, in this order, so you are never left unhedged. Closing early costs about the spread on both
            options plus the perp fee.
          </p>
          <ol className="numbered">
            <li><b>01</b>Buy back the put</li>
            <li><b>02</b>Sell the call</li>
            <li><b>03</b>Buy back the perp</li>
          </ol>
        </Confirm>
      )}
    </div>
  );
}

function RecentEvents() {
  const { data } = usePolled(() => api.events(8));
  if (!data) return <Loading />;
  if (data.events.length === 0) return <p className="muted">No events yet.</p>;
  return (
    <ul className="numbered" style={{ margin: 0 }}>
      {data.events.map((e) => (
        <li key={e.id}>
          <b style={{ minWidth: 92, color: "#7f7f7f", fontWeight: 400 }}>
            {new Date(e.ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
          </b>
          <span>{e.message}</span>
        </li>
      ))}
    </ul>
  );
}
