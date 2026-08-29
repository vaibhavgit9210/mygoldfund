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
var MONTHLY_BUDGET = 5000; // the default monthly amount the whole page is written around.
                           // The daily mode is the same money at the same pace, 167 a day.

/* The two metal boards and the two multi asset boards render different sections, because
   almost nothing on a gold page applies to a six sleeve global one. */
function isMulti() { return S.board === "growth" || S.board === "offshore"; }
function B() { return D[S.board] || null; }
function sleeveFund(id) {
  var r = (I.sleeveFunds || {})[S.board] || {};
  return r[id] || null;
}

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
function budgetNow() {
  return S.mode === "custom" ? S.amt : MONTHLY_BUDGET;
}

/* Multi asset boards: rupees per sleeve, plus the fund that implements each one.
   Weights already sum to exactly 1 in data.js, so the rupee legs sum to the budget. */
function allocMulti() {
  var b = B(), budget = budgetNow();
  if (!b) return null;
  return {
    budget: budget,
    legs: b.sleeves.map(function (sl) {
      var f = sleeveFund(sl.id);
      return {
        id: sl.id, name: sl.name, short: sl.short, what: sl.what,
        weight: sl.weight, amount: budget * sl.weight, sleeve: sl, fund: f,
        belowMin: !!(f && f.minSip && budget * sl.weight < f.minSip)
      };
    })
  };
}

function alloc() {
  var b = S.board === "safe" ? D.safe : D.signal;
  var budget = budgetNow();
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
  if (isMulti()) return renderHeadlineMulti();
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

  /* If a leg is real money but no fund will take a ticket that small at this cadence,
     say so out loud. Silently dropping it would contradict the split shown below. */
  var dropped = [];
  if (a.gold > 1 && !gI) dropped.push(["gold", a.gold]);
  if (a.silver > 1 && !sI) dropped.push(["silver", a.silver]);
  if (dropped.length) {
    h += '<div class="hl-note" style="color:var(--warn)"><span>&#9888;</span><span>' +
      dropped.map(function (d) {
        var per = cadence === "day" ? d[1] / DAYS_PM : d[1];
        var lo = Math.min.apply(null, candidates(d[0]).filter(function (x) {
          return !x.avoid && (cadence !== "day" || x.dailySip);
        }).map(function (x) { return (cadence === "day" ? (x.minSipDaily || x.minSip) : x.minSip) || 0; }).concat([99]));
        return "The <b>" + d[0] + " leg is only " + inr(Math.round(per)) +
          (cadence === "day" ? " a day" : "") + "</b>, below the " + inr(lo) +
          " minimum every fund enforces. Run it monthly instead: " + inr(Math.round(d[1])) +
          " once a month clears the minimum and costs the same.";
      }).join(" ") + "</span></div>";
  }

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

/* Rupees actually reaching the market on the offshore route, once the wire fee and the FX
   markup are paid. The flat fee is the whole story at this budget: it is charged per wire,
   not per rupee, so the number of wires a year dominates everything else on the board. */
function lrsCost(amountPerWire, wiresPerYear) {
  var L = I.lrs;
  var perWire = L.wireFlat + amountPerWire * L.fxSpreadPct;
  var yearly = perWire * wiresPerYear;
  var remitted = amountPerWire * wiresPerYear;
  return {perWire: perWire, yearly: yearly, remitted: remitted,
          pct: remitted > 0 ? yearly / remitted * 100 : 0};
}

function renderHeadlineMulti() {
  var a = allocMulti(), el = document.getElementById("headline"), b = B();
  el.className = "headline " + S.board;
  if (!a) { el.innerHTML = '<div class="hl-kicker">This board has no data in the last build.</div>'; return; }

  var kicker = (S.board === "growth" ? "Growth board · equal risk across " + a.legs.length + " asset classes"
                                     : "Offshore board · equal risk, under the Liberalised Remittance Scheme")
             + " · " + monthName();
  var h = '<div class="hl-kicker">' + kicker + "</div>";

  var offshore = S.board === "offshore";
  a.legs.forEach(function (g) {
    if (!g.fund) return;
    h += '<div class="hl-line sl-' + esc(g.id) + '">' +
      "<span>" + (offshore ? "Target" : "Buy") + "</span>" +
      '<span class="hl-amt" style="color:var(--c)">' + inr(Math.round(g.amount)) + "</span>" +
      '<span class="hl-in">' + (offshore ? "a month of" : "of") + "</span>" +
      '<span class="hl-fund">' + esc(g.fund.name) + "</span>" +
      '<span class="hl-in">on ' + esc(g.fund.venue) + "</span>" +
      "</div>";
  });

  /* A sleeve smaller than its fund's own minimum SIP cannot actually be placed. Saying so
     matters more here than on the metal boards, because six legs out of one budget makes
     small legs the normal case rather than an edge case. */
  var small = a.legs.filter(function (g) { return g.belowMin; });
  if (small.length) {
    h += '<div class="hl-note" style="color:var(--warn)"><span>&#9888;</span><span>' +
      small.map(function (g) {
        return "The <b>" + esc(g.short) + " leg is " + inr(Math.round(g.amount)) +
          "</b>, under this fund's " + inr(g.fund.minSip) + " minimum SIP.";
      }).join(" ") +
      " Either raise the budget, or run that sleeve once a quarter at three times the size and leave the rest monthly." +
      "</span></div>";
  }

  if (offshore) {
    var monthly = lrsCost(a.budget, 12), yearly = lrsCost(a.budget * 12, 1);
    h += '<div class="hl-alt"><b>Do not wire this today.</b> Sending ' + inr(a.budget) +
      " abroad every month costs about " + inr(Math.round(monthly.yearly)) + " a year in wire fees and FX markup, " +
      "which is <b>" + pc(monthly.pct) + "</b> of everything you remit. Sending the same " +
      inr(Math.round(yearly.remitted)) + " in one wire costs about " + inr(Math.round(yearly.yearly)) +
      ", or <b>" + pc(yearly.pct) + "</b>. Keep the monthly habit in rupees, park it in " + esc(I.parking.name) +
      ", and remit once a year into the split above. One wire a year means buying " +
      a.legs.map(function (g) { return inr(Math.round(g.amount * 12)) + " of " + esc(g.short); }).join(", ") +
      ".</div>";
  } else {
    var capped = a.legs.filter(function (g) { return g.fund && g.fund.capRisk; });
    h += '<div class="hl-alt">All ' + a.legs.length + " are ordinary SIPs on one platform, so this is " +
      "<b>" + a.legs.length + " standing instructions set once</b>, no foreign account and no extra tax form. " +
      (capped.length ? "The " + capped.map(function (g) { return esc(g.short); }).join(", ") +
        " sleeves invest abroad and can stop accepting fresh money when SEBI's overseas ceiling fills." : "") +
      "</div>";
  }

  h += '<div class="hl-note"><span>&#8226;</span><span>Every rupee is deployed: this board never parks anything. ' +
    "Portfolio volatility comes out at <b>" + pc(b.portVol) + "</b> a year on today's covariance, against the " +
    "Safe board's " + pc(D.safe.volTarget) + " target and Signal's " + pc(D.signal.portVol) + ". " +
    "Spreading across " + a.legs.length + " weakly correlated sleeves lowers measured risk even though every one of " +
    "them is more volatile than a bank deposit.</span></div>";

  el.innerHTML = h;
}

/* ---------------------------------------------------------------- multi asset sections */
function renderSleeves() {
  var a = allocMulti(), b = B();
  if (!a) return;
  document.getElementById("sleevelede").textContent =
    "Equal risk contribution across " + a.legs.length + " asset classes, measured in rupees over " +
    b.window.years + " years of overlapping trading days. One fund per sleeve, all of them mutual funds.";

  var h = "<thead><tr><th>Sleeve</th><th>Weight</th><th>This month</th><th>Risk share</th>" +
          "<th>Volatility</th><th>Avg corr</th><th>12m</th><th>Cost</th></tr></thead><tbody>";
  a.legs.forEach(function (g) {
    var sl = g.sleeve;
    h += '<tr class="sl-' + esc(g.id) + '"><td><b style="color:var(--c)">' + esc(g.name) + "</b>" +
      '<span style="display:block;color:var(--tx3);font-size:11px;white-space:normal">' +
      (g.fund ? esc(g.fund.name) : "no fund mapped") +
      (g.fund && g.fund.capRisk ? ' <span class="tag no">Cap risk</span>' : "") + "</span></td>" +
      "<td>" + pc(g.weight * 100) + "</td>" +
      "<td><b>" + inr(Math.round(g.amount)) + "</b></td>" +
      "<td>" + pc(sl.riskShare * 100) + "</td>" +
      "<td>" + pc(sl.vol) + "</td>" +
      "<td>" + sl.avgCorr.toFixed(2) + "</td>" +
      "<td>" + (sl.ret1y === null ? "n/a" : sgn(sl.ret1y)) + "</td>" +
      "<td>" + (g.fund ? pc(g.fund.ter * 100, 2) : "n/a") + "</td></tr>";
  });
  h += "</tbody>";
  document.getElementById("sleevetbl").innerHTML = h;

  var worst = a.legs.slice().sort(function (x, y) {
    return (y.fund ? y.fund.ter : 0) - (x.fund ? x.fund.ter : 0); })[0];
  var n = '<div class="note"><b>Risk share is the column that proves the method.</b> ' +
    "The weights are chosen so every sleeve is responsible for the same share of portfolio variance, which is why " +
    "the volatile sleeves get less money and the quiet ones get more. They come out at " +
    pc(100 / a.legs.length) + " each, give or take the " + pc(b.bounds[0] * 100, 0) + " to " +
    pc(b.bounds[1] * 100, 0) + " bounds. Nothing here is a view on what will go up.</div>";
  if (worst && worst.fund) {
    n += '<div class="note warn"><b>The expensive one.</b> ' + esc(worst.name) + " costs " +
      pc(worst.fund.ter * 100, 2) + " a year through " + esc(worst.fund.name) + ". " + esc(worst.fund.alt || "") + "</div>";
  }
  if (S.board === "growth") n += '<div class="note warn"><b>Funds that invest abroad can close to new money.</b> ' + esc(I.notes.overseasCap) + "</div>";
  document.getElementById("sleevenote").innerHTML = n;
}

function renderCorr() {
  var b = B();
  if (!b) return;
  var sl = b.sleeves, C = b.corr;
  var h = "<thead><tr><th></th>" + sl.map(function (x) {
    return '<th class="rot">' + esc(x.short) + "</th>"; }).join("") + "</tr></thead><tbody>";
  sl.forEach(function (x, i) {
    h += "<tr><td style=\"text-align:left\"><b>" + esc(x.short) + "</b></td>" +
      C[i].map(function (v, j) {
        if (i === j) return '<td class="v" style="color:var(--tx3)">&mdash;</td>';
        var t = Math.max(0, Math.min(1, (v + 0.2) / 1.2));
        return '<td class="v" style="background:color-mix(in srgb,var(--accent) ' +
          Math.round(t * 55) + '%,transparent)">' + v.toFixed(2) + "</td>";
      }).join("") + "</tr>";
  });
  h += "</tbody>";
  document.getElementById("corrtbl").innerHTML = h;

  var byCorr = sl.slice().sort(function (x, y) { return x.avgCorr - y.avgCorr; });
  var best = byCorr[0], worst = byCorr[byCorr.length - 1];
  var boardAvg = sl.reduce(function (t, x) { return t + x.avgCorr; }, 0) / sl.length;
  document.getElementById("corrnote").innerHTML =
    '<div class="note"><b>Two numbers set every weight, and this is the second one.</b> ' +
    esc(best.name) + " is the least correlated thing on the board at " + best.avgCorr.toFixed(2) +
    ", against " + boardAvg.toFixed(2) + " for the set as a whole; " + esc(worst.name) + " is the most connected at " +
    worst.avgCorr.toFixed(2) + ". Under equal risk contribution low correlation earns a sleeve weight, because money " +
    "that moves on its own adds less to portfolio variance than money that moves with everything else, while high " +
    "volatility takes weight away again. Those two forces are the entire model. " +
    "It is the low correlations across the whole set, not any one sleeve, that make the board's worst drawdown in the " +
    "table below smaller than any single sleeve's.</div>" +
    '<div class="note"><b>These correlations are shrunk.</b> Every one is pulled 20% toward the average correlation ' +
    "before it is used, which is the standard defence against reading noise in a matrix estimated from a few years of " +
    "data (Ledoit and Wolf 2003). The raw numbers would be a little more extreme and a lot less trustworthy.</div>";
}

function renderBacktest() {
  var b = B(), bt = b && b.backtest;
  var lede = document.getElementById("btlede"), tbl = document.getElementById("bttbl"),
      note = document.getElementById("btnote");
  if (!bt) { lede.textContent = "Not enough overlapping history to backtest this board yet."; tbl.innerHTML = ""; note.innerHTML = ""; return; }

  lede.textContent = bt.sips + " monthly instalments of " + inr(bt.monthly) + " from " + bt.from + " to " + bt.to +
    ", " + inr(bt.invested) + " invested in total. At every rebalance the covariance is re estimated from data " +
    "available on that date only, so there is no lookahead anywhere in this table.";

  var rows = [{k: "This board, equal risk", v: bt.board, me: true},
              {k: "Equal weight, no model", v: bt.equal}];
  b.sleeves.forEach(function (sl) {
    if (bt.solo[sl.id]) rows.push({k: "100% " + sl.name, v: bt.solo[sl.id], solo: sl.id});
  });
  var bestMul = Math.max.apply(null, rows.map(function (r) { return r.v.multiple; }));
  var bestDd = Math.max.apply(null, rows.map(function (r) { return r.v.maxDrawdown; }));

  var h = "<thead><tr><th>Strategy</th><th>Value per rupee invested</th><th>Worst drawdown</th></tr></thead><tbody>";
  rows.forEach(function (r) {
    h += "<tr" + (r.me ? ' class="pick"' : "") + "><td>" + esc(r.k) +
      (r.v.multiple === bestMul ? ' <span class="tag ok">Best return</span>' : "") +
      (r.v.maxDrawdown === bestDd ? ' <span class="tag mid">Smoothest</span>' : "") +
      "</td><td>&times;" + r.v.multiple.toFixed(3) + "</td><td>" + pc(r.v.maxDrawdown) + "</td></tr>";
  });
  h += "</tbody>";
  tbl.innerHTML = h;

  var bestName = (b.sleeves.filter(function (x) { return x.id === bt.bestSolo; })[0] || {}).name || bt.bestSolo;
  var n = '<div class="note"><b>Read the second column before the first.</b> ' +
    "Over this window the board's worst peak to trough fall was " + pc(bt.board.maxDrawdown) +
    (bt.boardBeatEverySoloDrawdown ? ", which is smaller than every single sleeve held on its own. That is the entire case for the method: " +
      "it did not find the best asset, it made the ride survivable." : ".") + "</div>";
  n += '<div class="note warn"><b>And now the uncomfortable part.</b> Holding nothing but ' + esc(bestName) +
    " returned &times;" + bt.solo[bt.bestSolo].multiple.toFixed(3) + " against the board's &times;" +
    bt.board.multiple.toFixed(3) + ", with a " + pc(bt.solo[bt.bestSolo].maxDrawdown) + " drawdown. " +
    "Picking the winner in advance beats diversifying, every time, if you can do it. " +
    (bt.boardBeatEqual
      ? "The board did at least beat naive equal weighting, though not by much."
      : "The board did not beat naive equal weighting on final value here either, it beat it on drawdown.") +
    " <b>" + b.window.years + " years is one regime, not evidence.</b> The metal boards on this site are measured " +
    "over 58 years and even they are cautious about what they claim.</div>";
  note.innerHTML = n;
}

function renderRoute() {
  var a = allocMulti(), el = document.getElementById("routecards"), b = B();
  if (!a) return;
  document.getElementById("routeTitle").textContent =
    S.board === "growth" ? "The route, and why it needs no paperwork" : "The route, and what it really costs";

  var blended = a.legs.reduce(function (t, g) { return t + g.weight * (g.fund ? g.fund.ter : 0); }, 0);
  var cards = "";

  if (S.board === "growth") {
    cards += card("What this route costs you",
      driverRow("Blended annual fee", pc(blended * 100, 2), "weighted across every sleeve") +
      driverRow("On " + inr(a.budget) + " a month", inr(Math.round(a.budget * 12 * blended)) + " a year", "at a full year's contributions") +
      driverRow("Entry or exit load", "none", "index funds and fund of funds transact at NAV") +
      driverRow("Extra tax forms", "none", "no foreign asset to disclose") +
      '<div class="note" style="margin-top:10px">Nothing here needs a demat account, a foreign broker or a Schedule FA entry. ' +
      'That simplicity is the reason this board exists alongside the offshore one.</div>');
    cards += card("What you give up versus going abroad",
      driverRow("Emerging markets", pc((I.sleeveFunds.growth.emxi.ter) * 100, 2) + " here", "against 0.07% for the same asset class offshore") +
      driverRow("Bitcoin", "not available", "no Indian mutual fund may hold crypto") +
      driverRow("Overseas cap", "live risk", "fresh subscriptions can be suspended") +
      '<div class="note warn" style="margin-top:10px">The single biggest cost of staying home is the emerging market sleeve, ' +
      'which is a feeder into a foreign fund and so charges two layers of fee for one exposure.</div>');
  } else {
    var monthly = lrsCost(a.budget, 12), quarterly = lrsCost(a.budget * 3, 4), yearly = lrsCost(a.budget * 12, 1);
    cards += card("Remittance schedule decides everything",
      driverRow("Monthly, 12 wires", inr(Math.round(monthly.yearly)) + " a year", pc(monthly.pct) + " of everything remitted") +
      driverRow("Quarterly, 4 wires", inr(Math.round(quarterly.yearly)) + " a year", pc(quarterly.pct) + " of everything remitted") +
      driverRow("Once a year, 1 wire", inr(Math.round(yearly.yearly)) + " a year", pc(yearly.pct) + " of everything remitted") +
      driverRow("The funds themselves", pc(blended * 100, 2) + " a year", "blended across the sleeves") +
      '<div class="note warn" style="margin-top:10px">' + esc(I.notes.lrsBatching) + "</div>");
    cards += card("Tax and paperwork",
      driverRow("TCS on this budget", "none", "applies above " + inr(I.lrs.tcsThreshold) + " a financial year") +
      driverRow("TCS rate beyond that", pc(I.lrs.tcsPct * 100, 0), "creditable against your tax, not a cost") +
      driverRow("RBI annual limit", "$" + I.lrs.annualCapUsd.toLocaleString("en-US"), "per person per financial year") +
      driverRow("US dividend withholding", pc(I.lrs.dividendWithholding * 100, 0), "creditable here under the treaty") +
      driverRow("Long term capital gains", I.lrs.ltcgMonths + " months", "a foreign ETF is not equity for Indian tax") +
      driverRow("Schedule FA", "mandatory", "every year, at any size") +
      '<div class="note warn" style="margin-top:10px"><b>Schedule FA is the real risk on this board.</b> ' +
      "Holding any foreign asset means disclosing it in your return every year, whether or not you sold anything. " +
      "Non disclosure sits under the Black Money Act, where the penalty is 10 lakh rupees on a holding that might be worth " +
      inr(a.budget * 12) + ". Nothing about the markets on this page is as dangerous as forgetting that form.</div>");
  }
  el.innerHTML = cards;
}

function monthName() {
  return new Date().toLocaleString("en-IN", {month: "long", year: "numeric"});
}

/* ---------------------------------------------------------------- split + drivers */
function renderSplitMulti() {
  var a = allocMulti(), b = B();
  var bar = document.getElementById("splitbar"), leg = document.getElementById("splitlegend");
  if (!a) { bar.innerHTML = ""; leg.innerHTML = ""; return; }
  bar.innerHTML = a.legs.map(function (g) {
    var w = g.weight * 100;
    return '<div class="sleeve sl-' + esc(g.id) + '" style="flex:0 0 ' + w + '%">' +
      (w > 11 ? pc(w, 0) : "") + "</div>";
  }).join("");
  leg.innerHTML = a.legs.map(function (g) {
    return '<span class="sl-' + esc(g.id) + '"><i class="sleeve"></i>' +
      esc(g.short) + " " + inr(Math.round(g.amount)) + "</span>";
  }).join("");
  var why = document.getElementById("whytext");
  why.className = "note";
  why.innerHTML = "<b>Why this split.</b> Every sleeve is sized so it contributes the same share of portfolio " +
    "variance, about " + pc(100 / a.legs.length) + " each. That is equal risk contribution, solved numerically " +
    "because with " + a.legs.length + " assets there is no formula for it, only with two. " +
    "The volatile sleeves therefore get less money, not more: " +
    esc(a.legs.slice().sort(function (x, y) { return y.sleeve.vol - x.sleeve.vol; })[0].short) +
    " is the most volatile at " + pc(a.legs.slice().sort(function (x, y) { return y.sleeve.vol - x.sleeve.vol; })[0].sleeve.vol) +
    " and is held small for exactly that reason. Nothing is tilted, timed or forecast.";
}

function renderSplit() {
  if (isMulti()) return renderSplitMulti();
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
  if (isMulti()) {
    var b = B();
    if (!b) { el.innerHTML = ""; return; }
    document.getElementById("driversTitle").textContent = "The window this is measured on";
    h += driverRow("Sleeves", String(b.sleeves.length), "each an ordinary mutual fund");
    h += driverRow("Common history", b.window.years + " years", b.window.from + " to " + b.window.to);
    h += driverRow("Observations", b.window.days.toLocaleString("en-IN"),
                   "days every market in the mix was open, " + b.window.obsPerYear + " a year");
    h += driverRow("Portfolio volatility", pc(b.portVol), "equal weight would be " + pc(b.equalWeightVol));
    h += driverRow("Deployed", pc(b.deploy * 100, 0), "this board never parks anything");
    h += driverRow("Weight bounds", pc(b.bounds[0] * 100, 0) + " to " + pc(b.bounds[1] * 100, 0),
                   "so no single sleeve can dominate");
    el.innerHTML = h;
    return;
  }
  document.getElementById("driversTitle").textContent = "What drove it";
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
    card("What " + MONTHLY_BUDGET.toLocaleString("en-IN") + " buys",
      driverRow("Gold", (MONTHLY_BUDGET * D.signal.wGold / perGram("gold")).toFixed(3) + " g", "at the Signal split") +
      driverRow("Silver", (MONTHLY_BUDGET * D.signal.wSilver / perGram("silver")).toFixed(2) + " g", "at the Signal split") +
      driverRow("Gold, Safe board", (MONTHLY_BUDGET * D.safe.deploy * D.safe.wGold / perGram("gold")).toFixed(3) + " g", "") +
      driverRow("Silver, Safe board", (MONTHLY_BUDGET * D.safe.deploy * D.safe.wSilver / perGram("silver")).toFixed(2) + " g", ""));

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
  var msg = overFair > 6 ? "That is a wide markup. On a " + MONTHLY_BUDGET.toLocaleString("en-IN") +
      " buy you are handing over " + inr(Math.round(MONTHLY_BUDGET * overFair / 100)) + " before the metal has moved at all."
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
  el.innerHTML = (m[S.board] || m.signal).map(function (d) {
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

/* The page must never present stale numbers as if they were today's. If the daily
   rebuild stops for any reason, this says so at the top rather than failing silently. */
function renderStaleness() {
  var el = document.getElementById("staleness");
  if (!el) return;
  var built = Date.parse(D.generatedAt);
  if (isNaN(built)) { el.innerHTML = ""; return; }
  var days = (Date.now() - built) / 86400000;
  if (days <= 2) { el.innerHTML = ""; return; }
  var bad = days > 7;
  el.innerHTML = '<div class="stale ' + (bad ? "bad" : "warn") + '"><span class="inner">' +
    (bad ? "&#9888; <b>Do not act on these numbers.</b> " : "&#9888; <b>Heads up.</b> ") +
    "The daily rebuild last ran <b>" + Math.floor(days) + " days ago</b> (" + esc(D.generatedIst) + "). " +
    (bad
      ? "Prices, volatility and the ratio have all moved since then, so the split below is out of date. "
      : "The split below is still close but no longer today's. ") +
    'Check the <a href="https://github.com/vaibhavgit9210/mygoldfund/actions" target="_blank" rel="noopener">' +
    "workflow runs</a>. GitHub also switches scheduled workflows off after 60 days without repository activity, " +
    "and re-running it once from that page turns it back on." +
    "</span></div>";
}

function renderAll() {
  renderStaleness();
  /* A board whose data source failed in the last build must not render as an empty page. */
  if (isMulti() && !B()) S.board = "signal";
  ["signal", "safe", "growth", "offshore"].forEach(function (b) {
    document.getElementById("tab" + b.charAt(0).toUpperCase() + b.slice(1))
      .setAttribute("aria-selected", S.board === b);
  });
  document.getElementById("tabsub").textContent = I.blurb[S.board] || I.blurb.signal;
  ["mMonth", "mDay"].forEach(function (id, i) {
    document.getElementById(id).setAttribute("aria-pressed", S.mode === (i ? "day" : "month"));
  });
  document.getElementById("curInr").setAttribute("aria-pressed", S.cur === "inr");
  document.getElementById("curUsd").setAttribute("aria-pressed", S.cur === "usd");
  /* Keep the box showing the number the page is actually using. A remembered custom amount
     used to leave the input reading 5,000 while every figure below was computed on something
     else, which reads as a bug even though the maths was right. */
  document.getElementById("amt").value = budgetNow();

  var multi = isMulti();
  document.getElementById("multiOnly").hidden = !multi;
  document.getElementById("metalOnly").hidden = multi;
  document.getElementById("gaugewrap").hidden = multi;
  /* The daily cadence is a gold and silver idea. Six sleeves cannot be bought daily at
     any sane ticket size, so the option is hidden rather than left to fail quietly. */
  document.getElementById("mDay").hidden = multi;
  if (multi && S.mode === "day") S.mode = "month";

  renderTicker(); renderHeadline(); renderSplit(); renderDrivers();
  if (multi) { renderSleeves(); renderCorr(); renderBacktest(); renderRoute(); }
  else { renderInstruments(); renderIndicators(); renderPrices(); }
  renderMethod();
  document.getElementById("footmeta").innerHTML =
    "Rebuilt " + esc(D.generatedIst) + " from LBMA benchmark fixings (" + esc(D.asOfFix) +
    "), AMFI daily NAVs and ECB reference rates. Rules and charges last reviewed " + esc(I.updated) + ".";
}

/* ---------------------------------------------------------------- wire up */
function on(id, fn) { document.getElementById(id).addEventListener("click", fn); }
["signal", "safe", "growth", "offshore"].forEach(function (b) {
  on("tab" + b.charAt(0).toUpperCase() + b.slice(1), function () {
    S.board = b; localStorage.setItem("mgf.board", b); renderAll();
  });
});
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

/* screenshot hooks: #shot=signal|safe|growth|offshore&theme=light|dark&mode=month|day */
(function () {
  var m = /shot=(\w+)/.exec(location.hash);
  if (m && ["signal", "safe", "growth", "offshore"].indexOf(m[1]) >= 0) { S.board = m[1]; }
  var t = /theme=(\w+)/.exec(location.hash);
  if (t) setTheme(t[1] === "light" ? "light" : "dark");
  var d = /mode=(\w+)/.exec(location.hash);
  if (d) S.mode = d[1];
  if (m || t || d) renderAll();
})();

})();
