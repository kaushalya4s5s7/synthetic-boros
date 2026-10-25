"""For each past BTC option expiry, rebuild the options-implied forward at several entry times.

F = K + Call_mark - Put_mark (put-call parity, European, same strike/expiry), using Delta MARK: candles.
Output: options_forward.csv with entry time, expiry, days, strike, perp mark, call, put, implied fixed APR.
"""
import csv, json, time, datetime as dt, collections
import requests

UA = {"User-Agent": "python-tokeniq"}
BASE = "https://api.india.delta.exchange"


def mark_at(symbol, t):
    r = requests.get(BASE + "/v2/history/candles", headers=UA, timeout=30,
                     params=dict(symbol=f"MARK:{symbol}", resolution="1h", start=t - 3 * 3600, end=t + 3600)).json()
    rows = [c for c in r.get("result") or [] if c["time"] <= t]
    return float(max(rows, key=lambda c: c["time"])["close"]) if rows else None


if __name__ == "__main__":
    perp = {int(r["time"]): float(r["mark"]) for r in csv.DictReader(open("BTC_1h.csv"))}
    opts = json.load(open("expired_btc_opts.json"))
    by_exp = collections.defaultdict(dict)
    for o in opts:
        by_exp[o["settlement_time"]][(o["contract_type"][0], float(o["strike_price"]))] = o["symbol"]
    out = []
    for exp_s, legs in sorted(by_exp.items()):
        exp = int(dt.datetime.fromisoformat(exp_s.replace("Z", "+00:00")).timestamp())
        strikes = sorted({k for (cp, k) in legs if ("c", k) in legs and ("p", k) in legs})
        if len(strikes) < 5:
            continue
        for days in (7, 14, 21, 28):
            t = (exp - days * 86400) // 3600 * 3600
            s = perp.get(t)
            if s is None:
                continue
            for k in sorted(strikes, key=lambda k: abs(k - s))[:2]:     # two nearest-the-money strikes
                c, p = mark_at(legs[("c", k)], t), mark_at(legs[("p", k)], t)
                if c is None or p is None:
                    continue
                F = k + c - p
                out.append([t, exp, days, k, s, c, p, F, (F / s - 1) * 365 / days * 100])
                break
        print(exp_s[:10], "done", len(out))
    with open("options_forward.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "expiry", "days", "strike", "perp", "call", "put", "F", "fixed_apr"])
        w.writerows(out)
