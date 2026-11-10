# Put-Call Parity Arbitrage Study

The question I started with was simple: if you buy a cheap small-cap stock and hedge it with options, can you set it up so you make money no matter what happens? It turns out there is exactly one structure that pays the same in every outcome, and checking whether it is profitable is the same thing as checking for a put-call parity violation after costs. So I scanned 40,733 option pairs to find out.

The short answer is no. Across 19,593 small-cap call/put pairs the structure pays a median of -$0.49 per share. Twenty pairs priced positive, and only three survived both a no-dividend check and one cent of slippage, paying $0.43, $0.37 and $0.10 per contract before assignment fees, on about $1,300 of capital each.

Research code. It places no orders and none of this is financial advice.

## Why it comes down to put-call parity

"Profit no matter what" means the payoff has to be the same in every state of the world. For long stock plus options at one strike `K` and expiry `T`, there is only one way to do that, the conversion:

```
long 100 shares  +  long 1 put (K, T)  +  short 1 call (K, T)
```

Above `K` the short call gets assigned, and below `K` the put gets exercised, so either way the shares leave at `K`. You pay `S + P - C` today and receive `K` at expiry, which makes it a loan rather than a trade. It only beats cash when

```
K * e^(-rT) + PV(dividends)  >  S_ask + P_ask - C_bid + fees
```

which is a put-call parity violation net of costs. There is also a neat consequence: since call bids fall as the strike rises, the cost above is lowest when the short call sits at the same strike as the put. So the hedge with the highest guaranteed floor is the one that keeps zero upside. Every dollar of upside you keep lowers the floor (measured further down).

## Data

| | |
|---|---|
| Snapshot | 2026-09-24 close, option quotes stamped 15:59:59 ET |
| Underlyings | 294 small-cap / low-priced stocks, plus 12 large-cap controls |
| Pairs | 40,733 (expiry, strike) pairs with both a call and a put quoted |
| Quotes | Alpaca's free `indicative` options feed (not the full OPRA NBBO) |
| Rates | US Treasury daily curve, 4.01% at 1 month to 4.51% at 1 year |
| Universe | optionable NASDAQ/NYSE/AMEX stocks priced $0.50 to $20 with at least $200k a day of volume, ETFs removed |

The control group is there on purpose. A scanner that finds free money in illiquid names and none in liquid ones is measuring liquidity, not arbitrage.

## Does the arithmetic work? (calibration)

Since a conversion is a loan, on liquid names it has to price close to the matched-maturity Treasury rate, otherwise something in the math is wrong:

```
pooled control:  conversion 2.12%   reversal 6.74%   (Treasury 4.20% at 3 months)
```

Conversion below the risk-free rate, and reversal above it, is the right ordering: you lend below the risk-free rate and borrow above it, and the gap between them is the bid-ask spread. So the screen reproduces the textbook, which is what makes its negative result believable.

## The funnel

Every filter is on executability only (volume, quote age, size at the touch, spread width), never on the size of the edge. Filtering on the edge itself would be assuming the conclusion.

```
  target (small caps)
    tier                                  pairs       conv>0        rev>0   med conv edge
    0. both sides quoted                  19593    391  2.0%    394  2.0%          -0.490
    1. + no bound violation               19487    285  1.5%    394  2.0%          -0.494
    2. + both legs traded today            5050     47  0.9%     82  1.6%          -0.222
    3. + all quotes fresh at close         4891     47  1.0%     67  1.4%          -0.222
    4. + >=10 contracts at the touch       4188     29  0.7%     53  1.3%          -0.231
    5. + both spreads < 25% of mid         1488     20  1.3%     28  1.9%          -0.157
```

The 20 survivors then face two more tests. The first is a dividend bracket: each conversion is priced once assuming every projected dividend arrives and once assuming none does, because an in-the-money short call is exercised early precisely to take the dividend. The second is one tick of slippage on each option leg.

```
  group      tier-5 pairs   edge>0  +no-div  +1 tick   both
  target             1488       20       16        6      3
```

| Symbol | Expiry | Strike | Capital | Profit | After 1 tick |
|---|---|---|---|---|---|
| SOFI | 2026-11-20 | 13.0 | $1,289 | $2.43 | $0.43 |
| NOK | 2026-10-09 | 10.5 | $1,046 | $2.37 | $0.37 |
| SOFI | 2027-09-17 | 17.0 | $1,625 | $2.10 | $0.10 |

Ten cents on $1,625, held for 358 days, is smaller than a single assignment fee. That is everything left after a market-wide sweep.

The dividend payers turned out to be traps. Under early assignment CPB goes from +$21.56 to -$55.97, and AGNC from +$1.92 to -$69.37. The trades that look best are the ones that lose the most, because the apparent edge is the dividend that the call holder will exercise early to collect.

## Why there is nothing there

### 1. The spread on cheap options

The original idea specifically wanted options under $1, which turns out to be the worst place to look:

```
  option mid          n   median spread   as % of mid  p90 % of mid
  <$0.25           6860           $0.09        83.33%       163.64%
  $0.25-0.50       4298           $0.20        53.85%       153.33%
  $0.50-1.00       5415           $0.26        36.92%       117.47%
  $1-2             6546           $0.40        27.89%       106.88%
  $2-5             9174           $0.70        22.06%        95.30%
  >$5              6893           $1.14        11.91%        34.98%
```

For options under $1.00 the median spread is 56% of the mid (90th percentile 154%). So even though these options look cheap in dollar terms, you pay over half of the option's value to cross the spread, each way, on every leg.

### 2. The hedge costs more where the volatility is

An at-the-money put, as a percentage of the share price:

| Horizon | Large caps | Small caps |
|---|---|---|
| under 2 weeks | 1.01% | 3.22% |
| 2 to 5 weeks | 1.97% | 6.46% |
| 5 to 11 weeks | 2.67% | 9.43% |
| 11 to 29 weeks | 4.86% | 14.10% |
| over 29 weeks | 9.29% | 19.79% |

Small caps are the most expensive place to buy a floor, and it is for the same reason they look appealing in the first place: they are volatile, and that volatility is exactly what the put seller charges for.

### 3. The borrow is already in the price

The other direction (short stock, short put, long call) shows apparent free money 11 times more often in hard-to-borrow names: 14.7% of tier-5 pairs against 1.3% for easy-to-borrow ones. This isn't free money. It is the borrow fee, and the options already price it in. Reading off the carry the options imply for holding each stock makes this clear:

```
  implied carry, liquid near-the-money pairs
  group    borrow         n    median       p90
  control  ETB         5240    -0.55%     1.45%
  target   HTB          157     6.84%    51.68%
  target   ETB         2062    -0.71%     8.35%
```

## The collar frontier

This is the trade-off from earlier, measured on SOFI's 2026-12-18 expiry with the floor at $17:

```
      cap Kc  upside kept     cost   GUARANTEED floor   best case
       17.00         0.00    16.83             +0.003      +0.003  <- conversion
       18.00         1.00    17.27             -0.437      +0.553
       19.00         2.00    17.54             -0.707      +1.273
       20.00         3.00    17.82             -0.987      +1.984
       21.00         4.00    17.99             -1.157      +2.804
       22.00         5.00    18.12             -1.287      +3.664
       23.00         6.00    18.17             -1.337      +4.605
```

The floor is positive in exactly one row, the one with zero upside, paying $0.003 a share. Keeping the first dollar of upside costs $0.44 of guaranteed floor, and every dollar after that costs more floor too (between $0.05 and $0.28 each, less as the call strike moves further out). So you can lock in a guaranteed floor or keep some upside, but not both, and the one version with a positive floor earns about what a T-bill would.

## Four mistakes that each created fake profit

I'm listing these because every one of them produced an "arbitrage" that wasn't there:

1. Reading a stale `dailyBar` as today's volume. On an illiquid contract Alpaca's `dailyBar` is the last day it traded, so a SPY 2027 LEAPS showed volume from a print four months old, and the "both legs traded today" filter was letting dead contracts through. The filter now uses each trade's own timestamp.
2. A flat 13-week rate applied to 358-day LEAPS. The curve ran from 4.01% at 1 month to 4.51% at 1 year, so discounting a one-year conversion at the 3-month rate overstated its edge by roughly 44 basis points a year, which is about the same size as the whole residual edge. `curve.py` now uses the full term structure.
3. Invented dividends. The first version of the dividend projection gave STLA a $0.77 payment in April 2027 off a last ex-date in April 2025, and read RKT's three irregular specials as a 736-day "cadence". Both then topped the screen by exactly the size of the made-up dividend. Now only regular dividend histories are projected.
4. Treating mid prices as tradable. 93.7% of small-cap pairs are more than a penny away from parity at mid prices. At executable prices that drops to 2.0%, and after liquidity and slippage, to 3 out of 19,593.

Before the filters, the naive signal was made of exactly what you would expect: quotes nobody had traded near, in sizes of one.

```
    median days since call traded         1.04   (all pairs: 0.16)
    call quote mid minus last print       0.75   (all pairs: 0.01)
    call has not traded today            59.6%   (all pairs: 46.4%)
    most common call (bid x ask) size: 1x1 (361), 10x1 (348), 2x2 (219)
```

## The large-cap survivors are a loan, not an arbitrage

The control group also has 142 pairs that survive both the dividend and slippage tests. They are mostly long-dated strikes below the stock price (median 148 days to expiry, median strike at 80% of spot), and they pay a median of 0.67 percentage points a year above Treasuries. My first guess was a financing spread: a conversion is a loan, and the rate it earns on big names should be what a market maker funds at, not the T-bill rate. `financing.py` tests that.

Every pair quotes three interest rates: the rate you earn doing the conversion at executable prices (lend), the rate you pay doing the reversal (borrow), and the rate implied by mid prices. In percentage points over the matched Treasury rate, on the liquid large-cap pairs:

| Days to expiry | Pairs | Lend | Mid | Borrow | Survivors |
|---|---|---|---|---|---|
| up to 30 | 839 | -5.81 | +0.92 | +8.16 | 15 |
| 31 to 90 | 628 | -1.75 | +0.41 | +2.60 | 29 |
| 91 to 180 | 441 | -0.73 | +0.39 | +1.50 | 48 |
| 181 to 365 | 319 | -0.28 | +0.45 | +1.18 | 46 |

So the mid rate sits about 0.4 points above Treasuries at every maturity past a month, and lending is below it and borrowing above it, as it should be. The survivors pile up at the long end for a plain reason: the same few cents of bid-ask is a much smaller rate when it is spread over a year than over a week.

However, the mid rate is not the same at every strike, which a pure interest rate would be. For expiries of 90 days or more:

| Strike against spot | Mid rate, dividend payers | Mid rate, non-payers |
|---|---|---|
| below (call in the money) | +0.77 | +0.81 |
| at the money | +0.31 | +0.26 |
| above (put in the money) | -0.38 | -0.34 |

The tilt is early exercise. These options are American, and an American put is worth more than the European put that parity assumes. That extra value grows as the put goes into the money, and it makes the conversion look more expensive, so the implied rate falls as the strike rises. The pattern is the same with and without dividends, so it isn't a dividend effect.

That leaves one corner where neither leg has any early-exercise value: stocks with no dividend, at strikes below spot. An American call on a stock that pays nothing is never exercised early, and the put there is nearly worthless. In that corner the mid rate is 0.81 points over Treasuries (92 pairs), and its 22 survivors lend at +0.50, below that mid rate. So the survivors are not quotes that are wrong. They are places where crossing the spread costs less than the funding spread that is already in the price.

What it is worth: the median survivor in that corner ties up about $29,500 a contract and earns about $147 a year more than a T-bill, before assignment fees. That is a real, small return for lending cash to market makers, and it is the same trade people do with index box spreads, which are a better vehicle for it (European, cash-settled, no stock leg). It is not a profit "no matter what" in the sense the question started with, since anyone who has to borrow to fund it pays more than it earns.

(Note: this is one snapshot and nine stocks at 90 days or more, the benchmark is the Treasury curve rather than the secured overnight rate, and the stock leg is still modelled. The size of the spread should be read as roughly half a point to one point, not as 0.81 exactly.)

## Limits of this measurement

- One snapshot. A single close (2026-09-24), so intraday and day-to-day behaviour isn't measured, and spreads at the close are wider than mid-session.
- `indicative`, not OPRA. The free Alpaca feed is a derived NBBO. True OPRA quotes would be tighter, which would narrow spreads but also remove phantom edges.
- The stock leg is modelled with a 5 basis point half-spread rather than quoted, since the free equity feed is IEX-only and much wider than the consolidated NBBO.
- Borrow fees are inferred from the options, not observed directly.
- Not modelled: assignment and exercise fees, pin risk at expiry, the difficulty of hitting both touches at once, and margin. Every one of these makes the result worse, none of them better.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env      # then add your Alpaca API keys (a free account works)
python universe.py        # build and summarise the optionable low-price universe
python fetch_chains.py    # save one chain snapshot per underlying to cache/
python scan.py            # calibration, executability funnel, spread tables
python verdict.py         # dividend bracket, slippage test, hedge cost, borrow, collar frontier
python hedge_report.py    # more collar frontiers, and how often any floor is positive
python financing.py       # the large-cap survivors as lend, mid and borrow rates
python tests/test_parity.py   # no keys or network needed
```

`cache/` holds the frozen snapshot. `scan.py` and `verdict.py` are pinned to the 2026-09-24 session (`ASOF` and `CLOSE_UTC` in `scan.py`), so change those after pulling a new snapshot.

| File | What it does |
|---|---|
| `parity.py` | conversion / reversal economics at mid and at executable prices, bounds, implied carry |
| `scan.py` | loads every chain, scores it, and runs the calibration and the funnel |
| `verdict.py` | what the survivors actually pay, and why there is nothing there |
| `hedge.py`, `hedge_report.py` | the collar frontier |
| `financing.py` | implied lending and borrowing rates, by maturity and strike |
| `tests/test_parity.py` | checks on the conversion arithmetic and the dividend projection |
| `divs.py` | dividend projection (regular histories only) |
| `curve.py` | Treasury zero curve |
| `universe.py`, `fetch_chains.py`, `alpaca.py` | universe, snapshot and API client |

Tested with Python 3.13.
