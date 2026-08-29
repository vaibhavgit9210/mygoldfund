/* Verified cost, tax and product constants.
   Every number here was checked against a primary source on the date in `updated`.
   Re-check the ones flagged `stale` in README.md before trusting them months from now. */
window.MGF_INSTRUMENTS = {
updated: "27 August 2026",

blurb: {
  signal: "Risk parity sets the neutral split, the gold silver ratio tilts it. Fully invested every month, no market timing.",
  safe:   "Lowest variance route. Gold heavy because silver is twice as volatile, and it holds back part of the budget when metal volatility runs hot.",
  growth: "The whole budget across six asset classes, every one of them an Indian mutual fund you can start today. No foreign account, no remittance, no extra tax form.",
  offshore: "The same idea run through a real foreign brokerage account under the RBI's Liberalised Remittance Scheme, which is the only route here that can hold Bitcoin."
},

parking: { name: "any liquid or overnight fund on Groww", note: "roughly 6% a year, redeemable next day" },

/* ---- cost model ----
   ter        annual, as a fraction. For a fund of funds this is the ALL IN number,
              FoF fee plus the underlying ETF fee, because you pay both.
   entryPct   fraction of the ticket paid on the way in
   entryFlat  rupees paid on the way in
   groww      true means apply Groww's real brokerage formula: max(5, min(20, 0.1%)) + 18% GST,
              plus 0.015% stamp duty and about 0.0032% exchange and regulator fees
   exitFlat   the one off sell cost, amortised over 36 monthly instalments, since you sell
              a SIP position once, not once per instalment
   ltcgMonths months you must hold for the 12.5% long term rate instead of your slab rate  */

instruments: [
  { id:"mirae_gold_fof", name:"Mirae Asset Gold ETF Fund of Fund", venue:"Groww", metals:["gold"],
    kind:"fof", regulated:true, dailySip:true, minSip:99, minSipDaily:99,
    ter:0.0045, entryPct:0, entryFlat:0, exitFlat:0, ltcgMonths:24,
    why:"Cheapest gold route in India all in, because it feeds its own 0.35% ETF instead of an expensive one." },

  { id:"uti_gold_fof", name:"UTI Gold ETF Fund of Fund", venue:"Groww", metals:["gold"],
    kind:"fof", regulated:true, dailySip:true, minSip:25, minSipDaily:25,
    ter:0.0087, entryPct:0, entryFlat:0, exitFlat:0, ltcgMonths:24,
    why:"Lowest minimum SIP in the market at 25 rupees, but it feeds UTI's 0.59% ETF so it costs more to hold." },

  { id:"mirae_gold_etf", name:"Mirae Asset Gold ETF (GOLDETF)", venue:"Groww, demat", metals:["gold"],
    kind:"etf", regulated:true, dailySip:false, minSip:0,
    ter:0.0035, entryPct:0, groww:true, exitFlat:0.82, ltcgMonths:12,
    why:"Cheapest gold ETF to hold, and long term tax kicks in at 12 months rather than 24." },

  { id:"goldbees", name:"Nippon India ETF Gold BeES (GOLDBEES)", venue:"Groww, demat", metals:["gold"],
    kind:"etf", regulated:true, dailySip:false, minSip:0,
    ter:0.0081, entryPct:0, groww:true, exitFlat:0.82, ltcgMonths:12,
    why:"By far the most traded gold ETF, so the tightest spread, but the highest expense ratio of any gold ETF in India." },

  { id:"groww_gold_fof", name:"Groww Gold ETF FOF", venue:"Groww", metals:["gold"],
    kind:"fof", regulated:true, dailySip:true, minSip:100, minSipDaily:100,
    ter:0.0085, entryPct:0, exitFlat:0, ltcgMonths:24,
    why:"Headline FoF fee is only 0.14%, but it feeds Groww's own 0.71% ETF, so all in it is nearly double the Mirae route." },

  { id:"nippon_gold_savings", name:"Nippon India Gold Savings Fund", venue:"Groww", metals:["gold"],
    kind:"fof", regulated:true, dailySip:true, minSip:100, minSipDaily:100,
    ter:0.0087, entryPct:0, exitFlat:0, ltcgMonths:24, avoid:true, avoidWhy:"Fee trap",
    why:"Advertises 0.06%, which is the FoF layer only. It feeds GOLDBEES at 0.81%, so you actually pay 0.87%." },

  { id:"mirae_silver_fof", name:"Mirae Asset Silver ETF FOF", venue:"Groww", metals:["silver"],
    kind:"fof", regulated:true, dailySip:true, minSip:99, minSipDaily:99,
    ter:0.0059, entryPct:0, exitFlat:0, ltcgMonths:24,
    why:"Feeds Mirae's own 0.36% silver ETF. Silver funds all cost more than gold funds." },

  { id:"angel_silver_fof", name:"Angel One Silver ETF FOF", venue:"Groww", metals:["silver"],
    kind:"fof", regulated:true, dailySip:true, minSip:100, minSipDaily:100,
    ter:0.0055, entryPct:0, exitFlat:0, ltcgMonths:24,
    why:"Cheapest silver fund of fund all in on the latest AMFI expense disclosure." },

  { id:"icici_silver_etf", name:"ICICI Prudential Silver ETF (SILVERIETF)", venue:"Groww, demat", metals:["silver"],
    kind:"etf", regulated:true, dailySip:false, minSip:0,
    ter:0.0040, entryPct:0, groww:true, exitFlat:0.82, ltcgMonths:12,
    why:"Best tracking error of any Indian silver ETF at 0.56%, against 0.80% for SILVERBEES." },

  { id:"silverbees", name:"Nippon India Silver ETF (SILVERBEES)", venue:"Groww, demat", metals:["silver"],
    kind:"etf", regulated:true, dailySip:false, minSip:0,
    ter:0.0058, entryPct:0, groww:true, exitFlat:0.82, ltcgMonths:12,
    why:"The most traded silver ETF, but its tracking error is among the worst and it ran a 5% to 12% premium during the October 2025 squeeze." },

  { id:"mirae_combo", name:"Mirae Asset Gold Silver Passive FoF", venue:"Groww", metals:["gold","silver"],
    kind:"fof", regulated:true, dailySip:true, minSip:99, minSipDaily:99,
    ter:0.0047, entryPct:0, exitFlat:0, ltcgMonths:24, combo:true, comboSplit:0.56,
    why:"One SIP covering both metals at 0.47%. The catch is that its split is fixed near 56/44, so it ignores everything this dashboard computes." },

  { id:"phonepe_gold", name:"PhonePe digital gold", venue:"PhonePe", metals:["gold"],
    kind:"digital", regulated:false, dailySip:true, minSip:10, minSipDaily:10,
    ter:0, entryPct:0.055, exitPct:0.025, ltcgMonths:24, avoid:true, avoidWhy:"Unregulated",
    why:"3% GST plus a buy sell spread PhonePe does not publish. SEBI cautioned on digital gold in November 2025 and it sits outside every securities regulator." },

  { id:"phonepe_silver", name:"PhonePe digital silver", venue:"PhonePe", metals:["silver"],
    kind:"digital", regulated:false, dailySip:true, minSip:10, minSipDaily:10,
    ter:0, entryPct:0.055, exitPct:0.03, ltcgMonths:24, avoid:true, avoidWhy:"Unregulated",
    why:"Same 3% GST and an even wider spread than gold, with no regulator behind it." },

  { id:"physical", name:"Physical coin or bar from a jeweller", venue:"offline", metals:["gold","silver"],
    kind:"physical", regulated:false, dailySip:false, minSip:0,
    ter:0.0, entryPct:0.08, exitPct:0.05, ltcgMonths:24, avoid:true, avoidWhy:"Worst value",
    why:"3% GST plus a dealer premium, then a haircut when you sell it back. You also pay the Indian retail markup that fund buyers do not." }
],

/* ---- multi asset sleeves ----------------------------------------------------
   One fund per sleeve per route. Everything here is a MUTUAL FUND: an index fund, a
   fund of funds, or a US listed ETF. No single stocks, no coins held directly.

   ter is the ALL IN annual cost. For an Indian feeder or fund of funds that means its own
   fee PLUS the underlying fund's, because you pay both, which is the same rule the gold
   table uses. capRisk flags a fund that invests overseas and can therefore stop accepting
   fresh money without notice, see notes.overseasCap.                                     */
sleeveFunds: {
  growth: {
    nifty50:   { name:"UTI Nifty 50 Index Fund", venue:"Groww, direct plan", ter:0.0018, minSip:100,
                 alt:"Navi Nifty 50 Index Fund is cheaper on paper at about 0.06%, but UTI's tracking difference has been smaller, and tracking difference is what you actually keep." },
    midcap150: { name:"Motilal Oswal Nifty Midcap 150 Index Fund", venue:"Groww, direct plan", ter:0.0030, minSip:500,
                 alt:"Nippon and ICICI run the same index at a similar cost if the 500 rupee minimum is awkward." },
    sp500:     { name:"Motilal Oswal S&P 500 Index Fund", venue:"Groww, direct plan", ter:0.0058, minSip:500, capRisk:true,
                 alt:"The only other honest route to the same index is Mirae's S&P 500 Top 50 fund, which holds 50 companies rather than 500 and is a more concentrated bet than it sounds." },
    ndx100:    { name:"ICICI Prudential Nasdaq 100 Index Fund", venue:"Groww, direct plan", ter:0.0050, minSip:100, capRisk:true,
                 alt:"Motilal's Nasdaq 100 fund of funds is the better known route but it feeds its own ETF, so all in it costs about 0.81% against this fund's single layer." },
    emxi:      { name:"Kotak Global Emerging Market Fund", venue:"Groww, direct plan", ter:0.0160, minSip:100, capRisk:true, expensive:true,
                 alt:"Every Indian route to emerging markets is a feeder into a foreign fund, so every one of them costs its own fee plus the underlying fund's. This is the cheapest of a bad set." },
    gold:      { name:"Mirae Asset Gold ETF Fund of Fund", venue:"Groww, direct plan", ter:0.0045, minSip:99,
                 alt:"Same fund the Signal and Safe boards pick, for the same reason: it is the cheapest gold route in India all in." }
  },
  offshore: {
    sp500:  { name:"Vanguard S&P 500 ETF (VOO)", venue:"US brokerage", ter:0.0003, minSip:0,
              alt:"SPY is more liquid and four times the cost. For buying and holding, liquidity is not what you are paying for." },
    ndx100: { name:"Invesco Nasdaq 100 ETF (QQQM)", venue:"US brokerage", ter:0.0015, minSip:0,
              alt:"QQQM is the buy and hold share class of QQQ: same index, 0.15% against 0.20%, thinner spreads only because fewer traders use it." },
    emxi:   { name:"Vanguard FTSE Emerging Markets ETF (VWO)", venue:"US brokerage", ter:0.0007, minSip:0,
              alt:"0.07% here against about 1.6% for the cheapest Indian feeder to the same asset class. This one sleeve is most of the case for the offshore route." },
    gold:   { name:"iShares Gold Trust Micro (IAUM)", venue:"US brokerage", ter:0.0009, minSip:0,
              alt:"GLD is the famous one at 0.40%. IAUM holds the same bullion for 0.09%." },
    btc:    { name:"iShares Bitcoin Trust (IBIT)", venue:"US brokerage", ter:0.0025, minSip:0,
              alt:"A spot Bitcoin ETF, so the coins sit with a regulated custodian and the position shows up in an ordinary brokerage statement. No exchange account, no wallet, no seed phrase to lose." }
  }
},

/* ---- what the Liberalised Remittance Scheme actually costs -------------------
   Verified against RBI's LRS rules and the Finance Act position as at the `updated` date.
   The flat wire fee is the number that decides everything at this budget size: see
   notes.lrsBatching, where sending 5,000 rupees a month costs about eight times as much
   as sending the same year's money once.                                                  */
lrs: {
  annualCapUsd: 250000,      // RBI limit per person per financial year
  tcsPct: 0.20,              // TCS on LRS remittances, on the amount ABOVE the threshold
  tcsThreshold: 1000000,     // 10 lakh a financial year, raised from 7 lakh in the 2025 Budget
  fxSpreadPct: 0.005,        // realistic all in FX markup at a discount broker. A bank is 1% to 2%.
  wireFlat: 500,             // typical flat outward remittance charge, in rupees
  dividendWithholding: 0.25, // US withholding for Indian residents under the DTAA, creditable in India
  ltcgMonths: 24,            // foreign ETFs are not "equity" for Indian tax: 24 months for the 12.5% rate
  scheduleFA: true
},

notes: {
  etfVsFof:
    "The break even depends almost entirely on how long you hold, not on how much you buy. Comparing the Mirae gold fund of funds at 0.45% against the Mirae gold ETF at 0.35% plus Groww's real charges, the ETF only becomes cheaper above a ticket of about 30,000 rupees if you hold one year, about 2,400 rupees at three years, about 1,400 at five, and about 700 at ten. The reason is Groww's 5 rupee minimum brokerage, which is a fixed cost that a small ticket cannot spread. So: holding for a year or two, use the fund of funds. Holding for many years and buying more than a couple of thousand at a time, the ETF wins. The fund of funds also buys fractional units, needs no demat account, can run a true daily SIP, and always transacts at NAV rather than at a market price that can sit above it. The ETF's one real edge beyond cost is tax, where long term treatment starts at 12 months instead of 24.",
  overseasCap:
    "Every Indian mutual fund that invests abroad shares one industry wide ceiling of about 7 billion dollars set by SEBI, and it has been full since February 2022. When it fills, funds stop taking fresh money: they suspend lumpsum purchases first and sometimes SIPs too, usually with a few days' notice. Nothing you already hold is affected and existing units stay redeemable, but a new SIP instalment can simply bounce. This is not a theoretical risk, it has happened repeatedly since 2022, and it applies to the S&P 500, Nasdaq 100 and emerging market sleeves on this board. If one of them is closed in the month you go to start, put that sleeve's share into the Nifty 50 or gold sleeve and add it later rather than leaving the money uninvested.",
  lrsBatching:
    "The offshore route has one flat cost that does not care how much you send: the outward remittance fee, roughly 500 rupees a wire. Send 5,000 rupees a month and you pay that twelve times, about 6,300 rupees a year on 60,000 remitted, which is over 10% gone before a single ETF is bought. Send the same year's money in one wire and it costs about 800 rupees, or 1.3%. The ETFs themselves cost 0.03% to 0.25% a year, so the remittance schedule is between forty and a hundred times more important than which fund you pick. The practical answer is to keep the monthly habit in rupees, park it in a liquid fund, and remit once or twice a year.",
  digitalGold:
    "PhonePe gold charges 3% GST on the way in plus a buy sell spread it does not publish anywhere. Independent estimates put the round trip at 3% to 8%. On 5,000 rupees that is 150 to 400 rupees gone before the metal moves at all, against about 2 rupees a month in a Mirae fund of funds. SEBI issued a public caution on digital gold in November 2025 and confirmed it regulates none of it. Use PhonePe for paying people, not for storing savings."
},

/* Copy shown under "Method and honest expectations". Written to be read by someone
   who wants to know where the numbers came from and where they might be wrong. */
method: {
  signal: [
    { q: "What exactly does this board compute?",
      a: "<p>Two steps. First, <b>equal risk contribution</b> sets a neutral anchor: weight each metal by the inverse of its volatility so each contributes the same risk. For two assets that is exactly <code>w_gold = (1/σ_gold) ÷ (1/σ_gold + 1/σ_silver)</code>, and it is provably independent of correlation. On 10 years of LBMA data that lands at about 67% gold.</p><p>Second, the <b>gold silver ratio</b> tilts it. The current ratio is ranked against its own last 30 years and the gold weight shifts by up to 25 points toward whichever metal is historically cheap. The result is clamped between 40% and 90% so it is never an all or nothing bet.</p>" },
    { q: "Is the gold silver ratio actually mean reverting, or is that folklore?",
      a: "<p>Partly folklore, and I would rather tell you than sell you. Running an augmented Dickey Fuller test on 58 years of LBMA fixings gives a statistic of about −3.05, which clears the 5% threshold but not the 1% one, with a half life near 2.5 years. Split the sample and it fails: the ratio is <b>not</b> stationary over 1975 to 2000, and not over 2010 to 2026. The academic literature is genuinely split, with Escribano and Granger (1998) and Baur and Tran (2014) finding a long run relationship and Ciner (2001) finding it broke down.</p><p>The widely quoted rule that above 80 you buy silver and below 50 you buy gold has <b>no primary institutional source</b>. I looked. It traces to bullion dealers, not to the World Gold Council, LBMA or CME. That is why this board uses a percentile rank of the actual history instead of those round numbers.</p>" },
    { q: "How much is the tilt actually worth?",
      a: "<p>Not much, and you should size your expectations accordingly. I walked it forward over 219 overlapping ten year monthly SIPs: the tilt beat a fixed 67/33 split about 84% of the time, with a <b>median gain of roughly 1%</b> on the final value. That is one percent over a decade, not a year.</p><p>For scale, simply holding 100% gold instead of 67/33 beat it in about two thirds of those windows with a median of +3%. The tilt is a small, fairly reliable edge, not a reason to expect outperformance.</p>" },
    { q: "Why does it never stop buying?",
      a: "<p>Because trying to time entry is a worse bet than the tilt. Vanguard's research is often quoted as lump sum beating dollar cost averaging two thirds of the time, but that paper explicitly excludes recurring savers like you, and it is about deploying a windfall. For someone adding new money every month there is no lump sum to time. This board always deploys the full budget and only changes the split.</p>" }
  ],
  growth: [
    { q: "What exactly does this board compute?",
      a: "<p>One rule, applied to six asset classes: <b>equal risk contribution</b>. Weights are chosen so that every sleeve is responsible for the same share of the portfolio's variance. The risk share column proves it, they come out at one sixth each.</p><p>With two assets that is a closed form, which is what the Signal board uses. With six it is not: it has to be solved numerically, by cyclical coordinate descent on the convex log barrier problem (Spinu 2013). So this is the one board here where an actual optimiser is doing actual work rather than a formula wearing a costume.</p><p>Two details that matter more than they look. Risk is measured <b>in rupees</b>, so the currency move on the US and emerging market sleeves counts as risk rather than a footnote, because rupees are what you spend. And the sleeves are lined up only on days when <b>every</b> market in the mix was actually open, so a holiday in one country never invents a zero return in another.</p>" },
    { q: "Why does a board meant to be aggressive still hold gold?",
      a: "<p>Because gold is the only thing in the set that is not correlated to the rest, and removing it makes the board worse on both counts at once. Its average correlation to the other sleeves is about 0.15, against roughly 0.35 for everything else.</p><p>I tested capping it, at 25%, 15%, 10%, and at zero. On the offshore board, removing gold entirely left the return almost unchanged while the worst drawdown went from about 19% to about 30%. On this board, cutting gold lowered the return and the drawdown moved the wrong way too. Capping it would have been a decision made for how the page looks rather than for what the numbers say, so it is uncapped.</p><p>The point of this board is that gold is <b>one sleeve of six</b> rather than the whole portfolio, which is what the other two boards are. That is the sense in which it is not limited to gold.</p>" },
    { q: "Did you try to tilt toward whatever is going up?",
      a: "<p>Yes, and it did not work, so it is not in the product.</p><p>I added a cross sectional momentum tilt, ranking the sleeves by their twelve month return excluding the most recent month, which is the standard construction, and shifting the risk budget toward the winners. Then I walked it forward with no lookahead at four tilt strengths.</p><p>On this board the tilt <b>hurt monotonically</b>: the harder it was applied, the worse the result. On the offshore board it helped very slightly on final value and made the drawdown worse. A signal whose sign flips between two overlapping universes is noise being read as a finding, and if I had shipped it you would have had a knob that looked scientific and did nothing. So the board has no tilt and no market timing at all. The only thing that changes month to month is the covariance matrix.</p>" },
    { q: "What could go wrong here?",
      a: "<p><b>The history is short.</b> This board can only be measured back to the point where all six sleeves existed, which is a few years, not a few decades. The metal boards run on 58 years of LBMA data. Treat every number on this page as one regime rather than as evidence, and note that the regime in question contained a historic run in both US technology and gold.</p><p><b>A single sleeve beat the mix.</b> Over the window tested, holding nothing but gold beat this six way split on final value, with roughly double the drawdown. That is the honest comparison and it is in the table above. Diversification bought a much smoother ride, not a bigger number.</p><p><b>The overseas funds can close.</b> Three of these six sleeves invest abroad and share one SEBI ceiling that has been full since 2022. A monthly instalment can bounce with a few days' notice. This is the most likely thing to actually interrupt you.</p><p><b>Emerging markets cost too much here.</b> Every Indian route to that asset class is a feeder into a foreign fund, so you pay both layers, roughly 1.6% a year against 0.07% for the same exposure on the offshore board. That single sleeve is most of the argument for going abroad.</p>" }
  ],
  offshore: [
    { q: "What does it cost to actually send money abroad?",
      a: "<p>Far more than the funds do, and the schedule matters more than the choice. The outward remittance fee is roughly 500 rupees and it does not care how much you send.</p><p>Send 5,000 rupees every month and that flat fee lands twelve times: about 6,300 rupees a year on 60,000 remitted, over <b>10% gone</b> before you buy a single ETF. Send the same year's money in one wire and it is about 800 rupees, or <b>1.3%</b>. The ETFs on this board cost between 0.03% and 0.25% a year, so the remittance schedule is somewhere between forty and a hundred times more consequential than which fund you pick.</p><p>So the instruction this board gives is deliberately not \"remit 5,000 today\". Keep the monthly habit in rupees, park it in a liquid fund, and remit once or twice a year in one go. This is the same lesson the gold table teaches about PhonePe: the wrapper costs more than the asset.</p>" },
    { q: "Do I owe 20% TCS on this?",
      a: "<p>Not at this size. TCS on money sent abroad under the Liberalised Remittance Scheme applies to the amount <b>above 10 lakh rupees</b> in a financial year, a threshold raised from 7 lakh in the 2025 Budget. At 5,000 rupees a month you are remitting 60,000 a year, so nothing is collected.</p><p>Worth knowing anyway: TCS is not a tax, it is a prepayment. Even if you did cross the threshold it is creditable against your income tax liability or refundable, so it is a cash flow cost rather than money lost. The RBI's overall LRS limit is 250,000 dollars per person per financial year, which is not a constraint you will meet here.</p>" },
    { q: "What do I have to tell the tax department?",
      a: "<p>This is the part people get wrong, and the penalty is out of all proportion to the sums involved.</p><p><b>Schedule FA is mandatory.</b> Once you hold a foreign asset you must disclose it in Schedule FA of your income tax return, every year, regardless of how small it is and regardless of whether you sold anything or made a rupee. It is not optional and it is not triggered by a threshold.</p><p>Non disclosure falls under the Black Money Act, where the penalty is <b>10 lakh rupees</b>, on a holding that might be worth 60,000. This is the single largest risk on this board and it has nothing to do with markets.</p><p>Two more: US dividends are withheld at 25% for Indian residents under the treaty, which you can claim as a foreign tax credit here. And a US ETF is not \"equity\" for Indian capital gains, so you need <b>24 months</b> for the 12.5% long term rate rather than the 12 months an Indian equity fund would need.</p>" },
    { q: "Why is Bitcoin such a small slice if this is the aggressive board?",
      a: "<p>Because equal risk contribution sizes by risk, not by conviction, and Bitcoin's volatility is roughly three times anything else in the mix. To contribute the same share of portfolio variance as the S&P 500 sleeve it has to be about a third of the size. That is the method working, not the method being timid.</p><p>The backtest is blunt about the trade. Over the window tested, holding nothing but Bitcoin returned far more than this board did, with a drawdown near 70%. Holding it at an equal risk weight gave up most of that upside and cut the worst fall to roughly a quarter of it. Both facts are in the table; which one matters to you is not something a dashboard can decide.</p><p>It is held through a spot Bitcoin ETF, so the coins sit with a regulated custodian and appear on an ordinary brokerage statement. There is no Indian mutual fund route to this, which is the only reason this board exists separately from the other one. Note that Indian tax on the ETF follows the foreign asset rules above, not the 30% flat rate that applies to buying crypto on an Indian exchange.</p>" },
    { q: "What could go wrong here?",
      a: "<p><b>Everything from the other board, plus a currency and a bureaucracy.</b> The short history caveat, the single regime caveat and the \"one sleeve beat the mix\" caveat all apply here too and are all in the table above.</p><p><b>The rupee cuts both ways.</b> Part of what looks like a return on this board is the rupee weakening, which has flattered every foreign asset an Indian investor has held for a decade. It is not a law of nature and it can reverse.</p><p><b>Money is slow to come back.</b> Repatriating takes days and costs another wire fee. This is not where an emergency fund goes.</p><p><b>The paperwork compounds.</b> Schedule FA every year, foreign tax credit forms if you claim the dividend withholding back, and a broker who may or may not still be operating in India in ten years. At 60,000 rupees a year, ask honestly whether that is worth it against the domestic board, which needs none of it and gives up mainly the emerging market cost and Bitcoin.</p>" }
  ],
  safe: [
    { q: "What does safest mean here, precisely?",
      a: "<p>Lowest variance of the accumulated pot, not highest return. Three things follow from that.</p><p><b>Gold heavy.</b> Silver's volatility is about twice gold's, roughly 32% against 16%, at a correlation near 0.6. Put those into the two asset minimum variance formula and it wants <i>more than 100%</i> gold, so the long only answer is all gold. I impose a 10% floor on each leg anyway.</p><p><b>Hold some back when volatility is high.</b> The board sizes the metal portion so the sleeve targets 15% annualised volatility, and parks the rest in a liquid fund.</p><p><b>Regulated only.</b> It will not route you to digital gold at any price.</p>" },
    { q: "Be honest: is the minimum variance optimiser doing real work?",
      a: "<p>No, and it would be dishonest to dress it up. With gold at half silver's volatility the optimiser returns the same corner solution every single day: all gold. The number you see is really the <b>10% floor I chose</b>, not a discovery. The floor exists because a single asset portfolio has no protection against my own model being wrong, and because unconstrained optimisers are famous for concentrating on estimation error rather than signal.</p><p>Same caveat for the risk parity anchor on the other board. At two assets it is a formula, not an optimisation. Both are honest ways to pick a number. Neither is machine learning and neither is magic.</p>" },
    { q: "Does holding money back actually reduce risk, or does it just lose returns?",
      a: "<p>It reduces risk measurably, and in this sample it did not cost anything. Across 552 overlapping ten year SIPs on the full LBMA history, volatility targeting cut the <b>median worst drawdown from 17.3% to 10.8%</b> and the bad case at the 95th percentile from 62% to 42%, while the final value came out slightly ahead.</p><p>It held up in every decade tested, the 1970s and 80s, the 1990s, the 2000s and the 2010s, and the drawdown reduction survives even if the parked cash earns nothing. The return benefit does depend on that cash earning about 6%, which an Indian liquid fund does.</p><p>The longest it ever sat below half deployed was 11 months, ending in September 1980, which is exactly when you would have wanted to be out of the way.</p>" },
    { q: "What could still go wrong?",
      a: "<p>Several things, and none of them are hypothetical.</p><p><b>Gold can fall for years.</b> It lost about 65% between 1980 and 1982 and about 45% between 2011 and 2015. Low variance is not low risk. Safe here means smoother, not protected.</p><p><b>The duty risk is live.</b> India raised the import duty on gold and silver from 6% to 15% on 13 May 2026, and a cut back toward 6% was under discussion as recently as late August 2026. If that happens the Indian price drops by roughly 8% overnight while the dollar price does not move.</p><p><b>Volatility targeting can lag.</b> It reacts to volatility that has already happened, so a sudden calm market leaves you underinvested for a month or two.</p><p><b>One year is far too short to judge this.</b> The differences being measured here are a few percent over a decade. Over twelve months the result will be dominated by what gold happens to do, not by anything on this page.</p>" }
  ]
}
};
