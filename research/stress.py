"""Monte Carlo stress test of the Synthetic Boros position under isolated margin.

Position per 1 BTC notional: short perp (separately margined) + long call + short put (same K, T).
The P&L is hedged, but under isolated margin the legs do NOT share collateral:
  - a rally drains the short-perp margin (the call gains, but that gain can't be used as perp collateral)
  - a crash raises the short-put margin (the perp gains, but likewise can't be used)
A reserve tops up whichever leg needs it. If the reserve runs out, the hedge is broken ("break").

Assumptions to verify with testnet keys (marked ASSUME):
  ASSUME short-put margin = put mark + PUT_MARGIN_PCT x spot
  ASSUME perp liquidated when equity in the position < maintenance (0.25% x notional, from /v2/products)
Run: python stress.py
"""
import math
import random

DAYS, STEPS_PER_DAY = 33, 24
PERP_MM = 0.0025            # BTCUSD maintenance margin 0.25% (testnet /v2/products)
PUT_MARGIN_PCT = 0.10       # ASSUME
TOPUP_TRIGGER = 0.5         # top up when the leg's spare margin falls below 50% of its starting margin
PATHS = 4000


def bs_put(s, k, t, vol):
    if t <= 0:
        return max(k - s, 0.0)
    d1 = (math.log(s / k) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return k * n(-d2) - s * n(-d1)


def path(vol, jump_p, jump_size, rng):
    s, out = 1.0, [1.0]
    dt = 1 / (365 * STEPS_PER_DAY)
    for _ in range(DAYS * STEPS_PER_DAY):
        s *= math.exp(-0.5 * vol * vol * dt + vol * math.sqrt(dt) * rng.gauss(0, 1))
        if rng.random() < jump_p / STEPS_PER_DAY:          # jump_p = expected jumps per day
            s *= 1 + rng.choice((-1, 1)) * jump_size
        out.append(s)
    return out


def run(leverage, reserve, vol, jump_p=0.02, jump_size=0.12, seed=7, rebalance=False):
    """Returns (break probability, capital used as fraction of notional).

    rebalance=True: excess margin on one leg (above its starting margin) is moved back to the reserve
    every step. ASSUME Delta's remove-position-margin call allows withdrawing that excess.
    """
    rng = random.Random(seed)
    perp_margin0 = 1 / leverage
    put_margin0 = bs_put(1, 1, DAYS / 365, vol) + PUT_MARGIN_PCT
    breaks = 0
    for _ in range(PATHS):
        p = path(vol, jump_p, jump_size, rng)
        perp_m, put_m, res = perp_margin0, put_margin0, reserve
        for i, s in enumerate(p):
            t_left = (DAYS * STEPS_PER_DAY - i) / (365 * STEPS_PER_DAY)
            # short perp: equity = margin - (s - 1)
            eq = perp_m - (s - 1)
            if eq < perp_margin0 * TOPUP_TRIGGER:
                add = min(res, perp_margin0 - eq)
                perp_m += add; res -= add; eq += add
            if eq < PERP_MM * s:
                breaks += 1; break
            if rebalance and eq > perp_margin0:
                res += eq - perp_margin0; perp_m -= eq - perp_margin0
            # short put: required margin rises as BTC falls
            need = bs_put(s, 1, t_left, vol) + PUT_MARGIN_PCT * s
            if need > put_m:
                add = min(res, need - put_m)
                put_m += add; res -= add
                if need > put_m:
                    breaks += 1; break
            elif rebalance and put_m > need * 1.2:
                res += put_m - need * 1.2; put_m = need * 1.2
    call_premium = bs_put(1, 1, DAYS / 365, vol)            # ATM call ~= ATM put at zero carry
    # the put premium received funds part of the put margin, so our own capital only covers PUT_MARGIN_PCT
    capital = perp_margin0 + PUT_MARGIN_PCT + call_premium + reserve
    return breaks / PATHS, capital


def run_delever(leverage, reserve, vol, jump_p=0.02, jump_size=0.12, seed=7, cut=0.5, fee=0.0006):
    """Rebalance + deleverage: when a leg can't be funded, close `cut` of BOTH legs (hedge stays intact).

    Returns (break probability, average fraction of size kept, extra fee drag as fraction of notional).
    """
    rng = random.Random(seed)
    perp_margin0 = 1 / leverage
    put_margin0 = bs_put(1, 1, DAYS / 365, vol) + PUT_MARGIN_PCT
    breaks, kept, drag = 0, 0.0, 0.0
    for _ in range(PATHS):
        p = path(vol, jump_p, jump_size, rng)
        q, perp_m, put_m, res = 1.0, perp_margin0, put_margin0, reserve
        entry, q_time = 1.0, 0.0
        broke = False
        for i, s in enumerate(p):
            t_left = (DAYS * STEPS_PER_DAY - i) / (365 * STEPS_PER_DAY)
            eq = perp_m - q * (s - entry)
            need = q * (bs_put(s, 1, t_left, vol) + PUT_MARGIN_PCT * s)
            short = max(0.0, q * perp_margin0 * TOPUP_TRIGGER - eq) + max(0.0, need - put_m)
            while short > res and q > 0.05:
                # close `cut` of both legs: frees that share of each leg's margin into the reserve
                freed_perp = eq * cut
                freed_put = put_m * cut - (need * cut)       # put margin released minus the buy-back cost
                res += freed_perp + max(freed_put, 0.0)
                perp_m -= perp_m * cut; put_m -= put_m * cut
                drag += q * cut * fee
                q *= 1 - cut
                eq = perp_m - q * (s - entry)
                need = q * (bs_put(s, 1, t_left, vol) + PUT_MARGIN_PCT * s)
                short = max(0.0, q * perp_margin0 * TOPUP_TRIGGER - eq) + max(0.0, need - put_m)
            if short > res:
                broke = True; break
            if eq < q * perp_margin0 * TOPUP_TRIGGER:
                add = q * perp_margin0 - eq; perp_m += add; res -= add; eq += add
            elif eq > q * perp_margin0:
                res += eq - q * perp_margin0; perp_m -= eq - q * perp_margin0
            if need > put_m:
                res -= need - put_m; put_m = need
            elif put_m > need * 1.2:
                res += put_m - need * 1.2; put_m = need * 1.2
            q_time += q
        breaks += broke
        kept += q_time / len(p)
    return breaks / PATHS, kept / PATHS, drag / PATHS


if __name__ == "__main__" and "--delever" in __import__("sys").argv:
    EDGE_NET = 0.0031
    print("REBALANCE + DELEVERAGE (close half of both legs when the reserve can't fund a leg)\n")
    print(f"{'vol':>4} {'lev':>4} {'reserve':>8} {'P(break)':>9} {'size kept':>10} {'fee drag':>9} {'capital':>8} {'APR on capital':>15}")
    for vol in (0.45, 0.70, 1.00):
        for lev in (3, 5, 10):
            for reserve in (0.10, 0.20):
                pb, kept, drag = run_delever(lev, reserve, vol)
                cap = 1 / lev + PUT_MARGIN_PCT + bs_put(1, 1, DAYS / 365, vol) + reserve
                apr = (EDGE_NET * kept - drag) / cap * 365 / DAYS * 100
                print(f"{vol:>4.2f} {lev:>4} {reserve:>8.2f} {pb:>9.2%} {kept:>10.1%} {drag:>9.4%} {cap:>8.2f} {apr:>14.1f}%")
        print()
    raise SystemExit

if __name__ == "__main__":
    EDGE_NET = 0.0031          # +0.31% of notional over 33 days (testnet, 27 Nov expiry, after fees)
    print("ISOLATED margin. Break = reserve exhausted, so one leg is liquidated or margin-called.\n")
    print(f"{'vol':>4} {'lev':>4} {'reserve':>8} {'rebalance':>9} {'P(break)':>9} {'capital':>8} {'APR on capital':>15}")
    for vol in (0.45, 0.70, 1.00):
        for lev in (3, 5, 10):
            for reserve in (0.10, 0.20):
                for reb in (False, True):
                    pb, cap = run(lev, reserve, vol, rebalance=reb)
                    apr = EDGE_NET / cap * 365 / DAYS * 100
                    print(f"{vol:>4.2f} {lev:>4} {reserve:>8.2f} {str(reb):>9} {pb:>9.2%} {cap:>8.2f} {apr:>14.1f}%")
        print()
