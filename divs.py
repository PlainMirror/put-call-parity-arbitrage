"""Projected dividends per underlying, for the parity correction.

A reversal screen run without dividends is a dividend screen: on the short
stock leg you owe every dividend, so every payer shows a fake edge exactly the
size of its payout. The correction matters most for precisely the kind of name
this universe is full of -- banks, REITs, shippers, BDCs -- where a quarterly
payout can be 2-4% of a $10 share price.

Future ex-dates are not published far ahead, so they are projected: take the
observed cadence and last amount, and step forward from the last ex-date.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "cache", "divs.json")


def _history(sym: str) -> dict:
    try:
        s = yf.Ticker(sym).dividends
        if s is None or len(s) == 0:
            return {"symbol": sym, "pays": False, "dates": [], "amount": 0.0}
        s = s.tail(12)
        dates = [d.date().isoformat() for d in s.index]
        gaps = np.diff([pd.Timestamp(d).value for d in s.index]) / 86_400e9
        cadence = float(np.median(gaps)) if len(gaps) else 91.0
        return {"symbol": sym, "pays": True, "dates": dates,
                "amount": float(s.iloc[-1]), "cadence_days": cadence,
                "last_ex": dates[-1]}
    except Exception as exc:                            # noqa: BLE001
        return {"symbol": sym, "pays": False, "dates": [], "amount": 0.0,
                "error": str(exc)[:120]}


def load(symbols: List[str], refresh: bool = False) -> Dict[str, dict]:
    have: Dict[str, dict] = {}
    if os.path.exists(PATH) and not refresh:
        with open(PATH, "r", encoding="utf-8") as fh:
            have = json.load(fh)
    todo = [s for s in symbols if s not in have]
    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for rec in pool.map(_history, todo):
                have[rec["symbol"]] = rec
        with open(PATH, "w", encoding="utf-8") as fh:
            json.dump(have, fh)
    return have


def pv_until(rec: dict, expiry: dt.date, asof: dt.date, r: float) -> float:
    """PV of dividends with a projected ex-date in (asof, expiry]."""
    if not rec or not rec.get("pays"):
        return 0.0
    amt = float(rec.get("amount") or 0.0)
    if amt <= 0:
        return 0.0
    cadence = float(rec.get("cadence_days") or 91.0)
    if not math.isfinite(cadence) or cadence < 5:
        cadence = 91.0
    try:
        last = dt.date.fromisoformat(rec["last_ex"])
    except Exception:                                   # noqa: BLE001
        return 0.0

    # Only *regular* dividends may be projected. The first cut of this
    # function was loose enough to invent cash flows, and those inventions
    # became the entire apparent edge: it gave STLA a $0.77 payment in April
    # 2027 off a last ex-date of April 2025, and read RKT's three irregular
    # specials (2021, 2022, 2025) as a 736-day "cadence" worth $0.80. Both
    # then topped the arbitrage screen by exactly the size of the fiction.
    # A projection is only trustworthy when the history is regular enough to
    # extrapolate, so anything else is treated as a non-payer.
    hist = rec.get("dates") or []
    if len(hist) < 4:                 # too short to establish a rhythm
        return 0.0
    if cadence > 200:                 # annual-or-worse: specials, not a policy
        return 0.0
    if (asof - last).days > 1.25 * cadence:   # a payment has been missed
        return 0.0
    pv, nxt = 0.0, last + dt.timedelta(days=int(round(cadence)))
    while nxt <= expiry and (nxt - asof).days <= 400:
        if nxt > asof:
            pv += amt * math.exp(-r * (nxt - asof).days / 365.0)
        nxt = nxt + dt.timedelta(days=int(round(cadence)))
    return pv
