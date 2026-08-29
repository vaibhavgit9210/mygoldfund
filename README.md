# mygoldfund

A monthly allocation dashboard. It answers one question at the start of each month:
**where does this month's money go, and through which fund.**
Two boards answer it for gold and silver, two answer it for a global multi asset mix.

Live at https://vaibhavkumar.is-a.dev/mygoldfund/

Four boards, same structure, different objective:

| Board | Universe | Objective |
|---|---|---|
| **Signal** | gold + silver | Allocate well. Risk parity anchor, gold silver ratio tilts it. Always fully invested. |
| **Safe** | gold + silver | Lose less. Minimum variance, and it parks part of the budget in a liquid fund when metal volatility runs hot. |
| **Growth** | 6 asset classes, Indian mutual funds only | Go global without leaving the country. Equal risk contribution, fully invested, no LRS and no extra tax form. |
| **Offshore** | 5 asset classes incl. Bitcoin, US listed ETFs | The same idea through a real foreign brokerage account under the Liberalised Remittance Scheme. |

The two multi asset boards deploy the **entire budget** every month and never park anything. Everything on
every board is a mutual fund: an index fund, a fund of funds, or a US listed ETF. No single stocks, no coins
held directly.

Budget modes: ₹5,000/month, ₹167/day, or any custom amount. Currency toggle for Indian landed prices or international spot. Light and dark.

## Where the numbers come from

Everything is keyless and public. No API keys, no accounts, no paid feeds.

| Data | Source | Why this one |
|---|---|---|
| Gold and silver prices | [LBMA benchmark fixings](https://prices.lbma.org.uk/), daily since 1968 | The settlement price the global bullion market actually uses |
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

Screenshot hooks: `#shot=signal|safe|growth|offshore`, `&theme=light|dark`, `&mode=month|day`.
(The daily cadence is hidden on the multi asset boards: six sleeves cannot be bought daily at a sane ticket size.)
