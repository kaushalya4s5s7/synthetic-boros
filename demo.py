"""Guided live demo: narrates each step in plain English and runs it against the running bot.

Start the backend first (uvicorn main:app --port 8000), then:
    ./.venv/bin/python demo.py            # full demo, places real TESTNET orders (about $40)
    ./.venv/bin/python demo.py --dry      # explain only, no orders
Press Enter to move to the next step. Keep the console (localhost:3000) open beside it.
"""
import sys
import time

import requests

API = "http://localhost:8000/api"
DRY = "--dry" in sys.argv


def get(p):
    return requests.get(API + p, timeout=30).json()


def post(p, body=None):
    return requests.post(API + p, json=body or {}, timeout=60).json()


def say(title, *lines):
    print("\n" + "=" * 72 + f"\n  {title}\n" + "=" * 72)
    for line in lines:
        print("  " + line)


def wait():
    input("\n  [Enter] next step ")


def pct(x):
    return "—" if x is None else f"{x:.2f}%"


def tick():
    post("/bot/tick")
    time.sleep(1)


# 1 ---------------------------------------------------------------------------------------------
h = get("/health")
s = get("/state")
a = s["account"] or {}
say("1. The idea",
    "Delta pays a FLOATING rate (funding) to anyone short a perpetual future.",
    "Options let us lock a FIXED rate. When fixed is cheaper than floating, we hold both",
    "and keep the difference. The coin's price cancels out. Like Pendle Boros, built from",
    "pieces Delta already lists.",
    "",
    f"Connected: {h['ok']} · orders on {h['trading_env'].upper()} · funding read from {h['funding_source'].upper()}",
    f"Your Delta wallet: ${a.get('equity', 0):.2f} (the money never leaves Delta)")
wait()

# 2 ---------------------------------------------------------------------------------------------
say("2. What the market pays (Overview page)")
for x in s["market"].get("assets", []):
    flat = "FLAT, never moves" if x["stable"] else "moves"
    sd = "—" if x["funding_stdev_apr"] is None else f"±{x['funding_stdev_apr']:.2f}"
    print(f"  {x['underlying']:5} {x['label']:20} funding {pct(x['funding_now_apr'])}  7-day spread {sd}  ({flat})")
print("\n  Gold (a real-world asset) funding has not moved in 90 days, so the bot checks it first.")
print("  On testnet gold's option books are empty, so BTC is what can actually trade here.")
wait()

# 3 ---------------------------------------------------------------------------------------------
o = get("/offers")
btc = [x for x in o["offers"] if x["underlying"] == "BTC" and x.get("put_px")]
say("3. Should we trade right now? (Offers page)", f"Bot says: {o['decision']['reason']}")
if btc:
    b = btc[0]
    qty = b["contracts"] * b.get("contract_value", 0.001)
    fwd = b["strike"] + b["call_px"] - b["put_px"]
    print(f"""
  Closest BTC trade, {b['contracts']} contract(s) = {qty:g} BTC per leg:
    1. SELL put  {b['put_symbol']:22} at ${b['put_px']:,.0f}  -> you receive ${b['put_px'] * qty:.2f}
    2. BUY  call {b['call_symbol']:22} at ${b['call_px']:,.0f}  -> you pay     ${b['call_px'] * qty:.2f}
    3. SHORT perp BTCUSD                  at ${b['perp_px']:,.1f} -> deposit only, still yours

  Call - put = a locked price to buy BTC at expiry: ${fwd:,.0f}
  Locked (fixed) rate {pct(b['fixed_apr'])}  vs  funding forecast {pct(b['forecast_apr'])}
  Expected after ALL fees: ${b['net_edge_usd']:+.2f}  ->  {'TRADE' if b['ok'] else 'NO TRADE (fee guard protects you)'}""")
    if not b["ok"]:
        print("  Reasons: " + " | ".join(b["reasons"]))
wait()

if DRY:
    say("Dry run finished", "Run without --dry to place real testnet orders and rehearse the risk engine.")
    sys.exit()

# 4 ---------------------------------------------------------------------------------------------
say("4. Show a real trade",
    "Testnet funding is pinned at 10.95%/yr and never moves. 'Rehearsal' feeds that number",
    "to the bot. Orders are REAL testnet orders.")
post("/demo/funding", {"apr": 10.95, "avg24_apr": 10.95})
settings = get("/settings")
if settings.get("capital_usd", 0) < 40:
    settings["capital_usd"] = 40
    requests.put(API + "/settings", json=settings, timeout=30)
tick()
o = get("/offers")
best = next((x for x in o["offers"] if x["ok"]), None)
if not best:
    print(f"  No offer passes even at 10.95%: {o['decision']['reason']}")
    post("/demo/funding", {"apr": None})
    sys.exit()
print(f"  Best offer: {best['underlying']} strike ${best['strike']:,.0f}, {best['contracts']} contract(s), "
      f"lock {pct(best['fixed_apr'])}, expected ${best['net_edge_usd']:+.2f}")
wait()
r = post("/enter", {"offer_id": best["id"]})
print(f"\n  {('PLACED: ' if r['ok'] else 'FAILED: ') + r['message']}")
if not r["ok"]:
    post("/demo/funding", {"apr": None})
    sys.exit()
tick()
p = get("/state")["position"]
print(f"\n  Status: {p['status']} - {p['status_reason']}")
for leg in p["legs"]:
    liq = f"liquidation ${leg['liquidation_price']:,.0f}" if leg.get("liquidation_price") else "no liquidation"
    print(f"    {leg['side']:5} {leg['size']:+3d} {leg['symbol']:22} entry ${leg['entry_price']:,.1f}  {liq}")
print("  Check Delta's own UI: the same orders are in its trade history.")
wait()

# 5 ---------------------------------------------------------------------------------------------
say("5. Safety: funding flips negative", "Most flips on Delta end within 8 hours, so the bot should HOLD.")
post("/demo/funding", {"apr": -10})
tick()
p = get("/state")["position"]
print(f"  Status: {p['status']} -> {p['action'].upper()}\n  {p['status_reason']}")
wait()

say("6. Safety: negative for a whole day", "Now the bot should close all three orders by itself.")
post("/demo/funding", {"apr": -30, "avg24_apr": -30})
tick()
time.sleep(2)
s = get("/state")
print(f"  Position open: {s['position'] is not None}")
for e in get("/events?limit=6")["events"][:4]:
    print(f"  [{e['level']}] {e['message']}")
wait()

# 7 ---------------------------------------------------------------------------------------------
post("/demo/funding", {"apr": None})
tick()
say("7. Done",
    "Rehearsal cleared; the bot is back on real funding.",
    "Every decision is in Activity with its reason, and every order has a Delta order ID.",
    "Recap: hedged against price, trades only when it pays after fees, exits by itself.")
