# Synthetic Boros: fixed vs floating funding on Delta Exchange

> **One line:** Delta pays a floating rate (funding) on perpetual futures. We use options to lock a fixed rate that is cheaper, and the bot earns the difference while staying hedged against BTC's price.

Built for the TokenIQ Algorithmic Trading Hackathon (Delta Exchange Testnet · Python + FastAPI · Next.js).

---

## 1. Strategy

### The idea
[Pendle Boros](https://boros.pendle.finance) lets traders swap a **floating funding rate for a fixed one**. Delta has no such market, so we **build it from parts Delta already lists**:

| Leg | Instrument | Role |
|---|---|---|
| Short `BTCUSD` perpetual | perp | **receives** funding every 8h (the floating leg) |
| Buy call + sell put, same strike K and expiry T | European BTC options | together = a **synthetic dated future** at price `F = K + C − P` (the fixed leg) |

The net position has **zero exposure to BTC's price**: short 1 perp plus long 1 synthetic future. Its P&L is:

```
P&L = funding received − (F − perp entry) − fees
implied fixed APR = (F / perp − 1) × 365 / days
```

When the fixed rate implied by the options is **below** the funding we expect, we enter. Delta testnet lists **no dated futures**, which is why the options build one.

### Evidence
| Test | Result |
|---|---|
| 2 years of Delta mainnet funding (BTC, hourly) | Average 8.8% APR. Sits exactly at 10.95% 60% of the time. Negative 8.2% of the time. |
| Backtest on 139 real option expiries | Option-implied fixed rate 5.4–7.2% vs realised funding 8.3–10.8%. **Long-funding wins.** 21-day rule: 91% win rate. |
| Live testnet fill (25 Oct 2026) | Put sold 3695, call bought 3800, perp shorted 80,590.5 → **locked 7.02% fixed vs 10.95% funding** |

Details: [`STRATEGY.md`](STRATEGY.md) · research scripts in [`research/`](research/).

### Decision rule (every 30s, `packages/strategy`)
1. Scan **every BTC expiry 21–45 days out × every strike within ±15% of spot**.
2. Price each leg by **walking the live L2 order book** for our exact size (never the ticker, whose quotes go stale within 1 second).
3. Forecast = trailing 24h funding average (**read from mainnet**, since testnet funding is fixed at 0.01%).
4. Enter only if **all** of the following hold:
   - net edge after every fee (×1.18 GST) and slippage ≥ **0.15%** of notional;
   - **fees ≤ 50% of the expected funding edge** (the fee guard: the user never pays more in fees than they earn);
   - option spread cost ≤ 0.12%;
   - our size is ≤ 20% of visible depth;
   - funding is not negative right now.
5. Every rejected candidate is shown in the console **with its reason**.

### RWA first: tokenised gold (XAUT)
The bot scans **XAUT (Tether Gold, a real-world-asset token) first, then BTC**, and ranks every candidate together; the fee guard decides.
- **Why gold:** over 90 days on Delta mainnet, RWA perp funding never moved: XAUT/PAXG sat at **5.48%**, stock and silver tokens at **10.95%**, with a standard deviation of 0.00 and never negative. BTC ranged from −3.8% to 11%. Flat funding removes the flip risk, so for an asset whose 7-day funding spread is ≤ 0.25 points the bot also prices **short expiries (2+ days)**.
- **Why only gold among RWAs:** XAUT is the only RWA with listed options on Delta (44 on mainnet, 77 on testnet). Stock, silver and oil tokens have no options and no spot books on testnet, so their constant funding can't be hedged on Delta.
- **What we found:** on mainnet, XAUT options are tight (≈ $1.5 spread) and the 5-day synthetic forward prices **below** the perp, so the position earns basis plus a constant 5.48%, with 0.01% fees. On testnet the XAUT option books are nearly empty (spread cost ≈ 4.8% of notional), so the guard rejects them and BTC is what trades there. Settings → *Assets to scan* switches to RWA only.

## 2. Risk management

Full catalogue: [`RISK_AND_DASHBOARD.md`](RISK_AND_DASHBOARD.md).

| Risk | Control |
|---|---|
| Funding flips negative | Flips usually last ~8h, so we **hold**. We exit when the **24h average < 0** (backtest: same mean P&L, worst loss cut 60%), or instantly at ≤ −25% APR, or at a hard stop of −0.5% of notional |
| Fees larger than funding | Fee guard at entry; no early exit in the last 3 days (exit costs more than holding) |
| Low liquidity | Depth-walk pricing, max 20% of visible depth, spread cap, IOC with 0.5% tolerance |
| One leg fills, another doesn't | Order: sell put → buy call → short perp. If any leg fails, every filled leg is **unwound immediately** (this happened on testnet and is handled) |
| Leverage | **Forced to 5x** (Delta defaults to 10x on testnet, 200x on mainnet) |
| Margin (isolated legs) | Health < 70% → alert; < 50% → top up from a 10% reserve; reserve empty → close 50% of all legs (stays hedged). Monte Carlo: 0% hedge breaks at 5x with a 10% reserve |
| Expiry | The perp is closed when the options settle (12:00 UTC) |
| Bot or network dies | Delta heartbeat (dead-man switch) cancels open orders; positions stay hedged |
| Stale data / IP block | Freeze on data > 30s old or `ip_not_whitelisted`, with an alert |

## 3. Setup

Plain-language explainer for judges: [`docs/FOR_JUDGES.md`](docs/FOR_JUDGES.md). Everything (idea, money, screens, every file) in the simplest words: [`docs/EXPLAIN_EVERYTHING.md`](docs/EXPLAIN_EVERYTHING.md). Full user guide, demo script and the UI → API → Delta endpoint map: [`docs/how-to-use.md`](docs/how-to-use.md). Landing page at `/`, console at `/overview`.

```bash
# backend (Python 3.11+)
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env               # add DELTA_KEY / DELTA_SECRET (testnet)
./.venv/bin/python -m uvicorn main:app --port 8000

# console
cd apps/web && npm install && npm run dev      # http://localhost:3000

# tests
./.venv/bin/python -m pytest -q
# live 1-contract proof on testnet (opens and closes a real position)
./.venv/bin/python -m apps.verify --live
```

**Without keys:** `DEMO_MODE=1` runs everything on an in-memory exchange.

### Environment variables
| Variable | Meaning |
|---|---|
| `DELTA_KEY`, `DELTA_SECRET` | Testnet API key with **Trading** permission |
| `TRADING_ENV` | Where orders go (`testnet`) |
| `FUNDING_SOURCE` | Where funding is read for decisions (`mainnet`, public, read-only) |
| `LEDGER_PATH` | SQLite journal (`./data/ledger.sqlite`) |
| `TICK_SECONDS` | Bot loop interval (30) |
| `DEMO_MODE` | `1` = in-memory exchange |
| `NEXT_PUBLIC_API_URL` | Console → API (`http://localhost:8000`) |

### API key
On testnet.delta.exchange, go to **Account → API Keys → Create** with *Trading* permission and **whitelist your public IP** (`curl api.ipify.org`). The key is IP-locked: if you change network, the API returns `ip_not_whitelisted_for_api_key` and the console shows how to fix it. Keys stay on the server and never reach the browser.

## 4. Delta API usage

| Purpose | Endpoint |
|---|---|
| Instruments, fees, margin specs | `GET /v2/products` (`maker/taker_commission_rate`, `premium_commission_rate`) |
| Option chain quotes | `GET /v2/tickers?contract_types=call_options,put_options` |
| Order books (depth-walk pricing) | `GET /v2/l2orderbook/{symbol}` |
| Funding (now / history) | `GET /v2/tickers/BTCUSD` · `GET /v2/history/candles?symbol=FUNDING:BTCUSD` |
| Account | `GET /v2/wallet/balances` · `GET /v2/positions/margined` · `GET /v2/profile` |
| Leverage | `POST /v2/products/{id}/orders/leverage` |
| Orders | `POST /v2/orders` (limit IOC, `client_order_id`, `reduce_only`) |
| Margin top-up | `POST /v2/positions/change_margin` |
| Funding received | `GET /v2/wallet/transactions` |
| Dead-man switch | `POST /v2/heartbeat/create` · `POST /v2/heartbeat` |

Signing: `HMAC_SHA256(secret, METHOD + timestamp + path + query + body)`.

## 5. Code

Architecture and boundary rules: [`docs/repo-architecture.md`](docs/repo-architecture.md). The template names map like this:

| Template | Here |
|---|---|
| `main.py` | shim → `apps/api/main.py` (FastAPI) |
| `strategy.py` | shim → `packages/strategy` (pure decision logic) + `packages/risk` |
| `delta_client.py` | shim → `packages/exchange` (signed REST client) |

## 6. Verified live on Delta testnet (25 Oct 2026)

| Check | Result |
|---|---|
| Mainnet funding 5.0–5.6% | Bot **refused all 10 candidates** with reasons (fixed rate above forecast, spreads > 0.12%, net edge < 0.15%) |
| Rehearsal at testnet's real 10.95% | 1 of 10 passed the fee guard (strike 80,000: costs 48% of edge). **Entered 3 contracts**: put sold 3,320, call bought 4,255, perp shorted 80,415.5 at 5x (liq $96,297, +19.7%). Locked 7.12% |
| Funding flip −10% | **FLIPPED → hold** ("most flips end within 8h") |
| Negative 24h average | **Auto-exit**: all 3 legs closed reduce-only; exchange shows zero positions. Cost $0.93 (two spread crossings within minutes, which is why the bot waits a full day instead of reacting to every flip) |
| Other live API checks | leverage set to 5x, margin add/remove, heartbeat, 18% GST on fees confirmed from wallet transactions |
| Tests | 79 passing (`pytest -q`): strategy, risk, exchange parsing, execution with leg failure/stale quotes, ledger, full bot journey, API, architecture rules |

## 7. Demo
1. **Health:** API connected, testnet trading, mainnet funding.
2. **Offers:** best fixed rate vs funding, and every rejected strike with its reason (fee guard).
3. **Enter:** three orders on Delta testnet. The position appears in Delta's own UI.
4. **Position:** locked vs floating, margin bars, liquidation prices.
5. **Rehearsal:** set funding to −10% (FLIPPED, hold), then −30% (EXITING, auto-close). Testnet funding is fixed, so this is clearly labelled as a rehearsal.
6. **Activity:** the "why" log for every decision.
