"""Risk: sizing, margin health, top-up / deleverage plan, preflight. Pure functions, no I/O.

Margin model (isolated, measured on Delta testnet 25 Oct 2026):
  short perp   needs notional / leverage; equity = posted margin + unrealised P&L
  short put    needs premium + put_margin_pct x spot per contract (= 5% measured)
  long call    needs nothing beyond its premium (paid up front)
health = equity / needed x 100. Top up below 50%, back to 100%. If the reserve can't cover every
top-up, close half of all three legs instead (the hedge stays intact).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from packages.domain.types import Account, Book, LegPosition, MarketSnapshot, OptionPair, PositionRecord
from packages.strategy import Params, capital_per_contract

TOPUP_BELOW_PCT = 50.0
WARN_BELOW_PCT = 70.0        # callers raise a warning alert below this
DELEVER_FRACTION = 0.5
MAX_DATA_AGE_S = 30.0


@dataclass(frozen=True)
class LegHealth:
    symbol: str
    health_pct: float
    liq_price: float | None
    distance_pct: float | None    # how far the mark is from liquidation, %


@dataclass(frozen=True)
class RiskAction:
    kind: Literal["topup", "deleverage", "freeze"]
    product_id: int | None
    amount: float                 # topup: USD; deleverage: fraction of the position to close
    reason: str


def _depth_within(book: Book, side: str, tolerance: float) -> int:
    """Contracts available within `tolerance` of the best price on the side we would hit."""
    levels = book.bids if side == "sell" else book.asks
    if not levels:
        return 0
    best = levels[0].price
    lim = best * (1 - tolerance) if side == "sell" else best * (1 + tolerance)
    return sum(l.size for l in levels if (l.price >= lim if side == "sell" else l.price <= lim))


def size_contracts(capital_usd: float, snap: MarketSnapshot, pair: OptionPair, params: Params) -> int:
    """Contracts the capital affords (margin + net premium + reserve), capped at max_book_share
    of the depth within price_tolerance on each of the three books."""
    put_px, call_px = pair.put_book.best_bid, pair.call_book.best_ask
    if put_px is None or call_px is None or capital_usd <= 0:
        return 0
    per = capital_per_contract(snap, call_px, put_px, params)
    by_capital = int(capital_usd // per) if per > 0 else 0
    depth = min(_depth_within(pair.put_book, "sell", params.price_tolerance),
                _depth_within(pair.call_book, "buy", params.price_tolerance),
                _depth_within(snap.perp_book, "sell", params.price_tolerance))
    by_book = int(depth * params.max_book_share)
    return max(0, min(by_capital, by_book))


def _leg(account: Account, symbol: str) -> LegPosition | None:
    return next((l for l in account.legs if l.symbol == symbol), None)


def _distance(liq: float | None, mark: float | None) -> float | None:
    if not liq or not mark:
        return None
    return abs(liq / mark - 1) * 100


def _needs(account: Account, pos: PositionRecord, params: Params, spot: float | None, cv: float,
           put_margin_pct: float):
    """[(leg, equity, needed)] for the perp and put legs."""
    out = []
    perp = _leg(account, pos.perp_symbol)
    if perp and perp.size:
        mark = perp.mark or perp.entry_price
        qty = abs(perp.size) * cv
        upnl = (perp.entry_price - mark) * qty if perp.size < 0 else (mark - perp.entry_price) * qty
        out.append((perp, perp.margin + upnl, qty * mark / params.leverage))
        spot = spot or mark
    put = _leg(account, pos.put_symbol)
    if put and put.size:
        prem = put.mark if put.mark is not None else put.entry_price
        out.append((put, put.margin, abs(put.size) * cv * (prem + put_margin_pct * (spot or pos.entry_perp))))
    return out


def margin_health(account: Account, pos: PositionRecord | None, params: Params,
                  snap: MarketSnapshot | None = None) -> list[LegHealth]:
    if pos is None:
        return []
    cv = snap.perp.contract_value if snap else 0.001
    out = []
    pct = snap.put_margin_pct if snap else 0.05
    for leg, equity, needed in _needs(account, pos, params, snap.spot if snap else None, cv, pct):
        health = equity / needed * 100 if needed > 0 else 100.0
        out.append(LegHealth(leg.symbol, health, leg.liquidation_price, _distance(leg.liquidation_price, leg.mark)))
    return out


def plan(account: Account, pos: PositionRecord | None, snap: MarketSnapshot, params: Params,
         reserve_usd: float) -> list[RiskAction]:
    """Top up any leg under 50% health back to 100% from the reserve; if the reserve can't
    cover every top-up, deleverage half of all legs instead."""
    if pos is None:
        return []
    cv = snap.perp.contract_value
    topups = []
    for leg, equity, needed in _needs(account, pos, params, snap.spot, cv, snap.put_margin_pct):
        health = equity / needed * 100 if needed > 0 else 100.0
        if health < TOPUP_BELOW_PCT:
            topups.append((leg, needed - equity, health))
    if not topups:
        return []
    total = sum(amt for _, amt, _ in topups)
    if total > reserve_usd:
        return [RiskAction("deleverage", None, DELEVER_FRACTION,
                           f"Reserve ${reserve_usd:.2f} can't cover ${total:.2f} of top-ups: closing "
                           f"{DELEVER_FRACTION:.0%} of all three legs. The hedge stays intact.")]
    return [RiskAction("topup", leg.product_id, amt,
                       f"{leg.symbol} margin health {h:.0f}% (top-up below {TOPUP_BELOW_PCT:.0f}%): "
                       f"adding ${amt:.2f} from the reserve.")
            for leg, amt, h in topups]


def preflight(snap: MarketSnapshot, now: float, params: Params) -> list[str]:
    """Blocking problems: if any, the bot must not trade this tick."""
    issues = []
    age = now - snap.ts
    if age > MAX_DATA_AGE_S:
        issues.append(f"Market data is {age:.0f}s old (limit {MAX_DATA_AGE_S:.0f}s): frozen until fresh data arrives.")
    if not snap.perp_book.bids or not snap.perp_book.asks:
        issues.append("BTCUSD perp order book is empty.")
    if not snap.pairs:
        issues.append("No option call/put pairs with live quotes.")
    if snap.spot <= 0 or snap.perp_mark <= 0:
        issues.append("Spot or mark price missing.")
    return issues
