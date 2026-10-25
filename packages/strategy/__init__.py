"""Synthetic Boros strategy: pure functions, no I/O.

Long YU on Delta = sell put + buy call (same strike/expiry, a synthetic dated future) + short BTCUSD perp.
We pay the fixed rate implied by the options and receive floating funding on the perp.

Everything is priced the way mainnet would charge it:
  - execution prices are a depth walk of the L2 book (VWAP), never the ticker
  - fees come from the instrument (maker/taker, option premium cap) times GST (Fees.tax_mult)
  - a trade is refused when costs would eat more than `max_fee_ratio` of the expected funding edge
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Callable, Literal

from packages.domain.types import (
    Account, Book, FundingView, Instrument, MarketSnapshot, OptionPair, PositionRecord,
)

FUNDING_CLAMP_APR = 54.75          # Delta clamps funding at 0.05%/8h
DAY = 86400.0

State = Literal["FLAT", "EARNING", "BELOW_FIXED", "FLIPPED", "EXITING", "HEDGE_BROKEN", "EXPIRING"]
Action = Literal["none", "hold", "exit", "unwind", "close_perp"]


@dataclass(frozen=True)
class Params:
    min_days: float = 21.0
    max_days: float = 45.0
    leverage: float = 5.0
    reserve_pct: float = 0.10
    min_net_edge_pct: float = 0.15     # net edge after all costs, % of notional, over the hold
    max_fee_ratio: float = 0.5         # costs must stay <= 50% of the expected gross funding edge
    max_spread_cost_pct: float = 0.12  # half-spread on both options, % of notional
    max_book_share: float = 0.2        # never take more than 20% of visible depth within tolerance
    price_tolerance: float = 0.005
    exit_avg24_apr: float = 0.0
    exit_now_apr: float = -25.0
    hard_stop_pct: float = -0.5
    no_early_exit_days: float = 3.0
    capital_usd: float = 100.0
    auto_enter: bool = False
    underlyings: str = "XAUT,BTC"      # scanned in this order; XAUT = tokenised gold (RWA)
    stable_min_days: float = 2.0       # shortest expiry allowed when funding is provably flat
    stable_stdev_apr: float = 0.25     # "flat" = hourly funding stdev over 7 days at or below this

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        known = {f.name for f in fields(cls)}
        base = cls()
        clean = {}
        for k, v in (d or {}).items():
            if k not in known:
                continue
            default = getattr(base, k)
            if isinstance(default, bool):
                clean[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, str):
                clean[k] = ",".join(x.strip().upper() for x in str(v).split(",") if x.strip()) or default
            else:
                clean[k] = float(v)
        return replace(base, **clean)


@dataclass(frozen=True)
class Cost:
    """USD for the planned size. slippage_entry is informational: it is already inside the VWAP
    fill prices, and so inside fixed_apr. `total` counts only what still comes off the gross edge."""
    fees_entry: float
    fees_exit: float
    slippage_entry: float
    slippage_exit: float

    @property
    def fees(self) -> float:
        return self.fees_entry + self.fees_exit

    @property
    def slippage(self) -> float:
        return self.slippage_entry + self.slippage_exit

    @property
    def total(self) -> float:
        return self.fees + self.slippage_exit


@dataclass(frozen=True)
class Candidate:
    pair: OptionPair
    days: float
    contracts: int
    put_px: float | None
    call_px: float | None
    perp_px: float | None
    forward: float | None
    fixed_apr: float | None
    forecast_apr: float
    notional_usd: float
    capital_usd: float
    gross_edge_usd: float
    cost: Cost
    net_edge_usd: float
    net_edge_pct: float
    fee_ratio: float
    ok: bool
    reasons: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.underlying}-{int(self.pair.expiry_ts)}-{int(self.pair.strike)}"

    @property
    def underlying(self) -> str:
        return self.pair.call.symbol.split("-")[1]          # C-XAUT-3990-301026 -> XAUT


@dataclass(frozen=True)
class Decision:
    action: Literal["enter", "skip"]
    reason: str
    best: Candidate | None
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class Status:
    state: State
    action: Action
    reason: str
    metrics: dict


# ---------------------------------------------------------------- pricing helpers

def depth_fill(book: Book, side: str, contracts: int) -> tuple[float | None, int]:
    """VWAP for `contracts` walking the book: buy walks asks, sell walks bids.
    Returns (vwap of what can fill, contracts fillable). vwap is None when nothing fills."""
    levels = book.asks if side == "buy" else book.bids
    left, cost, got = contracts, 0.0, 0
    for lvl in levels:
        if left <= 0:
            break
        take = min(left, lvl.size)
        cost += take * lvl.price
        got += take
        left -= take
    return (cost / got if got else None), got


def forecast_apr(f: FundingView) -> float:
    """Floating-rate forecast: the trailing 24h mean (backtest winner), clamped to Delta's limits."""
    return max(-FUNDING_CLAMP_APR, min(FUNDING_CLAMP_APR, f.avg24_apr))


def fee_usd(inst: Instrument, price: float, contracts: int, maker: bool = False, spot: float | None = None) -> float:
    """Fee in USD incl. GST. Perp: rate x notional at `price`. Option: rate x notional at spot,
    capped at premium_cap x premium (`price` is the option premium)."""
    rate = inst.fees.maker if maker else inst.fees.taker
    qty = abs(contracts) * inst.contract_value
    if inst.kind == "perp":
        fee = rate * qty * price
    else:
        underlying = spot if spot is not None else (inst.strike or price)
        fee = rate * qty * underlying
        if inst.fees.premium_cap is not None:
            fee = min(fee, inst.fees.premium_cap * price * qty)
    return fee * inst.fees.tax_mult


def _mid(book: Book) -> float | None:
    if book.best_bid is None or book.best_ask is None:
        return None
    return (book.best_bid + book.best_ask) / 2


def capital_per_contract(snap: MarketSnapshot, call_px: float, put_px: float, params: Params) -> float:
    """USD capital one contract of the full position ties up, including the reserve.
    perp margin + short-put margin (premium + put_margin_pct x spot) + call premium - put premium received."""
    cv = snap.perp.contract_value
    perp_margin = snap.perp_mark * cv / params.leverage
    put_margin = put_px * cv + snap.put_margin_pct * snap.spot * cv
    net_premium = call_px * cv - put_px * cv
    return (perp_margin + put_margin + net_premium) * (1 + params.reserve_pct)


def days_to(expiry_ts: float, now: float) -> float:
    return (expiry_ts - now) / DAY


# ---------------------------------------------------------------- entry decision

def _price_pair(snap: MarketSnapshot, pair: OptionPair, n: int, params: Params, forecast: float) -> Candidate:
    days = days_to(pair.expiry_ts, snap.ts)
    reasons: list[str] = []
    cv = snap.perp.contract_value
    empty = Cost(0.0, 0.0, 0.0, 0.0)

    if n < 1:
        return Candidate(pair, days, 0, None, None, None, None, None, forecast, 0.0, 0.0, 0.0, empty,
                         0.0, 0.0, 0.0, False, ("Too small for your capital: not even 1 contract fits.",))

    put_px, put_n = depth_fill(pair.put_book, "sell", n)
    call_px, call_n = depth_fill(pair.call_book, "buy", n)
    perp_px, perp_n = depth_fill(snap.perp_book, "sell", n)
    thin = [name for name, got in (("put", put_n), ("call", call_n), ("perp", perp_n)) if got < n]
    if thin:
        return Candidate(pair, days, n, put_px, call_px, perp_px, None, None, forecast, 0.0, 0.0, 0.0, empty,
                         0.0, 0.0, 0.0, False,
                         (f"Book too thin for {n} contracts on the {', '.join(thin)} leg.",))

    forward = pair.strike + call_px - put_px
    fixed = (forward / perp_px - 1) * 365 / days * 100
    notional = n * cv * perp_px
    gross = (forecast - fixed) / 100 * days / 365 * notional

    fees_entry = (fee_usd(pair.put, put_px, n, spot=snap.spot) + fee_usd(pair.call, call_px, n, spot=snap.spot)
                  + fee_usd(snap.perp, perp_px, n))
    # exit: perp closes at expiry (taker); options settle, charged at taker too (conservative)
    fees_exit = (fee_usd(snap.perp, perp_px, n) + fee_usd(pair.put, put_px, n, spot=snap.spot)
                 + fee_usd(pair.call, call_px, n, spot=snap.spot))
    slip_entry = 0.0
    for book, px, sign in ((pair.put_book, put_px, -1), (pair.call_book, call_px, 1), (snap.perp_book, perp_px, -1)):
        m = _mid(book)
        if m is not None:
            slip_entry += max(0.0, sign * (px - m)) * n * cv
    perp_half_spread = ((snap.perp_book.best_ask - snap.perp_book.best_bid) / 2
                        if snap.perp_book.best_ask and snap.perp_book.best_bid else 0.0)
    slip_exit = perp_half_spread * n * cv
    cost = Cost(fees_entry, fees_exit, slip_entry, slip_exit)

    net = gross - cost.total
    net_pct = net / notional * 100 if notional else 0.0
    fee_ratio = cost.total / gross if gross > 0 else float("inf")

    spread_pct = 0.0
    if None not in (pair.call_book.best_ask, pair.call_book.best_bid, pair.put_book.best_ask, pair.put_book.best_bid):
        spread_pct = ((pair.call_book.best_ask - pair.call_book.best_bid)
                      + (pair.put_book.best_ask - pair.put_book.best_bid)) / 2 / perp_px * 100

    if snap.funding.now_apr < 0:
        reasons.append(f"Funding is negative right now ({snap.funding.now_apr:.2f}%/yr): we would be paying, not earning.")
    if gross <= 0:
        reasons.append(f"Fixed rate {fixed:.2f}% is above the funding forecast {forecast:.2f}%: no edge to capture.")
    elif fee_ratio > params.max_fee_ratio:
        reasons.append(f"Fees would eat {fee_ratio:.0%} of the funding edge (limit {params.max_fee_ratio:.0%}): "
                       f"${cost.total:.2f} cost vs ${gross:.2f} expected.")
    if spread_pct > params.max_spread_cost_pct:
        reasons.append(f"Option spreads cost {spread_pct:.3f}% of notional (limit {params.max_spread_cost_pct:.2f}%).")
    if net_pct < params.min_net_edge_pct:
        reasons.append(f"Net edge {net_pct:.3f}% after all costs is below the {params.min_net_edge_pct:.2f}% minimum.")

    capital = capital_per_contract(snap, call_px, put_px, params) * n
    return Candidate(pair, days, n, put_px, call_px, perp_px, forward, fixed, forecast, notional, capital, gross,
                     cost, net, net_pct, fee_ratio, not reasons, tuple(reasons))


def funding_stable(f: FundingView, params: Params) -> bool:
    """Flat funding (RWA perps on Delta sat at one value for 90 days) removes the flip risk that makes
    short holds lose on BTC, so a shorter expiry may be priced. Needs >= 3 days of hourly history."""
    vals = [apr for _, apr in f.history]
    if len(vals) < 72:
        return False
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 <= params.stable_stdev_apr


def min_days_for(f: FundingView, params: Params) -> float:
    return min(params.min_days, params.stable_min_days) if funding_stable(f, params) else params.min_days


def evaluate(snap: MarketSnapshot, params: Params, contracts_for: Callable[[OptionPair], int]) -> Decision:
    """Price every pair in the tenor window and pick the best. Never enters unless the best passes every guard."""
    forecast = forecast_apr(snap.funding)
    lo = min_days_for(snap.funding, params)
    cands = [_price_pair(snap, p, contracts_for(p), params, forecast)
             for p in snap.pairs if lo <= days_to(p.expiry_ts, snap.ts) <= params.max_days]
    cands.sort(key=lambda c: (not c.ok, -c.net_edge_usd))
    if not cands:
        return Decision("skip", f"No option expiry between {lo:g} and {params.max_days:g} days "
                                f"with quotes on both the call and the put.", None, ())
    best = cands[0]
    if best.ok:
        return Decision("enter", f"Lock {best.fixed_apr:.2f}% fixed for {best.days:.0f} days vs funding forecast "
                                 f"{best.forecast_apr:.2f}%: net +${best.net_edge_usd:.2f} "
                                 f"({best.net_edge_pct:.2f}%) after ${best.cost.total:.2f} costs.", best, tuple(cands))
    return Decision("skip", "No trade: " + best.reasons[0], best, tuple(cands))


# ---------------------------------------------------------------- position status

def _leg_size(account: Account, symbol: str) -> int:
    return sum(l.size for l in account.legs if l.symbol == symbol)


def status(pos: PositionRecord | None, account: Account, snap: MarketSnapshot, params: Params,
           funding_usd: float, now: float) -> Status:
    """Status model from RISK_AND_DASHBOARD.md §4. pnl is a carry approximation:
    funding received - entry fees - fixed rate accrued so far."""
    f = snap.funding
    if pos is None:
        return Status("FLAT", "none", "No open position.", {"floating_apr": f.now_apr, "spread_apr": 0.0,
                      "days_left": 0.0, "pnl_usd": 0.0, "pnl_pct": 0.0, "projected_usd": 0.0,
                      "worst_case_usd": 0.0, "funding_usd": 0.0})

    cv = snap.perp.contract_value
    notional = pos.contracts * cv * pos.entry_perp
    elapsed = max(0.0, (now - pos.opened_ts) / DAY)
    days_left = days_to(pos.expiry_ts, now)
    carry = pos.fixed_apr / 100 * elapsed / 365 * notional
    pnl = funding_usd - pos.entry_fees_usd - carry
    pnl_pct = pnl / notional * 100 if notional else 0.0
    projected = pnl + (forecast_apr(f) - pos.fixed_apr) / 100 * max(days_left, 0.0) / 365 * notional
    m = {"floating_apr": f.now_apr, "spread_apr": f.now_apr - pos.fixed_apr, "days_left": days_left,
         "pnl_usd": pnl, "pnl_pct": pnl_pct, "projected_usd": projected,
         "worst_case_usd": params.hard_stop_pct / 100 * notional, "funding_usd": funding_usd}

    want = {pos.perp_symbol: -pos.contracts, pos.call_symbol: pos.contracts, pos.put_symbol: -pos.contracts}
    bad = [f"{s} is {_leg_size(account, s):+d} (expected {w:+d})" for s, w in want.items() if _leg_size(account, s) != w]
    if bad:
        return Status("HEDGE_BROKEN", "unwind", "Hedge broken: " + "; ".join(bad) + ". Unwinding the stray legs.", m)

    if days_left <= 1:
        return Status("EXPIRING", "close_perp", f"Options settle in {max(days_left, 0) * 24:.0f}h (12:00 UTC); "
                                                f"the perp closes at the same time so nothing is left unhedged.", m)

    if pnl_pct <= params.hard_stop_pct:
        return Status("EXITING", "exit", f"Hard stop: loss {pnl_pct:.2f}% reached the {params.hard_stop_pct:.2f}% "
                                         f"limit (${pnl:.2f}). Closing all three legs.", m)
    if days_left >= params.no_early_exit_days:
        if f.avg24_apr < params.exit_avg24_apr:
            return Status("EXITING", "exit", f"Funding averaged {f.avg24_apr:.2f}%/yr over 24h, below the "
                                             f"{params.exit_avg24_apr:.2f}% exit line. Closing all three legs.", m)
        if f.now_apr <= params.exit_now_apr:
            return Status("EXITING", "exit", f"Funding crashed to {f.now_apr:.2f}%/yr (emergency line "
                                             f"{params.exit_now_apr:.2f}%). Closing all three legs.", m)
    late = days_left < params.no_early_exit_days
    if f.now_apr < 0:
        tail = (f" Only {days_left:.1f} days left: holding to expiry is cheaper than paying exit fees."
                if late else " Most flips end within 8h; the bot exits if the 24h average drops below "
                             f"{params.exit_avg24_apr:.2f}%.")
        return Status("FLIPPED", "hold", f"Funding turned negative ({f.now_apr:.2f}%/yr), 24h average still "
                                         f"{f.avg24_apr:.2f}%." + tail, m)
    if f.avg24_apr < pos.fixed_apr:
        loss_day = (pos.fixed_apr - f.avg24_apr) / 100 / 365 * notional
        return Status("BELOW_FIXED", "hold", f"Funding {f.avg24_apr:.2f}% is below your locked {pos.fixed_apr:.2f}%: "
                                             f"about -${loss_day:.3f}/day. Holding: an early exit costs more.", m)
    return Status("EARNING", "hold", f"Earning {f.now_apr:.2f}% floating vs your locked {pos.fixed_apr:.2f}%: "
                                     f"{f.now_apr - pos.fixed_apr:+.2f}%/yr.", m)
