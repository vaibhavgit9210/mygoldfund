# mygoldfund

Monthly gold/silver allocation dashboard. Repo `vaibhavgit9210/mygoldfund`, live at
https://vaibhavkumar.is-a.dev/mygoldfund/ (Pages from `gh-pages`, so push BOTH `main` and `main:gh-pages`).

## Shape

- `index.html` + `app.js` + `instruments.js` (hand maintained constants) + `data.js` (generated, never edit)
- `scripts/build_data.py` pulls data and computes everything. **Stdlib only, deliberately** so the Action needs no pip install.
- `scripts/sanity_check.py` gates publication. Add a check here whenever a new failure mode appears.
- `.github/workflows/daily.yml` reruns at 02:30 UTC, commits only on change, force pushes `main:gh-pages`.

## Things that will bite you

- **AMFI `NAVAll.txt` field layout is `code;isin1;isin2;name;plan;option;nav;date`. NAV is index 6, not 5.**
  Using index 5 silently yields zero matches because `float("Growth")` throws and the row is skipped.
- **Import duty is 15%, not 6%** (BCD 10% + AIDC 5%, from 13 May 2026). Getting this wrong understates the
  Indian price by ~8.5%. A cut back to 6% was under discussion in Aug 2026, so re-check it.
- **A fund of funds costs its own TER PLUS the underlying ETF's.** Nippon Gold Savings advertises 0.06% and
  actually costs 0.87% because it feeds GOLDBEES at 0.81%. Always quote the all in number.
- **Combined gold+silver FoFs must be excluded from single leg selection.** They hold both metals at a fixed
  internal split, so using one as the "silver leg" quietly buys gold twice. See `candidates()` in app.js.
- Stooq is behind a JS proof of work challenge now, unusable. LBMA + FRED + frankfurter + mfapi all work keyless.
- The analytics beacon's `setInterval` will hang headless Chrome unless you pass `--timeout`. It early returns
  on `file:`, so local screenshots are fine.

## The model, and what is honest about it

Signal board: ERC anchor (67% gold, and for N=2 that is a formula not an optimiser) tilted by the GSR's
30 year percentile, capped ±25pp, clamped [40%, 90%].

Safe board: two asset min variance on EWMA covariance, bounded [10%, 90%], then vol targeting at 15%.

**Min variance is degenerate here.** Silver's vol is ~2x gold's so it returns >100% gold every single day.
The 90/10 is the floor, not a result. The UI says so explicitly and it should keep saying so.

**Vol targeting is the one piece that earns its keep**: median worst drawdown 17.3% to 10.8% across 552 rolling
ten year SIPs, and it held in every decade. It is not CPPI, it never sells, it only directs new contributions,
so the usual "vol targeting sells the bottom" critique does not apply.

The GSR tilt is worth about **+1% median over a decade**. Do not oversell it. Instrument choice matters far more:
PhonePe digital gold costs 3-8% up front against ~0.45%/yr for the cheapest fund of funds.

## Backtest scratch

Analysis scripts live in the session scratchpad, not the repo. If you need to redo them, the LBMA JSON
(`prices.lbma.org.uk/json/gold_pm.json`, `silver.json`) goes back to 1968 and is the source of truth.
