/* Verified cost, tax and product constants.
   Every number here was checked against a primary source on the date in `updated`.
   Re-check the ones flagged `stale` in README.md before trusting them months from now. */
window.MGF_INSTRUMENTS = {
updated: "27 August 2026",

blurb: {
  signal: "Risk parity sets the neutral split, the gold silver ratio tilts it. Fully invested every month, no market timing.",
  safe:   "Lowest variance route. Gold heavy because silver is twice as volatile, and it holds back part of the budget when metal volatility runs hot."
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

notes: {
  etfVsFof:
    "The break even depends almost entirely on how long you hold, not on how much you buy. Comparing the Mirae gold fund of funds at 0.45% against the Mirae gold ETF at 0.35% plus Groww's real charges, the ETF only becomes cheaper above a ticket of about 30,000 rupees if you hold one year, about 2,400 rupees at three years, about 1,400 at five, and about 700 at ten. The reason is Groww's 5 rupee minimum brokerage, which is a fixed cost that a small ticket cannot spread. So: holding for a year or two, use the fund of funds. Holding for many years and buying more than a couple of thousand at a time, the ETF wins. The fund of funds also buys fractional units, needs no demat account, can run a true daily SIP, and always transacts at NAV rather than at a market price that can sit above it. The ETF's one real edge beyond cost is tax, where long term treatment starts at 12 months instead of 24.",
  digitalGold:
    "PhonePe gold charges 3% GST on the way in plus a buy sell spread it does not publish anywhere. Independent estimates put the round trip at 3% to 8%. On 3,000 rupees that is 90 to 240 rupees gone before the metal moves at all, against about 1 rupee 40 paise a month in a Mirae fund of funds. SEBI issued a public caution on digital gold in November 2025 and confirmed it regulates none of it. Use PhonePe for paying people, not for storing savings."
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
