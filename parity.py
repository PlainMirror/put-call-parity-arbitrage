"""Put-call parity arithmetic, at mid prices and at executable prices.

The strategy under test is "buy the equity, hedge it with options, profit no
matter what". The structure that actually delivers "no matter what" is the
**conversion**:

    long 100 shares  +  long 1 put (K, T)  +  short 1 call (K, T)

At expiry the position is worth exactly K per share whatever the stock does:
above K the short call is assigned, below K the long put is exercised, and
either way the shares leave at K. There is no path dependence and no scenario
to model. It is a loan, not a trade.

So the profit question has a closed form. You pay

    cost = S_ask + P_ask - C_bid          (today, per share)

and you receive K at expiry plus any dividends D paid meanwhile. The position
makes a guaranteed profit *above cash* only when

    K * exp(-rT) + PV(D)  >  cost + fees

which is exactly a put-call parity violation net of costs. Everything below is
that inequality, measured.

Two sign conventions worth stating once, because getting them backwards
produces confident nonsense:

* Ignoring dividends **understates** conversion edge (you receive them) and
  **overstates** reversal edge (you owe them on the short). A reversal screen
  run without dividends is mostly a dividend detector.
* Mid prices are not a market you can trade in. Every number is reported both
  at mid and at the executable side, and the gap between them is the answer.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import alpaca

# Retail all-in option cost per contract, per side: $0.65 commission is the
# Schwab/Fidelity/E*Trade standard, plus ~$0.05 of OCC + exchange fees.
FEE_PER_CONTRACT = 0.70
FEE_PER_SHARE = FEE_PER_CONTRACT / 100.0


def year_frac(expiry: dt.date, asof: dt.date) -> float:
    return max((expiry - asof).days, 0) / 365.0


def _quote(snap: dict) -> Optional[dict]:
    q = (snap or {}).get("latestQuote") or {}
    bid, ask = q.get("bp"), q.get("ap")
    if bid is None or ask is None:
        return None
    bid, ask = float(bid), float(ask)
    # A zero bid means nobody is willing to buy at any price -- you cannot sell
    # that leg, so any structure containing it is not executable. A crossed or
    # inverted book is stale data, not an opportunity.
    if ask <= 0 or bid <= 0 or ask < bid:
        return None
    return {"bid": bid, "ask": ask, "mid": 0.5 * (bid + ask),
            "spread": ask - bid, "rel_spread": (ask - bid) / (0.5 * (bid + ask)),
            "bid_size": q.get("bs"), "ask_size": q.get("as"),
            "ts": q.get("t")}


def pairs_from_chain(chain: Dict[str, dict], asof: dt.date) -> pd.DataFrame:
    """One row per (expiry, strike) that has BOTH a call and a put quoted."""
    calls: Dict[tuple, dict] = {}
    puts: Dict[tuple, dict] = {}
    for occ, snap in chain.items():
        try:
            meta = alpaca.occ_parse(occ)
        except Exception:                              # noqa: BLE001
            continue
        q = _quote(snap)
        if q is None:
            continue
        q["occ"] = occ
        # `dailyBar` on an illiquid contract is the last day it traded, NOT
        # today: a SPY 2027 LEAPS shows v=1 from a print four months old.
        # Using it as "volume today" silently passes dead contracts through a
        # liquidity filter, so the trade's own timestamp is carried instead
        # and the bar volume is kept only under a name that says what it is.
        dbar = snap.get("dailyBar") or {}
        trade = snap.get("latestTrade") or {}
        q["bar_volume"] = dbar.get("v") or 0
        q["last_trade_px"] = trade.get("p")
        q["last_trade_ts"] = trade.get("t")
        key = (meta["expiry"], meta["strike"])
        (calls if meta["right"] == "C" else puts)[key] = q

    rows = []
    for key in set(calls) & set(puts):
        expiry, strike = key
        c, p = calls[key], puts[key]
        rows.append({
            "expiry": expiry, "strike": float(strike),
            "dte": (expiry - asof).days, "T": year_frac(expiry, asof),
            "c_bid": c["bid"], "c_ask": c["ask"], "c_mid": c["mid"],
            "p_bid": p["bid"], "p_ask": p["ask"], "p_mid": p["mid"],
            "c_spread": c["spread"], "p_spread": p["spread"],
            "c_rel_spread": c["rel_spread"], "p_rel_spread": p["rel_spread"],
            "c_vol": c["bar_volume"], "p_vol": p["bar_volume"],
            "c_last_px": c["last_trade_px"], "p_last_px": p["last_trade_px"],
            "c_trade_ts": c["last_trade_ts"], "p_trade_ts": p["last_trade_ts"],
            # Size at the touch is the only real test of whether a quote is a
            # market or a placeholder. A conversion sells the call (hits the
            # call bid) and buys the put (lifts the put ask), so those are the
            # two sizes that have to exist; a reversal needs the other two.
            "c_bid_sz": c.get("bid_size") or 0, "c_ask_sz": c.get("ask_size") or 0,
            "p_bid_sz": p.get("bid_size") or 0, "p_ask_sz": p.get("ask_size") or 0,
            # Quote age is the other half of executability. The snapshot is
            # taken at one instant; a leg whose NBBO last updated hours
            # earlier is not being quoted at that instant, it is a leftover.
            "c_ts": c.get("ts"), "p_ts": p.get("ts"),
            "c_occ": c["occ"], "p_occ": p["occ"],
        })
    return pd.DataFrame(rows)


def rates_for(r, T):
    """Accept either a flat rate or a curve object exposing .rate(T)."""
    if hasattr(r, "rate"):
        return np.asarray(r.rate(T), dtype=float)
    return np.full(len(np.atleast_1d(T)), float(r))


def score(df: pd.DataFrame, spot: float, r, div_pv: float = 0.0,
          stock_spread_bp: float = 0.0, fees: bool = True) -> pd.DataFrame:
    """Add conversion / reversal economics to a pairs frame.

    `div_pv` is the present value of dividends with an ex-date inside the
    option's life, per share. `stock_spread_bp` is the half-spread paid on the
    equity leg in basis points of spot; the free Alpaca equity feed is
    single-venue and its quotes are far wider than the consolidated NBBO, so
    the stock leg is modelled rather than taken from that feed.
    """
    if df.empty:
        return df
    d = df.copy()
    half = spot * stock_spread_bp / 1e4
    s_ask, s_bid = spot + half, spot - half
    tt = d["T"].to_numpy()
    rt = rates_for(r, tt)
    d["rf"] = rt
    disc = np.exp(-rt * tt)
    k_pv = d["strike"].to_numpy() * disc
    fee = 2 * FEE_PER_SHARE if fees else 0.0     # two option legs, one side

    # --- parity deviation at mid: the number a naive screen reports ---------
    d["dev_mid"] = ((d["c_mid"] - d["p_mid"])
                    - (spot - div_pv - k_pv))

    # --- conversion: long stock, long put, short call ----------------------
    d["conv_cost"] = s_ask + d["p_ask"] - d["c_bid"] + fee
    d["conv_edge"] = k_pv + div_pv - d["conv_cost"]
    with np.errstate(divide="ignore", invalid="ignore"):
        d["conv_rate"] = np.where(
            (d["conv_cost"] > 0) & (d["T"] > 0),
            np.log((d["strike"] + div_pv * np.exp(rt * tt))
                   / d["conv_cost"]) / d["T"].replace(0, np.nan),
            np.nan)

    # --- reversal: short stock, short put, long call -----------------------
    d["rev_proceeds"] = s_bid + d["p_bid"] - d["c_ask"] - fee
    d["rev_edge"] = d["rev_proceeds"] - k_pv - div_pv
    with np.errstate(divide="ignore", invalid="ignore"):
        d["rev_rate"] = np.where(
            (d["rev_proceeds"] > 0) & (d["T"] > 0),
            np.log((d["strike"] + div_pv * np.exp(rt * tt))
                   / d["rev_proceeds"]) / d["T"].replace(0, np.nan),
            np.nan)

    # --- how much of the mid-price signal the spread eats ------------------
    d["spread_cost"] = (0.5 * d["c_spread"] + 0.5 * d["p_spread"]
                        + half + fee)
    d["moneyness"] = d["strike"] / spot
    d["spot"] = spot

    # --- single-leg no-arbitrage bounds ------------------------------------
    # A European call is worth at most the stock and at least its discounted
    # intrinsic; the put bounds mirror that. A *bid* above the upper bound is
    # not an opportunity, it is a stale or auto-generated quote -- and it is
    # exactly where a naive screen finds its "free money", because the whole
    # apparent edge is the bad print. Flagging it is not a filter of
    # convenience: a quote that violates a one-leg bound cannot be traded
    # against, or the market maker showing it would already be bankrupt.
    # Hard bounds: a call is worth at most the ex-dividend stock, a put at
    # most the discounted strike. A *bid* above those cannot be hit, because
    # the maker showing it would be handing out money. These catch the
    # grossest bad prints, though not the subtler ones -- a deep-ITM call bid
    # can sit well inside its bounds and still be nonsense, which is why
    # quote age and size do the real work below.
    d["c_bid_impossible"] = d["c_bid"] > (spot - div_pv) + 1e-9
    d["p_bid_impossible"] = d["p_bid"] > d["strike"] * disc + div_pv + 1e-9
    d["bound_violation"] = d["c_bid_impossible"] | d["p_bid_impossible"]

    # The put value implied by the call bid, via parity. When this is far
    # above the put's own ask, one of the two legs is not a real market --
    # which is the same statement as a conversion edge, made leg-explicit.
    d["put_implied_by_call_bid"] = (d["c_bid"] - spot + div_pv
                                    + d["strike"] * disc)
    return d


# --------------------------------------------------------------------------- #
# Implied carry
# --------------------------------------------------------------------------- #
def implied_carry(d: pd.DataFrame, spot: float, r) -> Optional[dict]:
    """What the option market itself thinks the stock costs to hold.

    Parity fixes the synthetic stock price: C - P + K*exp(-rT) equals the
    spot discounted by whatever carry the market applies -- dividends plus
    stock borrow. Reading that back out is the single most useful diagnostic
    here, because the apparent free money in a hard-to-borrow name is the
    borrow fee, and this measures it directly instead of assuming it.

    Returns the implied discounted forward and the continuous carry rate
    q such that  fwd_pv = spot * exp(-q*T).  q > 0 means holding the stock
    earns you something (dividend) or costs the short seller something
    (borrow); on a non-payer it is essentially the borrow rate.
    """
    liq = d[(d.get("c_traded_today", d["c_vol"] > 0))
            & (d.get("p_traded_today", d["p_vol"] > 0))
            & d["moneyness"].between(0.85, 1.15)
            & (d["c_rel_spread"] < 0.5) & (d["p_rel_spread"] < 0.5)]
    if len(liq) < 3:
        return None
    T = float(liq["T"].median())
    if T <= 0:
        return None
    synth = (liq["c_mid"] - liq["p_mid"]
             + liq["strike"] * np.exp(-rates_for(r, liq["T"].to_numpy())
                                      * liq["T"].to_numpy())).to_numpy()
    fwd_pv = float(np.median(synth))
    if fwd_pv <= 0 or spot <= 0:
        return None
    return {"n": len(liq), "T": T, "fwd_pv": fwd_pv,
            "basis": fwd_pv - spot,
            "carry": -math.log(fwd_pv / spot) / T,
            "dispersion": float(np.std(synth))}
