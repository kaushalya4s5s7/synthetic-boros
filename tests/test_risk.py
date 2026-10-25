import pytest

from packages.domain.types import Account, LegPosition
from packages.risk import (
    RiskAction, margin_health, plan, preflight, size_contracts,
)
from packages.strategy import Params
from tests.test_strategy import NOW, make_pair, make_pos, make_snap


def test_size_for_100_usd():
    snap = make_snap()
    n = size_contracts(100, snap, snap.pairs[0], Params())
    # per contract: 80592*0.001/5 + 3.695 + 0.05*80.6 + 3.8 - 3.695 = 23.95, x1.1 reserve = 26.35
    assert n == 3
    assert 2 <= n <= 4


def test_size_zero_when_capital_too_small():
    snap = make_snap()
    assert size_contracts(10, snap, snap.pairs[0], Params()) == 0


def test_size_capped_by_book_share():
    pair = make_pair(put_bids=((3695, 10),))
    snap = make_snap([pair])
    assert size_contracts(10_000, snap, pair, Params()) == 2      # 20% of 10 contracts


def test_size_ignores_depth_outside_tolerance():
    pair = make_pair(put_bids=((3695, 5), (3000, 100_000)))
    snap = make_snap([pair])
    assert size_contracts(10_000, snap, pair, Params()) == 1


def account(perp_mark=80590.5, perp_margin=16.12, put_margin=7.73, put_mark=3695.0, perp_liq=96500.0):
    return Account(100, 60, perp_margin + put_margin, 0, "isolated", (
        LegPosition("BTCUSD", 84, -1, 80590.5, perp_margin, perp_liq, perp_mark),
        LegPosition("C-BTC-81000", 197111, 1, 3800, 0.0, None, 3800),
        LegPosition("P-BTC-81000", 197105, -1, 3695, put_margin, 7322.7, put_mark),
    ))


def test_health_at_entry_is_about_100():
    h = margin_health(account(), make_pos(contracts=1), Params(), make_snap())
    assert [x.symbol for x in h] == ["BTCUSD", "P-BTC-81000"]
    assert h[0].health_pct == pytest.approx(16.12 / (80.5905 / 5) * 100)
    assert h[1].health_pct == pytest.approx(7.73 / (3.695 + 0.05 * 80.6) * 100)
    assert h[0].distance_pct == pytest.approx((96500 / 80590.5 - 1) * 100)


def test_no_actions_when_healthy():
    assert plan(account(), make_pos(contracts=1), make_snap(), Params(), 8.0) == []


def test_rally_triggers_topup_from_reserve():
    acc = account(perp_mark=90000)
    h = margin_health(acc, make_pos(contracts=1), Params(), make_snap())[0]
    assert h.health_pct < 50
    acts = plan(acc, make_pos(contracts=1), make_snap(), Params(), 20.0)
    assert len(acts) == 1 and acts[0].kind == "topup" and acts[0].product_id == 84
    equity = 16.12 + (80590.5 - 90000) * 0.001
    assert acts[0].amount == pytest.approx(90 / 5 - equity)
    assert "adding $" in acts[0].reason


def test_crash_tops_up_put():
    acc = account(put_mark=12000, perp_mark=70000)
    acts = plan(acc, make_pos(contracts=1), make_snap(), Params(), 50.0)
    assert [a.product_id for a in acts] == [197105]


def test_reserve_too_small_deleverages_half():
    acts = plan(account(perp_mark=90000), make_pos(contracts=1), make_snap(), Params(), 1.0)
    assert acts == [RiskAction("deleverage", None, 0.5, acts[0].reason)]
    assert "hedge stays intact" in acts[0].reason


def test_no_position_no_plan():
    assert plan(account(), None, make_snap(), Params(), 10) == []
    assert margin_health(account(), None, Params()) == []


def test_preflight():
    assert preflight(make_snap(), NOW + 5, Params()) == []
    stale = preflight(make_snap(), NOW + 120, Params())
    assert len(stale) == 1 and "old" in stale[0]
    empty = preflight(make_snap(pairs=[], perp_bids=()), NOW, Params())
    assert any("perp order book" in x for x in empty) and any("No option" in x for x in empty)
