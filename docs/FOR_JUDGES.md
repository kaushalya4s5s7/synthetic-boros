# Synthetic Boros: explained simply

> **In one sentence:** Delta pays a floating rate to anyone who is short a perpetual future. We use options to lock a fixed rate. When the fixed rate is cheaper than the floating one, our bot holds both and keeps the difference, and the asset's price cancels out.

---

## 1. The idea in plain words

Think of a bank:

- You **borrow at a fixed 7%**.
- You **lend at a floating 11%**.
- You keep **4%**, and you don't care what happens to house prices, gold prices or BTC prices. You only care about the **gap between the two rates**.

Our bot does exactly that on Delta Exchange:

| Bank version | Our version on Delta |
|---|---|
| Lend at a floating rate | **Short the perpetual future.** Delta pays "funding" to shorts every 8 hours. That's the floating rate. |
| Borrow at a fixed rate | **Buy a call + sell a put** (same strike, same date). Together they lock a fixed price, which works like a fixed rate. |
| Keep the gap | Funding received − fixed rate paid − fees |

This is the same product that [Pendle Boros](https://boros.pendle.finance) offers in DeFi (swapping floating funding for fixed). Delta has no such market, so **we build it from pieces Delta already lists.**

---

## 2. What exactly we put on (real numbers from our console)

The smallest size is **1 contract = 0.001 BTC ≈ $80**. The bot places **three orders**:

| # | Order | Price | Cash |
|---|---|---|---|
| 1 | **Sell 1 put**, strike $79,000, expires 27 Nov | $3,005 | **+$3.01** you receive |
| 2 | **Buy 1 call**, strike $79,000, expires 27 Nov | $4,780 | **−$4.78** you pay |
| 3 | **Short 1 BTCUSD perpetual** | $80,271.50 | No payment. **$16.05** of your money is locked as margin (it stays yours) |

**What these three orders mean together:**

- **Call − put** = "On 27 Nov, I will buy BTC at $79,000 + $4,780 − $3,005 = **$80,775**." That's a locked price.
- **Short perp** = "I sold BTC at **$80,271.50**."
- We sold at $80,271.50 and will buy back at $80,775, so we pay **$0.50**. That's the **fixed cost**. Per year, it's the **fixed rate: 6.92%**.
- The short perp **receives funding** every 8 hours. That's what we earn.

---

## 3. Why the BTC price doesn't matter

On expiry day, whatever BTC does, one side gains what the other loses:

| BTC on 27 Nov | Short perp | Call − put | Together |
|---|---|---|---|
| **$88,299** (+10%) | loses | gains | **+$1.27** |
| **$80,271** (flat) | — | — | **+$1.27** |
| **$72,244** (−10%) | gains | loses | **+$1.27** |

Always the same. Add the option cash (+$3.01 − $4.78) and you get the fixed cost: −$0.50.
**So the only thing left to earn is funding.** That's what "hedged" means.

---

## 4. When the bot trades, and when it refuses

```
expected profit = funding received − fixed cost − fees and spreads
```

| Situation | Funding received | Fixed cost | Fees | Result | Bot does |
|---|---|---|---|---|---|
| **Today** (real funding 5.0%/yr) | +$0.36 | −$0.50 | −$0.13 | **−$0.27** | **Refuses.** Shows "No trade" and why |
| **Funding at 10.95%/yr** (Delta's usual level) | +$0.80 | −$0.50 | −$0.13 | **+$0.16** | **Enters** |

**The fee guard:** the bot only trades if fees and spreads take **less than half** of the expected funding gain, and the profit after all costs is at least 0.15% of the position. The user never pays more in fees than they earn.

Every possible trade (each expiry × each strike) is listed in the console **with the reason it was accepted or rejected.**

---

## 5. Why this has an edge (our research)

| Evidence | What we found |
|---|---|
| **2 years of Delta funding** (17,502 hourly records) | BTC funding sat at exactly 10.95%/yr **60% of the time**, average 8.8%. |
| **Option prices on 139 past expiries** | Options priced a fixed rate of **5.4–7.2%**. Realised funding was **8.3–10.8%**. Fixed was usually cheaper than floating. |
| **Backtest** | The rule won **91%** of trades (21-day holds). Short 7-day holds always lost to fees, so we don't do them on BTC. |
| **Live testnet trade** (25 Oct) | Locked **7.12%** fixed against **10.95%** funding, with 3 real orders on Delta. |

---

## 6. Real-world assets (RWA): tokenised gold first

We checked every RWA perpetual on Delta mainnet for the last 90 days:

| Asset | Funding | How much it moved | Has options? |
|---|---|---|---|
| **Gold (XAUT, PAXG)** | 5.48%/yr | **0.00** (never moved) | **XAUT: yes** |
| Stocks (SPYX, NVDAX, AAPLX…), silver, oil | 10.95%/yr | 0.00 | No |
| Bitcoin (for comparison) | 8.4% avg | ±4.2, ranged −3.8% to 11% | Yes |

- **RWA funding is completely flat.** There's no risk of it suddenly turning negative.
- **XAUT (Tether Gold) is the only RWA with options**, so it's the only one we can build the fixed leg for.
- The bot **scans gold first, then BTC**, and picks whatever passes the fee guard. Because gold funding is flat, the bot also allows short expiries (2+ days) for gold.
- **Honest result:** on mainnet, gold looks like the best version of this trade: tight option prices, a fixed rate below funding, and fees of only 0.01%. On **testnet**, gold's option books are nearly empty (spread cost 4.8% of the position), so the bot correctly rejects it. On testnet, BTC is what trades.

---

## 7. How we protect the user (risk management)

| Risk | What could go wrong | What the bot does automatically |
|---|---|---|
| **Funding turns negative** | We'd pay instead of earn | Most flips end within 8 hours, so it **holds** and shows a countdown. If funding is negative for a **whole day**, or below −25%/yr, it **closes everything**. (In 2 years of data this kept the average profit and cut the worst loss by 60%.) |
| **Fees bigger than profit** | User loses money to fees | **Fee guard** at entry. No early exit in the last 3 days, because exiting costs more than holding. |
| **Low liquidity** | Bad prices | Prices every order by walking the **real order book** for our exact size. Never takes more than 20% of visible depth. Rejects wide spreads. |
| **One order fills, another doesn't** | Unhedged position | Sells the put first, then buys the call, then shorts the perp. If any step fails, it **closes the filled ones within seconds**. (This happened in our live test; the fix is verified.) |
| **Too much leverage** | Liquidation | **Forces 5x** before every trade. Delta's default is 10x on testnet and **200x on mainnet**. |
| **Big price move** | Margin runs low | Alert below 70% margin health, **automatic top-up** below 50% from a 10% reserve, and if the reserve runs out it **closes half of all legs** (still hedged). A 4,000-scenario stress test showed 0% failures at 5x. |
| **Not enough money** | Order fails | Refuses any trade that needs more than the wallet's available balance. |
| **Bot crashes** | Nobody watching | Delta's **heartbeat** (a dead-man switch) cancels the bot's open orders by itself. Positions stay hedged. |
| **Stale data / connection lost** | Acting on old prices | Freezes all trading and shows an alert. |

---

## 8. The platform, screen by screen

| Screen | Answers | What you see |
|---|---|---|
| **Landing** | What is this? | The idea, plus live funding from the real market |
| **Overview** | Is it connected? What is the market paying? | Funding now, best fixed rate, position status, assets scanned (gold flat, BTC moving) |
| **Offers** | How much do I put in? What exactly would I put on? | Amount box → best offer in one sentence → the 3 orders with cash in/out → the dollar maths → every candidate with its reason |
| **Position** | How is my trade doing? Is it safe? | Locked vs floating rate, earned so far, funding chart, margin and liquidation price per leg, the bot's reasons. Pause and Close all buttons |
| **Activity** | What did the bot do, and why? | Every decision and alert with a timestamp and reason, plus past trades with final profit/loss |
| **Settings** | How do I control it? | Start/pause, auto-enter, assets (gold/RWA, BTC), risk limits, **Rehearsal** |
| **Guide** | What does this word mean? | Plain-language glossary |

**Where is the money?** Always in the user's **Delta account**. The API key lets the bot trade it. The amount entered on Offers only limits how much the bot may use. **Minimum: about $30** (one BTC contract).

**What is Rehearsal?** Testnet funding is fixed at 10.95% and never changes, so you could never see the risk engine react. Rehearsal replaces **only the funding number** the bot reads, so judges can watch it hold through a flip and then exit. The orders are still real testnet orders, and every screen shows a "Rehearsal" label while it's on.

---

## 9. What we proved live on Delta testnet (25 Oct 2026)

| Test | Result |
|---|---|
| Real funding (5%) | Bot **refused all candidates** and explained why ✅ |
| Funding at 10.95% | Placed **3 real orders**: sold put at 3,320, bought call at 4,255, shorted perp at 80,415.5. Locked **7.12%** fixed. Liquidation 19.7% away ✅ |
| Funding flips to −10% | Bot **held**: "most flips end within 8h" ✅ |
| Negative for a full day | Bot **closed all 3 orders automatically**. Delta shows zero positions ✅ |
| Leverage, margin top-up, heartbeat, fees with 18% GST | All checked against Delta's API ✅ |
| Every order | Has a real Delta order ID, visible in Delta's own trade history ✅ |
| Code | 81 automated tests passing ✅ |

---

## 10. How it's built

```
Next.js console  ──►  FastAPI backend (Python)  ──►  Delta Exchange API
 (what you see)        bot loop every 30s             testnet: orders
                       strategy · risk · orders       mainnet: funding data (read-only)
                       SQLite journal
```

- **Orders go to testnet.** Funding data for decisions comes from **mainnet**, because testnet funding never moves and would hide the real risk.
- **Every fee is read from Delta's API**, plus 18% GST. Every price comes from the **live order book**, never a stale quote.
- The strategy and risk code is **pure logic** (no network calls), so it's fully tested.

---

## 11. Quick answers

| Question | Answer |
|---|---|
| Where does the profit come from? | Funding received minus the fixed rate, after fees. |
| What if BTC crashes or pumps? | The legs cancel. Same result at −10%, flat or +10%. |
| Why didn't it trade today? | Real funding (5%) is below the fixed rate (6.9%). Trading would lose money, so it refuses. |
| Is the user's money safe from the bot? | It stays on Delta. The bot uses 5x leverage, a reserve, top-ups, and caps how much it can use. |
| Why options, not normal futures? | Delta testnet has no dated futures. Call − put builds one. |
| Why gold? | Its funding hasn't moved in 90 days, and it's the only RWA with options. |
| Is this financial advice? | No. It's a testnet bot that explains every decision it makes. |
