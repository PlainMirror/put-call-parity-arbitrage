"""The literal request: buy the equity, hedge it with options, always profit.

A collar -- long stock, long put at Kp, short call at Kc >= Kp -- is the
general form of "hedged equity". Its worst case is fixed at Kp and its best
case at Kc, so a guaranteed profit exists iff

    Kp * exp(-rT) + PV(D)  >  S_ask + P_ask(Kp) - C_bid(Kc) + fees

Because C_bid is non-increasing in strike, the right-hand side is minimised at
Kc = Kp. That is the conversion, and it is the collar that keeps **zero**
upside. So there is a theorem hiding in the request:

    Every dollar of upside you keep lowers your guaranteed floor.

The floor is highest exactly where the trade stops being a trade. This module
measures the frontier rather than asserting it, so the shape can be seen.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Optional

import numpy as np
import pandas as pd

import alpaca
import divs
import parity

ROOT = os.path.dirname(os.path.abspath(__file__))
CHAINS = os.path.join(ROOT, "cache", "chains")
FEE = parity.FEE_PER_SHARE


def legs(symbol: str, asof: dt.date):
    with open(os.path.join(CHAINS, f"{symbol}.json")) as fh:
        chain = json.load(fh)
    calls, puts = {}, {}
    for occ, snap in chain.items():
        try:
            m = alpaca.occ_parse(occ)
        except Exception:                               # noqa: BLE001
            continue
        q = parity._quote(snap)
        if q is None:
            continue
        # See parity.pairs_from_chain: dailyBar is the last day this
        # contract traded, not today. Keep the trade stamp too.
        q["volume"] = (snap.get("dailyBar") or {}).get("v") or 0
        q["last_trade_ts"] = (snap.get("latestTrade") or {}).get("t")
        (calls if m["right"] == "C" else puts)[(m["expiry"], m["strike"])] = q
    return calls, puts


def frontier(symbol: str, spot: float, expiry: dt.date, asof: dt.date,
             r: float, div_pv: float = 0.0,
             stock_half_bp: float = 5.0) -> pd.DataFrame:
    """Collar grid for one expiry: guaranteed floor vs retained upside."""
    calls, puts = legs(symbol, asof)
    ks_c = sorted(k for (e, k) in calls if e == expiry)
    ks_p = sorted(k for (e, k) in puts if e == expiry)
    if not ks_c or not ks_p:
        return pd.DataFrame()
    T = parity.year_frac(expiry, asof)
    disc = np.exp(-r * T)
    s_ask = spot * (1 + stock_half_bp / 1e4)

    # The put strike that floors closest to today's price -- "insure at spot".
    rows = []
    for kp in ks_p:
        p = puts[(expiry, kp)]
        for kc in ks_c:
            if kc < kp:
                continue
            c = calls[(expiry, kc)]
            cost = s_ask + p["ask"] - c["bid"] + 2 * FEE
            rows.append({
                "symbol": symbol, "expiry": expiry, "dte": (expiry - asof).days,
                "kp": kp, "kc": kc, "spot": spot, "cost": cost,
                # worst case: stock <= kp, put exercised, you receive kp
                "floor_profit": kp * disc + div_pv - cost,
                # best case: stock >= kc, call assigned, you receive kc
                "cap_profit": kc * disc + div_pv - cost,
                "upside_kept": max(kc - kp, 0.0),
                "put_cost_pct": p["ask"] / spot,
                "call_credit_pct": c["bid"] / spot,
                "p_vol": p["volume"], "c_vol": c["volume"],
                "p_trade_ts": p["last_trade_ts"],
                "c_trade_ts": c["last_trade_ts"],
            })
    return pd.DataFrame(rows)


def insurance_cost(d: pd.DataFrame) -> pd.DataFrame:
    """What an at-the-money put actually costs, by group and horizon."""
    atm = d[(d["moneyness"].between(0.97, 1.03))].copy()
    atm["put_pct_of_spot"] = atm["p_ask"] / atm["spot"]
    atm["dte_bucket"] = pd.cut(atm["dte"], [0, 14, 35, 75, 200, 400],
                               labels=["<2w", "2-5w", "5-11w", "11-29w", ">29w"])
    g = (atm.groupby(["group", "dte_bucket"], observed=True)["put_pct_of_spot"]
         .agg(n="size", median="median", p90=lambda s: s.quantile(0.90)))
    return g.reset_index()
