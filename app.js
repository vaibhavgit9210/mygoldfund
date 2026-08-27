/* mygoldfund dashboard logic.
   All allocation maths is computed in scripts/build_data.py and arrives in window.MGF_DATA.
   This file turns those weights into rupee instructions and renders them. */
(function () {
"use strict";

var D = window.MGF_DATA, I = window.MGF_INSTRUMENTS;
if (!D) { document.body.innerHTML = '<div class="wrap" style="padding:40px">data.js failed to load.</div>'; return; }

/* ---------------------------------------------------------------- state */
var S = {
  board: localStorage.getItem("mgf.board") || "signal",
  cur:   localStorage.getItem("mgf.cur")   || "inr",
  mode:  localStorage.getItem("mgf.mode")  || "month",
  amt:   +(localStorage.getItem("mgf.amt") || 5000),
  theme: localStorage.getItem("mgf.theme") || "dark"
};
var DAYS_PM = 30;          // for the daily cadence conversion
var HORIZON = 3;           // years, the default holding assumption for cost comparisons

/* ---------------------------------------------------------------- format */
function inr(n, dp) {
  return "₹" + (+n).toLocaleString("en-IN", {minimumFractionDigits: dp || 0, maximumFractionDigits: dp || 0});
}
function usd(n, dp) {
  return "$" + (+n).toLocaleString("en-US", {minimumFractionDigits: dp === undefined ? 2 : dp, maximumFractionDigits: dp === undefined ? 2 : dp});
}
function pc(n, dp) { return (+n).toFixed(dp === undefined ? 1 : dp) + "%"; }
function sgn(n, dp) { return (n >= 0 ? "+" : "") + (+n).toFixed(dp === undefined ? 1 : dp) + "%"; }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
  return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }

/* Price of one gram, in the currently selected currency. */
function perGram(metal) {
  var m = D[metal];
  return S.cur === "inr" ? m.inr.withGst : m.liveUsdOz / D.constants.troyOzG;
}
function money(n, dp) { return S.cur === "inr" ? inr(n, dp) : usd(n, dp); }

/* ---------------------------------------------------------------- allocation */
function alloc() {
  var b = S.board === "safe" ? D.safe : D.signal;
  var budget = S.mode === "day" ? 100 * DAYS_PM : (S.mode === "month" ? 3000 : S.amt);
  var deploy = S.board === "safe" ? D.safe.deploy : 1;
  var metals = budget * deploy;
  return {
    budget: budget,
    deploy: deploy,
    gold: metals * b.wGold,
    silver: metals * b.wSilver,
    park: budget - metals,
    wGold: b.wGold,
    wSilver: b.wSilver
  };
}

/* ---------------------------------------------------------------- instruments */
/* All-in rupee cost of putting `amt` into an instrument and holding it `yrs` years.
   entryPct/exitPct are fractions of the ticket; ter is annual. */
function drag(x, amt, yrs) {
  var entry = amt * (x.entryPct || 0) + (x.entryFlat || 0);
  if (x.groww) entry += growwBuy(amt);
  var hold  = amt * (x.ter || 0) * yrs;
  var exit  = amt * (x.exitPct || 0) + (x.exitFlat || 0);
  return {entry: entry, hold: hold, exit: exit, total: entry + hold + exit};
}

/* Groww's actual charge stack on a delivery buy, verified against their pricing page
   in August 2026. The 5 rupee brokerage floor is what makes small ETF tickets expensive. */
function growwBuy(amt) {
  var brokerage = Math.max(5, Math.min(20, 0.001 * amt));
  var exch = amt * 0.0000297;          // NSE transaction charge
  var ipft = amt * 0.000001;
  var sebi = amt * 0.000001;
  var stamp = amt * 0.00015;           // buy side only
  var gst = 0.18 * (brokerage + exch + ipft + sebi);
  return brokerage + exch + ipft + sebi + stamp + gst;
}

/* Single leg candidates. Combined gold+silver funds are deliberately excluded here:
   they hold BOTH metals at a fixed internal split, so using one as the "silver leg"
   would quietly buy gold a second time. They are offered separately instead. */
function candidates(metal) {
  return (I.instruments || []).filter(function (x) {
    return !x.combo && x.metals.indexOf(metal) >= 0;
  });
}

function comboFund() {
  return (I.instruments || []).filter(function (x) { return x.combo; })
    .sort(function (a, b) { return (a.ter || 0) - (b.ter || 0); })[0] || null;
}

/* Choose the instrument for one leg. The Safe board refuses anything that is not
   a SEBI regulated fund, and the daily cadence refuses anything that cannot do a
   true daily SIP. Among what survives, pick the lowest all-in cost. */
function pick(metal, amt, cadence) {
  var list = candidates(metal).filter(function (x) {
    if (x.exclude) return false;
    if (S.board === "safe" && !x.regulated) return false;
    if (cadence === "day" && !x.dailySip) return false;
    if (amt > 0 && amt < (cadence === "day" ? (x.minSipDaily || x.minSip || 0) : (x.minSip || 0))) return false;
    return true;
  });
  list.sort(function (a, b) { return drag(a, amt, HORIZON).total - drag(b, amt, HORIZON).total; });
  return list[0] || null;
}

/* ---------------------------------------------------------------- headline */
function renderHeadline() {
  var a = alloc(), el = document.getElementById("headline");
  el.className = "headline" + (S.board === "safe" ? " safe" : "");
  var cadence = S.mode === "day" ? "day" : "month";
  var gI = pick("gold", cadence === "day" ? a.gold / DAYS_PM : a.gold, cadence);
  var sI = a.silver > 0 ? pick("silver", cadence === "day" ? a.silver / DAYS_PM : a.silver, cadence) : null;

  var kicker = S.board === "safe"
    ? "Safe board · lowest variance route · " + monthName()
    : "Signal board · risk parity plus valuation tilt · " + monthName();

  var h = '<div class="hl-kicker">' + kicker + "</div>";

  function line(cls, amt, inst, metal) {
    if (!inst || amt < 1) return "";
    var per = cadence === "day" ? amt / DAYS_PM : amt;
    return '<div class="hl-line ' + cls + '">' +
      "<span>Buy</span>" +
      '<span class="hl-amt">' + inr(Math.round(per)) + (cadence === "day" ? "/day" : "") + "</span>" +
      '<span class="hl-in">of</span>' +
      '<span class="hl-fund">' + esc(inst.name) + "</span>" +
      '<span class="hl-in">on ' + esc(inst.venue) + "</span>" +
      "</div>";
  }
  h += line("hl-g", a.gold, gI, "gold");
  h += line("hl-s", a.silver, sI, "silver");

  if (a.park > 1) {
    h += '<div class="hl-line hl-c"><span>Park</span><span class="hl-amt">' +
      inr(Math.round(cadence === "day" ? a.park / DAYS_PM : a.park)) + (cadence === "day" ? "/day" : "") +
      '</span><span class="hl-in">in ' + esc(I.parking.name) + " until metal volatility cools</span></div>";
  }

  /* the alternative cadence */
  var alt;
  if (cadence === "month") {
    var perDayG = Math.round(a.gold / DAYS_PM), perDayS = Math.round(a.silver / DAYS_PM);
    alt = "Prefer a daily habit? The same month is <b>" + inr(perDayG) + "/day into gold</b>" +
      (perDayS > 0 ? " and <b>" + inr(perDayS) + "/day into silver</b>" : "") +
      ". Use a daily SIP in the fund of funds, not an ETF, because a daily ETF order cannot buy fractional units.";
  } else {
    alt = "As one monthly instruction that is <b>" + inr(Math.round(a.gold)) + " into gold</b>" +
      (a.silver > 0 ? " and <b>" + inr(Math.round(a.silver)) + " into silver</b>" : "") +
      ", total " + inr(Math.round(a.budget)) + " for the month.";
  }
  h += '<div class="hl-alt">' + alt + "</div>";

  var cf = comboFund();
  if (cf && a.silver > 0 && a.gold > 0) {
    var mismatch = Math.abs(cf.comboSplit - a.wGold) * 100;
    h += '<div class="hl-note"><span>&#8226;</span><span>Want one instruction instead of two? <b>' +
      esc(cf.name) + "</b> covers both metals in a single " + pc(cf.ter * 100, 2) +
      " SIP. It holds them at a fixed " + pc(cf.comboSplit * 100, 0) + "/" + pc((1 - cf.comboSplit) * 100, 0) +
      " split though, which is " + mismatch.toFixed(0) + " points away from what this board just computed" +
      (mismatch < 8 ? ", so today it is close enough to be a fair trade for the simplicity." :
        ", so it would override the whole point of this page.") + "</span></div>";
  }

  var gg = a.gold / perGram("gold"), sg = a.silver / perGram("silver");
  h += '<div class="hl-note"><span>≈</span><span>That is about <b>' + gg.toFixed(3) +
    " g of gold</b>" + (a.silver > 0 ? " and <b>" + sg.toFixed(2) + " g of silver</b>" : "") +
    " at today's " + (S.cur === "inr" ? "Indian landed price including GST" : "international spot") +
    ". Fund units are priced off the same metal, so this is the metal you are really buying.</span></div>";

  el.innerHTML = h;
}

function monthName() {
  return new Date().toLocaleString("en-IN", {month: "long", year: "numeric"});
}

/* ---------------------------------------------------------------- split + drivers */
function renderSplit() {
  var a = alloc();
  var bar = document.getElementById("splitbar"), leg = document.getElementById("splitlegend");
  var parts = [
    {c: "bg", v: a.gold, l: "Gold"},
    {c: "bs", v: a.silver, l: "Silver"},
    {c: "bc", v: a.park, l: "Liquid"}
  ].filter(function (p) { return p.v > 0.5; });
  bar.innerHTML = parts.map(function (p) {
    var w = p.v / a.budget * 100;
    return '<div class="' + p.c + '" style="flex:0 0 ' + w + '%">' + (w > 13 ? pc(w, 0) : "") + "</div>";
  }).join("");
  leg.innerHTML = parts.map(function (p) {
    return '<span><i style="background:var(--' + ({bg: "gold", bs: "silver", bc: "cash"}[p.c]) + ')"></i>' +
      p.l + " " + inr(Math.round(p.v)) + "</span>";
  }).join("");

  document.getElementById("pin").style.left = ((1 - a.wGold) * 100) + "%";

  var why = document.getElementById("whytext");
  if (S.board === "safe") {
    why.className = "note";
    why.innerHTML = "<b>Why this split.</b> Minimum variance on the live covariance matrix wants " +
      pc(D.safe.minVarRaw * 100, 0) + " gold, which is above 100%, so the long only answer is all gold. " +
      "A " + pc(D.safe.bounds[0] * 100, 0) + " floor on each leg is imposed anyway, standard practice to stop an " +
      "optimiser concentrating on estimation error. Portfolio volatility lands at " + pc(D.safe.portVol) +
      " against a " + pc(D.safe.volTarget) + " target, so only " + pc(D.safe.deploy * 100, 0) +
      " of the budget goes into metal this month.";
  } else {
    var t = D.signal.tilt;
    why.className = "note";
    why.innerHTML = "<b>Why this split.</b> Equal risk contribution sets the neutral anchor at " +
      pc(D.signal.baseWeight * 100, 0) + " gold, because silver is about twice as volatile. " +
      "The gold silver ratio sits at the <b>" + pc(D.gsr.percentile * 100, 0) + " percentile</b> of its last " +
      D.gsr.windowYears + " years, so the valuation tilt is " + sgn(t * 100, 1) + " on the gold leg" +
      (Math.abs(t) < 0.02 ? ", which is close to neutral" : "") + ".";
  }
}

function driverRow(k, v, sub) {
  return '<div class="kv"><span class="k">' + k + (sub ? '<span class="sub">' + sub + "</span>" : "") +
    '</span><span class="v">' + v + "</span></div>";
}

function renderDrivers() {
  var el = document.getElementById("drivers"), h = "";
  if (S.board === "safe") {
    h += driverRow("Gold volatility", pc(D.vol.goldEwma), "EWMA, λ=" + D.vol.lambda);
    h += driverRow("Silver volatility", pc(D.vol.silverEwma), "about " + (D.vol.silverEwma / D.vol.goldEwma).toFixed(1) + "x gold");
    h += driverRow("Correlation", D.vol.rho60.toFixed(2), "60 day");
    h += driverRow("Min variance weight", pc(D.safe.minVarRaw * 100), "unconstrained, before bounds");
    h += driverRow("Portfolio volatility", pc(D.safe.portVol), "target " + pc(D.safe.volTarget));
    h += driverRow("Deploy this month", pc(D.safe.deploy * 100), "rest to a liquid fund");
  } else {
    h += driverRow("Gold silver ratio", D.gsr.value.toFixed(2), "LBMA fix, " + D.asOfFix);
    h += driverRow("Percentile", pc(D.gsr.percentile * 100), "within " + D.gsr.windowYears + " year window");
    h += driverRow("Robust z score", (D.gsr.robustZ >= 0 ? "+" : "") + D.gsr.robustZ.toFixed(2), "median and MAD, log scale");
    h += driverRow("Risk parity anchor", pc(D.signal.baseWeight * 100), "equal risk contribution");
    h += driverRow("Valuation tilt", sgn(D.signal.tilt * 100, 1), "capped at ±" + pc(D.signal.k * 100, 0));
    h += driverRow("Portfolio volatility", pc(D.signal.portVol), "no target, fully deployed");
  }
  el.innerHTML = h;
}

/* ---------------------------------------------------------------- instrument table */
function renderInstruments() {
  var a = alloc();
  var cadence = S.mode === "day" ? "day" : "month";
  var ticket = cadence === "day" ? a.gold / DAYS_PM : a.gold;
  var chosen = pick("gold", ticket, cadence);

  document.getElementById("instlede").textContent =
    "All in cost of routing " + inr(Math.round(ticket)) + " into gold and holding it " + HORIZON +
    " years. Ranked by what actually reaches the metal." +
    (S.board === "safe" ? " The Safe board only considers SEBI regulated funds." : "");

  var rows = candidates("gold").slice().sort(function (x, y) {
    return drag(x, ticket, HORIZON).total - drag(y, ticket, HORIZON).total;
  });

  var h = "<thead><tr><th>Route</th><th>Entry cost</th><th>Annual</th><th>3y all in</th><th>Drag</th><th>Verdict</th></tr></thead><tbody>";
  rows.forEach(function (x) {
    var d = drag(x, ticket, HORIZON);
    var isPick = chosen && x.id === chosen.id;
    var blocked = (S.board === "safe" && !x.regulated) || (cadence === "day" && !x.dailySip);
    var cls = isPick ? "pick" : (x.avoid || blocked ? "avoid" : "");
    var verdict = isPick ? '<span class="tag pick">Buy this</span>'
      : x.avoid ? '<span class="tag no">' + esc(x.avoidWhy || "Avoid") + "</span>"
      : blocked ? '<span class="tag mid">' + (cadence === "day" ? "No daily SIP" : "Not regulated") + "</span>"
      : '<span class="tag mid">Workable</span>';
    h += "<tr class=" + '"' + cls + '"' + "><td>" + esc(x.name) +
      (x.venue ? ' <span style="color:var(--tx3);font-size:11px">' + esc(x.venue) + "</span>" : "") +
      "</td><td>" + inr(d.entry, 0) + "</td><td>" + pc((x.ter || 0) * 100, 2) + "</td><td>" +
      inr(Math.round(d.total)) + "</td><td>" + pc(d.total / ticket * 100, 2) + "</td><td>" + verdict + "</td></tr>";
  });
  h += "</tbody>";
  document.getElementById("insttbl").innerHTML = h;

  var n = document.getElementById("instnote");
  n.innerHTML = '<div class="note"><b>Read this before you switch to an ETF.</b> ' + esc(I.notes.etfVsFof) + "</div>" +
    '<div class="note warn"><b>The single biggest leak.</b> ' + esc(I.notes.digitalGold) + "</div>";
}

/* ---------------------------------------------------------------- indicators */
function card(title, body) { return '<div class="card"><h3>' + title + "</h3>" + body + "</div>"; }

function renderIndicators() {
  var g = D.gold, s = D.silver, el = document.getElementById("indicators");
  document.getElementById("histdesc").textContent =
    D.history.days.toLocaleString("en-IN") + " trading days since " + D.history.from.slice(0, 4);

  var gsrBody =
    driverRow("Now", D.gsr.value.toFixed(2), "gold oz ÷ silver oz") +
    driverRow("Median, " + D.gsr.windowYears + "y", D.gsr.bands.median.toFixed(1), "") +
    driverRow("Range, " + D.gsr.windowYears + "y", D.gsr.bands.min.toFixed(0) + " to " + D.gsr.bands.max.toFixed(0), "") +
    driverRow("Percentile", pc(D.gsr.percentile * 100), "") +
    '<div class="note" style="margin-top:10px">' + gsrVerdict() + "</div>";

  var windows = Object.keys(D.gsr.byWindow).map(function (w) {
    var o = D.gsr.byWindow[w];
    return driverRow(w + " year window", pc(o.pct * 100), "median " + o.median.toFixed(1) + ", z " + (o.rz >= 0 ? "+" : "") + o.rz.toFixed(2));
  }).join("");

  function metalCard(m, name) {
    return driverRow("Volatility", pc(m.volEwma), "EWMA annualised") +
      driverRow("vs 200 day avg", sgn(m.vs200), "") +
      driverRow("RSI 14", m.rsi14.toFixed(0), m.rsi14 > 70 ? "overbought zone" : m.rsi14 < 30 ? "oversold zone" : "neutral zone") +
      driverRow("From all time high", sgn(m.ddFromAth), "") +
      driverRow("12 month return", m.ret1y === null ? "n/a" : sgn(m.ret1y), "") +
      driverRow("5 year CAGR", m.ret5yCagr === null ? "n/a" : sgn(m.ret5yCagr), "in USD");
  }

  el.innerHTML =
    card("Gold silver ratio", gsrBody) +
    card("The ratio by lookback", windows + '<div class="note" style="margin-top:10px">' +
      "The window you pick changes the answer. The board uses " + D.gsr.windowYears +
      " years because that lookback had the best walk forward hit rate, but a shorter window tells a different story and you should see it.</div>") +
    card("Gold", metalCard(g)) +
    card("Silver", metalCard(s)) +
    card("Risk inputs", driverRow("Gold vol", pc(D.vol.goldEwma), "EWMA λ=" + D.vol.lambda) +
      driverRow("Silver vol", pc(D.vol.silverEwma), "") +
      driverRow("Gold vol, 10y", pc(D.vol.gold10y), "simple") +
      driverRow("Silver vol, 10y", pc(D.vol.silver10y), "simple") +
      driverRow("Correlation, 60d", D.vol.rho60.toFixed(3), "") +
      driverRow("Correlation, 5y", D.vol.rho5y.toFixed(3), "")) +
    card("Currency", driverRow("USD INR", D.fx.usdinr.toFixed(2), D.fx.source) +
      driverRow("Import duty", pc(D.constants.importDuty * 100, 0), "customs, on landed value") +
      driverRow("GST on bullion", pc(D.constants.gstBullion * 100, 0), "") +
      '<div class="note" style="margin-top:10px">A falling rupee lifts the Indian gold price even when the dollar price is flat. Part of what looks like a gold return in rupees is really a currency move.</div>');
}

function gsrVerdict() {
  var p = D.gsr.percentile, v = D.gsr.value;
  if (p > 0.80) return "Silver is <b>historically cheap</b> against gold. The board tilts toward silver, within its cap.";
  if (p < 0.20) return "Silver is <b>historically expensive</b> against gold. The board tilts toward gold, within its cap.";
  return "At " + v.toFixed(1) + " the ratio is <b>near the middle</b> of its " + D.gsr.windowYears +
    " year range, so the tilt is small and the split sits close to the risk parity anchor. " +
    "The widely quoted dealer rule of thumb, above 80 favours silver and below 50 favours gold, also reads neutral here.";
}

/* ---------------------------------------------------------------- price cards */
function renderPrices() {
  var g = D.gold, s = D.silver, el = document.getElementById("pricecards");
  function pcard(m, label, dpG) {
    var body;
    if (S.cur === "inr") {
      body = driverRow("Import parity", inr(m.inr.parity, 2), "spot × USDINR, per gram") +
        driverRow("Plus " + pc(D.constants.importDuty * 100, 0) + " duty", inr(m.inr.withDuty, 2), "") +
        driverRow("Plus " + pc(D.constants.gstBullion * 100, 0) + " GST", inr(m.inr.withGst, 2), "what a fair retail price looks like") +
        driverRow("Per 10 g", inr(m.inr.withGst * 10, 0), "");
    } else {
      body = driverRow("Spot", usd(m.liveUsdOz), "per troy ounce") +
        driverRow("Per gram", usd(m.liveUsdOz / D.constants.troyOzG), "") +
        driverRow("LBMA fix", usd(m.usdOz), D.asOfFix) +
        driverRow("200 day average", usd(m.sma200), sgn(m.vs200) + " away");
    }
    return card(label, body);
  }
  el.innerHTML = pcard(g, "Gold") + pcard(s, "Silver") + premiumCard() +
    card("Is your quoted price fair?",
      '<p style="font-size:13px;color:var(--tx2);margin:0 0 11px">Paste the buy rate your app is showing you, per gram, and this tells you the markup over import parity.</p>' +
      '<div class="custom"><input id="quoteIn" type="number" step="1" placeholder="e.g. 16298" aria-label="Quoted rate per gram">' +
      '<button id="quoteGo">Check</button></div><div id="quoteOut" style="margin-top:11px"></div>') +
    card("What 3,000 buys",
      driverRow("Gold", (3000 * D.signal.wGold / perGram("gold")).toFixed(3) + " g", "at the Signal split") +
      driverRow("Silver", (3000 * D.signal.wSilver / perGram("silver")).toFixed(2) + " g", "at the Signal split") +
      driverRow("Gold, Safe board", (3000 * D.safe.deploy * D.safe.wGold / perGram("gold")).toFixed(3) + " g", "") +
      driverRow("Silver, Safe board", (3000 * D.safe.deploy * D.safe.wSilver / perGram("silver")).toFixed(2) + " g", ""));

  document.getElementById("quoteGo").onclick = checkQuote;
  document.getElementById("quoteIn").addEventListener("keydown", function (e) { if (e.key === "Enter") checkQuote(); });
}

/* How far the Indian price sits from import parity, measured off real ETF NAVs. */
function premiumCard() {
  var p = D.indiaPremium;
  if (!p || (!p.gold && !p.silver)) return "";
  var body = "";
  ["gold", "silver"].forEach(function (m) {
    var x = p[m];
    if (!x) return;
    body += driverRow(m === "gold" ? "Gold in India" : "Silver in India", sgn(x.premium, 2),
      "vs its own normal, " + pc(x.percentile * 100, 0) + " percentile");
  });
  var note = premiumVerdict(p);
  return card("India vs import parity", body +
    '<div class="note' + (note.warn ? " warn" : "") + '" style="margin-top:10px">' + note.text + "</div>");
}

function premiumVerdict(p) {
  var s = p.silver, g = p.gold;
  if (s && s.premium > 3) return {warn: true, text:
    "<b>Indian silver is trading " + pc(s.premium, 1) + " above its normal level against import parity.</b> " +
    "That is a local squeeze, not a metal move, and you pay it on the way in. Indian silver ETFs ran a 5% to 12% premium " +
    "during the October 2025 shortage and five fund houses froze fresh lumpsum subscriptions. Consider deferring the silver leg."};
  if (g && g.premium < -3) return {warn: false, text:
    "Indian gold is <b>" + pc(Math.abs(g.premium), 1) + " cheaper</b> than its usual level against import parity, at the " +
    pc(g.percentile * 100, 0) + " percentile. Domestic demand is soft relative to the landed cost, which is a mild tailwind for buying here."};
  return {warn: false, text:
    "Both metals are close to their usual relationship with import parity, so there is no local squeeze to work around this month. " +
    "This is measured from real ETF NAVs at " + (p.gold ? p.gold.sources.length : 2) + " fund houses, which agree within " +
    (p.silver ? p.silver.spread.toFixed(2) : "0.5") + " points on silver."};
}

function checkQuote() {
  var v = +document.getElementById("quoteIn").value, out = document.getElementById("quoteOut");
  if (!v || v <= 0) { out.innerHTML = ""; return; }
  var parity = D.gold.inr.parity, fair = D.gold.inr.withGst;
  var overParity = (v / parity - 1) * 100, overFair = (v / fair - 1) * 100;
  var cls = overFair > 6 ? "bad" : overFair > 2 ? "warn" : "ok";
  var msg = overFair > 6 ? "That is a wide markup. On a 3,000 buy you are handing over "
      + inr(Math.round(3000 * overFair / 100)) + " before the metal has moved at all."
    : overFair > 2 ? "A little rich but within normal retail range."
    : "That is close to fair value for physical or digital gold.";
  out.innerHTML = '<div class="kv"><span class="k">Over import parity</span><span class="v">' + sgn(overParity) + "</span></div>" +
    '<div class="kv"><span class="k">Over duty and GST inclusive</span><span class="v">' + sgn(overFair) + "</span></div>" +
    '<div style="margin-top:9px"><span class="pill ' + cls + '">' + msg + "</span></div>";
}

/* ---------------------------------------------------------------- method */
function renderMethod() {
  var el = document.getElementById("method");
  var m = I.method;
  el.innerHTML = (S.board === "safe" ? m.safe : m.signal).map(function (d) {
    return "<details><summary>" + esc(d.q) + "</summary><div class=\"body\">" + d.a + "</div></details>";
  }).join("");
}

/* ---------------------------------------------------------------- ticker + chrome */
function renderTicker() {
  var t = document.getElementById("ticker");
  var gsrLive = D.gsr.liveValue || D.gsr.value;
  t.innerHTML =
    '<div class="tk"><b class="num" style="color:var(--gold)">' + money(perGram("gold"), S.cur === "inr" ? 0 : 2) + "</b><span>gold / g</span></div>" +
    '<div class="tk"><b class="num" style="color:var(--silver)">' + money(perGram("silver"), S.cur === "inr" ? 0 : 2) + "</b><span>silver / g</span></div>" +
    '<div class="tk"><b class="num">' + gsrLive.toFixed(1) + "</b><span>ratio</span></div>";
}

function setTheme(t) {
  S.theme = t; localStorage.setItem("mgf.theme", t);
  document.documentElement.setAttribute("data-theme", t);
  document.getElementById("thLight").setAttribute("aria-pressed", t === "light");
  document.getElementById("thDark").setAttribute("aria-pressed", t === "dark");
}

function renderAll() {
  document.getElementById("tabSignal").setAttribute("aria-selected", S.board === "signal");
  document.getElementById("tabSafe").setAttribute("aria-selected", S.board === "safe");
  document.getElementById("tabsub").textContent = S.board === "safe" ? I.blurb.safe : I.blurb.signal;
  ["mMonth", "mDay"].forEach(function (id, i) {
    document.getElementById(id).setAttribute("aria-pressed", S.mode === (i ? "day" : "month"));
  });
  document.getElementById("curInr").setAttribute("aria-pressed", S.cur === "inr");
  document.getElementById("curUsd").setAttribute("aria-pressed", S.cur === "usd");
  renderTicker(); renderHeadline(); renderSplit(); renderDrivers();
  renderInstruments(); renderIndicators(); renderPrices(); renderMethod();
  document.getElementById("footmeta").innerHTML =
    "Rebuilt " + esc(D.generatedIst) + " from LBMA benchmark fixings (" + esc(D.asOfFix) +
    "), AMFI daily NAVs and ECB reference rates. Rules and charges last reviewed " + esc(I.updated) + ".";
}

/* ---------------------------------------------------------------- wire up */
function on(id, fn) { document.getElementById(id).addEventListener("click", fn); }
on("tabSignal", function () { S.board = "signal"; localStorage.setItem("mgf.board", "signal"); renderAll(); });
on("tabSafe",   function () { S.board = "safe";   localStorage.setItem("mgf.board", "safe");   renderAll(); });
on("mMonth",    function () { S.mode = "month"; localStorage.setItem("mgf.mode", "month"); renderAll(); });
on("mDay",      function () { S.mode = "day";   localStorage.setItem("mgf.mode", "day");   renderAll(); });
on("amtGo",     function () {
  var v = +document.getElementById("amt").value;
  if (v >= 100) { S.amt = v; S.mode = "custom"; localStorage.setItem("mgf.amt", v); localStorage.setItem("mgf.mode", "custom"); renderAll(); }
});
document.getElementById("amt").addEventListener("keydown", function (e) {
  if (e.key === "Enter") document.getElementById("amtGo").click();
});
on("curInr", function () { S.cur = "inr"; localStorage.setItem("mgf.cur", "inr"); renderAll(); });
on("curUsd", function () { S.cur = "usd"; localStorage.setItem("mgf.cur", "usd"); renderAll(); });
on("thLight", function () { setTheme("light"); });
on("thDark",  function () { setTheme("dark"); });

setTheme(S.theme);
renderAll();

/* screenshot hooks: #shot=signal|safe&theme=light|dark&mode=month|day */
(function () {
  var m = /shot=(\w+)/.exec(location.hash);
  if (m) { S.board = m[1] === "safe" ? "safe" : "signal"; }
  var t = /theme=(\w+)/.exec(location.hash);
  if (t) setTheme(t[1] === "light" ? "light" : "dark");
  var d = /mode=(\w+)/.exec(location.hash);
  if (d) S.mode = d[1];
  if (m || t || d) renderAll();
})();

})();
