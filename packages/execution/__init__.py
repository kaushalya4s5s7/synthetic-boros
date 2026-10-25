"""Order execution for the three-leg position.

Leg order is put -> call -> perp: the short put is the leg the market is least willing to take,
so if it fails nothing else is open yet. Each leg is priced from a fresh L2 book (the ticker goes
stale within a second, verified live on testnet), with a tolerance, as an IOC. Any partial or
failed leg unwinds every filled leg reduce-only, so the account is never left unhedged.
"""
from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field

from packages.domain.types import Fill, Instrument, PositionRecord
from packages.exchange import ExchangeClient, ExchangeError
from packages.strategy import Candidate, Params

UNWIND_TOLERANCES = (0.01, 0.02, 0.05)     # widen on each retry: getting flat beats a better price


@dataclass
class EntryResult:
    ok: bool
    fills: dict = field(default_factory=dict)      # leg -> Fill
    unwound: list = field(default_factory=list)    # Fills from unwinding
    reason: str = ""
    record: PositionRecord | None = None


def _limit(ex: ExchangeClient, inst: Instrument, side: str, tol: float) -> float | None:
    book = ex.book(inst.symbol)                    # fresh book, never the ticker snapshot
    top = book.best_bid if side == "sell" else book.best_ask
    if top is None:
        return None
    raw = top * (1 - tol) if side == "sell" else top * (1 + tol)
    steps = math.floor(raw / inst.tick) if side == "sell" else math.ceil(raw / inst.tick)
    return round(max(steps, 1) * inst.tick, 8)


def _coid(tag: str, leg: str, attempt: int) -> str:
    return f"{tag}-{leg}-{attempt}"[:32]


def _ioc(ex: ExchangeClient, inst: Instrument, side: str, size: int, tol: float, coid: str,
         reduce_only: bool = False) -> Fill:
    px = _limit(ex, inst, side, tol)
    if px is None:
        return Fill(None, inst.product_id, side, size, 0, None, 0.0, "empty_book", coid)
    return ex.place_ioc(inst.product_id, side, size, px, coid, reduce_only=reduce_only)


def _flatten(ex: ExchangeClient, inst: Instrument, side: str, size: int, tag: str, leg: str) -> list[Fill]:
    """Close `size` contracts reduce-only, widening the price on each attempt."""
    fills, left = [], size
    for attempt, tol in enumerate(UNWIND_TOLERANCES):
        if left <= 0:
            break
        f = _ioc(ex, inst, side, left, tol, _coid(tag, leg, attempt), reduce_only=True)
        fills.append(f)
        left -= f.filled
    return fills


def enter(ex: ExchangeClient, cand: Candidate, params: Params, tag: str, now: float,
          perp: Instrument | None = None) -> EntryResult:
    perp = perp or ex.instruments()[0]
    n = cand.contracts
    if n <= 0:
        return EntryResult(False, reason="Size is 0 contracts: not enough capital or book depth.")
    try:
        ex.set_leverage(perp.product_id, params.leverage)
    except ExchangeError as e:
        return EntryResult(False, reason=f"Could not set perp leverage to {params.leverage:g}x ({e.code}); "
                                         f"entry aborted so Delta's default leverage is never used.")
    legs = [("put", cand.pair.put, "sell"), ("call", cand.pair.call, "buy"), ("perp", perp, "sell")]
    fills: dict[str, Fill] = {}
    tol = params.price_tolerance
    for leg, inst, side in legs:
        try:
            f = _ioc(ex, inst, side, n, tol, _coid(tag, leg, 0))
        except ExchangeError as e:
            f = Fill(None, inst.product_id, side, n, 0, None, 0.0, e.code, _coid(tag, leg, 0))
        fills[leg] = f
        if f.filled != n:
            unwound = []
            for done_leg, done_inst, done_side in legs:
                df = fills.get(done_leg)
                if df and df.filled:
                    back = "buy" if done_side == "sell" else "sell"
                    unwound += _flatten(ex, done_inst, back, df.filled, tag + "u", done_leg)
            got = f"{f.filled}/{n}"
            return EntryResult(False, fills, unwound,
                               f"The {leg} leg filled {got} ({f.reason or 'no liquidity'}). "
                               f"Every filled leg was closed again, so there is no open exposure.")
    put_px, call_px, perp_px = (fills[k].avg_price for k in ("put", "call", "perp"))
    forward = cand.pair.strike + call_px - put_px
    days = max((cand.pair.expiry_ts - now) / 86400, 1e-9)
    fixed_apr = (forward / perp_px - 1) * 365 / days * 100
    notional = n * perp.contract_value * perp_px
    rec = PositionRecord(
        id=0, state="open", opened_ts=now, expiry_ts=cand.pair.expiry_ts, strike=cand.pair.strike, contracts=n,
        perp_symbol=perp.symbol, call_symbol=cand.pair.call.symbol, put_symbol=cand.pair.put.symbol,
        perp_product_id=perp.product_id, call_product_id=cand.pair.call.product_id,
        put_product_id=cand.pair.put.product_id, entry_perp=perp_px, entry_call=call_px, entry_put=put_px,
        forward=forward, fixed_apr=fixed_apr, entry_fees_usd=sum(f.fee for f in fills.values()),
        reserve_usd=params.reserve_pct * notional,
        extra={"expected_fixed_apr": cand.fixed_apr, "expected_net_edge_usd": cand.net_edge_usd,
               "fee_ratio": cand.fee_ratio, "tag": tag})
    return EntryResult(True, fills, [], f"Locked {fixed_apr:.2f}% fixed for {days:.0f} days.", rec)


def _legs(pos: PositionRecord) -> list[tuple[str, Instrument]]:
    # Instruments rebuilt from the record; tick only matters for rounding limits, 0.5 is valid on both envs
    from packages.domain.types import Fees
    mk = lambda sym, pid, kind: Instrument(sym, pid, kind, 0.001, 0.5, Fees(0.0, 0.0))  # noqa: E731
    return [("put", mk(pos.put_symbol, pos.put_product_id, "put")),
            ("call", mk(pos.call_symbol, pos.call_product_id, "call")),
            ("perp", mk(pos.perp_symbol, pos.perp_product_id, "perp"))]


def close_all(ex: ExchangeClient, pos: PositionRecord, reason: str, tag: str | None = None,
              fraction: float = 1.0) -> list[Fill]:
    """Reduce-only close of every leg the account actually holds, in put -> call -> perp order."""
    tag = tag or f"x{pos.id}{secrets.token_hex(3)}"
    held = {l.symbol: l.size for l in ex.account().legs}
    fills = []
    for leg, inst in _legs(pos):
        size = held.get(inst.symbol, 0)
        if not size:
            continue
        qty = abs(size) if fraction >= 1 else max(1, int(abs(size) * fraction))
        fills += _flatten(ex, inst, "buy" if size < 0 else "sell", qty, tag, leg)
    return fills


def topup(ex: ExchangeClient, product_id: int, usd: float) -> None:
    ex.change_margin(product_id, round(usd, 2))


def deleverage(ex: ExchangeClient, pos: PositionRecord, fraction: float, tag: str | None = None) -> list[Fill]:
    return close_all(ex, pos, "deleverage", tag or f"d{pos.id}{secrets.token_hex(3)}", fraction=fraction)


__all__ = ["EntryResult", "enter", "close_all", "topup", "deleverage"]
