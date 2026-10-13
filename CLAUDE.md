# mygoldfund

Monthly allocation dashboard, five boards (gold/silver + global multi asset + the high risk US board). Default
budget is **₹5,000/month** (`MONTHLY_BUDGET` in both `app.js` and `build_data.py`, keep them in step); the US board
has its own $30 + $20. Repo `vaibhavgit9210/mygoldfund`, live at https://vaibhavkumar.is-a.dev/mygoldfund/
(Pages from `gh-pages`, so push BOTH `main` and `main:gh-pages`).

## Shape

- `index.html` + `app.js` + `instruments.js` (hand maintained constants) + `data.js` + `us.js` (both generated, never edit)
- **Five boards:** `signal` and `safe` (gold + silver), `growth` and `offshore` (multi asset), `us`. `isMulti()` in
  app.js switches whole sections: `#metalOnly` vs `#multiOnly`, and hides the gauge and the daily cadence; `isUs()`
  hides both and shows `#usOnly` (the rupee row and the split grid are hidden by `hidden` attribute rules).
  Board copy is keyed by board name in `I.blurb[board]` and `I.method[board]`, so adding a board means adding
  those keys, not another ternary.
- `scripts/build_data.py` pulls data and computes everything. **Stdlib only, deliberately** so the Action needs no pip install.
- `scripts/sanity_check.py` gates publication. Add a check here whenever a new failure mode appears.
- `.github/workflows/daily.yml` runs **twice daily** (02:30 and 08:30 UTC), commits only on change,
  force pushes `main:gh-pages`, and opens an issue on failure. Two crons because GitHub drops scheduled
  runs under load and the job is idempotent. **It `git pull --rebase`s before pushing** because the US workflow
  commits on its own schedule (they raced on 13 Oct 2026 and opened issue #1).

## The US board (added 13 Oct 2026)

- `scripts/us_universe.py` (16 ETFs, 40 stocks, `EXCLUDED` with reasons), `us_data.py` (Yahoo v8 chart + v7
  quote with cookie and crumb, Cboe VIX CSVs, Treasury bills, NYSE calendar, `session_now()`), `us_models.py`
  (pure maths, 133 checks in `test_us_models.py`), `us_build.py` (the twice daily run), `us_sanity.py` (gates
  `us.js`). `ledger/` is the Cloudflare Worker `mygoldfund-ledger` (vaibhavpro9210 account, SQLite Durable
  Object; deploy with the npx cached wrangler, `npx --no-install` refuses when a newer version exists upstream).
  The Bearer key lives in gitignored `ledger/.ledger-key` and must equal the GitHub secret `LEDGER_KEY`.
- `us/ledger.ndjson` is the source of truth (append only, last row per natural key wins, the Worker upserts
  the same keys). `us/state.json` holds learner state; a missing file silently resets every learner, a corrupt
  one exits 1 on purpose. `us/history/*.csv` is the 10 year adjclose cache (index symbols drop the caret:
  `_VIX.csv`; the Cboe files own `VIX.csv`).
- `.github/workflows/us.yml` runs at 08:35 and 16:25 ET using the schedule `timezone` key; the Worker cron fires
  a few minutes later in UTC under both DST offsets, guards on the New York clock, and only dispatches if a
  `GH_PAT` secret exists. `us_build.py` is idempotent per (date, session) and exits 0 when the market is closed,
  except that a pre cron fired late (09:30 to 16:15 ET, no pre run yet that day) still runs as the pre run.
- **Sizing decisions, do not quietly revert:** no Kelly cap (quarter Kelly at a 10% mean on 60% vol names caps
  every line under the $3 minimum, so the stock sleeve held cash every month), sleeve vol targets 25% ETF / 50%
  stock, the 3x line sized on its own vol and capped at $10 with its gates on the proxy, held cash not carried
  into next month. The backtest in README says the shipped rules **lost to buy and hold** (1.665 vs 1.717 per
  dollar, -41% vs -27% drawdown, 2021 to 2026); the board exists to record and test, not because it won.
- **Direction skill is displayed, never gates.** The gate is day 8 plus integrity (both runs on 8 of the last 10
  trading days, run not degraded, 100 scored forecasts, 90% band coverage in [80%, 97%] over the last 60 days).
- Scorecard statistics aggregate to one number per day before any test (58 tickers move together; pooled DM
  rejects a true null 53% of the time at the 5% level). `varSkill` uses the non negative QLIKE form and the log
  score skill is a geometric likelihood ratio, because `1 - model/base` on negative losses is meaningless.
- Known biases left in: the pre market gap uses raw prices, so on an ex dividend day it carries the dividend
  drop (about 0.3% on VOO, four days a year); the hit rate Wilson interval pools tickers and is optimistic by
  the same factor as the DM t would have been.

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
- **LBMA blocks all scripted access since 30 Sep 2026** (Cloudflare WAF 403 "Sorry, you have been blocked",
  from GitHub runners and home IPs alike, browser headers do not help). Metal history therefore lives in
  `history/gold_usd.csv` + `silver_usd.csv` (column `source` = `lbma` or `spot`), committed by the Action.
  Each run tries LBMA once, then extends from fawazahmed0 currency-api snapshots. **That snapshot dated D+1
  is day D's close** (lag 1 correlates 0.68 with LBMA returns, lag 0 only 0.35). The archive has holes
  (2025-12-09), so only a miss in the last 3 days stops the extension. Silver from 8 Apr 2025 is spot
  (Wayback only had LBMA silver to 7 Apr 2025); cross checked against COMEX futures, median gap 0.15%.
  `history.lbmaTo` in data.js drives the "Spot close" vs "LBMA fix" labels.
- Stooq is behind a JS proof of work challenge now, unusable. FRED + frankfurter + mfapi all work keyless.
- **Yahoo answers only `User-Agent: Mozilla/5.0`, exactly that string** (the Python default UA gets a 429 at
  once). The v7 quote endpoint needs a cookie from `fc.yahoo.com` plus a crumb from `/v1/test/getcrumb`, and
  both must come from the **same opener** (one `build_opener(HTTPCookieProcessor)` reused); a fresh opener per
  call gets "Invalid Crumb". After a real 429 the data layer sleeps 60 s once, falls back to the `curl` binary,
  then to the cache, and flags the whole process so the other 61 symbols do not each sleep a minute.
- **Yahoo's adjclose shifts by the dividend yield every ex date**, so the cache overlap check must be tight
  (`ADJ_TOL = 1e-4`; two full pulls agree to 1e-6). At 0.5% a VOO dividend splices two bases and fakes a 0.3%
  return every quarter, feeding the vol fits and the momentum rank.
- Yahoo's `^VIX` daily series carries a forming bar dated today during pre market and stale bars on some NYSE
  holidays; the builder uses the Cboe CSV for history and ignores today's bar before the close.
- The analytics beacon's `setInterval` will hang headless Chrome unless you pass `--timeout`. It early returns
  on `file:`, so local screenshots are fine. **Give every headless run its own `--user-data-dir`**: a stuck
  instance holding the default profile lock froze a screenshot batch for ten minutes.
- House CSS rule `.bar{height:38px}` beats the `height` attribute on SVG rects (CSS geometry wins on SVG2 in
  Chrome), which is why the PIT histogram bars are class `hbar`. Never reuse `.bar` inside an SVG.

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

Analysis scripts live in the session scratchpad, not the repo. If you need to redo them, `history/*.csv`
goes back to 1968 (the LBMA JSON itself is no longer downloadable, see above).
