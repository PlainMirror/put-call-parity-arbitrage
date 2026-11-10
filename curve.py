"""The Treasury term structure, because a flat rate invents arbitrage.

Discounting a 358-day LEAPS conversion at the 13-week bill understates the
discount factor and hands back the difference as a fake edge. On 2026-09-24
the curve ran 4.01% at 1 month to 4.51% at 1 year, so a flat 4.07% overstated
every long-dated conversion by roughly 44bp of annualised "profit" -- which is
the same order as the residual edge that survived every liquidity filter. The
curve is not a refinement here; it is the difference between a result and an
artefact.

Treasury CMT quotes are bond-equivalent par yields. Option discounting wants
continuously compounded zero rates, so they are converted with
r_c = 2*ln(1 + y/2). At these maturities the par/zero distinction is a basis
point or two and is ignored; the compounding convention is not, because it is
worth ~5bp at 4.5% and the numbers being judged are that small.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
from typing import Dict, Optional

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "cache", "treasury_curve.json")

URL = ("https://home.treasury.gov/resource-center/data-chart-center/"
       "interest-rates/daily-treasury-rates.csv/{yr}/all"
       "?type=daily_treasury_yield_curve&field_tdr_date_value={yr}"
       "&page&_format=csv")

TENORS = {"1 Mo": 1 / 12, "1.5 Month": 1.5 / 12, "2 Mo": 2 / 12,
          "3 Mo": 0.25, "4 Mo": 4 / 12, "6 Mo": 0.5, "1 Yr": 1.0,
          "2 Yr": 2.0, "3 Yr": 3.0, "5 Yr": 5.0}


def fetch(asof: dt.date, refresh: bool = False) -> Dict[str, float]:
    if os.path.exists(PATH) and not refresh:
        with open(PATH, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("asof") == asof.isoformat():
            return cached
    r = requests.get(URL.format(yr=asof.year),
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y").dt.date
    row = df[df["Date"] == asof]
    if row.empty:                       # fall back to the latest prior close
        row = df[df["Date"] <= asof].nlargest(1, "Date")
    if row.empty:
        raise RuntimeError(f"no Treasury curve on or before {asof}")
    row = row.iloc[0]
    out = {"asof": str(row["Date"]),
           "yields": {k: float(row[k]) / 100.0 for k in TENORS
                      if k in row.index and pd.notna(row[k])}}
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return out


class Curve:
    """Log-linear interpolation of continuously compounded zero rates."""

    def __init__(self, asof: dt.date):
        raw = fetch(asof)
        self.asof = raw["asof"]
        pts = sorted((TENORS[k], v) for k, v in raw["yields"].items())
        self.t = np.array([p[0] for p in pts])
        # bond-equivalent par -> continuously compounded
        self.r = np.array([2.0 * math.log(1.0 + p[1] / 2.0) for p in pts])

    def rate(self, T) -> np.ndarray:
        """Zero rate for maturity T in years; flat outside the quoted range."""
        T = np.atleast_1d(np.asarray(T, dtype=float))
        return np.interp(np.clip(T, self.t[0], self.t[-1]), self.t, self.r)

    def discount(self, T) -> np.ndarray:
        T = np.atleast_1d(np.asarray(T, dtype=float))
        return np.exp(-self.rate(T) * T)

    def __repr__(self) -> str:
        pts = "  ".join(f"{t:.2f}y={100 * r:.2f}%"
                        for t, r in zip(self.t, self.r))
        return f"<Curve {self.asof}  {pts}>"


if __name__ == "__main__":
    c = Curve(dt.date(2026, 9, 24))
    print(c)
    for d in (7, 30, 90, 180, 266, 358):
        T = d / 365.0
        print(f"  {d:>4}d  T={T:.3f}  r={100 * float(c.rate(T)[0]):.3f}%  "
              f"df={float(c.discount(T)[0]):.6f}")
