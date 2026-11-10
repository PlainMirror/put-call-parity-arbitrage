"""Build the scan universe: optionable low-priced / small-cap US stocks.

Two groups are built on purpose:

* `target`  -- what the strategy asks for: cheap, small, optionable names.
* `control` -- large, liquid, tight-spread names.

The control group is not decoration. A scanner that reports "free money" in
illiquid names and none in liquid ones is measuring liquidity, not arbitrage.
Running both is the only way to tell those apart.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

import alpaca

ROOT = os.path.dirname(os.path.abspath(__file__))

CONTROL = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META",
           "GOOGL", "TSLA", "JPM", "XLF"]

# ETF-heavy venues. The strategy is about small-cap *stocks*, and ETF chains
# would dominate a price filter (many ETFs trade under $20).
STOCK_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}


def build(max_price: float = 20.0, min_price: float = 0.50,
          min_dollar_vol: float = 200_000) -> Dict[str, List[dict]]:
    assets = alpaca.assets()
    opt = [a for a in assets
           if a["tradable"]
           and "has_options" in (a.get("attributes") or [])
           and a["exchange"] in STOCK_EXCHANGES]

    snaps = alpaca.stock_snapshots([a["symbol"] for a in opt])

    rows = []
    for a in opt:
        s = snaps.get(a["symbol"]) or {}
        bar = s.get("dailyBar") or s.get("prevDailyBar") or {}
        trade = s.get("latestTrade") or {}
        px = trade.get("p") or bar.get("c")
        vol = bar.get("v") or 0
        if not px or px <= 0:
            continue
        rows.append({
            # Option quotes in this snapshot are stamped 15:59:59 ET. A stock
            # whose last print is minutes away from that is being compared to
            # options from a different moment, which manufactures parity
            # deviations out of nothing. Keep the stamp so it can be filtered.
            "trade_ts": trade.get("t"),
            "quote_ts": (s.get("latestQuote") or {}).get("t"),
            "symbol": a["symbol"],
            "name": a["name"],
            "exchange": a["exchange"],
            "price": float(px),
            "volume": float(vol),
            "dollar_vol": float(px) * float(vol),
            "shortable": bool(a["shortable"]),
            "easy_to_borrow": bool(a["easy_to_borrow"]),
            "borrow_status": a.get("borrow_status"),
            "marginable": bool(a["marginable"]),
        })

    target = [r for r in rows
              if min_price <= r["price"] <= max_price
              and r["dollar_vol"] >= min_dollar_vol]
    target.sort(key=lambda r: -r["dollar_vol"])

    ctl_snaps = alpaca.stock_snapshots(CONTROL)
    control = []
    for sym in CONTROL:
        s = ctl_snaps.get(sym) or {}
        bar = s.get("dailyBar") or s.get("prevDailyBar") or {}
        trade = s.get("latestTrade") or {}
        px = trade.get("p") or bar.get("c")
        if px:
            control.append({"symbol": sym, "name": sym, "exchange": "-",
                            "trade_ts": trade.get("t"),
                            "quote_ts": (s.get("latestQuote") or {}).get("t"),
                            "price": float(px),
                            "volume": float(bar.get("v") or 0),
                            "dollar_vol": float(px) * float(bar.get("v") or 0),
                            "shortable": True, "easy_to_borrow": True,
                            "borrow_status": "easy_to_borrow",
                            "marginable": True})

    return {"target": target, "control": control, "all_optionable": rows}


if __name__ == "__main__":
    import numpy as np

    u = build()
    rows, tgt = u["all_optionable"], u["target"]
    px = np.array([r["price"] for r in rows])
    print(f"optionable stocks (NASDAQ/NYSE/AMEX): {len(rows)}")
    print("price percentiles: " + "  ".join(
        f"p{q}={np.percentile(px, q):.2f}" for q in (1, 5, 10, 25, 50, 75, 95)))
    for hi in (1, 2, 5, 10, 20, 50):
        n = int((px <= hi).sum())
        print(f"  price <= ${hi:>2}: {n:5d} ({n / len(px):5.1%})")
    print(f"\ntarget set (price 0.50-20, dollar_vol >= 200k): {len(tgt)}")
    htb = sum(1 for r in tgt if not r["easy_to_borrow"])
    print(f"  hard to borrow: {htb} ({htb / max(len(tgt), 1):.1%})")
    print(f"  not shortable : {sum(1 for r in tgt if not r['shortable'])}")
    print("\n  top 15 by dollar volume:")
    for r in tgt[:15]:
        print(f"    {r['symbol']:<6} ${r['price']:>7.2f}  "
              f"${r['dollar_vol']/1e6:>8.1f}M  {r['borrow_status']}")

    with open(os.path.join(ROOT, "cache", "universe.json"), "w") as fh:
        json.dump(u, fh)
