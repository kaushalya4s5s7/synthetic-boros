# Strategy: "Synthetic Boros" — trading fixed vs floating funding using only Delta Testnet

> **In one line:** We copy what Pendle Boros does — swap a *fixed* funding rate for the *floating* perp funding rate — but build it from parts that already exist on Delta: a BTC perpetual plus a call and a put. Both legs trade on Delta Testnet, the position has no exposure to BTC's price by construction, and the entry and exit rules are written down and can be checked.

All numbers are from Delta's public API on 25 Oct 2026, unless marked otherwise.

---

## 0. Rules from the hackathon PDF, and how this design meets them

| PDF requirement | How this design meets it |
|---|---|
| Delta Testnet only; *"All trading must occur via testnet APIs. No historical datasets will be provided."* | Every input — perp, options, funding, mark price — is read from the **testnet** API. No production data or outside datasets. |
| Python + FastAPI; WebSocket optional | FastAPI service; polling first, WebSocket as an upgrade |
| Template: `main.py`, `strategy.py`, `delta_client.py` | Same layout (see section 7) |
| Judged on: Strategy 30 · Risk 20 · Tech 20 · Execution 15 · Demo 15. *"Live short-term PnL will not be the primary evaluation criteria."* | We aim for a strategy with a clear rationale, strong risk controls, and execution you can see — not for a big PnL number |
| No more than 20–30% AI-generated code; the team must be able to explain it | **The team writes the code.** This document is the design, and any snippets in it are reference only. |
| Submission deadline 5:00 PM | Section 8 is a build order with cut-lines |

---

## 1. What Boros does (the idea we're copying)

- A perp's **funding rate** is a floating interest rate, settled every 8 hours.
- **Boros** (by Pendle) lets you trade that floating rate against a **fixed** rate:
  - **Long YU** = pay fixed, receive floating. You profit if funding ends up *above* the fixed rate.
  - **Short YU** = receive fixed, pay floating. You lock in a known yield.
- Boros needs its own on-chain market, which isn't available on Delta.

**Our insight:** Delta's **options market already contains a fixed rate.**
Put-call parity: for European options with the same strike K and expiry T,

```
Call − Put = Forward − K        ⇒        Forward F = K + Call − Put
```

"Buy a call and sell a put" is a **synthetic forward**: a contract to buy BTC at price F on the expiry date, and it pays **no funding**. The gap between F and today's price is the market's **fixed rate** for that period:

```
implied_fixed_APR = (F / perp_mark − 1) × 365 / days_to_expiry
```

## 2. The trade

| Leg | Instrument | Why |
|---|---|---|
| A | **Short BTCUSD perp** (N contracts) | Receives floating funding while funding is positive |
| B | **Long synthetic forward** = buy N calls + sell N puts at the same strike and expiry | Gives back the BTC exposure that leg A removed, at a price fixed at entry |

**Price exposure:** −N (perp) + N (call − put) = **0**. For European options, call − put moves exactly one-for-one with BTC, so the hedge **never needs rebalancing**.

**P&L at expiry:**
```
P&L = (perp_entry − F_entry)   ← fixed cost, known on day 1
    + Σ funding received        ← floating
    − fees
```
That is exactly **Long YU: pay fixed, receive floating.**

**The reverse trade (Short YU / lock in a fixed yield):** when the implied fixed rate is **above** expected funding, flip both legs: **long perp + short synthetic** (sell the call, buy the put).

## 3. Testnet data supports this (checked)

| Fact | Value | Source |
|---|---|---|
| Testnet BTC funding | pinned at **0.01%/8h = 10.95% APR** (unchanged for 7 days) | testnet `FUNDING:BTCUSD` candles |
| Options listed | 167 BTC calls (107 have traded) with two-sided quotes | testnet `/v2/tickers` |
| Contract sizes | perp **0.001 BTC**, options **0.001 BTC** → legs match one-for-one | `/v2/products` |
| Fees | perp taker 0.05% / maker 0.02%; **options 0.01%** of notional | `/v2/products` |
| Option expiry | 12:00 UTC, European, cash-settled | `settlement_time` |

**Testnet term structure** (BTC, at-the-money strike, annualised, computed from **executable** prices: buy the call at the ask, sell the put at the bid):

| Expiry | Days | Cost of the fixed leg (long synthetic) | Floating funding received | Edge before fees |
|---|---|---|---|---|
| 06 Nov | 12.3 | 8.53% | 10.95% | **+2.4%** |
| 13 Nov | 19.3 | 7.66% | 10.95% | **+3.3%** |
| 27 Nov | 33.3 | 6.94% | 10.95% | **+4.0%** |

Expiries under 3 days are excluded: dividing by a tiny time to expiry makes the annualised rate meaningless (it shows 20–60% APR).

**Fees for 27 Nov, held to expiry:** perp maker 0.02% in and out (0.04%) + two option legs 0.02% ≈ **0.06%** of notional.
Edge over 33.3 days = 4.0% × 33.3 / 365 ≈ **0.37%**, so **≈ +0.31% net ≈ 3.4% APR, with no exposure to BTC's price.**
Using taker fees on the perp instead: ≈ +0.25% net.

> **Be honest about this:** it's a small, clean carry trade, not a money printer. On production Delta today the gap is smaller (funding is 7.3% and the fixed leg costs 6.4–7.3%). The strategy's value is that **it only enters when the spread pays for its fees, and it tells you why it isn't trading the rest of the time.** That's exactly what "clear rationale" means in the judging criteria.

## 4. Decision rules (`strategy.py`, pure functions, unit-tested)

Every polling cycle (for example, every 30 seconds):

1. **Forecast floating funding:** the mean of the last 24h of testnet `FUNDING:BTCUSD` (APR) → `f_hat`. *(Changed after the stress test, see section 10.)*
2. **Build the term structure:** for each expiry between **21 and 45 days** out, check **every strike** and keep the one with the cheapest executable forward. Compute
   - `fixed_buy` = cost APR of the long synthetic (buy call at ask, sell put at bid)
   - `fixed_sell` = APR received from the short synthetic (sell call at bid, buy put at ask)
3. **Edge, net of fees**, per expiry:
   - Long YU edge = `(f_hat − fixed_buy) × days/365 − fees`
   - Short YU edge = `(fixed_sell − f_hat) × days/365 − fees`
4. **Enter** the single best expiry only if **edge ≥ 0.15%** of notional **and** both option quotes have at least N contracts at the touch.
5. **Manage the position while open:**
   - **Take profit early:** if unwinding *now* (at executable prices) already captures ≥ 70% of the expected edge, close it.
   - **Funding flip:** if `f_hat` moves against the position by more than the remaining edge, close it.
   - **Roll:** within 24h of expiry, close the perp; the options cash-settle. Re-enter on the next expiry if the edge rule passes again.
6. **Otherwise, do nothing** — and log the reason (for example, "edge 0.08% < 0.15% threshold").

## 5. Risk management

| Risk | Control |
|---|---|
| **Leg risk** (one leg fills, the other doesn't) | Trade the less liquid legs (options) first, using IOC limit orders. Then the perp, using an IOC limit. If the perp fails within 10s, unwind the options with reduce-only orders. |
| **Net price exposure drifts** | After every fill, recompute net BTC delta = −perp + (calls − puts). If it's non-zero for more than 15s, **kill switch**: flatten everything with reduce-only orders. |
| **Short perp liquidated in a rally** (the option gain offsets it in P&L, but **margin is held separately per contract**) | Perp leverage ≤ 3x; monitor `liquidation_price` against the mark and keep ≥ 25% distance; **add margin through Delta's add-position-margin API** before the gap closes. |
| **Selling the put needs margin** | Before entry, check the wallet's available margin covers both legs plus a 30% buffer. |
| **Settlement mismatch** (the perp close doesn't match the option settlement price) | Close the perp near 12:00 UTC on expiry day; the difference is bounded by the perp-vs-index premium. Log it. |
| **The bot crashes with orders resting** | Delta **heartbeat** (dead-man switch): resting orders are cancelled if the bot stops acknowledging |
| **Stale or bad data** | Skip the cycle if a quote is older than 10s, crossed, or the spread is above X% of the mid |
| **Size limits** | Max notional per position, max one open position, daily loss limit, and the exchange's per-contract position limit |
| **Duplicate orders on retry** | Every order carries a `client_order_id` |
| **Testnet funding is synthetic** | We say so clearly. The *logic* is what's being judged, and on production the same code simply finds fewer trades. |

## 6. Other strategies we considered and rejected (with evidence)

| Idea | Why not |
|---|---|
| Cross-exchange funding arbitrage (short Delta DOGE, long CoinDCX) | Strong on production (DOGE spread **+33.8% APR**), but the second leg **can't run on testnet** — breaks the testnet-only rule |
| Spot vs perp cash-and-carry on Delta | Testnet spot order books are **empty** (BTC_USDT: 0 bids, 0 asks) |
| Perp vs dated futures (calendar basis) | **No dated futures** on testnet |
| Using funding as a contrarian "crowding" signal | Testnet funding is **pinned at 0.01%**, so the signal is meaningless there |
| Holding just across each funding timestamp | One funding payment (0.01%) is far smaller than round-trip fees (0.04–0.10%) |
| Moving-average crossover (baseline) | Lost **−16.98%** over 60 days on BTC in our own backtest; every team will have one |

## 7. Code layout (matches the template)

```
main.py          FastAPI: /health  /scan (live term structure + edges + reasons)
                          /positions (both legs + net delta + liquidation distance)
                          /start  /stop  /kill   /log (decision log)
strategy.py      pure functions: implied_fixed_apr, edge, decide(), manage()  ← unit tests
risk.py          pre-trade checks, delta check, margin guard, kill switch
delta_client.py  signed REST: tickers, products, orders, positions, margin, heartbeat, candles
executor.py      leg sequencing, IOC limits, unwind on failure, client_order_id
README.md        strategy, risk, setup, env vars, API usage   ← PDF requires these four
```

## 8. Build order with cut-lines (deadline 5:00 PM)

1. `delta_client.py` with keys: balance, place/cancel, positions — **verify first**
2. `strategy.py` `/scan` — live term structure and edges (public data only)
3. `executor.py` — enter both legs at minimum size, with unwind on failure
4. `risk.py` — delta check, kill switch, heartbeat, margin guard
5. FastAPI endpoints and a simple page showing scan, positions, and log
6. *(cut here if late)* early take-profit, roll logic, Short YU mode, WebSocket

## 9. Demo (5 min)

1. `/scan`: "Here is Delta's fixed-rate curve from options vs the floating funding rate. The best expiry clears fees by X%."
2. Press **start**: options legs fill, then the perp. `/positions` shows **net delta = 0**.
3. Explain the P&L formula: the fixed cost is locked; funding comes in every 8h.
4. Risk demo: manually cancel or close one leg → the bot detects non-zero delta → kill switch flattens everything.
5. Stop the bot → heartbeat cancels resting orders.
6. Closing line: "This is Boros's fixed-vs-floating swap, built from Delta's own instruments. No new protocol needed."

---

## 10. Stress test results and what changed (research/ folder)

Research only. These use Delta **production** history to test the idea offline. Live trading stays testnet-only.

| Script | What it tests |
|---|---|
| `research/fetch_funding.py` | 2 years of hourly BTC/ETH funding + mark (17,502 hours) |
| `research/fetch_options.py` | rebuilds the options-implied fixed rate for 15 past BTC expiries (Jun–Oct 2026) from expired-option mark prices |
| `research/backtest_boros.py` | replays Long YU, Short YU and the rule, held to expiry, at cost levels 0.06 / 0.10 / 0.20% |
| `research/stress.py` | Monte Carlo, 4,000 paths × 33 days, volatility 45–100% plus ±12% jumps: margin calls and hedge breaks under isolated margin |

### Finding 1 — the edge is real and points one way
- Over 2 years, Delta BTC funding averaged **8.8% APR** and sat exactly at the **10.95% baseline 60% of the time**. The **30-day forward realised funding exceeded 6% in 78% of windows.**
- In the 15 historical expiries, the options-implied fixed rate averaged **5.4–7.2%**, while realised funding averaged **8.3–10.8%**. **Delta's options price a lower rate than Delta's perps actually pay.**
- **Long YU wins; Short YU lost in 82–100% of cases.** → Short YU is **disabled** unless the fixed rate exceeds the forecast by more than 3% APR.

### Finding 2 — costs decide everything
| Cost per trade (fees + spread) | 21-day tenor, rule | 7-day tenor, any |
|---|---|---|
| 0.06% (maker perp, options at mid) | **+3.4% APR, 91% win** (n=11) | loses |
| 0.10% | +2.7% APR, 90% win | loses |
| 0.20% (crossing spreads) | no trades pass the filter | loses |
→ **Tenor ≥ 21 days only.** **Enter options as post-only maker orders at mid, then hedge with the perp immediately.** **Hard cost budget of 0.10% per trade.**
→ The entry filter improves the win rate from 77% (always Long YU) to 91%. The worst trade improves from −0.28% to −0.10%.

### Finding 3 — isolated margin breaks the hedge (fixed)
The legs don't share collateral. In a crash, the **short put** needs more margin while the perp's profit is stuck in its own position.

| 3x perp, 10% reserve, vol 45% | P(hedge break in 33 days) |
|---|---|
| top up from reserve only | **30.7%** |
| + move excess margin between legs | 3.2% |
| + **deleverage: close half of BOTH legs** when the reserve can't fund a leg | **0.0%** (also 0.0% at vol 70%) |
Cost of the deleveraging: about 9% of size given up on average. ≈ **5% APR on capital** at 3x leverage with a 10–20% reserve.
→ Risk engine: **rebalance + deleverage both legs together**, never let a single leg get liquidated. 5x is acceptable (0.15% breaks); **10x is not** (2.4–8.2% breaks).
→ **Use portfolio margin mode** if testnet allows it. Delta docs: "Portfolio margin is shared across positions to net out risk". It should remove this whole problem and cut capital needs. **Verify with keys.**

### Updated parameters
| Parameter | Before | After |
|---|---|---|
| Tenor | 7–45 days | **21–45 days** |
| Direction | both | **Long YU only** (Short YU needs fixed > forecast + 3%) |
| Forecast | 72h EMA | **24h mean** (best in backtest; small sample) |
| Strike | at-the-money | **cheapest executable forward across all strikes** |
| Execution | IOC on both | **options post-only at mid → perp IOC hedge on fill** |
| Cost budget | — | **≤ 0.10%** of notional per trade |
| Leverage / reserve | ≤ 3x | **3x (max 5x), reserve 10–20%, rebalance + deleverage** |

### Honest limits
- **Small sample:** 36 entries over 15 expiries (Delta's expired-option listing reached back to June 2026), only 1 at 28 days. Treat the APRs as indicative, not proven.
- Option prices are **mark** prices (model fair value), not trades. Spreads are modelled by the cost parameter.
- `stress.py` *assumes* short-put margin = put value + 10% of spot, and that excess position margin can be withdrawn. Check both on testnet.
- Funding is accrued hourly from the 8h rate, an approximation of settlement every 8h.

## 11. Live testnet verification (25 Oct 2026, `research/verify_keys.py --live`, 1 contract, opened and closed)

| Check | Result |
|---|---|
| Auth, wallet, positions | PASS (keys from env vars; key is IP-locked) |
| Short put allowed (naked_short_restricted?) | **PASS**: not restricted |
| Short put margin | **premium + 5.0% of spot** (we assumed 10%). Put liq price 7,322 |
| Perp default leverage | **10x** (margin 10% of notional, liq at +9.75%). Must be set to 5x in code |
| Real fills, 27 Nov 81000 | put sold 3695, call bought 3800, perp shorted 80590.5, so **locked fixed = 7.02% APR** |
| Edge vs 10.95% funding | +0.358% over 33 days; fees ≈ 0.074% entry, ≈ 0.14% round trip, so **net ≈ +0.22% (≈ 2.4% APR on notional)** |
| Capital used | $15.86 blocked for $80.6 notional (≈ 20%) at 10x |
| Heartbeat | PASS after fixes: `default_ttl` and `contract_types` are required |
| **Failure found** | First run: put IOC at the *ticker* bid matched nothing (`order_size_not_available_in_orderbook`); the quote had moved within 1 second, leaving a call-only position. **Fix (verified): sell the put first, price from a fresh L2 book, 0.5% tolerance, only then the call, then the perp; unwind if any leg fails.** |
| Not yet tested | Portfolio margin mode (account is isolated); post-only at mid fills |

Margin stress rerun with the measured 5% put margin (2,000 paths, 33 days, ±12% jumps):

| Leverage | Reserve | P(break) vol 45% / 70% | APR on capital |
|---|---|---|---|
| 3x | 10% | 0.00% / 0.00% | 4.0% / 3.5% |
| **5x** | **10%** | **0.00% / 0.40%** | **5.2% / 4.4%** |
| 10x (Delta default) | 10% | 2.40% / 4.05% | 6.6% / 5.3% |

**Updated parameters:** set perp leverage **5x** via the API (do not use the 10x default), keep a **10% reserve** with auto top-up and deleverage, use the put → call → perp leg order with fresh-book pricing.
