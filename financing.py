"""Are the large-cap conversions that survive every filter an arbitrage, or a financing spread?

    python financing.py          # needs artifacts/scan.parquet from scan.py

A conversion is a loan: pay S + P - C today, receive K at expiry. So every
(expiry, strike) pair quotes three interest rates:

    lend    the rate earned doing the conversion at executable prices (conv_rate)
    borrow  the rate paid doing the reversal at executable prices (rev_rate)
    mid     the rate implied by mid prices, with no fees or stock spread

If the survivors are a financing spread, then
  1. the mid rate sits above Treasuries by a similar amount for every strike of
     one expiry (a rate is a property of the expiry, not of a strike),
  2. that amount is similar across different stocks (funding is common to all),
  3. the survivors earn LESS than the mid rate of their own expiry: they are
     places where crossing the spread costs less than the funding spread, not
     places where one quote is wrong.
If they are mispricings, the survivors would instead sit above their own
expiry's mid rate, and the mid rate would be scattered across strikes.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
TICK = 0.01
BUCKETS = [0, 30, 90, 180, 365, 5000]
LABELS = ["up to 30d", "31-90d", "91-180d", "181-365d", "over 365d"]


def add_rates(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    div_fv = d["div_pv"] * np.exp(d["rf"] * d["T"])
    with np.errstate(divide="ignore", invalid="ignore"):
        d["mid_rate"] = np.log((d["strike"] + div_fv) / (d["spot"] + d["p_mid"] - d["c_mid"])) / d["T"]
    d["lend"] = 100 * (d["conv_rate"] - d["rf"])          # percentage points over Treasuries
    d["borrow"] = 100 * (d["rev_rate"] - d["rf"])
    d["mid"] = 100 * (d["mid_rate"] - d["rf"])
    d["half_width"] = (d["borrow"] - d["lend"]) / 2       # cost of crossing, as a rate
    # the same survival test as verdict.py: positive with and without the dividend, after one tick a leg
    nodiv = d["strike"] * np.exp(-d["rf"] * d["T"]) - d["conv_cost"]
    d["survivor"] = (np.minimum(nodiv, d["conv_edge"]) - 2 * TICK) > 0
    d["bucket"] = pd.cut(d["dte"], BUCKETS, labels=LABELS)
    return d


def main() -> None:
    d = pd.read_parquet(os.path.join(ROOT, "artifacts", "scan.parquet"))
    small = d[d["group"] == "target"]
    print(f"small caps: {100 * (small['dev_mid'].abs() > 0.01).mean():.2f}% of {len(small)} pairs are more than "
          f"a cent from parity at mid prices")
    c = add_rates(d[(d["group"] == "control") & d["t5_spread"] & (d["dte"] >= 7)])
    c = c[np.isfinite(c["mid"]) & np.isfinite(c["lend"]) & np.isfinite(c["borrow"])]
    surv = c[c["survivor"]]
    print(f"\nlarge-cap control, liquid pairs (tier 5, 7+ days): {len(c)}; survivors {len(surv)}")

    print("\n1. the three rates, in percentage points over the matched Treasury rate (medians)")
    t = c.groupby("bucket", observed=True).agg(n=("mid", "size"), lend=("lend", "median"), mid=("mid", "median"),
                                               borrow=("borrow", "median"), half_width=("half_width", "median"),
                                               survivors=("survivor", "sum"))
    print(t.round(2).to_string())

    print("\n2. is the mid rate a property of the expiry? (expiries with 5+ liquid strikes, 30+ days)")
    g = c[c["dte"] >= 30].groupby(["symbol", "expiry"])
    per = g["mid"].agg(["size", "median", "std"])
    per = per[per["size"] >= 5]
    print(f"   {len(per)} expiries: median level {per['median'].median():+.2f} points, "
          f"median spread across strikes of one expiry {per['std'].median():.2f} points")
    sym = c[c["dte"] >= 90].groupby("symbol")["mid"].median()
    print(f"   across {len(sym)} stocks (90+ days): median {sym.median():+.2f} points, "
          f"standard deviation {sym.std():.2f}, range {sym.min():+.2f} to {sym.max():+.2f}")

    print("\n3. where the survivors sit against their own expiry's mid rate")
    own = c.groupby(["symbol", "expiry"])["mid"].transform("median")
    s = c[c["survivor"]].assign(own_mid=own[c["survivor"]])
    below = (s["lend"] <= s["own_mid"] + 0.05).mean()
    print(f"   survivors: median days {s['dte'].median():.0f}, median strike/spot {s['moneyness'].median():.2f}, "
          f"earn {s['lend'].median():+.2f} points over Treasuries")
    print(f"   their own expiry's mid rate: {s['own_mid'].median():+.2f} points; "
          f"{100 * below:.0f}% of survivors earn no more than that")
    print(f"   cost of crossing, as a rate: survivors {s['half_width'].median():.2f} points, "
          f"all liquid pairs {c['half_width'].median():.2f}")

    print("\n4. the mid rate by strike (90+ days): does it depend on moneyness?")
    m = c[c["dte"] >= 90].copy()
    m["mny"] = pd.cut(m["moneyness"], [0, 0.7, 0.85, 0.95, 1.05, 1.15, 1.3, 10],
                      labels=["<0.70", "0.70-0.85", "0.85-0.95", "0.95-1.05", "1.05-1.15", "1.15-1.30", ">1.30"])
    print(m.groupby("mny", observed=True).agg(n=("mid", "size"), mid=("mid", "median"), lend=("lend", "median"),
                                              half_width=("half_width", "median"),
                                              survivors=("survivor", "sum")).round(2).to_string())
    print("\n5. dividend payers against non-payers (90+ days)")
    m["pays"] = np.where(m["div_pv"] > 0, "dividend inside the option's life", "no dividend")
    print(m.groupby("pays").agg(n=("mid", "size"), mid=("mid", "median"), survivors=("survivor", "sum")).round(2).to_string())
    # On a stock with no dividend an American call is never exercised early, so at LOW strikes (call in the
    # money, put nearly worthless) neither leg carries an early-exercise premium. That corner is the cleanest
    # reading of the financing rate. At high strikes the American put's premium pushes the implied rate down.
    m["side"] = np.where(m["moneyness"] < 0.95, "strike below spot", np.where(m["moneyness"] > 1.05, "strike above spot", "at the money"))
    print("\n6. the mid rate by side, split by dividend (90+ days)")
    print(m.groupby(["pays", "side"]).agg(n=("mid", "size"), mid=("mid", "median"), lend=("lend", "median"),
                                          survivors=("survivor", "sum")).round(2).to_string())
    clean = m[(m["div_pv"] == 0) & (m["moneyness"] < 0.95)]
    cs = clean[clean["survivor"]]
    print(f"\n   cleanest corner (no dividend, strike below spot): mid rate {clean['mid'].median():+.2f} points over "
          f"Treasuries on {len(clean)} pairs; its {len(cs)} survivors lend at {cs['lend'].median():+.2f} points, "
          f"median capital ${100 * cs['conv_cost'].median():,.0f} a contract, "
          f"= ${cs['lend'].median() / 100 * 100 * cs['conv_cost'].median():,.0f} a year over a T-bill")


if __name__ == "__main__":
    main()
