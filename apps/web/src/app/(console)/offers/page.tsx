"use client";
import Link from "next/link";
import { useState } from "react";
import { api, type Offer } from "@/lib/api";
import { useLive, usePolled } from "@/components/Live";
import { Confirm, Empty, Loading, PageHead } from "@/components/ui";
import { PutOn } from "@/components/PutOn";
import { date, pct, price, signedPct, usd } from "@/lib/format";

function HeroOffer({ o, funding, leverage, onEnter }: { o: Offer; funding: number | null; leverage: number; onEnter: () => void }) {
  return (
    <div className="offer">
      <div className="offer-body">
        <div className="kicker" style={{ marginBottom: 14 }}>Best offer · {o.label} · {date(o.expiry_ts)} expiry · strike {price(o.strike)}</div>
        <p className="statement">
          Lock <span className="num">{pct(o.fixed_apr)}</span> fixed for {o.days.toFixed(0)} days. Funding now{" "}
          <span className="num">{pct(funding)}</span>.{" "}
          <span className="faint">Expected net {usd(o.net_edge_usd, true)} ({signedPct(o.net_edge_pct, 3)}).</span>
        </p>
      </div>
      <div className="field-dots">
        <div className="stats four">
          <div className="stat"><div className="fig blue">{usd(o.net_edge_usd, true)}</div><div className="lbl">Expected net, after every cost</div></div>
          <div className="stat"><div className="fig">{usd(o.cost_usd)}</div><div className="lbl">Fees {usd(o.fees_usd)} + slippage {usd(o.slippage_usd)} = {pct(o.fee_ratio * 100, 0)} of the edge</div></div>
          <div className="stat"><div className="fig">{usd(o.capital_usd)}</div><div className="lbl">Capital needed (margin + reserve)</div></div>
          <div className="stat"><div className="fig">{o.contracts}</div><div className="lbl">Contracts · {usd(o.notional_usd)} notional</div></div>
        </div>
      </div>
      <div className="offer-foot">
        <span className="muted" style={{ fontSize: 14 }}>
          Hedged: BTC price moves don’t change the result. Leverage is forced to {leverage}x.
        </span>
        <button className="btn primary lg" onClick={onEnter}>Enter this position</button>
      </div>
    </div>
  );
}

// Step 1 of entering: how much of the Delta wallet the bot may commit. The offer below is re-sized to it.
function AmountPanel({ capital, available, onApply, offers }: { capital: number | null; available: number | null; onApply: (usd: number) => Promise<void>; offers: Offer[] }) {
  // what the saved amount actually buys: contracts move in whole steps, so $30 and $40 can buy the same 1 contract
  // size against an asset that can actually trade: a passing offer first, else one with a sane price (|fixed| < 50%/yr);
  // empty option books (testnet gold) give absurd prices and would make the sizing meaningless
  const usable = offers.filter((o) => o.contracts > 0 && o.fixed_apr != null && Math.abs(o.fixed_apr) < 50);
  const btc = (usable.find((o) => o.ok) ?? usable.sort((a, b) => b.net_edge_usd - a.net_edge_usd)[0]) ?? null;
  const perContract = btc ? btc.capital_usd / btc.contracts : null;
  const [value, setValue] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const shown = value !== "" ? value : capital !== null ? String(capital) : "";
  const amount = Number(shown);
  const tooMuch = available !== null && amount > available;
  const invalid = !Number.isFinite(amount) || amount <= 0;

  async function apply(usd: number) {
    setBusy(true); setMsg(null);
    try { await onApply(usd); setValue(""); setMsg(`Offers re-priced for ${usd.toFixed(2)} USD.`); }
    catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="panel">
      <div className="amount-row">
        <div className="amount-field">
          <label htmlFor="amount">Amount to put in (USD)</label>
          <div className="amount-input">
            <span>$</span>
            <input id="amount" type="number" min="1" step="1" inputMode="decimal" value={shown}
              onChange={(e) => setValue(e.target.value)} />
            {available !== null && (
              <button type="button" className="link-btn" onClick={() => setValue(String(Math.floor(available * 0.95)))}>Max</button>
            )}
          </div>
          <div className="hint">
            Available in your Delta wallet: <b>{available === null ? "—" : usd(available)}</b>. The bot keeps 5% aside for fees.
            Funds stay on Delta; this only sets how much the bot may use.
          </div>
        </div>
        <button className="btn primary lg" disabled={busy || invalid || tooMuch || (value === "" && capital !== null)}
          onClick={() => apply(amount)}>{busy ? "Re-pricing…" : "Update offers"}</button>
      </div>
      {perContract !== null && capital !== null && btc && (
        <div className="sizing">
          Nothing is bought yet. If a trade passes, <b>{usd(capital)}</b> is enough for <b>{Math.floor(capital / perContract)} contract{Math.floor(capital / perContract) === 1 ? "" : "s"}</b> of {btc.underlying}
          {" "}({Math.floor(capital / perContract)} × {btc.contract_value ?? 0.001} {btc.underlying} per leg; each contract needs about {usd(perContract)} of margin + reserve).
          {" "}Next contract at {usd((Math.floor(capital / perContract) + 1) * perContract)}.
          {capital < perContract && <> <b>Too small:</b> enter at least {usd(Math.ceil(perContract))}.</>}
        </div>
      )}
      {tooMuch && <div className="hint attention-text">That’s more than your available balance. Add funds on Delta or lower the amount.</div>}
      {msg && <div className="hint">{msg}</div>}
    </div>
  );
}

export default function Offers() {
  const { state, refresh, toast } = useLive();
  const { data, err } = usePolled(() => api.offers());
  const [confirming, setConfirming] = useState<Offer | null>(null);
  const funding = state?.market.funding_avg24_apr ?? null;
  const { data: settings, setData: setSettings } = usePolled(() => api.settings());
  const leverage = typeof settings?.leverage === "number" ? settings.leverage : 5;
  const capital = typeof settings?.capital_usd === "number" ? settings.capital_usd : null;
  const available = state?.account?.available ?? null;

  async function applyAmount(usd: number) {
    const saved = await api.saveSettings({ ...(settings ?? {}), capital_usd: usd });
    setSettings(saved);
    await api.tick();          // re-price every candidate for the new size now, not in 30s
    await refresh();
  }

  if (err && !data) return <div className="banner"><strong>Couldn’t load offers.</strong> {err}</div>;
  if (!data) return <Loading />;

  const offers = [...data.offers].sort((a, b) => Number(b.ok) - Number(a.ok) || b.net_edge_usd - a.net_edge_usd);
  const best = offers.find((o) => o.ok) ?? null;
  const hasPosition = !!state?.position;

  return (
    <div className="stack">
      <PageHead
        title="Offers"
        support="Every scanned asset (tokenised gold first, then BTC), every expiry and every strike, priced against the live order book with all fees and spreads included. The bot only shows a trade when costs are under half of the expected funding edge."
      />

      {hasPosition && (
        <div className="banner">
          <strong>You already hold a position.</strong> One position at a time. <Link href="/position">View it</Link>.
        </div>
      )}

      {!hasPosition && <AmountPanel capital={capital} available={available} onApply={applyAmount} offers={data.offers} />}

      {best ? (
        <HeroOffer o={best} funding={funding} leverage={leverage} onEnter={() => setConfirming(best)} />
      ) : (
        <div className="panel attention">
          <div className="kicker" style={{ marginBottom: 10 }}>No trade</div>
          <div className="section-title">{data.decision?.reason || "No offer passes the fee and edge filters."}</div>
          <p className="support" style={{ marginTop: 0 }}>
            This protects you: entering now would pay more in fees and spreads than the funding is expected to return.
            The bot keeps scanning every tick.
          </p>
        </div>
      )}

      {(() => {
        const shown = best ?? offers.find((o) => o.put_px != null && o.contracts > 0) ?? null;
        return shown ? <PutOn o={shown} funding={funding} example={!best} /> : null;
      })()}

      <div>
        <h2 className="section-title">All candidates ({offers.length})</h2>
        {offers.length === 0 ? (
          <Empty title="No option pairs with live quotes">The scan found no expiry in range with both a call and a put quoted.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Asset</th><th>Expiry</th><th className="num">Strike</th><th className="num">Days</th><th className="num">Fixed</th>
                  <th className="num">Forecast</th><th className="num">Net edge</th><th className="num">Fees / edge</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {offers.map((o) => (
                  <tr key={o.id} className={o.id === best?.id ? "attention" : ""}>
                    <td>{o.underlying}{o.rwa && <div className="caption">RWA</div>}</td>
                    <td>{date(o.expiry_ts)}</td>
                    <td className="num">{price(o.strike)}</td>
                    <td className="num">{o.days.toFixed(1)}</td>
                    <td className="num">{pct(o.fixed_apr)}</td>
                    <td className="num">{pct(o.forecast_apr)}</td>
                    <td className="num">{usd(o.net_edge_usd, true)}<div className="caption">{signedPct(o.net_edge_pct, 3)}</div></td>
                    <td className="num">{Number.isFinite(o.fee_ratio) ? pct(o.fee_ratio * 100, 0) : "—"}</td>
                    <td>
                      {o.ok ? <span className="chip ok">Passes</span> : <span className="reasons">{o.reasons.join(" · ") || "Rejected"}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {confirming && (
        <Confirm
          title="Enter this position?"
          confirmLabel="Place 3 orders"
          onClose={() => setConfirming(null)}
          onConfirm={async () => {
            const r = await api.enter(confirming.id);
            if (!r.ok) throw new Error(r.message);
            toast(r.message || "Position opened");
            await refresh();
          }}
        >
          <p className="muted" style={{ margin: 0 }}>
            The bot sets leverage to {leverage}x, then places three orders in this order. Each is priced from a fresh order
            book. If any leg fails, the legs already filled are closed at once.
          </p>
          <ol className="numbered">
            <li><b>01</b>Sell {confirming.contracts} put · strike {price(confirming.strike)} · {date(confirming.expiry_ts)}</li>
            <li><b>02</b>Buy {confirming.contracts} call · strike {price(confirming.strike)} · {date(confirming.expiry_ts)}</li>
            <li><b>03</b>Short {confirming.contracts} {confirming.underlying}USD perpetual (collects funding every 8h)</li>
          </ol>
          <div className="kv">
            <span className="k">Locked fixed rate</span><span className="v">{pct(confirming.fixed_apr)}</span>
            <span className="k">Expected net</span><span className="v">{usd(confirming.net_edge_usd, true)}</span>
            <span className="k">Capital used</span><span className="v">{usd(confirming.capital_usd)}</span>
          </div>
        </Confirm>
      )}
    </div>
  );
}
