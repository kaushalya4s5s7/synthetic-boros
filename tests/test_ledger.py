import threading

from packages.domain.types import FundingPayment, PositionRecord
from packages.ledger import Ledger


def rec(**kw):
    base = dict(id=0, state="open", opened_ts=1.0, expiry_ts=100.0, strike=81000.0, contracts=2,
                perp_symbol="BTCUSD", call_symbol="C", put_symbol="P", perp_product_id=84, call_product_id=1,
                put_product_id=2, entry_perp=81000.0, entry_call=3800.0, entry_put=3700.0, forward=81100.0,
                fixed_apr=7.0, entry_fees_usd=0.1, reserve_usd=16.2, extra={"tag": "e1"})
    base.update(kw)
    return PositionRecord(**base)


def test_position_roundtrip_and_close():
    L = Ledger(":memory:", now=lambda: 50.0)
    pid = L.open_position(rec())
    cur = L.current()
    assert cur.id == pid and cur.extra == {"tag": "e1"} and cur.fixed_apr == 7.0
    L.close(pid, "test", -1.5)
    assert L.current() is None
    closed = L.get(pid)
    assert closed.state == "closed" and closed.realized_pnl_usd == -1.5 and closed.closed_ts == 50.0


def test_funding_is_idempotent():
    L = Ledger(":memory:")
    pid = L.open_position(rec())
    p = FundingPayment(ts=10.0, symbol="BTCUSD", usd=0.02)
    assert L.add_funding(pid, p) is True
    assert L.add_funding(pid, p) is False       # same payment seen again on the next poll
    L.add_funding(pid, FundingPayment(ts=20.0, symbol="BTCUSD", usd=-0.01))
    assert abs(L.funding_total(pid) - 0.01) < 1e-12


def test_events_kv_and_levels():
    L = Ledger(":memory:")
    L.event("warn", "margin", "low", {"h": 40})
    assert L.events(5)[0]["data"] == {"h": 40}
    L.kv_set("mode", "running")
    assert L.kv_get("mode") == "running" and L.kv_get("missing", 3) == 3
    try:
        L.event("loud", "x", "y")
        raise AssertionError("bad level accepted")
    except ValueError:
        pass


def test_thread_safe_writes(tmp_path):
    L = Ledger(str(tmp_path / "l.sqlite"))
    ts = [threading.Thread(target=lambda: [L.event("info", "t", "m") for _ in range(50)]) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(L.events(500)) == 200
