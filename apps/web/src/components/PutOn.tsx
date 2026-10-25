"use client";
import type { Offer } from "@/lib/api";
import { date, pct, price, usd } from "@/lib/format";

// "What exactly am I putting on?" for one offer: the three orders, the cash each one moves,
// and a worked example showing the asset's price cancelling out at expiry.
export function PutOn({ o, funding, example }: { o: Offer; funding: number | null; example?: boolean }) {
  if (o.put_px == null || o.call_px == null || o.perp_px == null) return null;
  const putPx: number = o.put_px, callPx: number = o.call_px, perpPx: number = o.perp_px;
  const u = o.underlying;
  const cv = o.contract_value || 0.001;
  const qty = o.contracts * cv;                     // amount of the asset each leg covers
  const putCash = putPx * qty;                   // received
  const callCash = callPx * qty;                 // paid
  const notional = perpPx * qty;
  const forward = o.strike + callPx - putPx;
  const lockedCost = (forward - perpPx) * qty;   // what the fixed leg costs over the hold
  const rate = o.forecast_apr ?? funding;            // the bot's forecast for THIS offer's asset
  const fundingIn = rate == null ? null : (notional * rate) / 100 * (o.days / 365);
  const up = Math.round(perpPx * 1.1), down = Math.round(perpPx * 0.9);
  const priceLegs = (end: number) => (perpPx - end) * qty + (end - o.strike) * qty;   // perp short + (call - put)
  const fmtQty = `${+qty.toFixed(6)} ${u}`;

  return (
    <div className="panel">
      <div className="kicker" style={{ marginBottom: 10 }}>
        {example ? "Example: the closest candidate (rejected, shown so you can see the trade)" : "What you are putting on"}
      </div>
      <h2 className="section-title" style={{ marginTop: 0 }}>
        Three orders on Delta, {o.contracts} contract{o.contracts === 1 ? "" : "s"} each = {fmtQty} per leg
      </h2>

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>#</th><th>Order</th><th>Instrument</th><th className="num">Price</th><th className="num">Cash now</th><th>Why</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>1</td><td>Sell {o.contracts} put</td>
              <td>{o.put_symbol}<div className="caption">right to sell {u} at {price(o.strike)} on {date(o.expiry_ts)}</div></td>
              <td className="num">{price(putPx)}</td><td className="num">+{usd(putCash)}<div className="caption">you receive</div></td>
              <td className="muted">Half of the fixed leg</td>
            </tr>
            <tr>
              <td>2</td><td>Buy {o.contracts} call</td>
              <td>{o.call_symbol}<div className="caption">right to buy {u} at {price(o.strike)} on {date(o.expiry_ts)}</div></td>
              <td className="num">{price(callPx)}</td><td className="num">−{usd(callCash)}<div className="caption">you pay</div></td>
              <td className="muted">Call − put = a locked price to buy {u} at expiry ({price(forward)})</td>
            </tr>
            <tr>
              <td>3</td><td>Short {o.contracts} perp</td>
              <td>{u}USD perpetual<div className="caption">no expiry, funding every 8h</div></td>
              <td className="num">{price(perpPx)}</td><td className="num">margin only<div className="caption">{usd(notional / 5)} at 5x, stays yours</div></td>
              <td className="muted">The floating leg: shorts receive funding</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="split-2" style={{ marginTop: 20 }}>
        <div>
          <h3 className="mini-title">How you make money</h3>
          <div className="kv">
            <span className="k">Funding you receive ({pct(rate)} × {usd(notional)} × {o.days.toFixed(0)}/365)</span>
            <span className="v">{fundingIn == null ? "—" : `+${usd(fundingIn)}`}</span>
            <span className="k">Fixed cost you locked ({price(forward)} − {price(perpPx)}) × {fmtQty}</span>
            <span className="v">{lockedCost >= 0 ? "−" : "+"}{usd(Math.abs(lockedCost))}</span>
            <span className="k">Fees + spreads (entry and exit, with GST)</span>
            <span className="v">−{usd(o.cost_usd)}</span>
            <span className="k"><b>Expected net</b></span>
            <span className="v"><b>{usd(o.net_edge_usd, true)}</b></span>
          </div>
          <p className="caption" style={{ marginTop: 8 }}>
            Fixed rate = ({price(forward)} ÷ {price(perpPx)} − 1) × 365 ÷ {o.days.toFixed(0)} = <b>{pct(o.fixed_apr)}</b> a year.
            You profit when funding stays above it.
          </p>
        </div>
        <div>
          <h3 className="mini-title">Why the {u} price doesn’t matter</h3>
          <div className="kv">
            <span className="k">{u} ends at {price(up)} (+10%): perp loses, call−put gains</span>
            <span className="v">{usd(priceLegs(up), true)}</span>
            <span className="k">{u} ends at {price(perpPx)} (flat)</span>
            <span className="v">{usd(priceLegs(perpPx), true)}</span>
            <span className="k">{u} ends at {price(down)} (−10%): perp gains, call−put loses</span>
            <span className="v">{usd(priceLegs(down), true)}</span>
          </div>
          <p className="caption" style={{ marginTop: 8 }}>
            Same result every time: ({price(perpPx)} − {price(o.strike)}) × {fmtQty}. Add the option cash
            (+{usd(putCash)} − {usd(callCash)}) and it equals the fixed cost on the left. Only funding is left to earn.
          </p>
        </div>
      </div>

      <p className="caption" style={{ marginTop: 12 }}>
        At expiry ({date(o.expiry_ts)}, 12:00 UTC) the options settle in cash and the bot closes the perp. Your money never
        leaves your Delta account: margin is locked while the position is open and released when it closes.
      </p>
    </div>
  );
}
