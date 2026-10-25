# How to use Synthetic Boros

## 1. Start it (two terminals)

```bash
# Terminal 1: backend (bot + API) on :8000
cd ~/Desktop/tokeniq
./.venv/bin/python -m uvicorn main:app --port 8000

# Terminal 2: console on :3000
cd ~/Desktop/tokeniq/apps/web
npx next start -p 3000        # or: npm run dev
```

Open **http://localhost:3000**. Keys are read from `.env` on the server (`DELTA_KEY`, `DELTA_SECRET`) and never reach the browser. No keys? Start the backend with `DEMO_MODE=1` and everything runs on an in-memory exchange.

## 2. The user journey

```
Landing (/) ──Open console──► Overview ──► Offers ──Enter──► Position ──► Activity
                                   ▲                              │
                                   └────────── Settings ◄─────────┘  (limits, start/pause, rehearsal)
```

| Step | Page | What you do | What you see |
|---|---|---|---|
| 1 | **Landing** `/` | Read the pitch, press **Open console** | Live funding strip (real mainnet numbers), how it works, fee guard, risk, evidence, FAQ |
| 2 | **Overview** `/overview` | Check that you're connected | Funding now / 24h avg / best fixed / position status. If the key or IP fails, a panel tells you exactly what to fix |
| 3 | **Offers** `/offers` | Type the **amount to put in** (or **Max**) → **Update offers**; review the best offer, press **Enter this position** | The offer as a sentence (lock X% for N days, expected net, fees as % of edge, capital), plus every candidate with the reason it was rejected |
| 4 | **Position** `/position` | Watch it; **Pause** or **Close all** if you want | Status + plain reason, locked vs floating, earned / projected / worst case, funding chart with your fixed line and exit line, margin bar and liquidation price per leg, "why" log |
| 5 | **Activity** `/activity` | Audit | Every decision, alert, top-up and exit, plus past positions with final P&L |
| 6 | **Settings** `/settings` | Tune limits, start the bot, rehearse | Risk limits form, Auto-enter + Start/Pause, **Run a tick now**, Rehearsal presets |

**Bot modes:** *Paused* (default) means no new entries, but risk checks, top-ups, exits and the heartbeat still run. *Running + Auto-enter* means the bot enters by itself when an offer passes every filter.

## Where the money is

Funds never leave Delta. The user deposits on Delta (on testnet, the demo wallet comes pre-funded), and the API key lets the bot trade that wallet. The amount entered on Offers only caps how much of it the bot may commit. The backend refuses any entry that needs more than the wallet's available balance.

## 3. Demo script (5 minutes)

1. **Landing → Open console.** "Testnet for orders, mainnet for funding."
2. **Offers.** With real mainnet funding (~5%) the bot refuses everything and shows why. *"The fee guard protects the user."*
3. **Settings → Rehearsal → Normal 10.95%** (testnet's real funding rate) → **Offers** → one strike passes → **Enter this position** → confirm.
4. **Position.** Three real orders on Delta testnet: locked rate, margin bars, liquidation prices. Optionally open Delta's own testnet UI to show the same positions.
5. **Rehearsal → Flip −10%** → status **FLIPPED, holding** ("most flips end within 8h").
6. **Rehearsal → Crash −30%** → status **EXITING**: the bot closes all three legs. **Activity** shows each step with a reason.
7. **Rehearsal → Clear.**

## 4. Which API is used where

### Console → our backend (`apps/web/src/lib/api.ts` is the only caller)

| UI place | Our endpoint | Refresh |
|---|---|---|
| Top bar (env pill, bot status, tick age, rehearsal pill); Overview stats; Position page | `GET /api/state` | every 5s |
| Connection panel (key OK? IP blocked?) | `GET /api/health` | every 5s |
| Landing live strip | `GET /api/state` | every 15s |
| Offers: **amount input** (re-sizes every offer) | `PUT /api/settings {capital_usd}` → `POST /api/bot/tick` | on click |
| Offers: available balance shown under the input | `GET /api/state` → `account.available` (Delta `/v2/wallet/balances`) | every 5s |
| Offers: best offer + candidates table | `GET /api/offers` | on load and refresh |
| Offers: leverage shown in the confirm dialog | `GET /api/settings` | on load |
| Offers: **Enter this position** | `POST /api/enter {offer_id}` | on click |
| Position: funding chart | `GET /api/funding?hours=168` | on load |
| Position: "why" log (last 8) | `GET /api/events?limit=8` | on load |
| Position: **Close all** | `POST /api/position/close` | on click |
| Position / Settings: **Pause**, **Start** | `POST /api/bot/pause`, `POST /api/bot/start {auto_enter}` | on click |
| Activity: event log | `GET /api/events?limit=200` | on load |
| Activity: past positions | `GET /api/positions` | on load |
| Settings: limits form | `GET /api/settings`, `PUT /api/settings` | load / save |
| Settings: **Run a tick now** | `POST /api/bot/tick` | on click |
| Settings: Rehearsal presets | `POST /api/demo/funding {apr}` | on click |

### Our backend → Delta Exchange (`packages/exchange` is the only caller)

| Backend need | Delta endpoint | Network | Used by |
|---|---|---|---|
| Perp + options list, **fees**, contract value, tick | `GET /v2/products` | testnet | `/api/offers`, entry pricing, fee guard |
| Option chain | `GET /v2/tickers?contract_types=call_options,put_options` | testnet | market snapshot |
| **Order books** (depth-walk pricing, fresh before each order) | `GET /v2/l2orderbook/{symbol}` | testnet | offers, execution |
| BTC spot / perp mark | `GET /v2/tickers/BTCUSD` | testnet | snapshot, margin health |
| **Funding now** | `GET /v2/tickers/BTCUSD` (`funding_rate`) | **mainnet** | decisions, status, chart |
| **Funding history** | `GET /v2/history/candles?symbol=FUNDING:BTCUSD&resolution=1h` | **mainnet** | 24h average, chart |
| Balance / equity | `GET /v2/wallet/balances` | testnet | `/api/state` account, sizing |
| Positions (size, margin, liquidation price) | `GET /v2/positions/margined` | testnet | hedge check, margin bars |
| Margin mode | `GET /v2/profile` | testnet | health |
| **Force 5x leverage** | `POST /v2/products/{id}/orders/leverage` | testnet | before every entry |
| **Orders** (limit IOC, `client_order_id`, `reduce_only`) | `POST /v2/orders` | testnet | enter, unwind, close, deleverage |
| Margin top-up | `POST /v2/positions/change_margin` | testnet | risk engine |
| Funding received | `GET /v2/wallet/transactions?transaction_types=funding` | testnet | earned so far |
| Dead-man switch | `POST /v2/heartbeat/create`, `POST /v2/heartbeat` | testnet | every tick |

### What one tick does (every 30s, `apps/bot/runner.py`)

```
build snapshot (books, mainnet funding) ─► preflight (data fresh? books?) ─► status of open position
   ├─ position open: hedge check → exit rules → margin top-up/deleverage → alert on state change
   └─ flat: evaluate every expiry × strike → fee guard → (auto-enter if running) → decision event
heartbeat ack ─► journal to SQLite ─► served to the console
```

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| Blue banner "Bot API unreachable" | Start the backend on :8000 |
| Overview says IP not whitelisted | Add your IP (`curl api.ipify.org`) to the key on testnet.delta.exchange, or create a new key |
| No offers ever pass | Expected when mainnet funding is low: the fee guard is working. Use Rehearsal → Normal 10.95% for a testnet demo |
| Want to start fresh | Stop the backend and delete `data/ledger.sqlite` |
