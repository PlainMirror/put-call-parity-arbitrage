"""The collar frontier: what a guaranteed floor actually costs you.

Run after `scan.py`, which writes artifacts/scan.parquet.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

import divs
import hedge
import scan as scan_mod

ROOT = os.path.dirname(os.path.abspath(__file__))
RF, ASOF = scan_mod.RF, scan_mod.ASOF


def main() -> None:
    d = pd.read_parquet(os.path.join(ROOT, "artifacts", "scan.parquet"))

    print("=" * 74)
    print("WHAT THE INSURANCE COSTS -- at-the-money put, % of share price")
    print("=" * 74)
    ins = hedge.insurance_cost(d)
    print(f"  {'group':<9}{'horizon':<10}{'n':>6}{'median':>10}{'p90':>10}")
    for _, r in ins.iterrows():
        print(f"  {r['group']:<9}{str(r['dte_bucket']):<10}{int(r['n']):>6}"
              f"{100*r['median']:>9.2f}%{100*r['p90']:>9.2f}%")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 74)
    print("THE COLLAR FRONTIER -- every dollar of upside kept lowers the floor")
    print("=" * 74)
    # Pick the most liquid target names with a ~30-60 day expiry.
    liq = (d[(d["group"] == "target") & d["dte"].between(25, 70)
             & (d["c_vol"] > 0) & (d["p_vol"] > 0)]
           .groupby(["symbol", "expiry"]).size().rename("n").reset_index()
           .sort_values("n", ascending=False))
    shown = 0
    for _, row in liq.iterrows():
        sym, expiry = row["symbol"], row["expiry"]
        sub = d[(d["symbol"] == sym) & (d["expiry"] == expiry)]
        spot = float(sub["spot"].iloc[0])
        dpv = float(sub["div_pv"].iloc[0])
        f = hedge.frontier(sym, spot, expiry, ASOF, RF, dpv)
        if f.empty:
            continue
        f = f[(f["p_vol"] > 0) & (f["c_vol"] > 0)]
        # Floor the stock at the money: the strike just below spot.
        cands = f[f["kp"] <= spot]
        if cands.empty:
            continue
        kp = cands["kp"].max()
        g = f[(f["kp"] == kp)].sort_values("kc")
        if len(g) < 4:
            continue
        print(f"\n  {sym}  spot ${spot:.2f}  expiry {expiry} "
              f"({int(row['n'])} liquid strikes, {g['dte'].iloc[0]}d)")
        print(f"    floor strike Kp = ${kp:.2f}")
        print(f"    {'cap Kc':>9}{'upside kept':>13}{'cost':>9}"
              f"{'GUARANTEED floor P&L':>23}{'best case':>12}")
        for _, q in g.head(9).iterrows():
            tag = "  <- conversion" if abs(q["kc"] - kp) < 1e-9 else ""
            print(f"    ${q['kc']:>8.2f}{q['upside_kept']:>13.2f}"
                  f"{q['cost']:>9.2f}{q['floor_profit']:>+23.3f}"
                  f"{q['cap_profit']:>+12.3f}{tag}")
        shown += 1
        if shown >= 4:
            break

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 74)
    print("IS THE FLOOR EVER POSITIVE? -- best collar per (symbol, expiry)")
    print("=" * 74)
    best = (d.assign(floor=d["conv_edge"])
            .groupby(["group", "symbol", "expiry"])["floor"].max()
            .reset_index())
    for g, part in best.groupby("group"):
        pos = (part["floor"] > 0).sum()
        print(f"  {g:<9} {len(part):>5} (symbol, expiry) pairs   "
              f"best-floor > 0 in {pos:>4} ({pos/len(part):.2%})   "
              f"median best floor ${part['floor'].median():+.3f}/share")


if __name__ == "__main__":
    main()
