"""The bot loop. One tick: snapshot -> status -> risk -> execute -> journal.

Pause stops NEW entries only. Exits, unwinds and margin top-ups always run, because they reduce risk.
Events are written on state changes, not on every tick, so the user hears about decisions, not noise.
"""
from __future__ import annotations

import secrets
import threading
import re
import time
from dataclasses import replace

from packages import execution, risk
from packages.domain.types import Account, MarketSnapshot, PositionRecord
from packages.exchange import ExchangeClient, ExchangeError
from packages.ledger import Ledger
from packages.market import build_snapshot
from packages.strategy import Candidate, Decision, Params, Status, evaluate, status

HB_ID = "synthboros"
EXPIRY_CLOSE_S = 300            # close everything in the last 5 minutes before 12:00 UTC settlement
WARN_HEALTH_PCT = 70.0
AUTH_CODES = ("ip_not_whitelisted", "unauthorized", "invalid_api_key", "no_api_key")
STATE_LEVEL = {"EARNING": "info", "BELOW_FIXED": "warn", "FLIPPED": "warn", "EXITING": "critical",
               "HEDGE_BROKEN": "critical", "EXPIRING": "info", "FLAT": "info"}


def _why(d: Decision) -> str:
    """A fixed rate of 250% is not a market view, it is an empty order book: say that instead."""
    c = d.candidates[0] if d.candidates else None
    if c is not None and c.fixed_apr is not None and abs(c.fixed_apr) > 50:
        spread = next((r for r in c.reasons if "spread" in r.lower()), None)
        return "option books too thin to price a fixed rate" + (f" ({spread.rstrip('.')})" if spread else "") + "."
    return d.reason.removeprefix("No trade: ")


def underlying_of(pos: PositionRecord) -> str:
    return pos.perp_symbol.removesuffix("USD")             # XAUTUSD -> XAUT


class Bot:
    def __init__(self, trade: ExchangeClient, funding_src: ExchangeClient, ledger: Ledger,
                 params: Params | None = None, now=time.time, demo: bool = False):
        self.trade, self.funding_src, self.ledger, self.now, self.demo = trade, funding_src, ledger, now, demo
        saved = ledger.kv_get("params")
        self.params = params or (Params.from_dict(saved) if saved else Params())
        self.mode = ledger.kv_get("mode", "paused")
        self.override = ledger.kv_get("override")           # {"apr", "avg24_apr", "set_ts"} or None
        self.lock = threading.RLock()
        self.snap: MarketSnapshot | None = None            # the asset in focus: open position's, else best offer's
        self.snaps: dict[str, MarketSnapshot] = {}           # every scanned asset this tick
        self.account: Account | None = None
        self.decision: Decision | None = None
        self.status: Status | None = None
        self.issues: list[str] = []
        self.last_tick_ts: float | None = None
        self.last_error: str | None = None
        self.key_ok: bool | None = None
        self.stale = True

    # ------------------------------------------------------------------ helpers
    def _once(self, key: str, value) -> bool:
        """True the first time `key` takes `value` (dedupes repeated alerts)."""
        if self.ledger.kv_get(key) == value:
            return False
        self.ledger.kv_set(key, value)
        return True

    def _event(self, level: str, kind: str, message: str, **data) -> None:
        self.ledger.event(level, kind, message, data)

    def _with_override(self, snap: MarketSnapshot) -> MarketSnapshot:
        ov = self.override
        if not ov:
            return snap
        f = snap.funding
        if ov.get("avg24_apr") is not None:
            avg = ov["avg24_apr"]
        else:                                           # step series: real funding before the override, override after
            hrs = min(max((self.now() - ov["set_ts"]) / 3600, 0.0), 24.0)
            avg = (f.avg24_apr * (24 - hrs) + ov["apr"] * hrs) / 24
        return replace(snap, funding=replace(f, now_apr=ov["apr"], avg24_apr=avg))

    def reserve_left(self, pos: PositionRecord) -> float:
        return max(pos.reserve_usd - pos.extra.get("reserve_used", 0.0), 0.0)

    # ------------------------------------------------------------------ tick
    def tick(self) -> None:
        with self.lock:
            now = self.now()
            pos = self.ledger.current()
            assets = list(dict.fromkeys(self.params.underlyings.split(",")))
            if pos and underlying_of(pos) not in assets:
                assets.insert(0, underlying_of(pos))          # always watch what we hold
            snaps, skipped = {}, {}
            try:
                for u in assets:
                    try:
                        snaps[u] = self._with_override(build_snapshot(
                            self.trade, self.funding_src, underlying=u, now=now,
                            stable_min_days=self.params.stable_min_days,
                            stable_stdev_apr=self.params.stable_stdev_apr))
                    except ExchangeError as e:
                        if e.code in AUTH_CODES:
                            raise
                        skipped[u] = e.code                   # e.g. no such market: skip this asset, keep the rest
                if not snaps:
                    raise ExchangeError("no_markets", "no scanned asset returned market data: "
                                        + ", ".join(f"{u} ({c})" for u, c in skipped.items()), 503)
                account = self.trade.account()
            except ExchangeError as e:
                self._fail(e.code, f"Bot can't reach Delta ({e.code}). Positions stay hedged; "
                                   f"open orders are cancelled by the heartbeat.")
                return
            except Exception as e:  # noqa: BLE001  (network stack, parsing)
                self._fail(type(e).__name__, f"Bot can't read market data ({type(e).__name__}: {e}). Frozen.")
                return
            if self.last_error:
                self._event("info", "reconnected", "Connection to Delta restored.")
            focus = underlying_of(pos) if pos and underlying_of(pos) in snaps else next(iter(snaps))
            snap = snaps[focus]
            self.snaps, self.snap, self.account = snaps, snap, account
            self.key_ok, self.last_error, self.stale = True, None, False
            self.ledger.kv_set("conn_error", None)
            # an asset with no option books (e.g. testnet gold) must not freeze the others: only the asset
            # being traded can block
            per_asset = {u: risk.preflight(sn, now, self.params) for u, sn in snaps.items()}
            self.issues = per_asset[focus] if pos else ([] if any(not v for v in per_asset.values())
                                                        else per_asset[focus])
            if self.issues and self._once("issues", self.issues):
                self._event("warn", "preflight", "Trading frozen: " + " ".join(self.issues))
            elif not self.issues:
                self.ledger.kv_set("issues", None)

            if pos:
                self._sync_funding(pos)
                self._manage(pos, snap, account, now)
            else:
                self.status = status(None, account, snap, self.params, 0.0, now)
                self.decision = self._decide(snaps)
                if self.decision.best is not None:
                    self.snap = snaps[self.decision.best.underlying]
                # key on the reason with numbers stripped: a fixed rate moving 6.93% -> 6.94% is not news
                if self._once("decision", self.decision.action + "|" + re.sub(r"[-+$\d.,%]+", "#", self.decision.reason)):
                    self._event("info", "decision", self.decision.reason)
                if (self.mode == "running" and self.params.auto_enter and self.decision.action == "enter"
                        and not self.issues):
                    self._enter(self.decision.best, now)
            try:
                if self.ledger.current():
                    self.trade.heartbeat(HB_ID)
            except ExchangeError as e:
                if self._once("hb_error", e.code):
                    self._event("warn", "heartbeat", f"Heartbeat failed ({e.code}); positions are still hedged.")
            self.last_tick_ts = now
            self.ledger.kv_set("last_tick_ts", now)

    def _decide(self, snaps: dict[str, MarketSnapshot]) -> Decision:
        """Evaluate every asset, then rank all candidates together. The fee guard decides; the asset doesn't."""
        decs = {u: evaluate(sn, self.params,
                            lambda p, sn=sn: risk.size_contracts(self.params.capital_usd, sn, p, self.params))
                for u, sn in snaps.items()}
        cands = sorted((c for d in decs.values() for c in d.candidates), key=lambda c: (not c.ok, -c.net_edge_usd))
        best = next((c for c in cands if c.ok), None)
        if best is not None:
            return Decision("enter", f"{best.underlying}: {decs[best.underlying].reason}", best, tuple(cands))
        reason = "No trade. " + " · ".join(f"{u}: {_why(d)}" for u, d in decs.items())
        return Decision("skip", reason, None, tuple(cands))

    def _fail(self, code: str, message: str) -> None:
        self.stale, self.last_error = True, message
        if code in AUTH_CODES:
            self.key_ok = False
        if self._once("conn_error", code):
            self._event("critical", "disconnected", message, code=code)

    def _sync_funding(self, pos: PositionRecord) -> None:
        try:
            pays = self.trade.funding_payments(pos.opened_ts)
        except ExchangeError:
            return
        new = [p for p in pays if p.symbol == pos.perp_symbol and self.ledger.add_funding(pos.id, p)]
        if new:
            got = sum(p.usd for p in new)
            total = self.ledger.funding_total(pos.id)
            self._event("info", "funding", f"Funding {'received' if got >= 0 else 'paid'} {got:+.4f} USD. "
                                           f"Total funding ${total:.4f}.", usd=got, total=total)

    def _manage(self, pos: PositionRecord, snap: MarketSnapshot, account: Account, now: float) -> None:
        self.decision = None
        st = status(pos, account, snap, self.params, self.ledger.funding_total(pos.id), now)
        self.status = st
        if self._once("last_state", st.state):
            self._event(STATE_LEVEL.get(st.state, "info"), "state", st.reason, state=st.state, position_id=pos.id)
        if st.action == "exit":
            self._close(pos, st.reason, st)
        elif st.action == "unwind":
            self._close(pos, st.reason, st, kind="unwound")
        elif st.action == "close_perp":
            if now >= pos.expiry_ts - EXPIRY_CLOSE_S:
                self._close(pos, "Expiry: options settled, perp closed.", st)
        else:
            self._risk(pos, snap, account)

    def _risk(self, pos: PositionRecord, snap: MarketSnapshot, account: Account) -> None:
        for h in risk.margin_health(account, pos, self.params, snap):
            low = h.health_pct < WARN_HEALTH_PCT
            if self._once(f"warn_{h.symbol}", low) and low:
                self._event("warn", "margin", f"{h.symbol} margin health {h.health_pct:.0f}%"
                            + (f", liquidation at ${h.liq_price:,.0f}" if h.liq_price else "")
                            + ". Auto top-up below 50%.", symbol=h.symbol, health=h.health_pct)
        for _ in range(3):                    # deleverage frees margin into the reserve, then top up again
            acct = self.trade.account() if _ else account
            reserve = min(self.reserve_left(pos), acct.available)
            actions = risk.plan(acct, pos, snap, self.params, reserve)
            if not actions:
                return
            delevered = False
            for a in actions:
                try:
                    if a.kind == "topup":
                        execution.topup(self.trade, a.product_id, a.amount)
                        pos.extra["reserve_used"] = pos.extra.get("reserve_used", 0.0) + a.amount
                        self.ledger.update(pos)
                        self._event("action", "topup", a.reason + f" Reserve left ${self.reserve_left(pos):.2f}.",
                                    amount=a.amount)
                    elif a.kind == "deleverage":
                        before = acct.available
                        execution.deleverage(self.trade, pos, a.amount)
                        after = self.trade.account()
                        sizes = {abs(l.size) for l in after.legs
                                 if l.symbol in (pos.perp_symbol, pos.call_symbol, pos.put_symbol)}
                        if len(sizes) == 1:
                            pos.contracts = sizes.pop()
                        pos.reserve_usd += max(after.available - before, 0.0)   # released margin joins the reserve
                        self.ledger.update(pos)
                        self._event("action", "deleverage", a.reason + f" Now {pos.contracts} contracts; "
                                    f"reserve ${self.reserve_left(pos):.2f}.")
                        delevered = True
                except ExchangeError as e:
                    self._event("critical", a.kind, f"{a.kind} failed ({e.code}): {e.message}")
                    return
            if not delevered:
                return

    def _close(self, pos: PositionRecord, reason: str, st: Status | None, kind: str = "exit") -> bool:
        fills = execution.close_all(self.trade, pos, reason)
        left = [l for l in self.trade.account().legs
                if l.symbol in (pos.perp_symbol, pos.call_symbol, pos.put_symbol) and l.size]
        exit_fees = sum(f.fee for f in fills)
        if left:
            self._event("critical", "exit_incomplete",
                        "Exit incomplete: " + ", ".join(f"{l.symbol} {l.size:+d}" for l in left)
                        + ". Retrying next tick.")
            return False
        before = pos.extra.get("equity_before")
        if before is not None:                         # real P&L: what the account actually gained or lost
            pnl = self.trade.account().equity - before
        else:
            pnl = (st.metrics.get("pnl_usd", 0.0) if st else 0.0) - exit_fees
        self.ledger.close(pos.id, reason, pnl, self.now())
        self.ledger.kv_set("last_state", "FLAT")
        verb = "Unwound" if kind == "unwound" else "Exited"
        self._event("critical" if kind != "user" else "action", kind,
                    f"{verb}: {reason} Final P&L ${pnl:+.2f}.", position_id=pos.id, pnl=pnl, exit_fees=exit_fees)
        return True

    def _enter(self, cand: Candidate, now: float) -> tuple[bool, str, int | None]:
        tag = f"e{int(now) % 10**6}{secrets.token_hex(2)}"   # unique per attempt: a reused id returns the OLD order
        equity_before = self.account.equity if self.account else None
        if self.account and cand.capital_usd > self.account.available:
            msg = (f"Not enough funds on Delta: this offer needs ${cand.capital_usd:.2f} (margin + reserve), "
                   f"available ${self.account.available:.2f}. Lower the amount or add funds on Delta.")
            self._event("warn", "entry_refused", msg, offer=cand.id)
            return False, msg, None
        sn = self.snaps.get(cand.underlying) or self.snap
        res = execution.enter(self.trade, cand, self.params, tag, now, perp=sn.perp if sn else None)
        if not res.ok:
            self._event("critical", "entry_failed", res.reason, offer=cand.id)
            return False, res.reason, None
        res.record.extra["equity_before"] = equity_before
        pos_id = self.ledger.open_position(res.record)
        self.ledger.kv_set("last_state", None)
        days = (res.record.expiry_ts - now) / 86400
        msg = (f"Locked {res.record.fixed_apr:.2f}% fixed for {days:.0f} days on {res.record.contracts} contracts. "
               f"Expected net {cand.net_edge_pct:+.2f}%.")
        self._event("info", "entered", msg, position_id=pos_id, offer=cand.id)
        try:
            self.trade.heartbeat(HB_ID)
        except ExchangeError:
            pass
        return True, msg, pos_id

    # ------------------------------------------------------------------ controls
    def start(self, auto_enter: bool | None = None) -> None:
        with self.lock:
            self.mode = "running"
            self.ledger.kv_set("mode", "running")
            if auto_enter is not None:
                self.update_params({"auto_enter": bool(auto_enter)})
            self._event("info", "bot", "Bot started" + (" with auto-entry." if self.params.auto_enter else
                                                        ". New entries need your click."))

    def pause(self) -> None:
        with self.lock:
            self.mode = "paused"
            self.ledger.kv_set("mode", "paused")
            self._event("info", "bot", "Bot paused: no new entries. Exits and margin protection keep running.")

    def close_now(self) -> tuple[bool, str]:
        with self.lock:
            pos = self.ledger.current()
            if not pos:
                return False, "No open position."
            self.mode = "paused"                       # "Close all" means stop: never re-enter on the next tick
            self.ledger.kv_set("mode", "paused")
            ok = self._close(pos, "Closed by you. Bot paused until you start it again.", self.status if self.status and self.status.state != "FLAT"
                             else None, kind="user")
            return ok, "All legs closed." if ok else "Exit incomplete; retrying next tick."

    def enter_offer(self, offer_id: str) -> tuple[bool, str, int | None]:
        with self.lock:
            if self.ledger.current():
                return False, "A position is already open.", None
            if self.issues:
                return False, "Trading frozen: " + " ".join(self.issues), None
            cands = self.decision.candidates if self.decision else ()
            cand = next((c for c in cands if c.id == offer_id), None)
            if cand is None:
                return False, "Offer expired; refresh offers.", None
            if not cand.ok:
                return False, "Offer rejected by the fee guard: " + "; ".join(cand.reasons), None
            return self._enter(cand, self.now())

    def set_funding_override(self, apr: float | None, avg24_apr: float | None = None) -> None:
        with self.lock:
            self.override = None if apr is None else {"apr": float(apr), "avg24_apr": avg24_apr, "set_ts": self.now()}
            self.ledger.kv_set("override", self.override)
            self._event("warn", "demo", "Demo override cleared: using real funding." if apr is None else
                        f"DEMO: funding input overridden to {apr:.2f}%/yr"
                        + (f" (24h avg {avg24_apr:.2f}%)" if avg24_apr is not None else "") + ".")

    def update_params(self, d: dict) -> Params:
        with self.lock:
            merged = Params.from_dict({**self.params.to_dict(), **d})
            if not 1 <= merged.leverage <= 5:
                raise ValueError("leverage must be between 1 and 5")
            if merged.capital_usd <= 0:
                raise ValueError("capital_usd must be > 0")
            if not 0 <= merged.reserve_pct <= 1:
                raise ValueError("reserve_pct must be between 0 and 1")
            self.params = merged
            self.ledger.kv_set("params", merged.to_dict())
            return merged
