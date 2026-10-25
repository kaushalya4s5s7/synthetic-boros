"""Download Delta production funding history (1h FUNDING: candles) to CSV for offline stress tests."""
import csv, sys, time
import requests

UA = {"User-Agent": "python-tokeniq"}
BASE = "https://api.india.delta.exchange"


def candles(symbol, res, start, end):
    out, step = [], 2000 * 3600
    t = start
    while t < end:
        r = requests.get(BASE + "/v2/history/candles", headers=UA, timeout=30,
                         params=dict(symbol=symbol, resolution=res, start=t, end=min(t + step, end))).json()
        out += r.get("result") or []
        t += step
    return sorted({c["time"]: c for c in out}.values(), key=lambda c: c["time"])


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 730
    end = int(time.time()); start = end - days * 86400
    for coin in ["BTC", "ETH"]:
        f = candles(f"FUNDING:{coin}USD", "1h", start, end)
        m = candles(f"MARK:{coin}USD", "1h", start, end)
        mk = {c["time"]: c["close"] for c in m}
        with open(f"{coin}_1h.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["time", "funding_pct_8h", "mark"])
            for c in f:
                if c["time"] in mk:
                    w.writerow([c["time"], c["close"], mk[c["time"]]])
        print(coin, len(f), "funding rows,", len(m), "mark rows")
