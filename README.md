# mygoldfund

A monthly allocation dashboard. It answers one question at the start of each month:
**where does this month's money go, and through which fund.**
Two boards answer it for gold and silver, two answer it for a global multi asset mix.

Live at https://vaibhavkumar.is-a.dev/mygoldfund/

Five boards, same structure, different objective:

| Board | Universe | Objective |
|---|---|---|
| **Signal** | gold + silver | Allocate well. Risk parity anchor, gold silver ratio tilts it. Always fully invested. |
| **Safe** | gold + silver | Lose less. Minimum variance, and it parks part of the budget in a liquid fund when metal volatility runs hot. |
| **Growth** | 6 asset classes, Indian mutual funds only | Go global without leaving the country. Equal risk contribution, fully invested, no LRS and no extra tax form. |
| **Offshore** | 5 asset classes incl. Bitcoin, US listed ETFs | The same idea through a real foreign brokerage account under the Liberalised Remittance Scheme. |
| **US** | 16 US ETFs + 40 single stocks, fractional via INDmoney | The deliberately high risk board. $30 + $20 a month, every daily forecast recorded and scored in public, recommendations only after a seven day recording period. See [The US board](#the-us-board) below. |

The two multi asset boards deploy the **entire budget** every month and never park anything. Everything on
the first four boards is a mutual fund: an index fund, a fund of funds, or a US listed ETF. No single stocks, no coins
held directly. The US board is the exception and says so on every screen.

Budget modes: ₹5,000/month, ₹167/day, or any custom amount. Currency toggle for Indian landed prices or international spot. Light and dark.

## Where the numbers come from

Everything is keyless and public. No API keys, no accounts, no paid feeds.

| Data | Source | Why this one |
|---|---|---|
| Gold and silver prices | [LBMA benchmark fixings](https://prices.lbma.org.uk/), daily since 1968 | The settlement price the global bullion market actually uses |
| Gold and silver, after LBMA blocked us | `history/*.csv` in this repo, extended daily from the keyless [fawazahmed0 currency-api](https://github.com/fawazahmed0/exchange-api) spot snapshots | LBMA's firewall has 403'd every scripted request since 30 Sep 2026. The LBMA history (gold to 25 Sep 2026, silver to 7 Apr 2025, from the Wayback Machine) is frozen in the repo; spot sits within ~0.5% of the fix. If LBMA answers again it overwrites the spot rows |
| USD/INR | ECB via `api.frankfurter.dev`, FRED `DEXINUS` as fallback | Official reference rates |
| Live spot | `api.gold-api.com` | Intraday colour only, signals run off the LBMA fix |
| Indian fund NAVs | AMFI `NAVAll.txt` | The official daily NAV file, 82 gold and silver schemes |
| NAV history | `api.mfapi.in` | The India local premium, and the Indian, emerging market and gold sleeves |
| S&P 500, Nasdaq 100 | FRED `SP500`, `NASDAQ100` | Daily index levels, keyless CSV |
| Bitcoin | `api.blockchain.info` market price chart | Daily BTC/USD back to 2010, keyless |

`scripts/build_data.py` pulls all of it, computes every indicator, and writes `data.js`. It is **stdlib only on purpose**, so the GitHub Action needs no `pip install` and cannot break on a dependency.

## The maths

Measured from 14,650 daily LBMA observations, 1968 to 2026. All of this is reproducible from `scripts/` and the analysis notes below.

**Neutral split, both boards start here.** Equal risk contribution: `w_gold = (1/σ_gold) / (1/σ_gold + 1/σ_silver)`. For two assets this is exactly the inverse volatility weight and is provably independent of correlation (Maillard, Roncalli, Teiletche 2010). It lands at **67% gold** and barely moves whether you measure over 10, 20 or 58 years, because silver has been about twice as volatile as gold throughout.

**Signal board tilt.** The current gold silver ratio is percentile ranked against its own last 30 years; the gold weight shifts by up to 25 points toward whichever metal is historically cheap, clamped to [40%, 90%]. Percentile rank rather than a z score because the series is visibly non normal (58 year range: 14 to 123).

**Safe board.** Two asset minimum variance, `w_gold = (σ_s² − ρσ_gσ_s) / (σ_g² + σ_s² − 2ρσ_gσ_s)`, on EWMA covariance (RiskMetrics λ = 0.94). Then volatility targeting: deploy `min(1, 15% / portfolio vol)` of the budget into metal, park the rest.

**India local premium.** An Indian metal ETF's NAV is (grams per unit) × (domestic price). Grams per unit is unknown but constant, so `NAV / import parity`, normalised by its own median, isolates how far the Indian price sits from parity. Computed at two fund houses per metal as a cross check. This is what flags a local squeeze like October 2025, when Indian silver ETFs ran 5% to 12% rich and five AMCs froze lumpsum subscriptions.

### The multi asset boards

**One rule: equal risk contribution.** Weights are chosen so every sleeve is responsible for the same share of
portfolio variance. With two assets that is a closed form, which is what the Signal board uses. With five or six
it is not, so it is solved numerically by cyclical coordinate descent on the convex log barrier problem
(Spinu 2013). The published risk share column comes out at exactly 1/n, which is the only proof the solver worked.

**Risk is measured in rupees.** The dollar leg of a US index fund is part of an Indian investor's risk, not a
footnote to it, so every series is converted to INR before anything is estimated.

**No forward filling.** Sleeves are aligned only on days when *every* market in the mix was open, about 229 a
year rather than 252. The annualisation factor is measured from that, not assumed. A holiday in one country
never invents a zero return in another.

**The covariance is deliberately blunted.** Volatility is half EWMA (λ = 0.94) and half full window, because
pure EWMA has an 11 day half life, which is far too twitchy for a mix set once a month: it read the Nifty 50 at
8% during a calm stretch against a realistic 13%. Correlations are shrunk 20% toward their average (Ledoit and
Wolf 2003).

#### Three things I tried and did not ship

**A momentum tilt.** Ranking sleeves by 12 month return excluding the last month and shifting the risk budget
toward the winners. Walked forward at four tilt strengths, it **hurt monotonically** on the Growth board and on
Offshore bought about 2.5% of terminal value for about 3 points more drawdown. A signal whose sign flips between
two overlapping universes is noise. There is no tilt and no market timing on these boards at all.

**A volatility target reached by concentration.** Bisecting a risk seeking exponent to hit a higher vol target
sounds principled and is not: because Bitcoin was both the most volatile sleeve and the worst performer, it
loaded 35% into the asset that was crashing, *because* it was crashing. Volatility seeking is not return seeking.

**Capping gold for optics.** Tested at 25%, 15%, 10% and zero. Removing gold from the Offshore board left the
return essentially unchanged and pushed the worst drawdown from about 19% to about 30%. Gold stays uncapped as
one sleeve of five or six, which is what "not limited to gold" actually means here.

### What the backtests actually showed

Walk forward, no lookahead, over overlapping ten year monthly SIPs.

| Question | Answer |
|---|---|
| Does the GSR tilt beat a fixed 67/33? | Yes, in 84% of 219 windows, but the **median gain is only about 1%** on final value over a decade |
| Does holding 100% gold beat 67/33? | Also yes, in about two thirds of windows, median +3%, **and with lower drawdown** |
| Is the ratio mean reverting? | Weakly. ADF t = −3.05 over 58 years, clears 5% but not 1%, half life ~2.5 years. **Not stationary in 1975-2000 or 2010-2026** |
| Does volatility targeting help? | Yes, clearly. Median worst drawdown **17.3% → 10.8%**, p95 **62% → 42%**, with terminal value slightly ahead. Held in every decade tested |
| Daily ₹167 vs monthly ₹5,000? | No material difference. Indian evidence: Nifty 500 TRI 1999-2020, daily 12.13% XIRR vs monthly 12.15% |
| Does equal risk contribution beat holding one thing? | **Not on return, clearly on drawdown.** On both multi asset boards its worst peak to trough fall was smaller than every single sleeve held alone. On both, one sleeve (gold on Growth, Bitcoin on Offshore) beat the mix on final value with roughly double to quadruple the drawdown |

The honest summary: the **instrument choice matters more than the allocation model**. Routing ₹5,000 through PhonePe digital gold instead of a cheap fund of funds costs 3% to 8% up front, which is several times larger than anything the split can earn back.

The offshore board is the same lesson at a larger scale. The outward remittance fee is flat, roughly ₹500 a
wire, so sending ₹5,000 every month costs about **10.5%** of everything remitted while sending the same year's
money in one wire costs about **1.3%**. The ETFs themselves cost 0.03% to 0.25% a year. The remittance schedule
is between forty and a hundred times more consequential than which fund you pick, which is why that board tells
you to keep the habit in rupees and remit once a year.

## The US board

Added 13 October 2026. A $30 a month ETF sleeve and a $20 a month single stock sleeve, bought in fractional
dollars through INDmoney's GIFT City route. The page loads twice every trading day, records what it sees, predicts
the next day for every ticker, scores yesterday's prediction, and keeps every row forever in two places. It is the
one board that holds single stocks and leveraged funds, and it is built so that the record, not the author,
decides whether the engine deserves trust.

**Universe.** 16 ETFs (VOO QQQM IWM SMH XBI ARKK URA ICLN TAN AIQ QTUM KWEB GDXJ, plus the 3x funds TQQQ SOXL
UPRO) and 40 stocks (semis, AI, crypto adjacent, space, quantum, nuclear, software, consumer), with SPY and QQQ
as benchmarks and the VIX family plus the 13 week bill as context. IBIT and WGMI are out because INDmoney blocks
them under IFSCA's crypto exclusion (August 2025 circular), so the only crypto exposure is COIN, MSTR and the
miners, which may be withdrawn too; those names carry a "confirm in app" tag. Everything in `scripts/us_universe.py`
with the reason for every exclusion.

**Two runs a day** (`.github/workflows/us.yml`, scheduled in New York time so the clock survives the DST change):

| Run | Time | What it records | What it predicts |
|---|---|---|---|
| pre market | 08:35 ET | pre market print and time per ticker, VIX, VIX3M, VIX9D, bill rate | the day's close, by adjusting the overnight forecast for the gap that has already opened |
| post close | 16:25 ET | the day's bar, post market print, the scores of yesterday's forecasts | tomorrow's close as a full density: mean, sigma, probability up, 90% and 50% bands |

**Where the record lives.** `us/ledger.ndjson` in this repo is the source of truth (append only, one JSON row per
line, readers take the last row per natural key). The same rows are POSTed to a Cloudflare Worker with a SQLite
Durable Object (`ledger/`, `mygoldfund-ledger`) which serves them publicly: `GET /summary`, `GET /ledger?kind=score&from=&to=&sym=`,
`GET /latest?kind=rec`. The page reads the Worker for its long history charts and falls back to the committed
snapshot when it cannot. `us/state.json` carries the learner state (fitted parameters, expert weights, conformal
alphas) between runs. Row kinds: `run`, `reading`, `forecast`, `score`, `rec`, `fan`, `paper`.

**The forecast is a committee, and the committee is scored.** Three variance experts (RiskMetrics EWMA,
GJR GARCH refit weekly by Nelder-Mead, HAR on the Yang-Zhang range variance) are combined by Hedge on the QLIKE
loss. Five density experts (random walk with the long run drift, GJR with zero mean, HAR density, a trend regime
density, a two state HMM mixture) are combined by Hedge on CRPS. Hedge weights are shrunk toward equal by
`min(0.5, t/250)` so the engine never trusts one model more than 50/50 in its first year. The 90% and 50% bands
are Adaptive Conformal Inference on the model's own past errors, which is what keeps coverage honest when the
Gaussian shape is wrong. Every forecast is scored with hit rate, Brier, log score, CRPS, pinball, coverage, PIT and
QLIKE against two baselines (random walk with drift, climatology), with Diebold-Mariano t statistics on daily cross
sectional means (58 tickers move together, pooling them inflates t by about seven), a Wilson interval on the hit
rate, and the Bailey and Lopez de Prado minimum track record length. All stdlib Python in `scripts/us_models.py`,
133 checks in `scripts/test_us_models.py`.

**The recommendation engine does not use the daily forecasts.** Seven days cannot validate a direction call (the
interval on a seven day hit rate is plus or minus 37 points), so the monthly recommendation runs on rules with
long published records instead: a 200 day and 10 month trend filter, 12-1 momentum ranked per unit of volatility,
the top three ETFs and the top three stocks (a 52 week high bonus and an earnings skip on the stock side), inverse
volatility weights scaled to a sleeve vol target (25% for ETFs, 50% for stocks), at most one 3x fund capped at
$10 and gated on its underlying index's trend, vol regime and the VIX term structure, and a deploy fraction that
halves the month's money when SPY is below trend, VIX is in backwardation or realised vol is in its top 30%.
Unspent dollars are held in the INDmoney cash balance, never forced into the top pick. Quarter Kelly was in the
first design and was dropped: at a 10% long run mean on 60% vol names it caps every line under the $3 minimum
order and the stock sleeve holds cash every month.

**The gate.** The engine runs in the shadows from day one and a paper book marks its picks to market daily against
$15 VOO + $15 QQQM and $20 SPY. Order lines appear on the page from day 8 and only while the integrity checks
pass: both runs on at least 8 of the last 10 trading days, today's run not degraded (fewer than five symbols from
cache, a working quote call), at least 100 scored forecasts, and the 90% band covering between 80% and 97% of the
last 60 scored days. Direction skill is displayed on the scorecard and never gates anything, because it cannot
be established in a week, or, as the backtest below shows, in three years.

### What the US backtest showed

Walk forward, cold start, no lookahead, on the repo's own functions. Scripts live in the session scratchpad, the
numbers are here.

**Daily density forecasts** (22 tickers, 2024-01-02 to 2026-10-12, 15,334 forecasts):

| Question | Answer |
|---|---|
| Does it call direction? | **No.** Hit rate 52.0% (Wilson 95% 51.3% to 52.8%), but "always up" scores 52.5% on the same days. Brier skill vs climatology is -0.16%, DM t 0.67. |
| Does it forecast volatility? | **Yes.** GJR GARCH beats EWMA on QLIKE by 3.0% pooled (DM t -3.33), 6.5% on SPY and QQQ. HAR adds nothing (0.7%, t -0.97). |
| Is the density better than a random walk with drift? | By a hair: CRPS skill +0.16% (DM t -2.42), log score +0.024 nats (DM t -4.51). Real, not usable. |
| Are the bands calibrated? | Yes: the 90% band covered 90.1% and the 50% band 50.1%, within a point on all 22 tickers. |
| Is the Gaussian shape right? | No: the PIT histogram is centre heavy (8% in each outer decile, 12% in the middle ones, chi2 328 on 9 df). Fat tails seen through a Gaussian; conformal repairs the two levels shown. |

**Monthly engine as shipped** (Jan 2021 to Oct 2026, 70 issues, $50 a month, fills at the next open, held cash
never redeployed):

| Strategy | Value per $ | IRR | TWR CAGR | Max drawdown |
|---|---|---|---|---|
| Baseline, $15 VOO + $15 QQQM + $20 SPY | 1.717 | +18.7% | +15.9% | -27% |
| Engine, both sleeves | **1.665** | +17.7% | +11.8% | **-41%** |
| ETF sleeve alone: engine vs VOO+QQQM | 1.590 vs 1.762 | | | -30% vs -30% |
| Stock sleeve alone: engine vs SPY | 1.778 vs 1.648 | | | -58% vs -24% |
| Equal weight over the same 56 names | 2.565 | +33.0% | +23.5% | -49% |

**The honest reading: the rules did not beat buy and hold on this universe, and they took far more drawdown to
lose.** The stock sleeve's small edge over SPY is survivorship, not skill: the 40 names were chosen in October 2026
knowing the winners, and naive equal weight across them made 3.77 per dollar, far above the engine's 1.78. Momentum
did what momentum does in a bubble, buying OKLO and QBTS after they went up five times. The board ships anyway
because the request was to record and test, the paper book runs live against the baseline from day one, and the
scorecard will say in public how long it takes before any apparent edge means something. Expect that number to
be in the thousands of days.

**Costs on the INDmoney route** (shown on every order line): brokerage 0.25% plus 18% GST on it, FX markup 0.5%
to 1.2% on each rupee to dollar transfer, no flat wire fee and no withdrawal fee, so monthly buying is viable
here in a way it is not on the Offshore board. Fund one transfer a month, not one per order. US dividends are
withheld at 25%, gains are at slab rate under 24 months and 12.5% after, Schedule FA applies from the first dollar.

### Running the US board

```bash
python3 scripts/test_us_models.py              # 133 checks on the maths
python3 scripts/us_build.py --session auto     # pre/post from the New York clock, exits 0 when closed
python3 scripts/us_build.py --session post --force   # redo a session
python3 scripts/us_build.py --offline          # cached history only, marks the run degraded
python3 scripts/us_build.py --rebuild-usjs     # regenerate us.js from the ledger
python3 scripts/us_sanity.py                   # refuse to publish a broken us.js
cd ledger && npx wrangler deploy               # the Worker (secrets: LEDGER_KEY, optional GH_PAT)
```

The Action needs one repository secret, `LEDGER_KEY`, equal to the Worker secret of the same name; without it the
run still commits the ledger and only the Worker mirror goes stale (the next run with a key resends the last three
days). The Worker's own cron fires a few minutes after each Action slot and, if a `GH_PAT` secret exists, dispatches
`us.yml`, which is the belt and braces against GitHub dropping a schedule. Screenshot hook: `#shot=us`.

## Honest limitations

- Minimum variance is **degenerate** on this pair. With silver at twice gold's volatility it returns the same corner solution every day: all gold. The 90/10 you see on the Safe board is the **10% floor I chose**, not a discovery. Stated in the UI too.
- Equal risk contribution at two assets is a formula, not an optimisation. It is an honest way to pick a number, not machine learning.
- The "above 80 buy silver, below 50 buy gold" rule has **no primary institutional source**. It traces to bullion dealers, not to the World Gold Council, LBMA or CME. That is why this uses percentile ranks instead.
- One or two years is far too short to judge any of this. The effects measured are a few percent over a decade.
- The multi asset boards can only be measured back to the point where every sleeve existed: about **7 years**
  for Growth and **10** for Offshore, against 58 for the metal boards. That window contained a historic run in
  both US technology and gold. Treat it as one regime, not as evidence.
- **Schedule FA is the real risk on the Offshore board**, and it is not a market risk. Holding any foreign asset
  means disclosing it in your return every year at any size; non disclosure sits under the Black Money Act where
  the penalty is ₹10 lakh, on a holding that might be worth ₹60,000.
- Three Growth sleeves invest abroad and share **one SEBI ceiling** that has been full since February 2022.
  Fresh subscriptions can be suspended with a few days' notice, so an instalment can simply bounce.
- Not investment advice. I am not a registered adviser.

## Stale risk register

Re-check these; they move and they change the answer.

| Constant | Value | Risk |
|---|---|---|
| Import duty | **15%** (BCD 10% + AIDC 5%), raised 13 May 2026 | **Highest.** A cut back to 6% was under discussion in late Aug 2026. If it happens the Indian price drops ~8% overnight |
| GST on bullion | 3% | Unchanged in GST 2.0 (56th Council) |
| Fund TERs | `instruments.js`, checked 27 Aug 2026 | AMFI republishes daily; FoF costs are FoF fee **plus** the underlying ETF fee |
| Groww charges | max(₹5, min(₹20, 0.1%)) + ₹23.60 DP on sell | Broker pricing changes |
| LTCG | ETF 12 months, FoF 24 months, both 12.5% | Income-tax Act 2025. Gold and silver funds escaped the s.76 specified-mutual-fund net |
| SGB | Not usable | Finance Act 2026 narrowed the maturity exemption to **original subscribers only** from 1 Apr 2026, so secondary buyers lose it |
| PhonePe spread | Unverified | PhonePe publishes no spread anywhere. The 3% to 8% range is from secondary sources |
| Sleeve fund TERs | `instruments.js` `sleeveFunds` | **Approximate, verify before acting.** Index fund and feeder TERs move, and a feeder costs its own fee plus the underlying fund's |
| LRS TCS threshold | ₹10 lakh a financial year | Raised from ₹7 lakh in the 2025 Budget. Irrelevant at ₹60,000 a year but it is a live number |
| Wire fee and FX markup | ₹500 flat + 0.5% | Broker and bank dependent; a bank is 1% to 2%. This constant drives the whole Offshore cost argument |
| SEBI overseas cap | ~$7bn, full since Feb 2022 | Reopens and closes without much notice |

## Running it

```bash
python3 scripts/build_data.py     # rebuild data.js
python3 scripts/sanity_check.py   # refuse to publish broken data
open index.html
```

## Does it really update daily?

Yes, and it is built to fail loudly rather than quietly.

The Action runs **twice a day**, at 02:30 and 08:30 UTC (08:00 and 14:00 IST). It rebuilds `data.js`,
runs `sanity_check.py`, commits only if something changed, and force pushes to `gh-pages`. Two runs
because GitHub delays and sometimes silently drops scheduled jobs under load; the job is idempotent,
so the second attempt costs nothing.

Three safety nets, because a dashboard you act on with money must never present stale numbers as current:

1. **`sanity_check.py` refuses to publish nonsense.** It rejects implausible prices, weights outside
   their own bounds, an India parity figure that does not reconcile against spot and FX, and any LBMA
   fix more than 7 days old. A failed check means the site keeps the last good data rather than
   publishing garbage.
2. **The page declares its own age.** More than 2 days since the last successful rebuild and a banner
   appears at the top. More than 7 days and it turns red and says *do not act on these numbers*.
3. **A failed run opens a GitHub issue**, which lands in your email.

**The one thing to know:** this is a public repository, and GitHub **automatically disables scheduled
workflows after 60 days of repository inactivity**. If that happens the staleness banner will tell you.
The fix is one click: open [the Actions tab](https://github.com/vaibhavgit9210/mygoldfund/actions),
select "daily rebuild", and press "Run workflow". That re-enables the schedule.

Screenshot hooks: `#shot=signal|safe|growth|offshore|us`, `&theme=light|dark`, `&mode=month|day`.
(The daily cadence is hidden on the multi asset boards: six sleeves cannot be bought daily at a sane ticket size.)
