"""Live testnet check of the Synthetic Boros assumptions (research, not the bot).

Keys come from env vars DELTA_KEY / DELTA_SECRET. Every step records PASS/FAIL and the next step still runs.
  python verify_keys.py          read-only checks + one far-away put sell that cannot fill (cancelled)
  python verify_keys.py --live   also opens the real 1-contract position, measures margin, then closes it
"""
import json
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "../starter")
from delta import Delta

d = Delta()
LIVE = "--live" in sys.argv
results = []


def step(name, fn):
    try:
        out = fn()
        results.append((name, "PASS", out))
        print(f"[PASS] {name}: {out}")
        return out
    except Exception as e:
        results.append((name, "FAIL", str(e)[:300]))
        print(f"[FAIL] {name}: {str(e)[:300]}")
        return None


def usd_wallet():
    for b in d.balances():
        if b["asset_symbol"] in ("USD", "USDT") and float(b["balance"]) > 0:
            return {k: b.get(k) for k in ("asset_symbol", "balance", "available_balance",
                                          "order_margin", "position_margin", "blocked_margin")}
    return "no USD balance"


def order(body):
    return d._req("POST", "/v2/orders", body=body, auth=True)


def pick_synthetic():
    """Expiry 21-45 days out, strike nearest spot with both call and put quoted."""
    spot = float(d.ticker("BTCUSD")["mark_price"])
    tk = d._req("GET", "/v2/tickers", {"contract_types": "call_options,put_options", "underlying_asset_symbols": "BTC"})
    now = time.time()
    by_key = {}
    for t in tk:
        sym = t["symbol"]                                  # C-BTC-110000-271126
        kind, _, strike, exp = sym.split("-")
        exp_ts = datetime.strptime(exp, "%d%m%y").replace(hour=12, tzinfo=timezone.utc).timestamp()
        days = (exp_ts - now) / 86400
        q = t.get("quotes") or {}
        if not (21 <= days <= 45 and q.get("best_bid") and q.get("best_ask")):
            continue
        by_key.setdefault((exp, float(strike), days), {})[kind] = t
    pairs = [(k, v) for k, v in by_key.items() if "C" in v and "P" in v]
    (exp, k, days), v = min(pairs, key=lambda kv: abs(kv[0][1] - spot))
    c, p = v["C"], v["P"]
    cb, ca = float(c["quotes"]["best_bid"]), float(c["quotes"]["best_ask"])
    pb, pa = float(p["quotes"]["best_bid"]), float(p["quotes"]["best_ask"])
    # Long YU entry: buy call at ask, sell put at bid -> forward paid
    F_exec = k + ca - pb
    F_mid = k + (cb + ca) / 2 - (pb + pa) / 2
    return {"spot": spot, "expiry": exp, "days": round(days, 1), "strike": k,
            "call": c["symbol"], "call_id": c["product_id"], "call_bid_ask": (cb, ca),
            "put": p["symbol"], "put_id": p["product_id"], "put_bid_ask": (pb, pa),
            "fixed_apr_mid_%": round((F_mid / spot - 1) * 365 / days * 100, 2),
            "fixed_apr_crossing_spread_%": round((F_exec / spot - 1) * 365 / days * 100, 2),
            "spread_cost_%_notional": round(((ca - cb) + (pa - pb)) / 2 / spot * 100, 3)}


step("1 auth + wallet", usd_wallet)
step("2 open positions", lambda: [(p["product_symbol"], p["size"]) for p in d.positions()])
s = step("3 synthetic pick + real spreads", pick_synthetic)
perp_id = d.product("BTCUSD")["id"]

if s:
    def far_put_sell():
        before = usd_wallet()
        o = order({"product_id": s["put_id"], "side": "sell", "size": 1, "order_type": "limit_order",
                   "limit_price": str(round(s["put_bid_ask"][1] * 3, 1)), "post_only": True})
        time.sleep(1)
        after = usd_wallet()
        d._req("DELETE", "/v2/orders", body={"id": o["id"], "product_id": s["put_id"]}, auth=True)
        return {"accepted": o["state"], "order_margin_before": before["order_margin"],
                "order_margin_after": after["order_margin"]}
    step("4 short put allowed? (far limit, cancelled)", far_put_sell)

step("5 heartbeat create+ack", lambda: (d.heartbeat_create("tokeniq-verify", ["BTCUSD"]),
                                        d.heartbeat_ack("tokeniq-verify", 60000))[1])
step("6 margin mode (read profile)", lambda: {k: v for k, v in d._req("GET", "/v2/profile", auth=True).items()
                                               if "margin" in k.lower() or k in ("id", "is_sub_account")})

if LIVE and s:
    w0 = usd_wallet()
    fills = {}
    TOL = 0.005                                   # accept up to 0.5% worse than the book top (quotes move every second)

    def ioc(pid, sym, side):
        ob = d.orderbook(sym)                     # fresh book, not the ticker snapshot
        top = float(ob["buy"][0]["price"] if side == "sell" else ob["sell"][0]["price"])
        px = round(top * (1 - TOL) if side == "sell" else top * (1 + TOL), 0 if sym != "BTCUSD" else 1)
        o = order({"product_id": pid, "side": side, "size": 1, "order_type": "limit_order",
                   "limit_price": str(px), "time_in_force": "ioc"})
        if o["unfilled_size"]:
            raise RuntimeError(f"no fill: {o['cancellation_reason']}")
        return {"top": top, "fill": o["average_fill_price"], "fee": o["paid_commission"]}

    # short put first: it is the leg the market is least willing to take; if it fails nothing else is open
    fills["put"] = step("7a sell 1 put IOC (fresh book)", lambda: ioc(s["put_id"], s["put"], "sell"))
    if fills["put"]:
        fills["call"] = step("7b buy 1 call IOC (fresh book)", lambda: ioc(s["call_id"], s["call"], "buy"))
        if fills["call"]:
            fills["perp"] = step("7c short 1 perp IOC", lambda: ioc(perp_id, "BTCUSD", "sell"))
    time.sleep(2)
    def implied():
        F = s["strike"] + float(fills["call"]["fill"]) - float(fills["put"]["fill"])
        perp = float(fills["perp"]["fill"])
        return {"F": F, "perp": perp, "locked_fixed_apr_%": round((F / perp - 1) * 365 / s["days"] * 100, 2),
                "edge_vs_10.95_%_over_period": round((10.95 * s["days"] / 365) - (F / perp - 1) * 100, 3)}
    step("8 locked fixed rate from real fills", implied)
    step("9 positions: margin + liquidation", lambda: [
        {k: p.get(k) for k in ("product_symbol", "size", "entry_price", "margin", "liquidation_price")}
        for p in d.positions()])
    step("10 wallet change from position", lambda: {"before": w0, "after": usd_wallet()})

    def close_all():
        out = []
        for p in d.positions():
            side = "buy" if int(p["size"]) < 0 else "sell"
            out.append(order({"product_id": p["product_id"], "side": side, "size": abs(int(p["size"])),
                              "order_type": "market_order", "reduce_only": True})["state"])
        return out
    step("11 close everything (reduce-only)", close_all)
    time.sleep(2)
    step("12 positions after close", lambda: [(p["product_symbol"], p["size"]) for p in d.positions()])

json.dump(results, open("verify_results.json", "w"), indent=1, default=str)
print("\nsaved verify_results.json")
