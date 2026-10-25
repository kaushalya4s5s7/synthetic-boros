from packages import risk
from packages.exchange.fake import FakeExchange
from packages.execution import close_all, enter
from packages.market import build_snapshot
from packages.strategy import Params, evaluate

P = Params(capital_usd=500)


def best(ex):
    snap = build_snapshot(ex, ex, now=ex.now())
    d = evaluate(snap, P, lambda p: risk.size_contracts(P.capital_usd, snap, p, P))
    assert d.action == "enter", d.reason
    return snap, d.best


def legs(ex):
    return {l.symbol: l.size for l in ex.account().legs if l.size}


def test_enter_success_locks_fixed_from_real_fills():
    ex = FakeExchange()
    snap, c = best(ex)
    r = enter(ex, c, P, "t1", ex.now(), perp=snap.perp)
    assert r.ok, r.reason
    n = c.contracts
    assert legs(ex) == {c.pair.put.symbol: -n, c.pair.call.symbol: n, "BTCUSD": -n}
    assert ex.leverage[84] == 5.0                       # never Delta's 10x/200x default
    rec = r.record
    assert rec.forward == rec.strike + rec.entry_call - rec.entry_put
    assert 3 < rec.fixed_apr < 6 and rec.entry_fees_usd > 0
    order = [o["symbol"] for o in ex.order_log]
    assert order == [c.pair.put.symbol, c.pair.call.symbol, "BTCUSD"]   # put first


def test_failed_call_leg_unwinds_put():
    ex = FakeExchange()
    snap, c = best(ex)
    ex.fail_next(c.pair.call.symbol)
    r = enter(ex, c, P, "t2", ex.now(), perp=snap.perp)
    assert not r.ok and "call" in r.reason and "no open exposure" in r.reason
    assert legs(ex) == {}
    assert r.unwound and all(f.filled for f in r.unwound)


def test_failed_perp_leg_unwinds_both_options():
    ex = FakeExchange()
    snap, c = best(ex)
    ex.fail_next("BTCUSD")
    r = enter(ex, c, P, "t3", ex.now(), perp=snap.perp)
    assert not r.ok and legs(ex) == {}


def test_stale_quote_inside_tolerance_still_fills():
    ex = FakeExchange()
    snap, c = best(ex)
    ex.stale_next(c.pair.put.symbol, -0.002)           # bid drops 0.2% between book read and order
    r = enter(ex, c, P, "t4", ex.now(), perp=snap.perp)
    assert r.ok, r.reason


def test_stale_quote_beyond_tolerance_aborts_cleanly():
    ex = FakeExchange()
    snap, c = best(ex)
    ex.stale_next(c.pair.call.symbol, 0.03)            # ask jumps 3%: more than the 0.5% tolerance
    r = enter(ex, c, P, "t5", ex.now(), perp=snap.perp)
    assert not r.ok and legs(ex) == {}


def test_leverage_failure_aborts_before_any_order():
    ex = FakeExchange()
    snap, c = best(ex)
    ex.fail_leverage = True
    r = enter(ex, c, P, "t6", ex.now(), perp=snap.perp)
    assert not r.ok and "leverage" in r.reason and ex.order_log == []


def test_close_all_flattens_and_idempotent_coids():
    ex = FakeExchange()
    snap, c = best(ex)
    r = enter(ex, c, P, "t7", ex.now(), perp=snap.perp)
    r.record.id = 1
    close_all(ex, r.record, "test")
    assert legs(ex) == {}
    assert len({o["coid"] for o in ex.order_log}) == len(ex.order_log)
    assert all(len(o["coid"]) <= 32 for o in ex.order_log)
