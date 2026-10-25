"""Tiny vectorless backtester on Delta candles. Run: python backtest.py

Strategy shown: moving-average crossover with a fee charged on every position change.
Swap `signal()` for your own idea; everything else (PnL, fees, drawdown, Sharpe) stays the same.
"""
import math
import time

from delta import PROD, Delta

FEE = 0.0005  # assumed 0.05% taker fee per side; check Delta's current fee page before quoting numbers


def signal(closes, fast=20, slow=60):
    """+1 long, -1 short, 0 flat for each bar, using only data up to that bar (no look-ahead)."""
    out = []
    for i in range(len(closes)):
        if i < slow:
            out.append(0)
            continue
        f = sum(closes[i - fast + 1:i + 1]) / fast
        s = sum(closes[i - slow + 1:i + 1]) / slow
        out.append(1 if f > s else -1)
    return out


def backtest(closes, pos):
    equity, peak, max_dd, rets = 1.0, 1.0, 0.0, []
    trades = 0
    for i in range(1, len(closes)):
        r = pos[i - 1] * (closes[i] / closes[i - 1] - 1)   # hold yesterday's position over this bar
        if pos[i] != pos[i - 1]:
            r -= FEE * abs(pos[i] - pos[i - 1])            # pay fees when the position changes
            trades += 1
        equity *= 1 + r
        rets.append(r)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / len(rets)) or 1e-12
    return {"return_%": round((equity - 1) * 100, 2), "max_drawdown_%": round(max_dd * 100, 2),
            "sharpe_per_bar": round(mean / sd, 4), "position_changes": trades}


if __name__ == "__main__":
    d = Delta(PROD)  # real historical prices; trade on TESTNET
    end = int(time.time())
    for sym in ["BTCUSD", "SNDKBUSD"]:
        c = d.candles(sym, "1h", end - 60 * 24 * 3600, end)
        c.sort(key=lambda x: x["time"])
        closes = [float(x["close"]) for x in c]
        buy_hold = round((closes[-1] / closes[0] - 1) * 100, 2)
        print(sym, f"{len(closes)} hourly bars | buy&hold {buy_hold}% |", backtest(closes, signal(closes)))
