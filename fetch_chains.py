"""Pull one option-chain snapshot per underlying and freeze it to disk.

The whole scan is an analysis of a single moment in the market. Re-pulling the
chains on every run would mix moments together and make two results
incomparable, so the snapshot is written once and stamped with the quote time
it actually carries.
"""
from __future__ import annotations

import json
import os
import sys
import time

import alpaca
import universe

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "cache", "chains")

# Leveraged/inverse ETFs list on NASDAQ and slip through a price filter. The
# strategy is about small-cap stocks, so they are tagged and kept separate.
ETF_WORDS = ("ETF", "TRUST", "DIREXION", "PROSHARES", "ISHARES", "SPDR",
             "INVESCO", "VANECK", "GRANITESHARES", "T-REX", "DEFIANCE",
             "YIELDMAX", "ROUNDHILL", "TIDAL", "FUND", "PORTFOLIO", "INDEX",
             "BULL 2X", "BEAR", "2X SHARES", "ETN", "NOTES")


def looks_like_etf(name: str) -> bool:
    n = (name or "").upper()
    return any(w in n for w in ETF_WORDS)


def main(n_target: int = 300) -> None:
    os.makedirs(OUT, exist_ok=True)
    u = universe.build()

    target = [r for r in u["target"] if not looks_like_etf(r["name"])]
    print(f"target after ETF filter: {len(target)} (was {len(u['target'])})")
    picks = target[:n_target]
    for r in picks:
        r["group"] = "target"
    for r in u["control"]:
        r["group"] = "control"
    picks = picks + u["control"]

    meta = {"symbols": [], "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for i, r in enumerate(picks, 1):
        sym = r["symbol"]
        path = os.path.join(OUT, f"{sym}.json")
        if os.path.exists(path):
            meta["symbols"].append(r)
            continue
        try:
            chain = alpaca.option_chain(sym)
        except Exception as exc:                      # noqa: BLE001
            print(f"  [{i:>3}/{len(picks)}] {sym:<6} FAILED {exc}")
            continue
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(chain, fh)
        r["n_contracts"] = len(chain)
        meta["symbols"].append(r)
        if i % 25 == 0 or i <= 3:
            print(f"  [{i:>3}/{len(picks)}] {sym:<6} {len(chain):>5} contracts")

    with open(os.path.join(ROOT, "cache", "chain_meta.json"), "w") as fh:
        json.dump(meta, fh)
    tot = sum(r.get("n_contracts", 0) for r in meta["symbols"])
    print(f"\ndone: {len(meta['symbols'])} underlyings, {tot} contracts")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
