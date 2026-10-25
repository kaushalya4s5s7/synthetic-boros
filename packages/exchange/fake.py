"""In-memory exchange that behaves like Delta testnet: L2 books with depth, IOC fills that walk levels,
isolated margin per leg, funding accrual, scripted failures and stale quotes. Used by tests and DEMO_MODE.

Prices come from a Black-76 model on a forward that embeds `carry_apr` (the fixed rate the options imply),
so the synthetic forward K + C - P equals spot x (1 + carry x T) at mid, like the real chain.
"""
from __future__ import annotations

import math
import time as _time
from datetime import datetime, timedelta, timezone

from packages.domain.types import (Account, Book, Fees, Fill, FundingPayment, Instrument, LegPosition, Level)
from packages.exchange._rest import ExchangeError

PERP_FEES = Fees(maker=0.0002, taker=0.0005)
OPT_FEES = Fees(maker=0.0001, taker=0.0001, premium_cap=0.1)
PERP_MM = 0.0025


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black76(f: float, k: float, t: float, vol: float, kind: str) -> float:
    if t <= 0:
        return max(f - k, 0.0) if kind == "call" else max(k - f, 0.0)
    d1 = (math.log(f / k) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if kind == "call":
        return f * _ncdf(d1) - k * _ncdf(d2)
    return k * _ncdf(-d2) - f * _ncdf(-d1)


class FakeExchange:
    env = "testnet"

    def __init__(self, spot: float = 81000.0, carry_apr: float = 4.0, vol: float = 0.45, funding_apr: float = 10.95,
                 expiry_days=(27, 34, 41), strike_step: float = 1000.0, strikes_each_side: int = 5,
                 spread_frac: float = 0.006, level_sizes=(600, 750, 1100, 1900), balance: float = 1000.0,
                 start_ts: float | None = None):
        self.t = start_ts if start_ts is not None else _time.time()
        self.spot, self.carry_apr, self.vol, self.spread_frac = spot, carry_apr, vol, spread_frac
        self.level_sizes = level_sizes
        self.cash = balance
        self.perp = Instrument("BTCUSD", 84, "perp", 0.001, 0.5, PERP_FEES)
        self.options: list[Instrument] = []
        pid = 200000
        day0 = datetime.fromtimestamp(self.t, timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        centre = round(spot / strike_step) * strike_step
        for d in expiry_days:
            exp = (day0 + timedelta(days=d)).timestamp()
            tag = datetime.fromtimestamp(exp, timezone.utc).strftime("%d%m%y")
            for i in range(-strikes_each_side, strikes_each_side + 1):
                k = centre + i * strike_step
                for kind, c in (("call", "C"), ("put", "P")):
                    pid += 1
                    self.options.append(Instrument(f"{c}-BTC-{int(k)}-{tag}", pid, kind, 0.001, 0.5, OPT_FEES,
                                                   strike=k, expiry_ts=exp))
        self._by_symbol = {i.symbol: i for i in [self.perp, *self.options]}
        self._by_id = {i.product_id: i for i in [self.perp, *self.options]}
        self.positions: dict[int, dict] = {}       # product_id -> {size, entry, margin}
        self.leverage: dict[int, float] = {self.perp.product_id: 10.0}   # Delta's default
        self.consumed: dict[tuple[str, float], int] = {}
        self.offsets: dict[str, float] = {}        # per-symbol price shift (stale-quote simulation)
        self._pending_move: dict[str, float] = {}
        self._fail_next: dict[str, str] = {}
        self.fail_leverage = False
        self.fail_account = False
        self.orders: dict[str, Fill] = {}
        self.order_log: list[dict] = []
        self.funding_log: list[tuple[float, float]] = [(self.t - 8 * 86400, funding_apr)]
        self.payments: list[FundingPayment] = []
        self.heartbeats: list[tuple[str, int, float]] = []
        self.put_margin_pct = 0.05

    # ---------- clock & scripting ----------
    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def set_spot(self, spot: float) -> None:
        self.spot = spot
        self.consumed.clear()

    def set_funding_apr(self, apr: float) -> None:
        self.funding_log.append((self.t, apr))

    @property
    def funding_apr(self) -> float:
        return self.funding_log[-1][1]

    def fail_next(self, symbol: str, reason: str = "order_size_not_available_in_orderbook") -> None:
        self._fail_next[symbol] = reason

    def stale_next(self, symbol: str, move_frac: float) -> None:
        """After the next book() on symbol, the quotes move by move_frac before any order arrives."""
        self._pending_move[symbol] = move_frac

    def accrue_funding(self, hours: float) -> None:
        """Advance the clock and pay funding hourly on the open perp position (short receives positive funding)."""
        for _ in range(int(hours)):
            self.t += 3600
            pos = self.positions.get(self.perp.product_id)
            if pos and pos["size"]:
                usd = -pos["size"] * self.perp.contract_value * self.mark("BTCUSD") * self.funding_apr / 100 / 365 / 24
                self.cash += usd
                self.payments.append(FundingPayment(ts=self.t, symbol="BTCUSD", usd=usd))

    # ---------- pricing ----------
    def mark(self, symbol: str) -> float:
        inst = self._by_symbol[symbol]
        if inst.kind == "perp":
            return self.spot
        t = max((inst.expiry_ts - self.t) / (365 * 86400), 0.0)
        fwd = self.spot * (1 + self.carry_apr / 100 * t)
        return max(black76(fwd, inst.strike, t, self.vol, inst.kind), 1.0)

    def _levels(self, symbol: str) -> tuple[list[Level], list[Level]]:
        inst = self._by_symbol[symbol]
        mid = self.mark(symbol) * (1 + self.offsets.get(symbol, 0.0))
        tick = inst.tick
        half = max(tick, mid * (0.00003 if inst.kind == "perp" else self.spread_frac / 2))
        step = max(tick, mid * (0.00001 if inst.kind == "perp" else 0.008))
        bids, asks = [], []
        for i, sz in enumerate(self.level_sizes):
            bp = math.floor((mid - half - i * step) / tick) * tick
            ap = math.ceil((mid + half + i * step) / tick) * tick
            bs = sz - self.consumed.get((symbol, bp), 0)
            as_ = sz - self.consumed.get((symbol, ap), 0)
            if bs > 0 and bp > 0:
                bids.append(Level(round(bp, 2), bs))
            if as_ > 0:
                asks.append(Level(round(ap, 2), as_))
        return bids, asks

    # ---------- ExchangeClient ----------
    def instruments(self, underlying: str = "BTC") -> tuple[Instrument, list[Instrument]]:
        if underlying != "BTC":                      # the in-memory exchange only lists BTC
            raise ExchangeError("not_found", f"no {underlying} market on the fake exchange", 404)
        return self.perp, [o for o in self.options if o.expiry_ts > self.t]

    def book(self, symbol: str, depth: int = 20) -> Book:
        if symbol not in self._by_symbol:
            raise ExchangeError("not_found", f"unknown symbol {symbol}", 404)
        bids, asks = self._levels(symbol)
        b = Book(symbol, tuple(bids[:depth]), tuple(asks[:depth]), self.t)
        if symbol in self._pending_move:
            self.offsets[symbol] = self.offsets.get(symbol, 0.0) + self._pending_move.pop(symbol)
        return b

    def ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "mark_price": str(self.mark(symbol)), "spot_price": str(self.spot),
                "funding_rate": str(self.funding_apr / 1095) if symbol == "BTCUSD" else None,
                "timestamp": int(self.t * 1e6)}

    def funding_history(self, symbol: str, hours: int) -> list[tuple[float, float]]:
        out = []
        start = math.floor(self.t / 3600) * 3600 - (hours - 1) * 3600
        for i in range(hours):
            ts = start + i * 3600
            apr = self.funding_log[0][1]
            for t0, a in self.funding_log:
                if t0 <= ts:
                    apr = a
            out.append((ts, apr))
        return out

    def account(self) -> Account:
        if self.fail_account:
            raise ExchangeError("network", "account unavailable")
        legs, upnl, opt_value, pos_margin = [], 0.0, 0.0, 0.0
        for pid, p in self.positions.items():
            if not p["size"]:
                continue
            inst = self._by_id[pid]
            m = self.mark(inst.symbol)
            liq = None
            if inst.kind == "perp":
                upnl += (m - p["entry"]) * p["size"] * inst.contract_value
                per = p["margin"] / (abs(p["size"]) * inst.contract_value)
                liq = p["entry"] + per - PERP_MM * p["entry"] if p["size"] < 0 else p["entry"] - per + PERP_MM * p["entry"]
            else:
                opt_value += m * p["size"] * inst.contract_value
                if p["size"] < 0:
                    p["margin"] = max(p["margin"], self._short_opt_margin(inst, p["size"]))
            pos_margin += p["margin"]
            legs.append(LegPosition(inst.symbol, pid, p["size"], p["entry"], round(p["margin"], 6), liq, m))
        equity = self.cash + upnl + opt_value + pos_margin
        return Account(equity=equity, available=self.cash, position_margin=pos_margin, order_margin=0.0,
                       margin_mode="isolated", legs=tuple(legs))

    def _short_opt_margin(self, inst: Instrument, size: int) -> float:
        return abs(size) * inst.contract_value * (self.mark(inst.symbol) + self.put_margin_pct * self.spot)

    def set_leverage(self, product_id: int, leverage: float) -> None:
        if self.fail_leverage:
            raise ExchangeError("leverage_rejected", "could not change order leverage", 400)
        self.leverage[product_id] = float(leverage)

    def place_ioc(self, product_id: int, side: str, size: int, limit_price: float, client_order_id: str,
                  reduce_only: bool = False) -> Fill:
        if client_order_id in self.orders:              # idempotent retry
            return self.orders[client_order_id]
        inst = self._by_id[product_id]
        sym = inst.symbol
        self.order_log.append({"symbol": sym, "side": side, "size": size, "limit": limit_price,
                               "coid": client_order_id, "reduce_only": reduce_only})
        pos = self.positions.setdefault(product_id, {"size": 0, "entry": 0.0, "margin": 0.0})
        if reduce_only:
            cur = pos["size"]
            if cur == 0 or (side == "buy") == (cur > 0):
                f = Fill(None, product_id, side, size, 0, None, 0.0, "reduce_only_no_position", client_order_id)
                self.orders[client_order_id] = f
                return f
            size = min(size, abs(cur))
        if sym in self._fail_next:
            f = Fill(None, product_id, side, size, 0, None, 0.0, self._fail_next.pop(sym), client_order_id)
            self.orders[client_order_id] = f
            return f
        bids, asks = self._levels(sym)
        levels = asks if side == "buy" else bids
        filled, cost = 0, 0.0
        for lv in levels:
            if (side == "buy" and lv.price > limit_price) or (side == "sell" and lv.price < limit_price):
                break
            take = min(lv.size, size - filled)
            self.consumed[(sym, lv.price)] = self.consumed.get((sym, lv.price), 0) + take
            filled += take
            cost += take * lv.price
            if filled == size:
                break
        if not filled:
            f = Fill(None, product_id, side, size, 0, None, 0.0, "order_size_not_available_in_orderbook", client_order_id)
            self.orders[client_order_id] = f
            return f
        avg = cost / filled
        notional = filled * inst.contract_value * (self.spot if inst.kind != "perp" else avg)
        fee = notional * inst.fees.taker
        if inst.fees.premium_cap is not None:
            fee = min(fee, inst.fees.premium_cap * avg * filled * inst.contract_value)
        fee *= inst.fees.tax_mult
        self.cash -= fee
        self._apply(inst, pos, side, filled, avg)
        f = Fill(len(self.order_log), product_id, side, size, filled, avg, fee,
                 None if filled == size else "order_size_not_available_in_orderbook", client_order_id)
        self.orders[client_order_id] = f
        return f

    def _apply(self, inst: Instrument, pos: dict, side: str, n: int, px: float) -> None:
        signed = n if side == "buy" else -n
        cv = inst.contract_value
        old = pos["size"]
        new = old + signed
        if inst.kind == "perp":
            lev = self.leverage.get(inst.product_id, 10.0)
            if old == 0 or (old > 0) == (signed > 0):          # open / add
                pos["entry"] = (pos["entry"] * abs(old) + px * n) / abs(new)
                add = n * cv * px / lev
                self.cash -= add
                pos["margin"] += add
            else:                                               # reduce
                closed = min(n, abs(old))
                pnl = (px - pos["entry"]) * closed * cv * (1 if old > 0 else -1)
                release = pos["margin"] * closed / abs(old)
                pos["margin"] -= release
                self.cash += release + pnl
                if abs(signed) > abs(old):                      # flipped through zero
                    rest = abs(signed) - abs(old)
                    pos["entry"] = px
                    add = rest * cv * px / lev
                    self.cash -= add
                    pos["margin"] = add
        else:
            self.cash -= signed * px * cv                       # buy pays premium, sell receives
            if old == 0 or (old > 0) == (signed > 0):
                pos["entry"] = (pos["entry"] * abs(old) + px * n) / abs(new)
            if new < 0:
                need = self._short_opt_margin(inst, new)
                self.cash -= need - pos["margin"]
                pos["margin"] = need
            elif pos["margin"]:
                self.cash += pos["margin"]
                pos["margin"] = 0.0
        pos["size"] = new
        if new == 0:
            pos["entry"] = 0.0
            if pos["margin"]:
                self.cash += pos["margin"]
                pos["margin"] = 0.0

    def change_margin(self, product_id: int, delta_usd: float) -> None:
        pos = self.positions.get(product_id)
        if not pos or not pos["size"]:
            raise ExchangeError("no_position", "no open position to change margin on", 400)
        if delta_usd > self.cash:
            raise ExchangeError("insufficient_margin", "not enough available balance", 400)
        pos["margin"] += delta_usd
        self.cash -= delta_usd

    def funding_payments(self, since_ts: float) -> list[FundingPayment]:
        return [p for p in self.payments if p.ts > since_ts]

    def heartbeat(self, hb_id: str, ttl_ms: int = 60000) -> None:
        self.heartbeats.append((hb_id, ttl_ms, self.t))
