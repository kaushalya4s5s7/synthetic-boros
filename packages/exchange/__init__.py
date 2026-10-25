"""Exchange access: the only package that talks to Delta Exchange.

Public API: ExchangeClient (protocol), DeltaClient, ExchangeError, from_env, FakeExchange (tests/demo).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from packages.domain.types import (Account, Book, Env, Fees, Fill, FundingPayment, Instrument, LegPosition,
                                   Level, Side)
from packages.exchange._rest import BASES, ExchangeError, Rest

PERP_SYMBOL = "BTCUSD"


def perp_symbol(underlying: str) -> str:
    return f"{underlying}USD"
APR_PER_8H_PCT = 3 * 365          # % per 8h -> % per year


@runtime_checkable
class ExchangeClient(Protocol):
    env: Env

    def instruments(self, underlying: str = "BTC") -> tuple[Instrument, list[Instrument]]: ...
    def book(self, symbol: str, depth: int = 20) -> Book: ...
    def ticker(self, symbol: str) -> dict: ...
    def funding_history(self, symbol: str, hours: int) -> list[tuple[float, float]]: ...
    def account(self) -> Account: ...
    def set_leverage(self, product_id: int, leverage: float) -> None: ...
    def place_ioc(self, product_id: int, side: Side, size: int, limit_price: float,
                  client_order_id: str, reduce_only: bool = False) -> Fill: ...
    def change_margin(self, product_id: int, delta_usd: float) -> None: ...
    def funding_payments(self, since_ts: float) -> list[FundingPayment]: ...
    def heartbeat(self, hb_id: str, ttl_ms: int = 60000) -> None: ...


def _f(x, default: float | None = None) -> float | None:
    try:
        return float(x) if x is not None and x != "" else default
    except (TypeError, ValueError):
        return default


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def parse_instrument(p: dict) -> Instrument | None:
    ct = p.get("contract_type")
    kind = {"perpetual_futures": "perp", "call_options": "call", "put_options": "put"}.get(ct)
    if kind is None:
        return None
    specs = p.get("product_specs") or {}
    fees = Fees(maker=_f(p.get("maker_commission_rate"), 0.0), taker=_f(p.get("taker_commission_rate"), 0.0),
                premium_cap=_f(specs.get("premium_commission_rate")) if kind != "perp" else None)
    expiry = _ts(p.get("settlement_time")) if kind != "perp" else None
    if kind != "perp" and expiry is None:          # fall back to the symbol: C-BTC-81000-271126 settles 12:00 UTC
        expiry = datetime.strptime(p["symbol"].split("-")[-1], "%d%m%y").replace(
            hour=12, tzinfo=timezone.utc).timestamp()
    return Instrument(symbol=p["symbol"], product_id=int(p["id"]), kind=kind,
                      contract_value=_f(p.get("contract_value"), 0.001), tick=_f(p.get("tick_size"), 0.5),
                      fees=fees, strike=_f(p.get("strike_price")) if kind != "perp" else None, expiry_ts=expiry)


def parse_book(symbol: str, r: dict, depth: int = 20) -> Book:
    def levels(rows):
        return tuple(Level(float(x["price"]), int(x["size"])) for x in (rows or [])[:depth] if int(x["size"]) > 0)
    bids = tuple(sorted(levels(r.get("buy")), key=lambda l: -l.price))
    asks = tuple(sorted(levels(r.get("sell")), key=lambda l: l.price))
    ts = _f(r.get("last_updated_at"))
    return Book(symbol=symbol, bids=bids, asks=asks, ts=ts / 1e6 if ts and ts > 1e12 else time.time())


def parse_fill(o: dict, side: Side, size: int, product_id: int, client_order_id: str | None) -> Fill:
    unfilled = int(o.get("unfilled_size") or 0)
    return Fill(order_id=o.get("id"), product_id=int(o.get("product_id") or product_id), side=side,
                requested=int(o.get("size") or size), filled=int(o.get("size") or size) - unfilled,
                avg_price=_f(o.get("average_fill_price")), fee=_f(o.get("paid_commission"), 0.0),
                reason=o.get("cancellation_reason"), client_order_id=o.get("client_order_id") or client_order_id)


class DeltaClient:
    """Delta Exchange India REST client. key=None gives a read-only client (e.g. mainnet funding source)."""

    def __init__(self, env: Env = "testnet", key: str | None = None, secret: str | None = None,
                 base: str | None = None):
        if env not in BASES:
            raise ValueError(f"env must be testnet or mainnet, got {env!r}")
        self.env = env
        self.rest = Rest(base or BASES[env], key, secret)
        self._hb_created: set[str] = set()
        self._products: dict[str, Instrument] = {}

    def _get(self, path, query=None, auth=False):
        return self.rest.request("GET", path, query, auth=auth)

    def _post(self, path, body):
        return self.rest.request("POST", path, body=body, auth=True)

    # ---- public market data ----
    def instruments(self, underlying: str = "BTC") -> tuple[Instrument, list[Instrument]]:
        perp = parse_instrument(self._get(f"/v2/products/{perp_symbol(underlying)}"))
        rows = self._get("/v2/products", {"contract_types": "call_options,put_options", "states": "live",
                                          "underlying_asset_symbols": underlying, "page_size": 1000})
        opts = [i for i in (parse_instrument(p) for p in rows) if i is not None and f"-{underlying}-" in i.symbol]
        self._products.update({i.symbol: i for i in [perp, *opts]})
        return perp, opts

    def book(self, symbol: str, depth: int = 20) -> Book:
        return parse_book(symbol, self._get(f"/v2/l2orderbook/{symbol}", {"depth": depth}), depth)

    def ticker(self, symbol: str) -> dict:
        return self._get(f"/v2/tickers/{symbol}")

    def option_tickers(self) -> list[dict]:
        return self._get("/v2/tickers", {"contract_types": "call_options,put_options",
                                         "underlying_asset_symbols": "BTC"})

    def funding_history(self, symbol: str, hours: int) -> list[tuple[float, float]]:
        end = int(time.time())
        start = end - hours * 3600
        rows = self._get("/v2/history/candles", {"resolution": "1h", "symbol": f"FUNDING:{symbol}",
                                                 "start": start, "end": end}) or []
        pts = sorted((float(r["time"]), float(r["close"]) * APR_PER_8H_PCT) for r in rows if r.get("close") is not None)
        return pts

    # ---- private ----
    def account(self) -> Account:
        bal = self._get("/v2/wallet/balances", auth=True)
        usd = next((b for b in bal if b.get("asset_symbol") in ("USD", "USDT")), {})
        pos = self._get("/v2/positions/margined", auth=True) or []
        try:
            mode = self._get("/v2/profile", auth=True).get("margin_mode") or "isolated"
        except ExchangeError:
            mode = "unknown"
        legs = tuple(LegPosition(symbol=p.get("product_symbol") or "", product_id=int(p.get("product_id") or 0),
                                 size=int(p.get("size") or 0), entry_price=_f(p.get("entry_price"), 0.0),
                                 margin=_f(p.get("margin"), 0.0), liquidation_price=_f(p.get("liquidation_price")),
                                 mark=_f(p.get("mark_price")))
                     for p in pos if int(p.get("size") or 0) != 0)
        return Account(equity=_f(usd.get("balance"), 0.0), available=_f(usd.get("available_balance"), 0.0),
                       position_margin=_f(usd.get("position_margin"), 0.0),
                       order_margin=_f(usd.get("order_margin"), 0.0), margin_mode=mode, legs=legs)

    def set_leverage(self, product_id: int, leverage: float) -> None:
        self._post(f"/v2/products/{product_id}/orders/leverage", {"leverage": str(leverage)})

    def place_ioc(self, product_id: int, side: Side, size: int, limit_price: float,
                  client_order_id: str, reduce_only: bool = False) -> Fill:
        o = self._post("/v2/orders", {"product_id": product_id, "side": side, "size": int(size),
                                      "order_type": "limit_order", "limit_price": str(limit_price),
                                      "time_in_force": "ioc", "reduce_only": reduce_only,
                                      "client_order_id": client_order_id})
        return parse_fill(o, side, size, product_id, client_order_id)

    def change_margin(self, product_id: int, delta_usd: float) -> None:
        self._post("/v2/positions/change_margin", {"product_id": product_id, "delta_margin": f"{delta_usd:.6f}"})

    def funding_payments(self, since_ts: float) -> list[FundingPayment]:
        rows = self._get("/v2/wallet/transactions", {"transaction_types": "funding", "page_size": 100},
                         auth=True) or []
        out = []
        for r in rows:
            ts = _ts(r.get("created_at")) or 0.0
            if ts < since_ts:
                continue
            sym = (r.get("meta_data") or {}).get("product_symbol") or r.get("product_symbol") or PERP_SYMBOL
            out.append(FundingPayment(ts=ts, symbol=sym, usd=_f(r.get("amount"), 0.0)))
        return sorted(out, key=lambda p: p.ts)

    def heartbeat(self, hb_id: str, ttl_ms: int = 60000) -> None:
        if hb_id not in self._hb_created:
            self._post("/v2/heartbeat/create", {
                "heartbeat_id": hb_id, "default_ttl": ttl_ms, "impact": "contracts",
                "contract_types": ["perpetual_futures", "call_options", "put_options"],
                "product_symbols": [PERP_SYMBOL],
                "config": [{"action": "cancel_orders", "unhealthy_count": 1}]})
            self._hb_created.add(hb_id)
        self._post("/v2/heartbeat", {"heartbeat_id": hb_id, "ttl": ttl_ms})

    # ---- extras used by apps/verify ----
    def cancel(self, order_id: int, product_id: int) -> None:
        self.rest.request("DELETE", "/v2/orders", body={"id": order_id, "product_id": product_id}, auth=True)

    def place_limit(self, product_id: int, side: Side, size: int, limit_price: float, post_only: bool = True) -> dict:
        return self._post("/v2/orders", {"product_id": product_id, "side": side, "size": int(size),
                                         "order_type": "limit_order", "limit_price": str(limit_price),
                                         "post_only": post_only})

    def profile(self) -> dict:
        return self._get("/v2/profile", auth=True)


def from_env() -> tuple[DeltaClient, DeltaClient]:
    """(trading client, funding-source client) built from packages/exchange/keys.py."""
    from packages.exchange.keys import load_keys
    k = load_keys()
    trade = DeltaClient(k.trading_env, k.key, k.secret)
    funding = trade if k.funding_source == k.trading_env else DeltaClient(k.funding_source, None, None)
    return trade, funding


try:
    from packages.exchange.fake import FakeExchange  # noqa: F401  (written by the bot agent)
except ImportError:  # pragma: no cover
    pass

__all__ = ["perp_symbol", "ExchangeClient", "DeltaClient", "ExchangeError", "from_env", "parse_book", "parse_instrument",
           "parse_fill", "PERP_SYMBOL", "APR_PER_8H_PCT"]
