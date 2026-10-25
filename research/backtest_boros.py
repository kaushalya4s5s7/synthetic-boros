"""Historical backtest of Synthetic Boros on Delta production data (research only, not trading).

Inputs (made by fetch_funding.py / fetch_options.py):
  BTC_1h.csv            hourly funding (% per 8h) and perp mark
  options_forward.csv   options-implied forward F = K + C - P at entry times before each expiry

Long YU  = short perp + long synthetic: P&L = realised funding - fixed cost - costs
Short YU = the reverse:                 P&L = fixed - realised funding - costs
All P&L is in % of notional, held to expiry.
"""
import csv
import statistics as st
import sys

BASELINE_APR = 10.95                       # Delta's funding sits here ~60% of the time (2y data)


def load():
    fund = {int(r["time"]): float(r["funding_pct_8h"]) for r in csv.DictReader(open("BTC_1h.csv"))}
    rows = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(open("options_forward.csv"))]
    return fund, rows


def realised_pct(fund, t0, t1):
    """Funding accrued by a 1-notional short perp from t0 to t1, in % of notional (hourly accrual of the 8h rate)."""
    return sum(fund.get(h, 0.0) / 8 for h in range(int(t0), int(t1), 3600))


def forecast_apr(fund, t, kind):
    hist = [fund[h] * 3 * 365 for h in range(int(t) - 7 * 86400, int(t), 3600) if h in fund]
    last24 = hist[-24:] or [BASELINE_APR]
    if kind == "last24":
        return st.mean(last24)
    if kind == "avg7d":
        return st.mean(hist or [BASELINE_APR])
    if kind == "blend":                      # recent average pulled halfway to the baseline
        return 0.5 * st.mean(hist or [BASELINE_APR]) + 0.5 * BASELINE_APR
    raise ValueError(kind)


def run(fund, rows, policy, forecast="blend", threshold=0.15, cost=0.10, min_days=7):
    """cost: total % of notional for fees + half-spreads (stress it). threshold: min expected edge in %."""
    res = []
    for r in rows:
        if r["days"] < min_days:
            continue
        fixed_pct = (r["F"] / r["perp"] - 1) * 100            # fixed leg cost over the holding period, %
        floating = realised_pct(fund, r["t"], r["expiry"])
        if policy == "long":
            side = 1
        elif policy == "short":
            side = -1
        else:
            exp_float = forecast_apr(fund, r["t"], forecast) * r["days"] / 365
            edge = exp_float - fixed_pct
            if abs(edge) - cost < threshold:
                continue
            side = 1 if edge > 0 else -1
        res.append(side * (floating - fixed_pct) - cost)
    return res


def report(name, pnl, days_hint):
    if not pnl:
        print(f"{name:42} no trades"); return
    print(f"{name:42} n={len(pnl):3}  win {sum(p > 0 for p in pnl)/len(pnl):5.0%}  "
          f"mean {st.mean(pnl):+.3f}%  worst {min(pnl):+.3f}%  ~APR {st.mean(pnl)*365/days_hint:+.2f}%")


if __name__ == "__main__":
    fund, rows = load()
    cost = float(sys.argv[1]) if len(sys.argv) > 1 else 0.10
    print(f"{len(rows)} entries from {len({r['expiry'] for r in rows})} expiries, cost per trade {cost}% of notional\n")
    for d in (7, 14, 21, 28):
        sub = [r for r in rows if r["days"] == d]
        if not sub:
            continue
        fx = [ (r["F"]/r["perp"]-1)*365/d*100 for r in sub]
        fl = [realised_pct(fund, r["t"], r["expiry"])*365/d for r in sub]
        print(f"--- {d}-day tenor: implied fixed APR mean {st.mean(fx):.2f}  realised floating APR mean {st.mean(fl):.2f}")
        report("always Long YU (short perp + long synth)", run(fund, sub, "long", cost=cost), d)
        report("always Short YU", run(fund, sub, "short", cost=cost), d)
        for fc in ("last24", "avg7d", "blend"):
            report(f"rule ({fc} forecast, 0.15% threshold)", run(fund, sub, "rule", fc, cost=cost), d)
        print()
