"""Market snapshot: one consistent view of everything the strategy needs, built once per tick.

Orders go to `trade` (testnet). Funding for decisions comes from `funding_src` (mainnet by default),
because testnet funding is pinned at 0.01%/8h and would hide the real risk.
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from packages.domain.types import FundingView, Instrument, MarketSnapshot, OptionPair
from packages.exchange import APR_PER_8H_PCT, PERP_SYMBOL, ExchangeClient, perp_symbol

MAX_BOOKS = 40
_LAST_HISTORY: dict[tuple[str, str], list] = {}   # last good hourly funding per (env, symbol)
PUT_MARGIN_PCT = 0.05            # measured on testnet 25 Oct 2026: short put margin = premium + 5% of spot


def funding_view(src: ExchangeClient, now: float, hours: int = 168, symbol: str = PERP_SYMBOL) -> FundingView:
    t = src.ticker(symbol)
    now_apr = float(t.get("funding_rate") or 0.0) * APR_PER_8H_PCT
    hist = []
    for _ in range(2):           # one retry: a dropped history call must not flip an asset from "flat" to "unknown"
        try:
            hist = src.funding_history(symbol, hours)
            break
        except Exception:        # history is a nice-to-have; the current rate is enough to decide
            continue
    if hist:
        _LAST_HISTORY[(src.env, symbol)] = hist
    else:
        hist = _LAST_HISTORY.get((src.env, symbol), [])
    last24 = [apr for ts, apr in hist if ts >= now - 24 * 3600] or [apr for _, apr in hist[-24:]]
    avg24 = statistics.mean(last24) if last24 else now_apr
    next_pay = (int(now // 28800) + 1) * 28800.0       # Delta pays every 8h on the 00/08/16 UTC grid
    return FundingView(now_apr=now_apr, avg24_apr=avg24, source=src.env, history=tuple(hist),
                       next_payment_ts=next_pay)


def candidate_pairs(options: list[Instrument], spot: float, now: float, min_days: float, max_days: float,
                    strike_band: float, max_books: int = MAX_BOOKS) -> list[tuple[Instrument, Instrument]]:
    """(call, put) pairs in the tenor window and strike band, nearest strikes first, interleaved across expiries."""
    by = defaultdict(dict)
    for o in options:
        if o.expiry_ts is None or o.strike is None:
            continue
        days = (o.expiry_ts - now) / 86400
        if not (min_days <= days <= max_days) or abs(o.strike / spot - 1) > strike_band:
            continue
        by[(o.expiry_ts, o.strike)][o.kind] = o
    per_expiry = defaultdict(list)
    for (exp, k), legs in by.items():
        if "call" in legs and "put" in legs:
            per_expiry[exp].append((abs(k - spot), legs["call"], legs["put"]))
    queues = [sorted(v, key=lambda x: x[0]) for _, v in sorted(per_expiry.items())]
    out, i = [], 0
    while len(out) * 2 < max_books and any(i < len(q) for q in queues):
        for q in queues:
            if i < len(q) and len(out) * 2 < max_books:
                out.append((q[i][1], q[i][2]))
        i += 1
    return out


def funding_stdev(f: FundingView) -> float | None:
    """Spread of hourly funding over the history window, APR points. None if there is too little history."""
    vals = [apr for _, apr in f.history]
    return statistics.pstdev(vals) if len(vals) >= 72 else None


def build_snapshot(trade: ExchangeClient, funding_src: ExchangeClient, *, underlying: str = "BTC",
                   min_days: float = 14, max_days: float = 60, strike_band: float = 0.15,
                   stable_min_days: float | None = None, stable_stdev_apr: float = 0.25,
                   now: float | None = None) -> MarketSnapshot:
    """stable_min_days: when funding has been flat (stdev <= stable_stdev_apr over 7 days, e.g. RWA perps),
    short expiries are worth pricing too, since there is no funding-flip risk to outlast."""
    now = time.time() if now is None else now
    sym = perp_symbol(underlying)
    perp, options = trade.instruments(underlying)
    t = trade.ticker(sym)
    mark = float(t["mark_price"])
    spot = float(t.get("spot_price") or mark)
    perp_book = trade.book(sym)
    funding = funding_view(funding_src, now, symbol=sym)
    sd = funding_stdev(funding)
    if stable_min_days is not None and sd is not None and sd <= stable_stdev_apr:
        min_days = min(min_days, stable_min_days)
    legs = candidate_pairs(options, spot, now, min_days, max_days, strike_band)
    with ThreadPoolExecutor(max_workers=8) as pool:      # books are independent reads; ~10s sequential -> ~2s
        books = dict(zip([i.symbol for cp in legs for i in cp],
                         pool.map(trade.book, [i.symbol for cp in legs for i in cp])))
    pairs = []
    for call, put in legs:
        cb, pb = books[call.symbol], books[put.symbol]
        if cb.bids and cb.asks and pb.bids and pb.asks:
            pairs.append(OptionPair(expiry_ts=call.expiry_ts, strike=call.strike, call=call, put=put,
                                    call_book=cb, put_book=pb))
    return MarketSnapshot(ts=now, env=trade.env, spot=spot, perp=perp, perp_mark=mark, perp_book=perp_book,
                          funding=funding, pairs=tuple(pairs), put_margin_pct=PUT_MARGIN_PCT)


__all__ = ["build_snapshot", "funding_view", "funding_stdev", "candidate_pairs", "PUT_MARGIN_PCT"]
