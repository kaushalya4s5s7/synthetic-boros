"""packages/market: snapshot assembly with in-memory stub clients (no network)."""
import pytest

from packages.domain.types import Book, Fees, Instrument, Level
from packages.market import build_snapshot, candidate_pairs

NOW = 1792900000.0
DAY = 86400.0
FEES = Fees(0.0001, 0.0001, 0.035)



@pytest.fixture(autouse=True)
def _fresh_history_cache():
    import packages.market as market
    market._LAST_HISTORY.clear()          # the last-good-history fallback must not leak between tests
    yield

def opt(kind, strike, days, pid):
    return Instrument(symbol=f"{kind[0].upper()}-BTC-{int(strike)}-{int(days)}", product_id=pid, kind=kind,
                      contract_value=0.001, tick=0.1, fees=FEES, strike=strike, expiry_ts=NOW + days * DAY)


PERP = Instrument("BTCUSD", 84, "perp", 0.001, 0.5, Fees(0.0002, 0.0005))


class Stub:
    def __init__(self, env, options=(), funding_rate=0.01, history=(), empty=()):
        self.env, self.options, self.funding_rate, self.history, self.empty = env, list(options), funding_rate, list(history), set(empty)
        self.books_fetched = []

    def instruments(self, underlying="BTC"):
        return PERP, self.options

    def ticker(self, symbol):
        return {"mark_price": "80000", "spot_price": "80010", "funding_rate": str(self.funding_rate)}

    def book(self, symbol, depth=20):
        self.books_fetched.append(symbol)
        if symbol in self.empty:
            return Book(symbol, (), (Level(10, 1),), NOW)
        return Book(symbol, (Level(100, 5),), (Level(105, 5),), NOW)

    def funding_history(self, symbol, hours):
        return self.history


def test_tenor_and_strike_filters():
    opts = [opt("call", 80000, 30, 1), opt("put", 80000, 30, 2),       # in
            opt("call", 80000, 5, 3), opt("put", 80000, 5, 4),         # too short
            opt("call", 120000, 30, 5), opt("put", 120000, 30, 6),     # outside strike band
            opt("call", 82000, 30, 7)]                                 # no matching put
    pairs = candidate_pairs(opts, 80000, NOW, 14, 60, 0.15)
    assert [(c.product_id, p.product_id) for c, p in pairs] == [(1, 2)]


def test_book_cap_nearest_strikes_first():
    opts = [opt(k, s, 30, i) for i, (k, s) in enumerate((k, s) for s in range(70000, 90001, 1000) for k in ("call", "put"))]
    pairs = candidate_pairs(opts, 80000, NOW, 14, 60, 0.15, max_books=6)
    assert len(pairs) == 3 and pairs[0][0].strike == 80000


def test_snapshot_uses_funding_source_and_drops_empty_books():
    opts = [opt("call", 80000, 30, 1), opt("put", 80000, 30, 2), opt("call", 81000, 30, 3), opt("put", 81000, 30, 4)]
    trade = Stub("testnet", opts, funding_rate=0.01, empty={"P-BTC-81000-30"})
    hist = [(NOW - h * 3600, 5.0) for h in range(30, 0, -1)]
    mainnet = Stub("mainnet", funding_rate=0.005, history=hist)
    s = build_snapshot(trade, mainnet, now=NOW)
    assert s.env == "testnet" and s.funding.source == "mainnet"
    assert s.funding.now_apr == pytest.approx(5.475) and s.funding.avg24_apr == pytest.approx(5.0)
    assert s.spot == 80010 and s.perp_mark == 80000 and s.put_margin_pct == 0.05
    assert len(s.pairs) == 1 and s.pairs[0].strike == 80000
    assert s.funding.next_payment_ts > NOW and s.funding.next_payment_ts % 28800 == 0


def test_avg24_falls_back_to_now_without_history():
    s = build_snapshot(Stub("testnet"), Stub("mainnet", funding_rate=-0.002), now=NOW)
    assert s.funding.avg24_apr == s.funding.now_apr == pytest.approx(-2.19)
