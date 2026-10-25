"""FastAPI app for the console. Handlers are thin: validate shape -> call the bot/ledger -> shape JSON."""
from __future__ import annotations

import dataclasses
import math
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api import errors
from apps.bot.keys import load_keys as bot_keys
from apps.bot.runner import Bot
from packages import risk
from packages.market import funding_stdev
from packages.strategy import Candidate


def _num(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return _num(o)


ASSET_LABEL = {"XAUT": "Tether Gold · RWA", "PAXG": "PAX Gold · RWA", "BTC": "Bitcoin", "ETH": "Ether"}
RWA = {"XAUT", "PAXG"}


def asset_view(u: str, snap) -> dict:
    f = snap.funding
    sd = funding_stdev(f)
    return {"underlying": u, "label": ASSET_LABEL.get(u, u), "rwa": u in RWA, "perp_symbol": snap.perp.symbol,
            "spot": snap.spot, "funding_now_apr": f.now_apr, "funding_avg24_apr": f.avg24_apr,
            "funding_stdev_apr": sd, "stable": sd is not None and sd <= 0.25, "pairs": len(snap.pairs)}


def offer(c: Candidate) -> dict:
    return _clean({"id": c.id, "underlying": c.underlying, "label": ASSET_LABEL.get(c.underlying, c.underlying),
                   "rwa": c.underlying in RWA, "expiry_ts": c.pair.expiry_ts, "strike": c.pair.strike, "days": c.days,
                   "call_symbol": c.pair.call.symbol, "put_symbol": c.pair.put.symbol,
                   "fixed_apr": c.fixed_apr, "forecast_apr": c.forecast_apr, "contracts": c.contracts,
                   "notional_usd": c.notional_usd, "capital_usd": c.capital_usd, "gross_edge_usd": c.gross_edge_usd,
                   "cost_usd": c.cost.total, "fees_usd": c.cost.fees, "slippage_usd": c.cost.slippage,
                   "net_edge_usd": c.net_edge_usd, "net_edge_pct": c.net_edge_pct, "fee_ratio": c.fee_ratio,
                   "ok": c.ok, "reasons": list(c.reasons),
                   # leg prices so the console can show exactly what is being put on
                   "put_px": c.put_px, "call_px": c.call_px, "perp_px": c.perp_px, "forward": c.forward,
                   "contract_value": c.pair.call.contract_value})


def position_view(bot: Bot) -> dict | None:
    pos = bot.ledger.current()
    if not pos:
        return None
    st, acct, snap = bot.status, bot.account, bot.snap
    m = st.metrics if st else {}
    health = {h.symbol: h for h in risk.margin_health(acct, pos, bot.params, snap)} if acct else {}
    legs = []
    for l in (acct.legs if acct else ()):
        if l.symbol not in (pos.perp_symbol, pos.call_symbol, pos.put_symbol):
            continue
        h = health.get(l.symbol)
        legs.append({"symbol": l.symbol, "side": "short" if l.size < 0 else "long", "size": l.size,
                     "entry_price": l.entry_price, "mark": l.mark, "margin": l.margin,
                     "liquidation_price": l.liquidation_price,
                     "health_pct": h.health_pct if h else None, "distance_pct": h.distance_pct if h else None})
    cv = snap.perp.contract_value if snap else 0.001
    return _clean({"id": pos.id, "status": st.state if st else None, "status_reason": st.reason if st else None,
                   "action": st.action if st else None, "opened_ts": pos.opened_ts, "expiry_ts": pos.expiry_ts,
                   "days_left": m.get("days_left"), "strike": pos.strike, "contracts": pos.contracts,
                   "call_symbol": pos.call_symbol, "put_symbol": pos.put_symbol, "perp_symbol": pos.perp_symbol,
                   "notional_usd": pos.contracts * cv * pos.entry_perp, "fixed_apr": pos.fixed_apr,
                   "floating_apr": m.get("floating_apr"), "spread_apr": m.get("spread_apr"),
                   "funding_usd": bot.ledger.funding_total(pos.id), "fees_usd": pos.entry_fees_usd,
                   "earned_usd": m.get("pnl_usd"), "projected_usd": m.get("projected_usd"),
                   "worst_case_usd": m.get("worst_case_usd"), "reserve_usd": bot.reserve_left(pos), "legs": legs})


class StartBody(BaseModel):
    auto_enter: bool | None = None


class EnterBody(BaseModel):
    offer_id: str


class FundingOverride(BaseModel):
    apr: float | None = None
    avg24_apr: float | None = None


def create_app(bot: Bot | None = None, start_loop: bool = True) -> FastAPI:
    holder: dict = {"bot": bot}
    stop = threading.Event()

    def get() -> Bot:
        if holder["bot"] is None:
            from apps.api.deps import build_bot
            holder["bot"] = build_bot()
        return holder["bot"]

    def loop(tick_s: float) -> None:
        while not stop.is_set():
            b = get()
            try:
                if b.demo and hasattr(b.trade, "accrue_funding"):
                    b.trade.accrue_funding(1)            # demo clock: 1 simulated hour per tick
                b.tick()
            except Exception as e:  # noqa: BLE001  (the loop must never die)
                b.ledger.event("critical", "tick_error", f"Tick failed: {type(e).__name__}: {e}")
            stop.wait(tick_s)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        t = None
        if start_loop:
            t = threading.Thread(target=loop, args=(bot_keys().tick_seconds,), daemon=True)
            t.start()
        yield
        stop.set()

    app = FastAPI(title="Synthetic Boros", lifespan=lifespan)
    @app.middleware("http")
    async def public_is_read_only(request, call_next):
        # Requests that come in through the public tunnel carry Cloudflare's header. They may look, never act:
        # entering, closing, settings and rehearsal stay with the local console on this machine.
        if "cf-connecting-ip" in request.headers and request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse({"ok": False, "message": "Public view is read-only. Trading is controlled from "
                                 "the operator's console.", "error": {"code": "read_only", "message": "read-only"}},
                                status_code=403)
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_origin_regex=r"https://[a-z0-9-]+\.vercel\.app",
                       allow_methods=["*"], allow_headers=["*"])
    errors.install(app)

    @app.get("/api/health")
    def health():
        b = get()
        return {"ok": b.last_error is None and b.last_tick_ts is not None, "trading_env": b.trade.env,
                "funding_source": b.funding_src.env, "key_ok": b.key_ok, "error": b.last_error,
                "bot_mode": b.mode, "last_tick_ts": b.last_tick_ts, "demo_mode": b.demo}

    @app.get("/api/state")
    def state():
        b = get()
        with b.lock:
            snap, acct, d = b.snap, b.account, b.decision
            f = snap.funding if snap else None
            return _clean({
                "bot": {"mode": b.mode, "auto_enter": b.params.auto_enter, "last_tick_ts": b.last_tick_ts,
                        "tick_seconds": bot_keys().tick_seconds, "stale": b.stale, "demo_mode": b.demo,
                        "issues": b.issues, "error": b.last_error},
                "market": None if not snap else {
                    "spot": snap.spot, "perp_mark": snap.perp_mark, "funding_now_apr": f.now_apr,
                    "funding_avg24_apr": f.avg24_apr, "funding_source": f.source,
                    "override_apr": b.override["apr"] if b.override else None, "trading_env": snap.env,
                    "underlying": snap.perp.symbol.removesuffix("USD"),
                    "assets": [asset_view(u, sn) for u, sn in b.snaps.items()]},
                "account": None if not acct else {"equity": acct.equity, "available": acct.available,
                                                  "margin_mode": acct.margin_mode},
                "position": position_view(b),
                "decision": None if not d else {"action": d.action, "reason": d.reason,
                                                "best": offer(d.best) if d.best else None},
            })

    @app.get("/api/offers")
    def offers():
        b = get()
        with b.lock:
            d = b.decision
            if d is None:
                pos = b.ledger.current()
                return {"decision": {"action": "skip", "reason": "A position is open: offers are paused." if pos
                                     else "Waiting for the first market snapshot."}, "offers": []}
            ranked = sorted(d.candidates, key=lambda c: (not c.ok, -(c.net_edge_usd or -1e9)))
            return {"decision": {"action": d.action, "reason": d.reason}, "offers": [offer(c) for c in ranked]}

    @app.get("/api/funding")
    def funding(hours: int = 168, underlying: str | None = None):
        b = get()
        snap = b.snaps.get((underlying or "").upper()) or b.snap
        pos = b.ledger.current()
        pts = [{"ts": t, "apr": a} for t, a in (snap.funding.history if snap else ())][-max(1, hours):]
        return _clean({"source": snap.funding.source if snap else None, "points": pts,
                       "underlying": snap.perp.symbol.removesuffix("USD") if snap else None,
                       "fixed_apr": pos.fixed_apr if pos else None, "exit_apr": b.params.exit_avg24_apr,
                       "override_apr": b.override["apr"] if b.override else None})

    @app.get("/api/events")
    def events(limit: int = 100):
        return {"events": get().ledger.events(min(max(limit, 1), 500))}

    @app.get("/api/positions")
    def positions():
        b = get()
        out = []
        for p in b.ledger.positions():
            d = dataclasses.asdict(p)
            d["funding_usd"] = b.ledger.funding_total(p.id)
            out.append(_clean(d))
        return {"positions": out}

    @app.get("/api/settings")
    def settings():
        return get().params.to_dict()

    @app.put("/api/settings")
    def put_settings(body: dict):
        return get().update_params(body).to_dict()

    @app.post("/api/bot/start")
    def start(body: StartBody | None = None):
        get().start(body.auto_enter if body else None)
        return {"ok": True}

    @app.post("/api/bot/pause")
    def pause():
        get().pause()
        return {"ok": True}

    @app.post("/api/bot/tick")
    def tick():
        b = get()
        b.tick()
        return {"ok": b.last_error is None, "error": b.last_error}

    @app.post("/api/enter")
    def enter(body: EnterBody):
        ok, msg, pos_id = get().enter_offer(body.offer_id)
        return {"ok": ok, "message": msg, "position_id": pos_id}

    @app.post("/api/position/close")
    def close():
        ok, msg = get().close_now()
        return {"ok": ok, "message": msg}

    @app.post("/api/demo/funding")
    def demo_funding(body: FundingOverride):
        b = get()
        b.set_funding_override(body.apr, body.avg24_apr)
        b.tick()
        return {"ok": True, "override_apr": body.apr, "avg24_apr": body.avg24_apr}

    return app


app = create_app()
