# mygoldfund

Monthly allocation dashboard, four boards (gold/silver + global multi asset). Default budget is **₹5,000/month**
(`MONTHLY_BUDGET` in both `app.js` and `build_data.py`, keep them in step). Repo `vaibhavgit9210/mygoldfund`, live at
https://vaibhavkumar.is-a.dev/mygoldfund/ (Pages from `gh-pages`, so push BOTH `main` and `main:gh-pages`).

## Shape

- `index.html` + `app.js` + `instruments.js` (hand maintained constants) + `data.js` (generated, never edit)
- **Four boards:** `signal` and `safe` (gold + silver), `growth` and `offshore` (multi asset). `isMulti()` in
  app.js switches whole sections: `#metalOnly` vs `#multiOnly`, and hides the gauge and the daily cadence.
  Board copy is keyed by board name in `I.blurb[board]` and `I.method[board]`, so adding a board means adding
  those keys, not another ternary.
- `scripts/build_data.py` pulls data and computes everything. **Stdlib only, deliberately** so the Action needs no pip install.
- `scripts/sanity_check.py` gates publication. Add a check here whenever a new failure mode appears.
- `.github/workflows/daily.yml` runs **twice daily** (02:30 and 08:30 UTC), commits only on change,
  force pushes `main:gh-pages`, and opens an issue on failure. Two crons because GitHub drops scheduled
  runs under load and the job is idempotent.

## Things that will bite you

- **FRED hangs on this project's User-Agent.** It does not 403, it never answers, so the socket sits until it
  times out. That is why the `DEXINUS` FX fallback had never once actually run. Requests to `stlouisfed.org`
  now go out with `FRED_UA` (a plain curl header) instead. If a new FRED series "times out", check the header first.
- **mfapi.in returns a 502 for individual scheme codes fairly often**, and it is not always transient. Every
  sleeve therefore lists several scheme codes and `fetch_first_nav()` takes the first that answers with enough
  history. Never build a sleeve on a single hardcoded code.
- **Rounding weights independently breaks the rupee split.** Six weights rounded to 4dp summed to 1.0001, which
  turned into a split that did not add up to the budget on screen. `round_weights()` fixes the largest weight so
  the total is exactly 1, and `sanity_check.py` enforces it.
- **Annualise by measured observations per year, not 252.** The multi asset boards intersect several market
  calendars, which leaves about **229** days a year. `obs_per_year()` derives it; assuming 252 overstates vol by ~5%.
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

## Keeping the daily update alive

The repo is **public**, so GitHub disables scheduled workflows after **60 days of repository
inactivity**. The bot's daily data commit may or may not reset that timer, so do not rely on it.
The real protection is that the page renders a staleness banner once the build is more than 2 days
old (red past 7, saying do not act on the numbers). Recovery is one `workflow_dispatch` from the
Actions tab. If this ever needs to be bulletproof, the clean fix is a Cloudflare Worker cron on the
vaibhavpro9210 account calling the `workflow_dispatch` API with a PAT stored as a Worker secret.

## The model, and what is honest about it

### Growth and Offshore (multi asset)

**One rule, no knobs: equal risk contribution.** N asset ERC has no closed form, so `risk_budget_weights()`
solves it by cyclical coordinate descent (Spinu 2013), then `bound_weights()` clips to [5%, 35%]. The published
`riskShare` column must come out at 1/n; sanity_check fails the build if the free weights drift more than 2pp
apart, which is the only way to catch a non converged solver.

Returns are measured **in rupees** (USD series x USDINR) and aligned on the **intersection** of trading
calendars, never forward filled. Vol is half EWMA half full window; correlations shrunk 20% toward their mean.

**Do not re-add any of these, they were tested and rejected** (details and numbers in README):
- a 12-1 momentum tilt: hurt monotonically on Growth, sign flipped on Offshore
- a vol target reached by a risk seeking exponent: loaded 35% into Bitcoin *because* it was crashing
- capping gold for optics: Offshore drawdown went from ~19% to ~30% for no extra return

**Sleeve universes differ on purpose.** Growth has no Bitcoin (no Indian mutual fund may hold crypto);
Offshore has no Indian equity (remitting money abroad to buy India back is strictly worse than buying it here).

**The Offshore board's whole point is the flat wire fee**, not the ETFs. Monthly remittance costs ~10.5% of the
amount remitted against ~1.3% once a year, while the ETFs cost 0.03%-0.25%. If that copy ever gets softened,
the board is actively bad advice. Schedule FA (₹10 lakh penalty under the Black Money Act) is the other
non market risk that must stay loud.

### Signal and Safe (metals)

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
