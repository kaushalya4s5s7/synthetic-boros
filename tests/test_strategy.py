"""Strategy tests. Numbers match the live testnet fill of 25 Oct 2026 (27 Nov 81000, 33 days)."""

import pytest

from packages.domain.types import (
    Account, Book, Fees, FundingView, Instrument, LegPosition, Level, MarketSnapshot, OptionPair, PositionRecord,
)
from packages.strategy import (
    DAY, Cost, Params, depth_fill, evaluate, fee_usd, forecast_apr, status,
)

NOW = 1_792_900_000.0
PERP_FEES = Fees(maker=0.0002, taker=0.0005)
OPT_FEES = Fees(maker=0.0001, taker=0.0001, premium_cap=0.1)


def book(sym, bids, asks, ts=NOW):
    return Book(sym, tuple(Level(p, s) for p, s in bids), tuple(Level(p, s) for p, s in asks), ts)


PERP = Instrument("BTCUSD", 84, "perp", 0.001, 0.1, PERP_FEES)


def make_pair(days=33.0, strike=81000.0, put_bids=((3695, 838), (3664, 1195)), put_asks=((3790, 486),),
              call_bids=((3705, 837),), call_asks=((3800, 513), (3831, 692)), now=NOW):
    exp = now + days * DAY
    call = Instrument(f"C-BTC-{int(strike)}", 197111, "call", 0.001, 0.1, OPT_FEES, strike, exp)
    put = Instrument(f"P-BTC-{int(strike)}", 197105, "put", 0.001, 0.1, OPT_FEES, strike, exp)
    return OptionPair(exp, strike, call, put, book(call.symbol, call_bids, call_asks), book(put.symbol, put_bids, put_asks))


def make_snap(pairs=None, now_apr=10.95, avg24=10.95, ts=NOW, perp_bids=((80590.5, 3380), (80590.0, 4299)),
              perp_asks=((80594.5, 1267),)):
    return MarketSnapshot(
        ts=ts, env="testnet", spot=80600.0, perp=PERP, perp_mark=80592.0,
        perp_book=book("BTCUSD", perp_bids, perp_asks, ts),
        funding=FundingView(now_apr, avg24, "mainnet"),
        pairs=tuple(pairs if pairs is not None else [make_pair()]))


def three(_pair):
    return 3


# ---------------------------------------------------------------- pricing

def test_depth_fill_walks_levels():
    b = book("X", [(100, 2), (99, 3)], [(101, 1), (102, 4)])
    assert depth_fill(b, "buy", 3) == (pytest.approx((101 + 2 * 102) / 3), 3)
    assert depth_fill(b, "sell", 4) == (pytest.approx((2 * 100 + 2 * 99) / 4), 4)
    vwap, got = depth_fill(b, "sell", 10)
    assert got == 5 and vwap == pytest.approx((200 + 297) / 5)
    assert depth_fill(book("X", [], []), "buy", 1) == (None, 0)


def test_fee_perp_includes_gst():
    assert fee_usd(PERP, 80590.5, 1) == pytest.approx(0.0005 * 0.001 * 80590.5 * 1.18)
    assert fee_usd(PERP, 80590.5, 1, maker=True) == pytest.approx(0.0002 * 0.001 * 80590.5 * 1.18)


def test_option_fee_premium_cap_applies_for_cheap_options():
    opt = make_pair().put
    # normal premium: rate x spot notional is the smaller one
    assert fee_usd(opt, 3695, 1, spot=80600) == pytest.approx(0.0001 * 0.001 * 80600 * 1.18)
    # cheap far-OTM option: capped at 10% of premium
    assert fee_usd(opt, 50, 1, spot=80600) == pytest.approx(0.1 * 50 * 0.001 * 1.18)


def test_forecast_is_24h_mean_clamped():
    assert forecast_apr(FundingView(5, 10.95, "mainnet")) == 10.95
    assert forecast_apr(FundingView(0, 300, "mainnet")) == 54.75
    assert forecast_apr(FundingView(0, -300, "mainnet")) == -54.75


# ---------------------------------------------------------------- entry decision

def test_realistic_testnet_case_enters():
    d = evaluate(make_snap(), Params(), three)
    assert d.action == "enter", d.reason
    c = d.best
    assert c.contracts == 3 and c.ok and c.reasons == ()
    assert c.forward == 81000 + 3800 - 3695
    assert c.fixed_apr == pytest.approx((81105 / 80590.5 - 1) * 365 / 33 * 100)
    assert 6.9 < c.fixed_apr < 7.2
    assert c.fee_ratio < 0.5
    assert c.net_edge_pct >= 0.15
    assert c.net_edge_usd == pytest.approx(c.gross_edge_usd - c.cost.total)
    assert c.id == f"BTC-{int(NOW + 33 * DAY)}-81000"
    assert "Lock" in d.reason


def test_lower_forecast_rejected_by_fee_guard():
    d = evaluate(make_snap(now_apr=8, avg24=8), Params(), three)
    assert d.action == "skip"
    joined = " ".join(d.best.reasons)
    assert "Fees would eat" in joined and "Net edge" in joined
    assert d.reason.startswith("No trade:")


def test_fixed_above_forecast_rejected():
    d = evaluate(make_snap(now_apr=5, avg24=5), Params(), three)
    assert any("above the funding forecast" in r for r in d.best.reasons)


def test_thin_book_rejected():
    pair = make_pair(put_bids=((3695, 1),))
    d = evaluate(make_snap([pair]), Params(), three)
    assert d.action == "skip"
    assert "Book too thin for 3 contracts on the put leg" in d.best.reasons[0]


def test_negative_funding_now_blocks_entry():
    d = evaluate(make_snap(now_apr=-2, avg24=10.95), Params(), three)
    assert d.action == "skip"
    assert any("negative right now" in r for r in d.best.reasons)


def test_wide_spread_rejected():
    pair = make_pair(put_asks=((3900, 486),), call_bids=((3600, 837),))
    d = evaluate(make_snap([pair]), Params(), three)
    assert any("Option spreads cost" in r for r in d.best.reasons)


def test_zero_contracts_rejected():
    d = evaluate(make_snap(), Params(), lambda p: 0)
    assert d.action == "skip" and "Too small" in d.best.reasons[0]


def test_tenor_window_and_sorting():
    short = make_pair(days=7)
    good = make_pair(days=33)
    worse = make_pair(days=40, strike=82000, call_asks=((3500, 500),), put_bids=((3300, 800),),
                      call_bids=((3420, 500),), put_asks=((3390, 500),))
    d = evaluate(make_snap([short, worse, good]), Params(), three)
    assert all(21 <= c.days <= 45 for c in d.candidates)
    assert len(d.candidates) == 2
    oks = [c for c in d.candidates if c.ok]
    assert d.candidates[0].ok or not oks
    if len(oks) == 2:
        assert oks[0].net_edge_usd >= oks[1].net_edge_usd


def test_no_pairs_in_window():
    d = evaluate(make_snap([make_pair(days=7)]), Params(), three)
    assert d.action == "skip" and d.best is None and "No option expiry" in d.reason


def test_cost_properties():
    c = Cost(1.0, 2.0, 0.5, 0.25)
    assert c.fees == 3.0 and c.slippage == 0.75 and c.total == 3.25


def test_params_roundtrip_and_parsing():
    p = Params.from_dict({"leverage": "3", "auto_enter": "true", "bogus": 1, "min_days": 14})
    assert p.leverage == 3.0 and p.auto_enter is True and p.min_days == 14.0
    assert Params.from_dict(p.to_dict()) == p
    assert Params.from_dict({"auto_enter": "false"}).auto_enter is False


# ---------------------------------------------------------------- status

def make_pos(days_open=2.0, days_left=31.0, contracts=3, fixed=7.06, now=NOW):
    return PositionRecord(
        id=1, state="open", opened_ts=now - days_open * DAY, expiry_ts=now + days_left * DAY, strike=81000,
        contracts=contracts, perp_symbol="BTCUSD", call_symbol="C-BTC-81000", put_symbol="P-BTC-81000",
        perp_product_id=84, call_product_id=197111, put_product_id=197105, entry_perp=80590.5, entry_call=3800,
        entry_put=3695, forward=81105, fixed_apr=fixed, entry_fees_usd=0.2, reserve_usd=8.0)


def make_account(perp=-3, call=3, put=-3):
    legs = tuple(LegPosition(s, pid, n, 1.0, 1.0) for s, pid, n in
                 (("BTCUSD", 84, perp), ("C-BTC-81000", 197111, call), ("P-BTC-81000", 197105, put)) if n)
    return Account(100, 80, 15, 0, "isolated", legs)


def st(pos=None, now_apr=10.95, avg24=10.95, funding_usd=0.05, account=None):
    return status(pos, account or make_account(), make_snap(now_apr=now_apr, avg24=avg24), Params(), funding_usd, NOW)


def test_flat():
    s = st()
    assert s.state == "FLAT" and s.action == "none"


def test_earning():
    s = st(make_pos())
    assert s.state == "EARNING" and s.action == "hold"
    m = s.metrics
    notional = 3 * 0.001 * 80590.5
    assert m["pnl_usd"] == pytest.approx(0.05 - 0.2 - 7.06 / 100 * 2 / 365 * notional)
    assert m["spread_apr"] == pytest.approx(10.95 - 7.06)
    assert m["worst_case_usd"] == pytest.approx(-0.5 / 100 * notional)
    assert m["days_left"] == pytest.approx(31)
    assert set(m) == {"floating_apr", "spread_apr", "days_left", "pnl_usd", "pnl_pct", "projected_usd",
                      "worst_case_usd", "funding_usd"}


def test_below_fixed_holds():
    s = st(make_pos(), now_apr=5, avg24=5)
    assert s.state == "BELOW_FIXED" and s.action == "hold" and "/day" in s.reason


def test_flipped_holds():
    s = st(make_pos(), now_apr=-3, avg24=4)
    assert s.state == "FLIPPED" and s.action == "hold" and "8h" in s.reason


def test_exit_on_negative_24h_average():
    s = st(make_pos(), now_apr=-3, avg24=-0.5)
    assert s.state == "EXITING" and s.action == "exit" and "24h" in s.reason


def test_exit_on_crash():
    s = st(make_pos(), now_apr=-30, avg24=2)
    assert s.state == "EXITING" and "crashed" in s.reason


def test_hard_stop():
    s = st(make_pos(), funding_usd=-2.0)
    assert s.state == "EXITING" and "Hard stop" in s.reason


def test_no_early_exit_window_holds_instead_of_exiting():
    s = st(make_pos(days_left=2.0), now_apr=-5, avg24=-1)
    assert s.state == "FLIPPED" and s.action == "hold" and "days left" in s.reason


def test_hard_stop_still_fires_in_late_window():
    s = st(make_pos(days_left=2.0), now_apr=-5, avg24=-1, funding_usd=-2.0)
    assert s.state == "EXITING"


def test_expiring():
    s = st(make_pos(days_left=0.5))
    assert s.state == "EXPIRING" and s.action == "close_perp"


def test_hedge_broken_missing_leg():
    s = st(make_pos(), account=make_account(put=0))
    assert s.state == "HEDGE_BROKEN" and s.action == "unwind" and "P-BTC-81000" in s.reason


def test_hedge_broken_wrong_size_takes_priority():
    s = st(make_pos(days_left=0.5), account=make_account(perp=-2))
    assert s.state == "HEDGE_BROKEN"


def test_flat_funding_allows_short_expiries_volatile_does_not():
    from packages.domain.types import FundingView
    from packages.strategy import Params, funding_stable, min_days_for
    p = Params()
    flat = FundingView(5.48, 5.48, "mainnet", tuple((i * 3600.0, 5.48) for i in range(168)))
    wobbly = FundingView(5.0, 5.0, "mainnet", tuple((i * 3600.0, 10.95 if i % 10 else -3.0) for i in range(168)))
    short = FundingView(5.48, 5.48, "mainnet", tuple((i * 3600.0, 5.48) for i in range(10)))
    assert funding_stable(flat, p) and min_days_for(flat, p) == p.stable_min_days
    assert not funding_stable(wobbly, p) and min_days_for(wobbly, p) == p.min_days
    assert not funding_stable(short, p)          # too little history to call it flat


def test_params_underlyings_normalised():
    from packages.strategy import Params
    assert Params.from_dict({"underlyings": " xaut , btc "}).underlyings == "XAUT,BTC"
    assert Params.from_dict({"underlyings": ""}).underlyings == "XAUT,BTC"
