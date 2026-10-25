# Repository Architecture: Synthetic Boros (TokenIQ · Delta Exchange)

> How the code is organised and how its boundaries are enforced. The strategy is in [`../STRATEGY.md`](../STRATEGY.md). Risk, alerts and the dashboard are in [`../RISK_AND_DASHBOARD.md`](../RISK_AND_DASHBOARD.md). The visual design is in [`ui-designg.md`](./ui-designg.md).
> Stack (from the hackathon brief): **Python + FastAPI** backend, **Delta Exchange REST API** (mandatory), **Next.js** console (optional frontend, which we use).

The goal is the **smallest structure that stays clean, testable and production-ready**. Every package exists because it is a real capability with a stable contract.

**Design rule: built for mainnet, run on testnet.** Testnet hides the costs that kill this strategy in production: fixed funding, a 10x leverage default (200x on mainnet), thin option books and stale quotes. So:
- Every cost is read from the API (`maker/taker_commission_rate`, `premium_commission_rate`) and multiplied by 18% GST.
- Every fill price is a **depth walk of the live L2 book**, never the top-of-book ticker.
- Funding for decisions comes from **mainnet** (`FUNDING_SOURCE=mainnet`, read-only public API). Orders go to `TRADING_ENV` (testnet).

---

## 1. Capabilities first

| Capability | What it does | Package |
|---|---|---|
| Exchange access | Signed Delta REST: instruments, L2 books, account, orders, leverage, margin, heartbeat, funding history | `packages/exchange` |
| Market snapshot | One consistent view per tick: spot, perp, funding (mainnet), option pairs with fresh books | `packages/market` |
| Strategy | **Pure.** Price every expiry × strike by depth walk, fee guard, entry decision, position status machine | `packages/strategy` |
| Risk | **Pure.** Sizing, margin health, top-up / deleverage plan, staleness and kill conditions | `packages/risk` |
| Execution | Leg sequencing put → call → perp, unwind on failure, leverage, margin top-up, close-all | `packages/execution` |
| Ledger | SQLite: positions, funding payments, events (alerts + "why" log), read model for the console | `packages/ledger` |
| Bot loop | One `tick()`: snapshot → status → risk → execute → journal. Pause / start / close-all, demo overrides | `apps/bot` |
| HTTP API | FastAPI routes for the console; thin handlers | `apps/api` |
| Console | Next.js dashboard: overview, offers, position, activity, settings | `apps/web` |
| Live verification | CLI that proves the API assumptions on testnet with 1 contract | `apps/verify` |

## 2. Layout

```text
.
├── pyproject.toml · requirements.txt · .env.example · README.md
├── main.py · strategy.py · delta_client.py    # hackathon template names: 3-line shims into apps/ and packages/
├── apps/                          # deployables: import packages, are never imported by packages
│   ├── api/        main.py (route table + lifespan) · deps.py (wiring) · errors.py
│   ├── bot/        runner.py (Bot.tick, controls, overrides) · __main__.py (headless loop)
│   ├── verify/     __main__.py (live 1-contract check)
│   └── web/        Next.js (app router) · src/{app,components,lib}. lib/api.ts is the ONLY path to the backend
├── packages/                      # named by capability, never by vendor
│   ├── domain/     types.py, the shared dataclasses (data only)
│   ├── exchange/   __init__.py (DeltaClient, ExchangeClient protocol) · _rest.py (signing, private) · fake.py (FakeExchange for tests/demo) · keys.py
│   ├── market/     __init__.py (build_snapshot) · keys.py
│   ├── strategy/   __init__.py (Params, evaluate, status, depth_fill, forecast_apr)
│   ├── risk/       __init__.py (size_contracts, margin_health, plan, preflight)
│   ├── execution/  __init__.py (enter, close_all, topup, deleverage)
│   └── ledger/     __init__.py (Ledger) · keys.py
├── tests/          test_<package>.py, test_e2e_fake.py (full tick loop on FakeExchange), test_architecture.py
├── research/       backtests and stress tests (not imported by anything)
└── docs/
```

## 3. Dependency direction

```text
apps/api ──► apps/bot ──► execution ──► exchange
   │            │  │         │
   │            │  │         └──► strategy, risk ──► domain
   │            │  └──► market ──► exchange, domain
   │            └──► ledger ──► domain
   └──► ledger (read)
```

| Package | May import | Must not import |
|---|---|---|
| `domain` | stdlib | anything internal |
| `exchange` | `domain`, `requests` | every other package |
| `market` | `domain`, `exchange` | strategy, risk, execution, ledger, apps |
| `strategy` | `domain` | **any I/O**: exchange, market, ledger, requests, sqlite3, os.environ |
| `risk` | `domain`, `strategy` (Params, types) | any I/O |
| `execution` | `domain`, `exchange` (protocol), `strategy`, `risk` | ledger, market, apps |
| `ledger` | `domain`, `sqlite3` | exchange, strategy, apps |
| `apps/*` | any package's public `__init__` | other apps (except api → bot), any `_private.py` module |

Enforced by `tests/test_architecture.py`, which parses imports with `ast`. It also checks that **only `keys.py` files read `os.environ`**.

## 4. Public contracts

### exchange
```python
class ExchangeClient(Protocol):
    env: Env
    def instruments(self) -> tuple[Instrument, list[Instrument]]       # (BTCUSD perp, live BTC options)
    def book(self, symbol: str, depth: int = 20) -> Book
    def ticker(self, symbol: str) -> dict                               # raw: mark_price, spot_price, funding_rate, timestamp
    def funding_history(self, symbol: str, hours: int) -> list[tuple[float, float]]   # (ts, apr) via FUNDING:BTCUSD candles
    def account(self) -> Account
    def set_leverage(self, product_id: int, leverage: float) -> None
    def place_ioc(self, product_id: int, side: Side, size: int, limit_price: float,
                  client_order_id: str, reduce_only: bool = False) -> Fill
    def change_margin(self, product_id: int, delta_usd: float) -> None
    def funding_payments(self, since_ts: float) -> list[FundingPayment]
    def heartbeat(self, hb_id: str, ttl_ms: int = 60000) -> None       # create once, then ack
DeltaClient(env: Env, key: str | None, secret: str | None)             # mainnet with key=None = read-only
FakeExchange(...)                                                      # in-memory books/fills/positions; scripted funding; used by tests and DEMO_MODE
```
Errors: `ExchangeError(code, message, http_status)`. `ip_not_whitelisted_for_api_key` → `code="ip_not_whitelisted"`.

### market
`build_snapshot(trade: ExchangeClient, funding_src: ExchangeClient, *, min_days=14, max_days=60, strike_band=0.15) -> MarketSnapshot`
It returns pairs with both books non-empty only.

### strategy (pure)
```python
@dataclass(frozen=True) class Params:
    min_days=21; max_days=45; leverage=5.0; reserve_pct=0.10
    min_net_edge_pct=0.15        # net edge after ALL costs, % of notional, over the hold
    max_fee_ratio=0.5            # costs (fees + slippage, entry + exit) <= 50% of expected gross funding edge
    max_spread_cost_pct=0.12     # half-spread on both options, % of notional
    max_book_share=0.2           # never take more than 20% of visible depth within tolerance
    price_tolerance=0.005
    exit_avg24_apr=0.0; exit_now_apr=-25.0; hard_stop_pct=-0.5; no_early_exit_days=3.0
    capital_usd=100.0; auto_enter=False
depth_fill(book, side, contracts) -> tuple[float | None, int]          # VWAP over levels, contracts fillable
forecast_apr(f: FundingView) -> float                                 # 24h mean, clamped to [-54.75, 54.75]
evaluate(snap, params, contracts_for) -> Decision                     # contracts_for(pair) -> int (from risk)
status(pos: PositionRecord | None, account: Account, snap, params, funding_usd: float) -> Status
```
`Candidate`, `Cost`, `Decision`, `Status` are frozen dataclasses defined in `strategy`. `Status.state` is one of `FLAT, EARNING, BELOW_FIXED, FLIPPED, EXITING, HEDGE_BROKEN, EXPIRING`, and `Status.action` one of `none, hold, exit, unwind, close_perp`. Every `Candidate` carries `reasons` (why rejected) so the console can show *why not*.

### risk (pure)
```python
size_contracts(capital_usd, snap, pair, params) -> int                # capital and book-depth caps
margin_health(account, pos, params) -> list[LegHealth]                # health_pct, liq_price, distance_pct per leg
plan(account, pos, snap, params, reserve_usd) -> list[RiskAction]     # topup | deleverage | freeze, with reason
preflight(snap, now, params) -> list[str]                             # blocking problems: stale data (>30s), no books, etc.
```

### execution
```python
enter(ex, cand, params, tag) -> EntryResult         # set leverage -> sell put -> buy call -> short perp; fresh book each; unwind on failure
close_all(ex, pos, reason) -> list[Fill]             # reduce-only put -> call -> perp
topup(ex, product_id, usd) / deleverage(ex, pos, fraction) -> list[Fill]
```
`client_order_id = f"{tag}-{leg}-{attempt}"`, so a retry can never double-place.

### ledger
`Ledger(path)` with `open_position(rec)`, `current() -> PositionRecord | None`, `close(id, reason, pnl)`, `add_funding(pos_id, FundingPayment)`, `funding_total(pos_id)`, `event(level, kind, message, data)`, `events(limit)`, `positions(limit)`, `kv_get/kv_set` (settings, bot mode). Levels: `info, warn, action, critical`.

### HTTP API (console contract, JSON, all under `/api`)
| Method · path | Returns / body |
|---|---|
| `GET /health` | `{ok, trading_env, funding_source, key_ok, error?, bot_mode, last_tick_ts}` |
| `GET /state` | `{bot:{mode:"running"\|"paused", auto_enter, last_tick_ts, tick_seconds, stale}, market:{spot, perp_mark, funding_now_apr, funding_avg24_apr, funding_source, override_apr}, account:{equity, available, margin_mode}, position: null \| {id, status, status_reason, action, opened_ts, expiry_ts, days_left, strike, contracts, notional_usd, fixed_apr, floating_apr, spread_apr, funding_usd, fees_usd, earned_usd, projected_usd, worst_case_usd, reserve_usd, legs:[{symbol, side, size, entry_price, mark, margin, liquidation_price, health_pct, distance_pct}]}, decision:{action, reason, best: Offer \| null}}` |
| `GET /offers` | `{decision:{action, reason}, offers:[Offer]}` where Offer = `{id, expiry_ts, strike, days, fixed_apr, forecast_apr, contracts, notional_usd, capital_usd, gross_edge_usd, cost_usd, fees_usd, slippage_usd, net_edge_usd, net_edge_pct, fee_ratio, ok, reasons[]}` |
| `GET /funding?hours=168` | `{source, points:[{ts, apr}], fixed_apr \| null, exit_apr}` |
| `GET /events?limit=100` | `{events:[{id, ts, level, kind, message, data}]}` |
| `GET /positions` | `{positions:[PositionRecord + funding_usd]}` (history and final reports) |
| `GET /settings` · `PUT /settings` | the `Params` fields |
| `POST /bot/start` `{auto_enter}` · `POST /bot/pause` · `POST /bot/tick` | `{ok}` |
| `POST /enter` `{offer_id}` | `{ok, message, position_id?}` (user takes an offer manually) |
| `POST /position/close` | `{ok, message}` |
| `POST /demo/funding` `{apr \| null}` | override the funding input to rehearse a flip on testnet, which is clearly labelled everywhere |

## 5. Environment variables (read only in `keys.py`)
| Owner | Variables |
|---|---|
| `exchange` | `DELTA_KEY`, `DELTA_SECRET`, `TRADING_ENV` (testnet), `FUNDING_SOURCE` (mainnet) |
| `ledger` | `LEDGER_PATH` (`./data/ledger.sqlite`) |
| `apps/bot` (via exchange/ledger keys only) | `TICK_SECONDS` (30), `DEMO_MODE` (0/1 → FakeExchange) |
| console | `NEXT_PUBLIC_API_URL` (read in one file, `src/lib/config.ts`) |

## 6. Testing
| Layer | How |
|---|---|
| strategy, risk | pure unit tests: depth walk, fee guard, every status transition, sizing caps, top-up/deleverage |
| exchange | signing against a known vector, response parsing from recorded JSON; live smoke via `apps/verify` |
| execution | FakeExchange: leg failure → unwind, stale quote, partial fill, leverage failure → abort |
| e2e | `tests/test_e2e_fake.py`: enter → earn → funding flip → 24h exit → flat, all through `Bot.tick()` |
| architecture | `tests/test_architecture.py` |
| console | `npm run build` + type check |

## 7. Not created on purpose
| Not created | Why |
|---|---|
| WebSocket feed | 30s ticks are enough for a 33-day position; REST is simpler and the brief makes WS optional |
| Database server | SQLite: one writer (the bot), small data |
| `utils` package | helpers stay in the package that needs them |
| ORM, task queue, auth service | single-user bot; the API key stays server-side and never reaches the browser |
