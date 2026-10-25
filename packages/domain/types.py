"""Domain types shared by every package. Data only: no I/O, no logic beyond trivial properties.

Units: prices in USD, sizes in contracts (1 contract = contract_value BTC, 0.001 for BTCUSD and BTC options),
rates as APR in percent (10.95 means 10.95%/yr), money in USD, time as unix seconds (float).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["perp", "call", "put"]
Side = Literal["buy", "sell"]
Env = Literal["testnet", "mainnet"]


@dataclass(frozen=True)
class Level:
    price: float
    size: int


@dataclass(frozen=True)
class Book:
    symbol: str
    bids: tuple[Level, ...]           # best first (descending price)
    asks: tuple[Level, ...]           # best first (ascending price)
    ts: float

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None


@dataclass(frozen=True)
class Fees:
    maker: float                      # fraction of notional, e.g. 0.0002
    taker: float
    premium_cap: float | None = None  # options: fee <= premium_cap x premium (Delta premium_commission_rate)
    tax_mult: float = 1.18            # India GST on fees; conservative so the fee guard never under-counts


@dataclass(frozen=True)
class Instrument:
    symbol: str
    product_id: int
    kind: Kind
    contract_value: float
    tick: float
    fees: Fees
    strike: float | None = None
    expiry_ts: float | None = None    # options settle 12:00 UTC on expiry day


@dataclass(frozen=True)
class FundingView:
    now_apr: float                    # current 8h rate annualised (rate% x 3 x 365)
    avg24_apr: float                  # trailing 24h mean of hourly readings
    source: Env                       # where the funding numbers came from (mainnet = real market)
    history: tuple[tuple[float, float], ...] = ()   # (ts, apr), hourly, oldest first, up to 7 days
    next_payment_ts: float | None = None


@dataclass(frozen=True)
class OptionPair:
    expiry_ts: float
    strike: float
    call: Instrument
    put: Instrument
    call_book: Book
    put_book: Book


@dataclass(frozen=True)
class MarketSnapshot:
    ts: float
    env: Env                          # where we TRADE
    spot: float
    perp: Instrument
    perp_mark: float
    perp_book: Book
    funding: FundingView
    pairs: tuple[OptionPair, ...]
    put_margin_pct: float = 0.05      # short put margin = premium + this x spot (measured on testnet 25 Oct 2026)


@dataclass(frozen=True)
class LegPosition:
    symbol: str
    product_id: int
    size: int                         # signed contracts: short = negative
    entry_price: float
    margin: float
    liquidation_price: float | None = None
    mark: float | None = None


@dataclass(frozen=True)
class Account:
    equity: float
    available: float
    position_margin: float
    order_margin: float
    margin_mode: str
    legs: tuple[LegPosition, ...] = ()


@dataclass(frozen=True)
class Fill:
    order_id: int | None
    product_id: int
    side: Side
    requested: int
    filled: int
    avg_price: float | None
    fee: float                        # USD actually paid
    reason: str | None = None         # cancellation_reason when not fully filled
    client_order_id: str | None = None


@dataclass(frozen=True)
class FundingPayment:
    ts: float
    symbol: str
    usd: float                        # + received, - paid


@dataclass
class PositionRecord:
    """One Synthetic Boros position (three legs) as the ledger stores it."""
    id: int
    state: Literal["open", "closed"]
    opened_ts: float
    expiry_ts: float
    strike: float
    contracts: int
    perp_symbol: str
    call_symbol: str
    put_symbol: str
    perp_product_id: int
    call_product_id: int
    put_product_id: int
    entry_perp: float
    entry_call: float
    entry_put: float
    forward: float                    # strike + call - put actually paid
    fixed_apr: float                  # locked fixed rate from real fills
    entry_fees_usd: float
    reserve_usd: float
    closed_ts: float | None = None
    close_reason: str | None = None
    realized_pnl_usd: float | None = None
    extra: dict = field(default_factory=dict)
