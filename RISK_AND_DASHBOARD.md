# Risk Engine, Alerts and Dashboard: Synthetic Boros on Delta

Every number in this file comes from Delta's own API: 2 years of hourly BTC/ETH funding from production, plus live testnet trades on 25 Oct 2026. The analysis scripts are in `research/`.

---

## 1. How Delta funding actually behaves

| Fact | Value | Source |
|---|---|---|
| Payment interval | **every 8 hours** (`rate_exchange_interval: 28800`) | `/v2/products/BTCUSD` |
| What the ticker shows | `funding_rate` = the current 8h rate in %. It can change during the window. | `/v2/tickers` |
| How often it changes (prod) | the value changes in only **6% of hours**. 60% of the time it sits exactly at 0.01%/8h (10.95% APR) | 2y FUNDING candles |
| Right now | prod BTC 0.00507%/8h = **5.55% APR** · testnet **pinned at 0.01** | live ticker |
| Flips from + to − | **~3.7 per month** (BTC 90 flips in 2y, ETH 88) | 2y |
| How long a negative streak lasts | median **8h**, 90% under **32h**, longest 112h (BTC) / 192h (ETH) | 2y |
| Worst hour | −87.7% APR (BTC) | 2y |
| Worst 7-day average | −5.6% APR | 2y |
| Worst 33-day average | −0.7% APR | 2y |

**Two key insights:**
1. **Most flips are noise.** 80 of the 90 negative streaks ended within 24h. A bot that panics on every flip pays exit costs for nothing.
2. **A full negative day is a real signal.** When the trailing 24h average is below 0, the next 7 days stay below our 7% fixed rate **95% of the time** (BTC). That is when we exit.

---

## 2. What the position earns or loses

The position earns **(floating funding − locked fixed rate)**, paid every 8h on the perp and fully hedged against BTC price.

With the fixed rate locked at **7.02%** (our live testnet fill):

| Funding regime | APR | Our carry per day (per $1,000 notional) |
|---|---|---|
| Baseline (60% of the time) | 10.95% | **+$0.108** |
| Thin | 5% | −$0.055 |
| Zero | 0% | −$0.192 |
| Bad flip | −25% | −$0.877 |
| Exchange clamp (worst possible) | −54.75% | −$1.69 |

Exit cost is about **0.15% of notional** (crossing the option spreads plus the perp fee). At the zero-funding level we lose 0.019% per day, so an early exit only pays off if bad funding lasts more than about 8 days or goes deeply negative.

---

## 3. Exit rules, backtested (BTC, 447 entries over 2y, 33-day hold, fixed 7%)

| Exit rule | Win rate | Mean P&L | Worst 5% | Worst |
|---|---|---|---|---|
| Never exit (hold to expiry) | 89% | +0.184% | −0.226% | **−0.753%** |
| **Exit when 24h avg < 0** ✅ | 83% | **+0.183%** | −0.215% | **−0.305%** |
| Exit when 24h avg < 3% | 78% | +0.167% | −0.200% | −0.242% |
| Exit when 24h avg < 5% | 73% | +0.150% | −0.208% | −0.242% |
| Instant exit on any hour < −10% | 81% | +0.171% | −0.216% | −0.305% |
| ETH, exit when 24h avg < 0 | 89% | +0.236% | −0.155% | −0.331% |

**Chosen rule: exit when the 24h average funding < 0%.** It keeps the full average profit and **cuts the worst loss by 60%**. Tighter rules exit too often and pay the exit cost repeatedly. Exiting on every flip is worse.

*Caveat: when exiting early, the options are sold at the market's new implied rate, which this test holds constant. The hard stop below covers that gap.*

---

## 4. The status model: one state per position, recomputed every 60 seconds

Inputs: `f_now` (ticker APR), `f_24` (24h average, from FUNDING candles), `fixed` (locked at entry), `pnl` (realised funding + mark-to-market − fees), margin health of each leg, and `days_left`.

| State | Condition | Bot action | What the user sees |
|---|---|---|---|
| 🟢 **EARNING** | `f_24 ≥ fixed` | Hold | "Earning 10.95% vs your locked 7.02% → +3.93%/yr" |
| 🟡 **BELOW FIXED** | `0 ≤ f_24 < fixed` | Hold (data: exiting here loses money) | "Funding 5.5% is below your 7.02%. Losing $0.05/day. Holding: early exit costs more." |
| 🟠 **FLIPPED** | `f_now < 0` but `f_24 ≥ 0` | Hold, start a 24h countdown | "Funding turned negative 3h ago. Most flips end within 8h. Exit if the 24h average goes below 0 (in ~21h)." |
| 🔴 **EXITING** | `f_24 < 0` **or** `f_now ≤ −25% APR` **or** `pnl ≤ −0.50%` | Close all 3 legs (put → call → perp, reduce-only) | "Exited: funding negative for 24h. Final P&L −$1.20." |
| ⚫ **HEDGE BROKEN** | leg sizes don't match (e.g. call filled, put didn't) | Close the stray leg within 10s, block new entries | "Order failed on 1 leg, unwound. No exposure." |
| 🔵 **EXPIRING** | `days_left ≤ 1` | Close the perp at 12:00 UTC expiry, when the options cash-settle | "Options settle tomorrow 12:00 UTC; the perp closes at the same time." |

**Why −25% for the instant exit:** history shows it is cost-neutral. It never fired on BTC and slightly helped ETH, so it is pure crash insurance. **Why −0.50% hard stop:** that is above the worst backtested loss with the 24h rule, so it only fires on something the model didn't expect, such as an option mark-to-market move or an API outage.

---

## 5. Full risk catalogue

| # | Risk | What can go wrong | Threshold / control | Automatic action | Status |
|---|---|---|---|---|---|
| 1 | **Funding falls** | floating < fixed | status model (section 4) | hold / exit | backtested |
| 2 | **Funding flips negative** | we pay instead of receiving | 24h average < 0, or a single reading ≤ −25% APR | exit | backtested |
| 3 | **Rally drains perp margin** | isolated short perp, liquidated at +19.75% (5x) | perp health = equity / initial margin. **Alert < 70%, top-up < 50%** | add margin from reserve (`/v2/positions/change_margin`) | stress-tested |
| 4 | **Crash raises put margin** | put margin = premium + 5% of spot, grows as BTC falls | same thresholds on the put leg | top-up from reserve | margin measured live |
| 5 | **Reserve exhausted** | can't fund a top-up | reserve < needed amount | **close 50% of all 3 legs** (stays hedged) | 0% break at 5x / 10% reserve |
| 6 | **Leg failure** | one option fills and the other doesn't | leg size mismatch | unwind within 10s; put sold first | **happened live, fix verified** |
| 7 | **Stale quotes** | ticker bid moved within 1s → IOC no fill | always price from a fresh L2 book with 0.5% tolerance | retry once, then abort | verified live |
| 8 | **Wide spreads** | entry cost eats the edge | spread cost > 0.12% of notional | don't enter, show "spread too wide" | measured 0.115% |
| 9 | **Expiry mismatch** | options settle, perp stays open = naked short | `days_left ≤ 1` | close perp at 12:00 UTC on expiry | design |
| 10 | **Leverage default** | Delta defaults to 10x (liq at +9.75%) | force 5x at entry via `/v2/products/{id}/orders/leverage` | reject entry if setting fails | default verified live |
| 11 | **Bot crash / network loss** | nobody watching | heartbeat with 60s TTL | Delta cancels open orders. Positions stay hedged and safe. | verified live |
| 12 | **Stale data** | acting on old prices | ticker timestamp older than 30s | freeze all actions, alert | design |
| 13 | **API key / IP** | IP changes → `ip_not_whitelisted_for_api_key` | 401 on any call | alert "Bot disconnected", freeze | **happened live** |
| 14 | **Rate limits / double orders** | retry places a duplicate order | `client_order_id` on every order | idempotent retry with backoff | design |
| 15 | **Size / concentration** | too large for the book | max notional = 20% of top-5 book depth; max 1 position per expiry | cap size | design |
| 16 | **Exit cost > remaining loss** | exiting with 2 days left wastes fees | `days_left < 3` | hold to expiry instead of exiting (unless the hard stop fires) | math in section 2 |
| 17 | **Liquidation price source** | Delta liquidates on mark, not last price | all checks use `mark_price` | — | from API |

---

## 6. Alerts: when the user hears from us

Rule: **we alert on decisions, not on noise.** Funding moves hourly, but the user is only notified when the state changes.

| Severity | Trigger | Example message |
|---|---|---|
| ℹ️ Info | Funding paid (every 8h) | "Funding received +$0.0024. Total earned $0.07." |
| ℹ️ Info | Entry filled | "Locked 7.02% fixed for 33 days. Expected net +0.22%." |
| ⚠️ Warn | State → BELOW FIXED | "Funding 5.5% < your 7.02%. Holding: exit costs more than expected loss." |
| ⚠️ Warn | State → FLIPPED | "Funding negative. Auto-exit in 21h if it stays negative." |
| ⚠️ Warn | Margin health < 70% (either leg) | "BTC +12%: perp margin at 65%. Auto top-up at 50%." |
| 🔶 Action | Top-up done | "Added $4.10 margin to the perp from reserve. Reserve left $6." |
| 🔶 Action | Deleverage | "Reserve low: closed half the position. Still hedged." |
| 🔴 Critical | Exit executed | "Exited: 24h funding < 0. P&L −$1.20." |
| 🔴 Critical | Hedge broken / unwound | "Call filled, put didn't. Call closed. No exposure." |
| 🔴 Critical | Bot disconnected / IP blocked | "Bot can't reach Delta. Positions hedged. Orders auto-cancelled." |

**Channels:** dashboard banner and event log (always) plus Telegram (Warn and above).

---

## 7. Dashboard

```
┌───────────────────────────────────────────────────────────────────┐
│ 🟢 EARNING            BTC · 27 Nov 2026 · 31 days left    [⏸ Pause] [✖ Close all] │
├───────────────────────────────────────────────────────────────────┤
│  You locked: 7.02%     Funding now: 10.95%     Spread: +3.93%/yr   │
│  Earned so far: +$0.07  (funding $0.14 − fees $0.07)               │
│  Projected at expiry: +$0.18     Worst case (history): −$0.25      │
├───────────────────────────────────────────────────────────────────┤
│  FUNDING vs YOUR FIXED (7 days)                                    │
│  11% ─────────────────╮   ╭──────────── floating                    │
│   7% ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  your fixed               │
│   0% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  exit line (24h avg)       │
├───────────────────────────────────────────────────────────────────┤
│  HEDGE   short perp −1 │ long call +1 │ short put −1 │ net Δ ≈ 0 ✅ │
│  MARGIN  perp ████████░░ 82%  liq at $96,500 (+19.7%)              │
│          put  █████████░ 91%  reserve $8.00                        │
├───────────────────────────────────────────────────────────────────┤
│  WHY THE BOT DID WHAT IT DID                                       │
│  08:00  Funding paid +$0.0024                                      │
│  07:41  Margin check OK (perp 82%, put 91%)                        │
│  Mon    ENTER: edge 3.93%/yr − cost 0.12% ≥ 0.15% buffer ✅         │
└───────────────────────────────────────────────────────────────────┘
```

**What makes it useful (not just pretty):**
1. **One number on top:** "You locked X, you earn Y." The user understands the position in 2 seconds.
2. **Fixed line on the funding chart.** The user sees when funding goes below their fixed rate, and where the exit line is.
3. **"Why" log.** Every action has a reason with the threshold it crossed. Nothing is a black box.
4. **Worst case shown up front**, from history, before the user enters.
5. **Margin bars with the liquidation price in dollars**, not just percentages.
6. **Two buttons only:** Pause (stop new entries) and Close all (put → call → perp, reduce-only).

---

## 8. How the user uses it (end to end)

1. **Connect** a Delta API key (trading permission, IP whitelisted). The bot checks the balance and margin mode.
2. **Offer screen.** The bot scans every BTC expiry 21–45 days out and every strike, and shows the best one:
   > "Lock **7.02%** fixed for 33 days. Funding is now **10.95%**. Expected net **+0.22%** (+$0.18 on $80). Historical worst **−0.31%**. Margin needed **$16 + $8 reserve**."
   If there's no edge: "No trade: fixed 9.8% vs forecast 10.95%, edge below costs. Checking again in 15 min."
3. **User sets** size and leverage (3–5x, default 5x) and presses **Start**.
4. **Bot enters:** set leverage → sell put → buy call → short perp (fresh book each, unwind on failure) → start heartbeat.
5. **Bot monitors** every 60s: status model, margin top-ups, alerts.
6. **Bot ends** the position at expiry (settle + close perp), on an exit rule, or on Close all. It then shows a final report: funding earned, fees, fixed paid, net P&L, every action taken.

**Who it's for:** anyone holding USD on Delta who wants yield **without betting on BTC's direction**. It works like a fixed-vs-floating savings product: we tell them the rate they lock in, what they're earning, and exactly when and why the bot would get out.
