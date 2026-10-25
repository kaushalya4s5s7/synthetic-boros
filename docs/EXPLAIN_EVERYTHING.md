# Explain Everything: Synthetic Boros in the simplest words

Read this once and you can explain the idea, the money, the bot, the screens and every file.
Each part starts with **one line you can say out loud**, then the detail.

---

## Part 1. The idea

> **Say:** "Delta pays people a floating rate for being short its perpetual futures. We lock a cheaper fixed rate using options, and keep the difference. The coin's price doesn't matter."

### The shop analogy

- A shop **pays you 11% a year** for doing a job (the job: being short the perpetual).
- Another shop **charges you 7% a year**, fixed, for insurance that protects you from price moves (the call + put).
- You do both and **keep 4%**. If the price of BTC goes up or down, the two cancel.

### Three words you must know

| Word | Dumbest explanation |
|---|---|
| **Perpetual (perp)** | A bet on a coin's price that never expires. To keep its price honest, one side pays the other every 8 hours. |
| **Funding** | That 8-hourly payment. When positive, **people who are short get paid**. This is the money we earn. It's "floating" because it changes. |
| **Options (call + put)** | A **call** = "I can buy at $79,000 on 27 Nov". A **put** = "I can sell at $79,000 on 27 Nov". Buy the call and sell the put, and it's the same as **"I will buy at a fixed price on 27 Nov"**. That fixed price gives us our **fixed rate**. |

### Why the name?

**Pendle Boros** is a crypto product that lets people swap floating funding for a fixed rate. Delta doesn't have one, so we **built it ("synthetic") from pieces Delta already sells.**

---

## Part 2. The trade, with real numbers

> **Say:** "Three orders. Sell a put, buy a call, short the perp. The first two lock a price; the third earns funding."

1 contract = 0.001 BTC ≈ $80.

| Step | Order | Price | Cash |
|---|---|---|---|
| 1 | Sell 1 put (strike $79,000, 27 Nov) | $3,005 | **you get $3.01** |
| 2 | Buy 1 call (strike $79,000, 27 Nov) | $4,780 | **you pay $4.78** |
| 3 | Short 1 BTC perpetual | $80,271.50 | no payment; **$16 deposit** locked (still yours) |

**What it adds up to:**
- Call − put = "I'll buy BTC on 27 Nov at $79,000 + $4,780 − $3,005 = **$80,775**."
- Short perp = "I sold BTC at **$80,271.50**."
- Sell at $80,271.50, buy back at $80,775 = **we pay $0.50**. That's the fixed cost. As a yearly rate it's **6.92%**: the **fixed rate**.
- The short perp **earns funding** every 8 hours. That's the income.

**Price doesn't matter.** Whatever BTC does by 27 Nov:

| BTC ends at | Result from the price legs |
|---|---|
| +10% | +$1.27 |
| flat | +$1.27 |
| −10% | +$1.27 |

Always the same. **So the only thing that decides profit is funding versus the fixed rate.**

---

## Part 3. When it trades, when it waits

> **Say:** "It only trades when the funding we expect to earn beats the fixed rate plus all fees. Otherwise it waits and tells you why."

```
profit = funding earned − fixed cost − fees
```

| | Funding earned | Fixed cost | Fees | Profit | Decision |
|---|---|---|---|---|---|
| **Today** (real funding ~5%) | +$0.36 | −$0.50 | −$0.13 | **−$0.27** | **Wait** ("No trade") |
| **Usual level** (10.95%, 60% of the last 2 years) | +$0.80 | −$0.50 | −$0.13 | **+$0.16** | **Trade** |

### Which numbers are real, and which are predictions?

| Number on screen | Real or prediction? |
|---|---|
| **Fixed rate** | **Real.** Calculated from actual prices in the order book right now. |
| **Funding now** | **Real.** What Delta's real market pays this minute. |
| **Funding forecast** | **A prediction from real data**: the average of the last 24 hours, assumed to continue. |

### The fee guard (our main protection)

The bot trades **only if** both are true:
1. Fees and spreads are **less than half** of the expected funding gain.
2. Profit after all costs is at least **0.15%** of the position.

---

## Part 4. Your money

> **Say:** "Your money never leaves Delta. You choose how much the bot may use; most of it is a refundable deposit."

| Question | Answer |
|---|---|
| Where is the money? | In **your Delta account**, always. The API key lets the bot trade it. |
| What does the amount box do? | It sets the **most the bot may use**. Typing it **buys nothing**. |
| What is it used for? | Mostly **deposits (margin)** that come back when the trade closes. Only the option prices are actually paid or received. |
| Minimum? | About **$30** (1 BTC contract needs about $27). |
| Why do $30 and $40 look the same? | Both cover **1 contract**. You need about $55 for 2. Contracts come in whole steps. |
| Which coin gets bought? | **Whichever passes the fee guard with the best profit**, gold or BTC. If none pass, nothing is bought. |
| Who limits the size? | **You** (the amount) and **us**: 5x leverage, 10% reserve, never more than 20% of the order book. **Delta**: minimum 1 contract. |

| Amount | Contracts (BTC, live) |
|---|---|
| $20 | 0 (too small) |
| $30–50 | 1 |
| $100 | 3 |
| $1,000 | 39 |
| $5,000 | 93 (capped by the order book) |

---

## Part 5. Gold / RWA

> **Say:** "Real-world assets on Delta have funding that never moves. Gold is the only one with options, so the bot checks gold first."

| Asset | Funding (last 90 days) | Moves? | Options? |
|---|---|---|---|
| Gold (XAUT) | 5.48% | **Never** | **Yes** |
| Stock and silver tokens | 10.95% | Never | No |
| BTC | 3.8% to 11% | Yes | Yes |

- Flat funding means **no surprise flips**, so for gold the bot also allows **short trades (2+ days)**.
- **On mainnet:** gold looks like the best version of the trade (tight prices, tiny 0.01% fees).
- **On testnet:** gold's option books are **empty** (a $0.10 bid against a $450 ask), so the bot rejects gold, and BTC is what trades.

---

## Part 6. Safety (risk management)

> **Say:** "Every risk has a rule, and the bot acts on it by itself."

| What could go wrong | What the bot does |
|---|---|
| Funding turns negative | **Waits.** Most flips end within 8 hours. Exits if it stays negative for a **whole day**, or drops below −25% a year. |
| Fees bigger than profit | **Fee guard** says no. No early exit in the last 3 days, because leaving would cost more. |
| Not enough buyers or sellers | Prices every order from the **real order book**, never takes more than 20% of it, and rejects wide spreads. |
| Only some orders fill | Orders go put first, then call, then perp. If one fails, it **undoes the others within seconds**. |
| Too much leverage | **Forces 5x.** Delta's default is 10x on testnet and 200x on mainnet. |
| Big price move | Tops up the deposit from a **10% reserve**. If that runs out, closes half of everything (still hedged). |
| Bot crashes | Delta's **heartbeat** (a dead-man switch) cancels the bot's open orders automatically. |
| Old data or lost connection | **Freezes** trading and shows an alert. |
| Not enough money | Refuses the trade. |

---

## Part 7. The screens (what to click, what to say)

| Screen | Click | Say |
|---|---|---|
| **Landing** | Open console | "Fixed vs floating, hedged. Live funding right here." |
| **Overview** | Point at the top bar and the assets table | "Wallet $98, testnet orders, real-market funding. Gold flat, BTC moving." |
| **Offers** | Type 40 → Update offers | "Enough for 1 contract. Nothing bought yet." |
| | Point at "No trade" | "Funding 5% < fixed 7%, so trading would lose. It waits." |
| | Scroll to "What you are putting on" | "The 3 orders, the cash each one moves, and proof the price cancels." |
| **Settings** | Rehearsal → Normal 10.95%, Auto-enter on | "Testnet's real funding rate. Watch it trade within 30 seconds." |
| **Position** | Show the numbers | "Locked 7% vs 10.95%. Liquidation nearly 20% away." |
| **Settings** | Flip −10% → Crash −30% | "It holds through a flip, then exits by itself." |
| **Activity** | Scroll | "Every decision with its reason. Order IDs match Delta's own history." |
| **Guide** | — | "Every word in plain English." |
| **Settings** | Rehearsal → Clear | (reset) |

**What is Rehearsal?** Testnet funding never changes, so you could never see the safety rules work. Rehearsal swaps **only the funding number** the bot reads. The orders are still real testnet orders.

---

## Part 8. The tech

> **Say:** "A website, a Python brain, and Delta's API. The brain runs every 30 seconds."

```
  Website (Next.js)          Brain (Python + FastAPI)              Delta Exchange
  what you see       ──►     checks every 30 seconds       ──►     testnet: places orders
  localhost:3000             localhost:8000                        mainnet: real funding (read only)
                             remembers everything in SQLite
```

**What the brain does every 30 seconds:**
1. **Look:** read prices, order books and funding.
2. **If a trade is open:** are all 3 orders still there? Any exit rule hit? Does the deposit need topping up?
3. **If no trade:** price every option, run the fee guard, and trade the best one if auto-enter is on.
4. **Heartbeat:** tell Delta "I'm alive".
5. **Write it down:** save what it did and why, only when something changes.

**Why read funding from mainnet?** Testnet funding is fake-flat at 10.95%. Real decisions need real data.

---

## Part 9. Every file, in plain English

### The brain (Python), in `packages/`: each folder does one job

| File | What it does, in plain English |
|---|---|
| `packages/domain/types.py` | **The dictionary.** Defines what a "price", "order book", "position" or "fill" looks like, so every part speaks the same language. |
| `packages/exchange/__init__.py` | **The phone to Delta.** Every call to Delta goes through here: get prices, get balance, place an order, set leverage, add margin, send the heartbeat. |
| `packages/exchange/_rest.py` | **The signature.** Signs each request with your API key (a secure stamp), retries when Delta is slow, and turns Delta's errors into clear messages (e.g. "IP not whitelisted"). |
| `packages/exchange/keys.py` | **Reads your secrets** (API key, secret, testnet/mainnet) from the `.env` file. The only place that does. |
| `packages/exchange/fake.py` | **A pretend Delta** that lives in memory. Used by tests and demo mode, so we can test everything without real money or internet. |
| `packages/market/__init__.py` | **The eyes.** Builds one "photo" of the market each tick: price, funding (from mainnet), and every option pair with its order book. Works for gold and BTC. |
| `packages/strategy/__init__.py` | **The decision maker.** Prices every possible trade on the real order book, adds every fee (+18% GST), runs the fee guard, picks the best. Also decides the position's status (Earning, Flipped, Exiting…). Pure maths, no internet, so it's easy to test. |
| `packages/risk/__init__.py` | **The safety officer.** How many contracts your money allows, how healthy each deposit is, when to top up, when to close half, and whether the data is too old to trust. |
| `packages/execution/__init__.py` | **The hands.** Actually places the 3 orders in the safe order (put → call → perp), each priced from a fresh order book. Undoes everything if one fails. Also closes trades and adds margin. |
| `packages/ledger/__init__.py` | **The diary.** Saves positions, funding received, and every event ("why the bot did what it did") in a small database file (`data/ledger.sqlite`). |
| `packages/ledger/keys.py` | Reads where the diary file lives. |

### The programs that run, in `apps/`

| File | What it does |
|---|---|
| `apps/bot/runner.py` | **The heartbeat of the brain.** The 30-second loop: look → check the position → decide → act → write down. Also handles Start, Pause, Close all, Enter offer and Rehearsal. Scans gold and BTC and picks the best. |
| `apps/bot/__main__.py` | Runs the bot alone without the website (`python -m apps.bot`). |
| `apps/bot/keys.py` | Reads how often to tick (30s) and whether demo mode is on. |
| `apps/api/main.py` | **The waiter.** The website asks it questions ("what's the state?", "enter this offer") and it asks the bot and answers. Also starts the 30-second loop. |
| `apps/api/deps.py` | **Wiring.** Connects the real Delta (or the fake one in demo mode) and the diary to the bot. |
| `apps/api/errors.py` | Turns any error into one clean message for the website. |
| `apps/verify/__main__.py` | **The proof script.** Places 1 real contract on testnet, checks leverage, margin, heartbeat and fees, then closes it. Proves the Delta API does what we assume. |

### The website, in `apps/web/src/`

| File | What it does |
|---|---|
| `app/page.tsx` + `app/landing.css` | **Landing page.** The pitch, how it works, the fee guard, safety, evidence, FAQ. |
| `app/(console)/overview/page.tsx` | **Overview.** Connection, funding, best fixed rate, assets table (gold vs BTC). |
| `app/(console)/offers/page.tsx` | **Offers.** Amount box, best offer, "what you're putting on", the table of every candidate with its reason. |
| `app/(console)/position/page.tsx` | **Position.** Locked vs floating, earnings, funding chart, deposits and liquidation prices, Pause and Close all. |
| `app/(console)/activity/page.tsx` | **Activity.** The bot's diary and past trades. |
| `app/(console)/settings/page.tsx` | **Settings.** Start/pause, auto-enter, assets, risk limits, Rehearsal. |
| `app/(console)/guide/page.tsx` | **Guide.** Plain-English help inside the app. |
| `app/(console)/layout.tsx` | Puts the top bar and left menu around every console page. |
| `components/Shell.tsx` | The **top bar** (wallet, testnet/mainnet, bot running) and the **left menu**. |
| `components/Live.tsx` | Asks the brain for fresh data **every 5 seconds** and shares it with every page. |
| `components/PutOn.tsx` | The **"What you are putting on"** panel: 3 orders, cash, the profit maths, the price-cancels table. |
| `components/FundingChart.tsx` | The funding line chart with your fixed rate and the exit line. |
| `components/LiveStrip.tsx` | The live funding numbers on the landing page. |
| `components/ui.tsx` | Small reusable pieces: stat cards, confirm pop-ups, loading and empty states. |
| `lib/api.ts` | **The only way the website talks to the brain.** Every request is listed here. |
| `lib/config.ts` | Where the brain lives (`localhost:8000`). |
| `lib/format.ts` | Makes numbers pretty ($, %, dates). |
| `app/globals.css` | The look: one blue, thin lines, no clutter (from `docs/ui-designg.md`). |

### Tests, in `tests/`: proof the code works (81 tests)

| File | Checks |
|---|---|
| `test_strategy.py` | Fee guard, pricing, every status, flat-funding rule |
| `test_risk.py` | Sizing, margin health, top-up, close-half |
| `test_exchange.py` | Signing, reading Delta's replies, error messages |
| `test_market.py` | Building the market photo, funding from mainnet |
| `test_execution.py` | Order sequence, undo on failure, moving prices |
| `test_ledger.py` | The diary saves and reads correctly |
| `test_e2e_fake.py` | **A full story** on pretend Delta: enter → earn → flip → exit, plus the website API |
| `test_architecture.py` | **The rules of the codebase**: e.g. the decision maker may never call the internet, and only `keys.py` files may read secrets |

### Research, in `research/`: how we found the idea works (not used by the bot)

| File | What it did |
|---|---|
| `fetch_funding.py` | Downloaded 2 years of Delta funding history |
| `fetch_options.py` | Rebuilt the fixed rate from 139 past option expiries |
| `backtest_boros.py` | Tested the rule on history: long-funding wins, 21-day holds best |
| `stress.py` | 4,000 random crash/pump scenarios: 5x + 10% reserve = no breaks |
| `verify_keys.py` | The first live testnet check (now `apps/verify`) |

### Root files

| File | What it is |
|---|---|
| `main.py`, `strategy.py`, `delta_client.py` | **The hackathon template names.** Three-line shortcuts to the real code (`apps/api`, `packages/strategy`, `packages/exchange`). |
| `.env` / `.env.example` | Your secrets (never uploaded) and an empty template. |
| `requirements.txt`, `pyproject.toml` | Python libraries and test settings. |
| `README.md` | Setup, strategy, risk, API usage (what the PDF asks for). |
| `STRATEGY.md`, `RISK_AND_DASHBOARD.md` | The full research notes and risk design. |
| `docs/FOR_JUDGES.md` | The short judge explainer. |
| `docs/how-to-use.md` | Step-by-step usage and which API powers which button. |
| `docs/repo-architecture.md` | The code structure rules. |
| `docs/ui-designg.md` | The design rules for the website. |
| `starter/` | Our first scripts from day one (kept for history). |

---

## Part 10. Questions judges may ask (and short answers)

| Question | Answer |
|---|---|
| Where's the profit? | Funding received minus the fixed rate, after fees. |
| What if BTC crashes? | The legs cancel. Same result at −10%, flat or +10%. Deposits are topped up automatically. |
| Why no trade now? | Real funding (5%) is below the fixed rate (7%). Trading would lose, so it waits. |
| Is the fixed rate real? | Yes, from real order-book prices. Only the funding forecast is a prediction (the 24h average). |
| Why options, not normal futures? | Delta testnet has no dated futures. Call − put builds one. |
| Why gold? | Its funding never moves, and it's the only real-world asset with options. |
| Who decides how much is bought? | The user sets the amount; the bot caps it at 5x leverage and 20% of the order book. |
| Is it really trading? | Yes. Every order has a Delta order ID, visible in Delta's own trade history. |
| What if the bot dies? | Delta's heartbeat cancels its orders; positions stay hedged. |
| Is the money safe? | It never leaves Delta. The bot only has trading permission. |
| How do you know it works? | 2 years of history, a 4,000-scenario stress test, live testnet trades, and 81 automated tests. |
