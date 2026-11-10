"""The economics of whatever survived the screen.

A positive edge is not the same as a trade. This asks the three questions that
decide whether the survivors are opportunities or rounding error:

1. **Does it survive the dividend bracket?** The conversion is computed twice,
   once assuming every projected dividend arrives and once assuming none does.
   The short call in a conversion is American, and when it is in the money
   before an ex-date the rational holder exercises early precisely to take the
   dividend -- so the zero-dividend case is not pessimism, it is the case that
   actually happens when the dividend is large enough to matter.

2. **Does it survive one tick?** Options quote in pennies. If being filled a
   single cent worse on either leg erases the edge, the edge was the quote's
   rounding, not a mispricing.

3. **What is it worth in dollars?** An annualised percentage on a structure
   that ties up $1,700 for 204 days and pays three cents is not a strategy.
"""
from __future__ import annotations

import datetime as dt
import os

import numpy as np
import pandas as pd

import scan as S

ROOT = os.path.dirname(os.path.abspath(__file__))
TICK = 0.01


def main() -> None:
    d = pd.read_parquet(os.path.join(ROOT, "artifacts", "scan.parquet"))

    # Re-price the conversion with the dividend switched off. `conv_cost`
    # already carries fees and the stock half-spread, so only the payoff moves.
    disc = np.exp(-d["rf"] * d["T"])
    d["conv_edge_nodiv"] = d["strike"] * disc - d["conv_cost"]
    # One tick worse on each option leg: sell the call a cent lower, pay a
    # cent more for the put.
    d["conv_edge_1tick"] = d["conv_edge"] - 2 * TICK
    d["conv_edge_worst"] = np.minimum(d["conv_edge_nodiv"],
                                      d["conv_edge"]) - 2 * TICK

    print("=" * 78)
    print("SURVIVAL OF THE SURVIVORS")
    print("=" * 78)
    print(f"  {'group':<9}{'tier-5 pairs':>14}{'edge>0':>9}"
          f"{'+no-div':>9}{'+1 tick':>9}{'both':>7}")
    for g in ("control", "target"):
        gd = d[(d["group"] == g) & d["t5_spread"]]
        n = len(gd)
        e = int((gd["conv_edge"] > 0).sum())
        nd = int(((gd["conv_edge"] > 0) & (gd["conv_edge_nodiv"] > 0)).sum())
        tk = int(((gd["conv_edge"] > 0) & (gd["conv_edge_1tick"] > 0)).sum())
        both = int((gd["conv_edge_worst"] > 0).sum())
        print(f"  {g:<9}{n:>14}{e:>9}{nd:>9}{tk:>9}{both:>7}")

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 78)
    print("WHAT THE SMALL-CAP SURVIVORS ACTUALLY PAY")
    print("(100 shares + 1 put + 1 short call, held to expiry)")
    print("=" * 78)
    t = d[(d["group"] == "target") & d["t5_spread"]
          & (d["conv_edge"] > 0)].copy()
    t["capital"] = t["conv_cost"] * 100
    t["profit_$"] = t["conv_edge"] * 100
    t["profit_nodiv_$"] = t["conv_edge_nodiv"] * 100
    t["excess_ann"] = t["conv_rate"] - t["rf"]
    t = t.sort_values("profit_$", ascending=False)
    print(f"  {'sym':<6}{'exp':<12}{'dte':>4}{'K':>7}{'capital':>10}"
          f"{'profit':>9}{'no-div':>9}{'-1 tick':>9}{'excess/yr':>11}")
    for _, r in t.iterrows():
        print(f"  {r['symbol']:<6}{str(r['expiry']):<12}{int(r['dte']):>4}"
              f"{r['strike']:>7.1f}{r['capital']:>10.0f}"
              f"{r['profit_$']:>9.2f}{r['profit_nodiv_$']:>9.2f}"
              f"{r['conv_edge_1tick'] * 100:>9.2f}"
              f"{100 * r['excess_ann']:>10.2f}%")
    if len(t):
        surv = t[t["conv_edge_worst"] > 0]
        print(f"\n  {len(t)} candidates; median profit "
              f"${t['profit_$'].median():.2f} on median capital "
              f"${t['capital'].median():,.0f}")
        print(f"  survive both the no-dividend case and one tick of "
              f"slippage: {len(surv)}")
        if len(surv):
            print(surv[["symbol", "expiry", "strike", "capital",
                        "profit_$", "conv_edge_worst"]].to_string(index=False))

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 78)
    print("THE COST OF THE HEDGE ITSELF")
    print("(at-the-money put ask, as % of the share price)")
    print("=" * 78)
    atm = d[d["moneyness"].between(0.97, 1.03) & d["t3_fresh"]].copy()
    atm["put_pct"] = atm["p_ask"] / atm["spot"]
    atm["bucket"] = pd.cut(atm["dte"], [0, 14, 35, 75, 200, 400],
                           labels=["<2w", "2-5w", "5-11w", "11-29w", ">29w"])
    g = (atm.groupby(["group", "bucket"], observed=True)["put_pct"]
         .agg(n="size", median="median", p90=lambda s: s.quantile(0.90))
         .reset_index())
    print(f"  {'group':<9}{'horizon':<9}{'n':>7}{'median':>10}{'p90':>10}")
    for _, r in g.iterrows():
        print(f"  {r['group']:<9}{str(r['bucket']):<9}{int(r['n']):>7}"
              f"{100 * r['median']:>9.2f}%{100 * r['p90']:>9.2f}%")

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 78)
    print("THE BORROW IS ALREADY IN THE PRICE")
    print("(reversal = short stock + short put + long call)")
    print("=" * 78)
    t5 = d[(d["group"] == "target") & d["t5_spread"]]
    for etb, lab in ((False, "hard to borrow"), (True, "easy to borrow")):
        part = t5[t5["easy_to_borrow"] == etb]
        if len(part):
            print(f"  {lab:<15} tier-5 pairs {len(part):>5}   reversal edge > 0 in "
                  f"{(part['rev_edge'] > 0).mean():6.1%}")
    # carry the options imply for holding the stock, pair by pair, on liquid
    # near-the-money strikes: fwd_pv = C - P + K*exp(-rT) = S*exp(-carry*T)
    liq = d[(d["c_trade_date"] == S.ASOF) & (d["p_trade_date"] == S.ASOF)
            & d["moneyness"].between(0.85, 1.15) & (d["c_rel_spread"] < 0.5)
            & (d["p_rel_spread"] < 0.5) & (d["T"] > 0)].copy()
    fwd_pv = liq["c_mid"] - liq["p_mid"] + liq["strike"] * np.exp(-liq["rf"] * liq["T"])
    liq["carry"] = -np.log(fwd_pv / liq["spot"]) / liq["T"]
    print("\n  implied carry, liquid near-the-money pairs")
    print(f"  {'group':<9}{'borrow':<9}{'n':>7}{'median':>10}{'p90':>10}")
    for (g, etb), part in liq.groupby(["group", "easy_to_borrow"]):
        print(f"  {g:<9}{'ETB' if etb else 'HTB':<9}{len(part):>7}"
              f"{100 * part['carry'].median():>9.2f}%{100 * part['carry'].quantile(0.90):>9.2f}%")

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 78)
    print("THE COLLAR FRONTIER -- keeping upside lowers the guaranteed floor")
    print("=" * 78)
    liq = d[d["t4_size"] & (d["group"] == "target") & d["dte"].between(25, 120)]
    if len(liq):
        pick = (liq.groupby(["symbol", "expiry"]).size()
                .rename("n").reset_index().nlargest(3, "n"))
        for _, row in pick.iterrows():
            sym, expiry = row["symbol"], row["expiry"]
            sub = liq[(liq["symbol"] == sym) & (liq["expiry"] == expiry)]
            spot = float(sub["spot"].iloc[0])
            atm_k = sub.loc[(sub["strike"] - spot).abs().idxmin(), "strike"]
            floor_leg = sub[sub["strike"] == atm_k].iloc[0]
            # Cost of the collar = stock + put(atm_k) - call(kc), for each kc.
            ups = sub[sub["strike"] >= atm_k].sort_values("strike")
            if len(ups) < 3:
                continue
            print(f"\n  {sym}  spot ${spot:.2f}  {expiry} "
                  f"({int(floor_leg['dte'])}d)  floor strike Kp=${atm_k:.2f}")
            print(f"    {'cap Kc':>8}{'upside kept':>13}{'cost':>9}"
                  f"{'GUARANTEED floor':>19}{'best case':>12}")
            for _, q in ups.head(7).iterrows():
                cost = (spot * 1.0005 + floor_leg["p_ask"] - q["c_bid"]
                        + 2 * 0.007)
                dfac = float(np.exp(-q["rf"] * q["T"]))
                tag = "  <- conversion" if q["strike"] == atm_k else ""
                print(f"    {q['strike']:>8.2f}{q['strike'] - atm_k:>13.2f}"
                      f"{cost:>9.2f}{atm_k * dfac - cost:>+19.3f}"
                      f"{q['strike'] * dfac - cost:>+12.3f}{tag}")


if __name__ == "__main__":
    main()
