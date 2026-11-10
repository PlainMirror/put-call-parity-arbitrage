"""Scan every cached chain for an executable risk-free profit.

Output order matters. The calibration block runs first: on liquid names the
conversion must price out near the T-bill rate, because that is what a
conversion *is*. If it does not, the arithmetic is wrong and nothing after it
means anything.

The funnel that follows filters on **executability only** -- volume, quote
age, size at the touch, spread width. It never filters on the size of the
edge. That distinction is the whole discipline of an arbitrage screen: if you
drop rows because the profit looks too good, you have assumed your conclusion.
Anything that survives on tradability grounds gets inspected by hand.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

import curve
import divs
import parity

ROOT = os.path.dirname(os.path.abspath(__file__))
CHAINS = os.path.join(ROOT, "cache", "chains")

# The whole Treasury term structure, not a single point on it. A flat 13-week
# rate applied to 358-day LEAPS understates their discount factor by ~44bp and
# hands the difference back as fake arbitrage -- see curve.py.
CURVE = curve.Curve(dt.date(2026, 9, 24))
RF = float(CURVE.rate(0.25)[0])   # 3-month point, for headline comparisons
ASOF = dt.date(2026, 9, 24)      # the session the snapshot quotes come from
CLOSE_UTC = pd.Timestamp("2026-09-24T20:00:00Z")   # 16:00 ET, EDT
STOCK_HALF_SPREAD_BP = 5.0       # modelled; see parity.score docstring


def load_all(stock_spread_bp: float = STOCK_HALF_SPREAD_BP) -> pd.DataFrame:
    with open(os.path.join(ROOT, "cache", "chain_meta.json")) as fh:
        meta = {r["symbol"]: r for r in json.load(fh)["symbols"]}
    syms = [s for s in meta if os.path.exists(os.path.join(CHAINS, f"{s}.json"))]
    div_recs = divs.load(syms)

    frames = []
    for sym in syms:
        with open(os.path.join(CHAINS, f"{sym}.json")) as fh:
            chain = json.load(fh)
        df = parity.pairs_from_chain(chain, ASOF)
        if df.empty:
            continue
        info = meta[sym]
        out = []
        for expiry, part in df.groupby("expiry"):
            T = parity.year_frac(expiry, ASOF)
            r_exp = float(CURVE.rate(T)[0])
            dpv = divs.pv_until(div_recs.get(sym), expiry, ASOF, r_exp)
            scored = parity.score(part, info["price"], CURVE, div_pv=dpv,
                                  stock_spread_bp=stock_spread_bp)
            scored["div_pv"] = dpv
            out.append(scored)
        d = pd.concat(out, ignore_index=True)
        d["symbol"] = sym
        d["group"] = info.get("group", "target")
        d["shortable"] = info["shortable"]
        d["easy_to_borrow"] = info["easy_to_borrow"]
        d["stock_price"] = info["price"]
        d["stock_trade_ts"] = info.get("trade_ts")
        d["dollar_vol"] = info["dollar_vol"]
        frames.append(d)

    d = pd.concat(frames, ignore_index=True)
    for leg in ("c", "p"):
        d[f"{leg}_age_s"] = (CLOSE_UTC - pd.to_datetime(
            d[f"{leg}_ts"], format="ISO8601", utc=True)).dt.total_seconds()
    d["stock_age_s"] = (CLOSE_UTC - pd.to_datetime(
        d["stock_trade_ts"], format="ISO8601", utc=True)).dt.total_seconds()
    for leg in ("c", "p"):
        ts = pd.to_datetime(d[f"{leg}_trade_ts"], format="ISO8601",
                            utc=True, errors="coerce")
        d[f"{leg}_trade_date"] = ts.dt.tz_convert("America/New_York").dt.date
        d[f"{leg}_trade_age_d"] = (CLOSE_UTC - ts).dt.total_seconds() / 86400.0
        # How far the quote midpoint sits from where the contract last
        # actually changed hands. A quote nobody has traded near is a
        # placeholder, and placeholders are where phantom arbitrage lives.
        d[f"{leg}_quote_vs_last"] = d[f"{leg}_mid"] - d[f"{leg}_last_px"]
    return d


def add_tiers(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["t1_bounds"] = ~d["bound_violation"]
    # "Traded today" must come from the trade's own timestamp. The bar volume
    # field looks like today's volume and is not; see parity.pairs_from_chain.
    d["t2_traded"] = (d["t1_bounds"] & (d["c_trade_date"] == ASOF)
                      & (d["p_trade_date"] == ASOF))
    # Fresh = both option legs and the stock all quoted within five minutes of
    # the close this snapshot represents.
    fresh = (d["c_age_s"].between(-60, 300)
             & d["p_age_s"].between(-60, 300)
             & d["stock_age_s"].between(-60, 300))
    d["t3_fresh"] = d["t2_traded"] & fresh
    # A conversion hits the call bid and lifts the put ask, so those are the
    # two sizes that have to be there.
    d["t4_size"] = d["t3_fresh"] & (d["c_bid_sz"] >= 10) & (d["p_ask_sz"] >= 10)
    d["t5_spread"] = (d["t4_size"] & (d["c_rel_spread"] < 0.25)
                      & (d["p_rel_spread"] < 0.25))
    return d


def pct(x) -> str:
    return f"{100 * x:6.2f}%"


def main() -> None:
    d = add_tiers(load_all())
    d = d[(d["dte"] >= 1) & (d["dte"] <= 400)].copy()
    print("=" * 76)
    print("UNIVERSE")
    print("=" * 76)
    for g, part in d.groupby("group"):
        print(f"  {g:<8} {part['symbol'].nunique():>4} underlyings  "
              f"{len(part):>7} call/put pairs quoted on both sides")

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 76)
    print("CALIBRATION -- a conversion is a loan; on liquid names it must")
    print(f"price near matched-maturity Treasury ({pct(RF)} at 3m) or the")
    print("arithmetic is wrong")
    print("=" * 76)
    ctl = d[(d["group"] == "control") & d["t5_spread"]
            & d["dte"].between(20, 120) & d["moneyness"].between(0.9, 1.1)]
    print(f"  {'symbol':<8}{'n':>5}{'median conv rate':>20}"
          f"{'median rev rate':>18}")
    for sym, part in ctl.groupby("symbol"):
        if len(part) < 5:
            continue
        print(f"  {sym:<8}{len(part):>5}{pct(part['conv_rate'].median()):>20}"
              f"{pct(part['rev_rate'].median()):>18}")
    print(f"\n  pooled control: conv {pct(ctl['conv_rate'].median())}   "
          f"rev {pct(ctl['rev_rate'].median())}   (risk-free {pct(RF)})")
    print("  conv < rf < rev is the correct ordering: you lend below the")
    print("  risk-free rate and borrow above it. The gap is the spread.")

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 76)
    print("EXECUTABILITY FUNNEL -- filters are on tradability only,")
    print("never on the size of the edge")
    print("=" * 76)
    tiers = [("0. both sides quoted", None),
             ("1. + no bound violation", "t1_bounds"),
             ("2. + both legs traded today", "t2_traded"),
             ("3. + all quotes fresh at close", "t3_fresh"),
             ("4. + >=10 contracts at the touch", "t4_size"),
             ("5. + both spreads < 25% of mid", "t5_spread")]
    for g in ("control", "target"):
        gd = d[d["group"] == g]
        print(f"\n  {g}")
        print(f"    {'tier':<36}{'pairs':>7}{'conv>0':>13}{'rev>0':>13}"
              f"{'med conv edge':>16}")
        for label, col in tiers:
            part = gd if col is None else gd[gd[col]]
            if part.empty:
                print(f"    {label:<36}{0:>7}")
                continue
            cw = int((part["conv_edge"] > 0).sum())
            rw = int((part["rev_edge"] > 0).sum())
            print(f"    {label:<36}{len(part):>7}"
                  f"{cw:>7}{cw / len(part):>6.1%}"
                  f"{rw:>7}{rw / len(part):>6.1%}"
                  f"{part['conv_edge'].median():>+16.3f}")

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 76)
    print("WHAT THE NAIVE SIGNAL WAS MADE OF")
    print("=" * 76)
    naive = d[d["conv_edge"] > 0]

    def row(label, col, fmt="{:>8.2f}"):
        print(f"    {label:<34}" + fmt.format(naive[col].median())
              + "   (all pairs: " + fmt.format(d[col].median()).strip() + ")")

    print(f"  {len(naive)} pairs show a profitable conversion before filters")
    row("median days since call traded", "c_trade_age_d")
    row("median days since put traded", "p_trade_age_d")
    row("call quote mid minus last print", "c_quote_vs_last")
    row("median moneyness K/S", "moneyness")
    stale_c = (naive["c_trade_date"] != ASOF).mean()
    stale_a = (d["c_trade_date"] != ASOF).mean()
    print(f"    {'call has not traded today':<34}{stale_c:>8.1%}"
          f"   (all pairs: {stale_a:.1%})")
    sz = naive.groupby(["c_bid_sz", "c_ask_sz"]).size().nlargest(3)
    print(f"    most common call (bid x ask) size: "
          + ", ".join(f"{int(a)}x{int(b)} ({n})" for (a, b), n in sz.items()))

    surv = d[d["t5_spread"] & (d["conv_edge"] > 0)]
    print(f"\n  surviving all five executability tiers: {len(surv)}")
    if len(surv):
        cols = ["symbol", "expiry", "dte", "strike", "spot", "c_bid", "c_ask",
                "p_bid", "p_ask", "div_pv", "conv_edge", "conv_rate", "rf"]
        print(surv.nlargest(15, "conv_edge")[cols].to_string(index=False))

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 76)
    print("WHY -- the option spread you must cross, by option price")
    print("=" * 76)
    tgt = d[d["group"] == "target"]
    px = pd.concat([
        pd.DataFrame({"price": tgt["c_mid"], "rel": tgt["c_rel_spread"],
                      "abs": tgt["c_spread"]}),
        pd.DataFrame({"price": tgt["p_mid"], "rel": tgt["p_rel_spread"],
                      "abs": tgt["p_spread"]})])
    bins = [0, 0.25, 0.50, 1.00, 2.00, 5.00, 1e9]
    labs = ["<$0.25", "$0.25-0.50", "$0.50-1.00", "$1-2", "$2-5", ">$5"]
    px["bucket"] = pd.cut(px["price"], bins=bins, labels=labs)
    print(f"  {'option mid':<13}{'n':>8}{'median spread':>16}"
          f"{'as % of mid':>14}{'p90 % of mid':>14}")
    for lab in labs:
        part = px[px["bucket"] == lab]
        if part.empty:
            continue
        print(f"  {lab:<13}{len(part):>8}"
              f"{'$' + format(part['abs'].median(), '.2f'):>16}"
              f"{pct(part['rel'].median()):>14}"
              f"{pct(part['rel'].quantile(0.90)):>14}")
    sub1 = px[px["price"] < 1.00]
    print("\n  Options under $1.00 -- the ones this strategy asked for:")
    print(f"    {len(sub1)} quotes, median spread {pct(sub1['rel'].median())}"
          f" of mid, p90 {pct(sub1['rel'].quantile(0.90))} of mid")

    os.makedirs(os.path.join(ROOT, "artifacts"), exist_ok=True)
    d.to_parquet(os.path.join(ROOT, "artifacts", "scan.parquet"))
    print(f"\n  wrote artifacts/scan.parquet  ({len(d)} rows)")


if __name__ == "__main__":
    main()
