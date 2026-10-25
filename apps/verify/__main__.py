"""Live testnet check of the API assumptions behind Synthetic Boros.

  python -m apps.verify          read-only checks + one far-away put sell that cannot fill (cancelled)
  python -m apps.verify --live   also opens a 1-contract position (put -> call -> perp), measures margin,
                                 tests leverage and margin top-up, then closes everything (reduce-only)
Every step records PASS/FAIL and the next step still runs. Results go to data/verify_results.json.
"""
import json
import sys
import time
from pathlib import Path

from packages.exchange import from_env
from packages.market import build_snapshot

TOL = 0.005
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


def main(live: bool) -> None:
    ex, funding_src = from_env()
    step("1 auth + account", lambda: ex.account())
    snap = step("2 snapshot (books + mainnet funding)", lambda: build_snapshot(ex, funding_src))
    if not snap or not snap.pairs:
        return
    print(f"      pairs={len(snap.pairs)} funding[{snap.funding.source}] now={snap.funding.now_apr:.2f}% "
          f"avg24={snap.funding.avg24_apr:.2f}%")
    pair = min((p for p in snap.pairs if 21 * 86400 <= p.expiry_ts - snap.ts <= 45 * 86400),
               key=lambda p: abs(p.strike - snap.spot), default=snap.pairs[0])
    days = (pair.expiry_ts - snap.ts) / 86400
    cb, ca, pb, pa = pair.call_book.best_bid, pair.call_book.best_ask, pair.put_book.best_bid, pair.put_book.best_ask
    step("3 pair + spreads", lambda: {
        "call": pair.call.symbol, "put": pair.put.symbol, "days": round(days, 1),
        "fixed_apr_mid_%": round(((pair.strike + (cb + ca) / 2 - (pb + pa) / 2) / snap.spot - 1) * 365 / days * 100, 2),
        "fixed_apr_cross_%": round(((pair.strike + ca - pb) / snap.spot - 1) * 365 / days * 100, 2),
        "half_spread_cost_%": round(((ca - cb) + (pa - pb)) / 2 / snap.spot * 100, 3)})

    def far_put():
        before = ex.account().order_margin
        o = ex.place_limit(pair.put.product_id, "sell", 1, round(pa * 3, 1), post_only=True)
        time.sleep(1)
        after = ex.account().order_margin
        ex.cancel(o["id"], pair.put.product_id)
        return {"state": o["state"], "order_margin_added": round(after - before, 4)}
    step("4 short put allowed (far limit, cancelled)", far_put)
    step("5 heartbeat create + ack", lambda: ex.heartbeat("synthboros-verify", 60000) or "ok")
    step("6 set perp leverage 5x", lambda: ex.set_leverage(snap.perp.product_id, 5) or "ok")
    if not live:
        return

    tag = f"v{int(time.time())}"

    def ioc(inst, side, reduce_only=False):
        b = ex.book(inst.symbol)
        top = b.best_bid if side == "sell" else b.best_ask
        px = top * (1 - TOL) if side == "sell" else top * (1 + TOL)
        px = round(round(px / inst.tick) * inst.tick, 6)
        f = ex.place_ioc(inst.product_id, side, 1, px, f"{tag}-{inst.kind}-{side}", reduce_only)
        if f.filled < 1:
            raise RuntimeError(f"no fill: {f.reason}")
        return f
    fp = step("7a sell 1 put", lambda: ioc(pair.put, "sell"))
    fc = step("7b buy 1 call", lambda: ioc(pair.call, "buy")) if fp else None
    fx = step("7c short 1 perp", lambda: ioc(snap.perp, "sell")) if fc else None
    if fp and fc and fx:
        F = pair.strike + fc.avg_price - fp.avg_price
        step("8 locked fixed rate", lambda: {"forward": F, "perp": fx.avg_price,
                                             "fixed_apr_%": round((F / fx.avg_price - 1) * 365 / days * 100, 2),
                                             "entry_fees_usd": round(fp.fee + fc.fee + fx.fee, 5)})
    time.sleep(2)
    step("9 positions (margin, liq)", lambda: [(l.symbol, l.size, l.margin, l.liquidation_price) for l in ex.account().legs])
    step("10 margin top-up +1 USD on perp", lambda: ex.change_margin(snap.perp.product_id, 1.0) or
         [l.margin for l in ex.account().legs if l.product_id == snap.perp.product_id])
    step("11 margin remove -1 USD on perp", lambda: ex.change_margin(snap.perp.product_id, -1.0) or
         [l.margin for l in ex.account().legs if l.product_id == snap.perp.product_id])

    def close_all():
        out = []
        by_id = {snap.perp.product_id: snap.perp, pair.call.product_id: pair.call, pair.put.product_id: pair.put}
        for leg in sorted(ex.account().legs, key=lambda l: {"put": 0, "call": 1, "perp": 2}.get(
                by_id.get(l.product_id, snap.perp).kind, 3)):
            inst = by_id.get(leg.product_id)
            if inst:
                out.append(ioc(inst, "buy" if leg.size < 0 else "sell", reduce_only=True).filled)
        return out
    step("12 close everything (reduce-only)", close_all)
    time.sleep(2)
    step("13 positions after close", lambda: [(l.symbol, l.size) for l in ex.account().legs])


if __name__ == "__main__":
    main("--live" in sys.argv)
    Path("data").mkdir(exist_ok=True)
    json.dump(results, open("data/verify_results.json", "w"), indent=1, default=str)
    print("\nsaved data/verify_results.json")
