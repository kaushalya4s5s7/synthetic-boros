"""Compare perp funding across Delta India, CoinDCX, Binance and Bybit, normalised to APR.

Run: python funding_scan.py
Live snapshot first, then 30 days of history for Delta vs Binance to see whether a spread persists.
"""
import time

import requests

UA = {"User-Agent": "python-tokeniq"}
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "LTC", "AVAX", "LINK"]


def get(url, **params):
    return requests.get(url, params=params, headers=UA, timeout=15).json()


def apr(rate_per_interval, hours):
    return rate_per_interval * (24 / hours) * 365 * 100  # percent per year


def delta_now():
    out = {}
    for t in get("https://api.india.delta.exchange/v2/tickers", contract_types="perpetual_futures")["result"]:
        sym = t["symbol"]
        if sym.endswith("USD") and t.get("funding_rate") is not None:
            # Delta reports funding in percent per 8h interval (0.01 == 0.01%)
            out[sym[:-3]] = apr(float(t["funding_rate"]) / 100, 8)
    return out


def coindcx_now():
    out = {}
    prices = get("https://public.coindcx.com/market_data/v3/current_prices/futures/rt")["prices"]
    for pair, p in prices.items():
        if pair.startswith("B-") and pair.endswith("_USDT") and p.get("fr") is not None:
            out[pair[2:-5]] = apr(float(p["fr"]), 8)  # fraction per 8h (funding_frequency: 8)
    return out


def binance_now():
    return {d["symbol"][:-4]: apr(float(d["lastFundingRate"]), 8)
            for d in get("https://fapi.binance.com/fapi/v1/premiumIndex") if d["symbol"].endswith("USDT")}


def bybit_now():
    out = {}
    for d in get("https://api.bybit.com/v5/market/tickers", category="linear")["result"]["list"]:
        if d["symbol"].endswith("USDT") and d.get("fundingRate"):
            hrs = float(d.get("fundingIntervalHour") or 8)
            out[d["symbol"][:-4]] = apr(float(d["fundingRate"]), hrs)
    return out


def delta_history(coin, days=30):
    end = int(time.time())
    rows = get("https://api.india.delta.exchange/v2/history/candles", resolution="1h",
               symbol=f"FUNDING:{coin}USD", start=end - days * 86400, end=end)["result"]
    return {int(r["time"]) // 3600 * 3600: float(r["close"]) for r in rows}


def binance_history(coin, days=30):
    start = (int(time.time()) - days * 86400) * 1000
    rows = get("https://fapi.binance.com/fapi/v1/fundingRate", symbol=f"{coin}USDT", startTime=start, limit=1000)
    return [(int(r["fundingTime"]) // 1000, float(r["fundingRate"])) for r in rows]


if __name__ == "__main__":
    venues = {"Delta": delta_now(), "CoinDCX": coindcx_now(), "Binance": binance_now(), "Bybit": bybit_now()}
    print("LIVE funding, annualised % (positive = longs pay shorts)\n")
    print(f"{'coin':6}" + "".join(f"{v:>10}" for v in venues) + f"{'max-min':>10}")
    for c in COINS:
        vals = [venues[v].get(c) for v in venues]
        have = [x for x in vals if x is not None]
        row = "".join(f"{x:>10.2f}" if x is not None else f"{'-':>10}" for x in vals)
        print(f"{c:6}{row}{(max(have) - min(have)) if len(have) > 1 else 0:>10.2f}")

    print("\n30-day history: Delta FUNDING candles vs Binance settlements (annualised %)")
    for c in ["BTC", "ETH", "SOL", "XRP", "DOGE"]:
        try:
            dh, bh = delta_history(c), binance_history(c)
            pairs = []
            for ts, b in bh:
                hour = ts // 3600 * 3600
                d = dh.get(hour) if hour in dh else dh.get(hour - 3600)
                if d is not None:
                    pairs.append((apr(d / 100, 8), apr(b, 8)))
            if not pairs:
                print(f"  {c}: no overlapping data")
                continue
            spread = [d - b for d, b in pairs]
            mean = sum(spread) / len(spread)
            same_sign = sum(1 for s in spread if s > 0) / len(spread) * 100
            print(f"  {c:5} n={len(pairs):3}  Delta avg {sum(p[0] for p in pairs)/len(pairs):7.2f}  "
                  f"Binance avg {sum(p[1] for p in pairs)/len(pairs):7.2f}  spread avg {mean:7.2f}  "
                  f"Delta>Binance {same_sign:5.1f}% of periods")
        except Exception as e:  # keep scanning the other coins
            print(f"  {c}: error {e}")
