"""Full user journey through Bot.tick() and the HTTP API, on the in-memory FakeExchange."""
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.bot.runner import Bot
from packages.exchange.fake import FakeExchange
from packages.ledger import Ledger


def make(capital=500, **fx):
    ex = FakeExchange(**fx)
    L = Ledger(":memory:", now=ex.now)
    b = Bot(ex, ex, L, now=ex.now)
    b.update_params({"capital_usd": capital})
    return ex, L, b


def kinds(L):
    return [e["kind"] for e in L.events(200)][::-1]


def test_journey_enter_earn_flip_exit():
    ex, L, b = make()
    b.tick()                                            # FLAT: offers, no entry while paused
    assert b.decision.action == "enter" and L.current() is None
    b.start(auto_enter=True)
    b.tick()
    pos = L.current()
    assert pos and pos.contracts > 0
    ex.accrue_funding(16)
    b.tick()
    assert b.status.state == "EARNING" and L.funding_total(pos.id) > 0
    b.set_funding_override(-10)                         # one bad reading: hold, don't pay exit fees
    b.tick()
    assert b.status.state == "FLIPPED" and L.current() is not None
    b.set_funding_override(-10, avg24_apr=-2)           # a full negative day: exit
    b.tick()
    assert L.current() is None
    closed = L.positions()[0]
    assert closed.state == "closed" and closed.realized_pnl_usd is not None
    assert {l.symbol: l.size for l in ex.account().legs if l.size} == {}
    k = kinds(L)
    for want in ("entered", "funding", "state", "exit"):
        assert want in k, (want, k)


def test_state_events_only_on_change():
    ex, L, b = make()
    b.start(auto_enter=True)
    for _ in range(5):
        b.tick()
    assert kinds(L).count("state") == 1


def test_paused_bot_never_enters_but_still_exits():
    ex, L, b = make()
    b.start(auto_enter=True)
    b.tick()
    b.pause()
    b.set_funding_override(-40)
    b.tick()
    assert L.current() is None                          # exit ran although paused
    b.set_funding_override(None)
    b.tick()
    assert L.current() is None                          # and no new entry while paused


def test_leg_failure_leaves_no_exposure():
    ex, L, b = make()
    b.start(auto_enter=True)
    b.tick()  # enters
    b.close_now()
    assert b.mode == "paused"
    b.tick()
    assert L.current() is None                          # no instant re-entry after "Close all"
    for o in ex.options:                                # whichever offer wins, its call leg fails
        if o.kind == "call":
            ex.fail_next(o.symbol)
    b.start()
    b.tick()
    assert L.current() is None
    assert {l.symbol: l.size for l in ex.account().legs if l.size} == {}
    assert "entry_failed" in kinds(L)


def test_rally_triggers_deleverage_and_topup_hedge_intact():
    ex, L, b = make()
    b.start(auto_enter=True)
    b.tick()
    n0 = L.current().contracts
    ex.set_spot(81000 * 1.15)
    b.tick()
    pos = L.current()
    assert pos is not None and pos.contracts < n0
    sizes = {abs(l.size) for l in ex.account().legs if l.size}
    assert sizes == {pos.contracts}
    assert {"deleverage", "topup"} <= set(kinds(L))


def test_fee_guard_blocks_wide_spreads():
    ex, L, b = make(spread_frac=0.05)                   # 5% option spreads: costs eat the edge
    b.start(auto_enter=True)
    b.tick()
    assert b.decision.action == "skip" and L.current() is None
    assert all(not c.ok for c in b.decision.candidates)


def test_disconnect_freezes_and_recovers():
    ex, L, b = make()
    b.tick()
    ex.fail_account = True
    b.tick()
    assert b.stale and "disconnected" in kinds(L)
    ex.fail_account = False
    b.tick()
    assert not b.stale and "reconnected" in kinds(L)


def test_api_contract_smoke():
    ex, L, b = make()
    c = TestClient(create_app(bot=b, start_loop=False))
    assert c.post("/api/bot/tick").json()["ok"]
    h = c.get("/api/health").json()
    assert h["trading_env"] == "testnet" and h["key_ok"] is True
    offers = c.get("/api/offers").json()
    assert offers["decision"]["action"] == "enter" and offers["offers"][0]["ok"]
    best = offers["offers"][0]
    r = c.post("/api/enter", json={"offer_id": best["id"]}).json()
    assert r["ok"] and r["position_id"]
    c.post("/api/bot/tick")
    s = c.get("/api/state").json()
    p = s["position"]
    assert p["status"] == "EARNING" and len(p["legs"]) == 3 and p["notional_usd"] > 0
    perp = next(l for l in p["legs"] if l["symbol"] == "BTCUSD")
    assert perp["health_pct"] and perp["liquidation_price"] > s["market"]["spot"]
    assert c.get("/api/funding").json()["fixed_apr"] == p["fixed_apr"]
    assert c.put("/api/settings", json={"leverage": 9}).status_code == 422
    assert c.put("/api/settings", json={"leverage": 4}).json()["leverage"] == 4
    assert c.post("/api/demo/funding", json={"apr": -40}).json()["ok"]
    assert c.get("/api/state").json()["position"] is None
    assert c.get("/api/positions").json()["positions"][0]["state"] == "closed"
    r = c.post("/api/position/close").json()
    assert r["ok"] is False                              # nothing to close, still HTTP 200
    assert len(c.get("/api/events").json()["events"]) > 3
