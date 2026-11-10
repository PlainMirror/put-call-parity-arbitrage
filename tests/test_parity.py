"""Checks on the conversion and reversal arithmetic and the dividend projection. No keys or network.

    python tests/test_parity.py        # or: pytest tests

Every "arbitrage" in this study is the output of a few lines of arithmetic, so a
sign error there would create or hide all of them.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import divs       # noqa: E402
import financing  # noqa: E402
import parity     # noqa: E402

S, K, T, R = 50.0, 55.0, 0.5, 0.04
ASOF = dt.date(2026, 9, 24)


def pair(c_mid, p_mid, c_spread=0.0, p_spread=0.0, strike=K, t=T):
    return pd.DataFrame([{"strike": strike, "T": t, "c_mid": c_mid, "p_mid": p_mid,
                          "c_bid": c_mid - c_spread / 2, "c_ask": c_mid + c_spread / 2,
                          "p_bid": p_mid - p_spread / 2, "p_ask": p_mid + p_spread / 2,
                          "c_spread": c_spread, "p_spread": p_spread}])


def parity_prices(div_pv=0.0, c=3.0, strike=K, t=T):
    """A call price and the put price that put-call parity then requires."""
    return c, c - (S - div_pv) + strike * math.exp(-R * t)


def test_conversion_pays_the_strike_in_every_state():
    """long stock + long put - short call is worth K at expiry whatever the stock does."""
    for st in np.linspace(0.0, 200.0, 41):
        assert abs(st + max(K - st, 0.0) - max(st - K, 0.0) - K) < 1e-12


def test_no_edge_when_prices_sit_on_parity():
    c, p = parity_prices()
    d = parity.score(pair(c, p), S, R, fees=False)
    assert abs(d["dev_mid"].iloc[0]) < 1e-12
    assert abs(d["conv_edge"].iloc[0]) < 1e-12 and abs(d["rev_edge"].iloc[0]) < 1e-12
    # on parity, with no costs, the conversion earns exactly the risk-free rate
    assert abs(d["conv_rate"].iloc[0] - R) < 1e-12 and abs(d["rev_rate"].iloc[0] - R) < 1e-12


def test_costs_come_straight_out_of_the_edge():
    c, p = parity_prices()
    d = parity.score(pair(c, p, c_spread=0.10, p_spread=0.06), S, R, stock_spread_bp=5.0, fees=True)
    half = S * 5.0 / 1e4
    cost = 0.05 + 0.03 + half + 2 * parity.FEE_PER_SHARE
    assert abs(d["conv_edge"].iloc[0] + cost) < 1e-12
    assert abs(d["rev_edge"].iloc[0] + cost) < 1e-12
    assert abs(d["spread_cost"].iloc[0] - cost) < 1e-12


def test_conversion_and_reversal_cannot_both_pay():
    """Their edges always sum to minus the full round-trip cost, whatever the prices are."""
    rng = np.random.default_rng(0)
    for _ in range(200):
        c, p = rng.uniform(0.2, 12, 2)
        cs, ps = rng.uniform(0.01, 0.8, 2)
        d = parity.score(pair(c, p, cs, ps), S, R, div_pv=rng.uniform(0, 1), stock_spread_bp=5.0)
        total = cs + ps + 2 * S * 5.0 / 1e4 + 4 * parity.FEE_PER_SHARE
        assert abs(d["conv_edge"].iloc[0] + d["rev_edge"].iloc[0] + total) < 1e-9


def test_dividend_sign_conventions():
    """A dividend helps the conversion (you receive it) and hurts the reversal (you owe it)."""
    c, p = parity_prices()
    base = parity.score(pair(c, p), S, R, fees=False)
    div = parity.score(pair(c, p), S, R, div_pv=0.40, fees=False)
    assert abs((div["conv_edge"].iloc[0] - base["conv_edge"].iloc[0]) - 0.40) < 1e-12
    assert abs((div["rev_edge"].iloc[0] - base["rev_edge"].iloc[0]) + 0.40) < 1e-12
    # and prices that respect parity WITH the dividend show no edge once it is included
    c2, p2 = parity_prices(div_pv=0.40)
    d2 = parity.score(pair(c2, p2), S, R, div_pv=0.40, fees=False)
    assert abs(d2["conv_edge"].iloc[0]) < 1e-12 and abs(d2["dev_mid"].iloc[0]) < 1e-12


def test_a_rich_call_shows_up_as_conversion_edge_only():
    c, p = parity_prices()
    d = parity.score(pair(c + 0.50, p), S, R, fees=False)
    assert abs(d["conv_edge"].iloc[0] - 0.50) < 1e-12 and abs(d["rev_edge"].iloc[0] + 0.50) < 1e-12


def test_impossible_bids_are_flagged():
    d = parity.score(pair(S + 1.0, 1.0), S, R)                  # call bid above the stock
    assert d["c_bid_impossible"].iloc[0] and d["bound_violation"].iloc[0]
    d = parity.score(pair(1.0, K + 1.0), S, R)                  # put bid above the strike
    assert d["p_bid_impossible"].iloc[0]
    c, p = parity_prices()
    assert not parity.score(pair(c, p), S, R)["bound_violation"].iloc[0]


def test_unusable_quotes_are_dropped():
    q = lambda b, a: parity._quote({"latestQuote": {"bp": b, "ap": a}})  # noqa: E731
    assert q(0.0, 0.5) is None          # no bid: the leg cannot be sold
    assert q(1.2, 1.0) is None          # crossed: stale data
    assert q(None, 1.0) is None
    assert abs(q(1.0, 1.2)["mid"] - 1.1) < 1e-12


def test_a_curve_and_a_flat_rate_are_both_accepted():
    class Curve:
        def rate(self, t):
            return 0.03 + 0.01 * np.atleast_1d(t)
    assert np.allclose(parity.rates_for(0.04, np.array([0.5, 1.0])), [0.04, 0.04])
    assert np.allclose(parity.rates_for(Curve(), np.array([0.5, 1.0])), [0.035, 0.04])


# ------------------------------------------------------------------ dividends
def _payer(last_ex, cadence=91.0, amount=0.25, n=8):
    last = dt.date.fromisoformat(last_ex)
    dates = [(last - dt.timedelta(days=int(cadence) * i)).isoformat() for i in range(n)][::-1]
    return {"pays": True, "amount": amount, "cadence_days": cadence, "last_ex": last_ex, "dates": dates}


def test_regular_dividends_are_projected_and_discounted():
    rec = _payer("2026-08-20")
    expiry = dt.date(2027, 3, 19)
    nxt = [dt.date(2026, 8, 20) + dt.timedelta(days=91 * i) for i in (1, 2)]      # Nov 19 and Feb 18
    want = sum(0.25 * math.exp(-R * (d - ASOF).days / 365.0) for d in nxt)
    assert abs(divs.pv_until(rec, expiry, ASOF, R) - want) < 1e-12
    assert divs.pv_until(rec, dt.date(2026, 10, 16), ASOF, R) == 0.0              # no ex-date before expiry


def test_irregular_histories_are_not_projected():
    """The two inventions that topped the first screen: a payer that had stopped, and three specials."""
    stopped = _payer("2025-04-22")                               # last ex-date 17 months ago: a payment was missed
    assert divs.pv_until(stopped, dt.date(2027, 6, 18), ASOF, R) == 0.0
    specials = {"pays": True, "amount": 0.80, "cadence_days": 736.0, "last_ex": "2025-03-20",
                "dates": ["2021-03-08", "2022-03-07", "2023-03-10", "2025-03-20"]}
    assert divs.pv_until(specials, dt.date(2027, 6, 18), ASOF, R) == 0.0
    short = _payer("2026-08-20", n=3)                            # too few payments to call it a policy
    assert divs.pv_until(short, dt.date(2027, 3, 19), ASOF, R) == 0.0
    assert divs.pv_until(None, dt.date(2027, 3, 19), ASOF, R) == 0.0
    assert divs.pv_until({"pays": False}, dt.date(2027, 3, 19), ASOF, R) == 0.0


# ----------------------------------------------------------- financing rates
def test_financing_rates_recover_a_planted_funding_spread():
    """Price a pair off a rate 50bp above Treasuries: the mid rate must read +0.50 points."""
    fund = R + 0.005
    c = 4.0
    p = c - S + K * math.exp(-fund * T)
    row = parity.score(pair(c, p, 0.04, 0.04), S, R, stock_spread_bp=0.0, fees=False)
    row["div_pv"], row["dte"], row["spot"] = 0.0, 182, S
    out = financing.add_rates(row)
    assert abs(out["mid"].iloc[0] - 0.50) < 1e-9
    assert out["lend"].iloc[0] < out["mid"].iloc[0] < out["borrow"].iloc[0]


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
