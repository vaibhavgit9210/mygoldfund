/* mygoldfund dashboard logic.
   All allocation maths is computed in scripts/build_data.py and arrives in window.MGF_DATA.
   This file turns those weights into rupee instructions and renders them. */
(function () {
"use strict";

var D = window.MGF_DATA, I = window.MGF_INSTRUMENTS, U = window.MGF_US || null;
if (!D) { document.body.innerHTML = '<div class="wrap" style="padding:40px">data.js failed to load.</div>'; return; }

/* ---------------------------------------------------------------- state */
var S = {
  board: localStorage.getItem("mgf.board") || "signal",
  cur:   localStorage.getItem("mgf.cur")   || "inr",
  mode:  localStorage.getItem("mgf.mode")  || "month",
  amt:   +(localStorage.getItem("mgf.amt") || 5000),
  theme: localStorage.getItem("mgf.theme") || "dark",
  usEtf:   +(localStorage.getItem("mgf.usEtf")   || 30),
  usStock: +(localStorage.getItem("mgf.usStock") || 20),
  usSort: {key: "mom", dir: -1},
  usDaily: null   /* long score history from the ledger Worker, once it answers */
};
var BOARDS = ["signal", "safe", "growth", "offshore", "us"];
var LEDGER = "https://mygoldfund-ledger.vaibhavpro9210.workers.dev";
var DAYS_PM = 30;          // for the daily cadence conversion
var HORIZON = 3;           // years, the default holding assumption for cost comparisons
var MONTHLY_BUDGET = 5000; // the default monthly amount the whole page is written around.
                           // The daily mode is the same money at the same pace, 167 a day.

/* The two metal boards and the two multi asset boards render different sections, because
   almost nothing on a gold page applies to a six sleeve global one. */
function isMulti() { return S.board === "growth" || S.board === "offshore"; }
/* The US board is dollars, two sleeves and a daily forecast ledger: none of the rupee
   machinery applies, so it gets its own section and its own budget row. */
function isUs() { return S.board === "us"; }
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
  if (isUs()) return renderHeadlineUs();
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
        if (i === j) return '<td class="v" style="color:var(--tx3)">-</td>';
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
    h += driverRow("Gold silver ratio", D.gsr.value.toFixed(2), fixLabel() + ", " + D.asOfFix);
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

// LBMA has blocked scripted downloads since 30 Sep 2026, so recent days are daily spot closes.
function spliced() { return D.history.lbmaTo && D.asOfFix > D.history.lbmaTo; }
function fixLabel() { return spliced() ? "Spot close" : "LBMA fix"; }

function renderIndicators() {
  var g = D.gold, s = D.silver, el = document.getElementById("indicators");
  document.getElementById("histdesc").textContent =
    D.history.days.toLocaleString("en-IN") + " trading days since " + D.history.from.slice(0, 4) +
    (spliced() ? " (LBMA fixes for both metals to " + D.history.lbmaTo + ", daily spot closes after, since LBMA now blocks automated downloads)" : "");

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
        driverRow(fixLabel(), usd(m.usdOz), D.asOfFix) +
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
  if (isUs() && U) {
    t.innerHTML =
      '<div class="tk"><b class="num" style="color:var(--us)">' + usd(U.market.spy.close) + "</b><span>SPY</span></div>" +
      '<div class="tk"><b class="num" style="color:var(--accent)">' + usd(U.market.qqq.close) + "</b><span>QQQ</span></div>" +
      '<div class="tk"><b class="num">' + (+U.market.vix).toFixed(2) + "</b><span>VIX</span></div>";
    return;
  }
  var gsrLive = D.gsr.liveValue || D.gsr.value;
  t.innerHTML =
    '<div class="tk"><b class="num" style="color:var(--gold)">' + money(perGram("gold"), S.cur === "inr" ? 0 : 2) + "</b><span>gold / g</span></div>" +
    '<div class="tk"><b class="num" style="color:var(--silver)">' + money(perGram("silver"), S.cur === "inr" ? 0 : 2) + "</b><span>silver / g</span></div>" +
    '<div class="tk"><b class="num">' + gsrLive.toFixed(1) + "</b><span>ratio</span></div>";
}

function setTheme(t) {
  S.theme = t; localStorage.setItem("mgf.theme", t);
  document.documentElement.setAttribute("data-theme", t);
  var tc = document.querySelector('meta[name="theme-color"]');
  if (tc) tc.setAttribute("content", t === "light" ? "#f6f7f9" : "#0b0d10");
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

/* ================================================================ US board
   Everything below reads window.MGF_US (us.js, written by scripts/us_build.py twice a trading
   day). Fractions arrive as fractions and are formatted here. Nothing on this board touches
   the rupee boards' state beyond the shared theme. */
function pf(x, dp) { return x === null || x === undefined ? "n/a" : sgn(x * 100, dp === undefined ? 1 : dp); }
function pu(x, dp) { return x === null || x === undefined ? "n/a" : pc(x * 100, dp === undefined ? 1 : dp); }
function fmtDate(iso) {
  if (!iso) return "";
  var d = new Date(iso + "T00:00:00");
  return isNaN(d) ? esc(iso) : d.toLocaleDateString("en-IN", {day: "numeric", month: "short", year: "numeric"});
}
function sessionLabel(s) { return s === "pre" ? "pre market" : "after the close"; }
function toInr(dollars) { return inr(Math.round(dollars * D.fx.usdinr)); }
function usdW(n) { return usd(n, 0); }
function usTicker(sym) { return (U && U.tickers && U.tickers[sym]) || null; }

/* Picks scale with the budget boxes, proportionally, with the largest line absorbing the
   rounding so the lines still add up to what the sleeve deploys. */
function scaledRec(sleeve) {
  var rec = U.recs && U.recs[sleeve];
  if (!rec) return null;
  var budget = sleeve === "etf" ? S.usEtf : S.usStock;
  var f = rec.budgetUsd > 0 ? budget / rec.budgetUsd : 1;
  var picks = (rec.picks || []).map(function (p) {
    var o = {}; for (var k in p) o[k] = p[k];
    o.usd = Math.max(1, Math.round(p.usd * f));
    return o;
  });
  /* The leveraged cap is a dollar figure, not a share of the sleeve: a bigger budget must not scale a 3x line past it. */
  var cap = (U.constants && U.constants.levCapUsd) || 10;
  picks.forEach(function (p) { if (p.lev > 1 && p.usd > cap) p.usd = cap; });
  var want = Math.round(rec.deployUsd * f), have = picks.reduce(function (t, p) { return t + p.usd; }, 0);
  if (picks.length && have !== want) {
    var unlev = picks.filter(function (p) { return !(p.lev > 1); });
    var big = (unlev.length ? unlev : picks).slice().sort(function (a, b) { return b.usd - a.usd; })[0];
    big.usd = Math.max(1, big.usd + (want - have));
  }
  var deployed = picks.reduce(function (t, p) { return t + p.usd; }, 0);
  return {rec: rec, budget: budget, picks: picks, deployed: deployed, held: Math.max(0, budget - deployed), f: f};
}

function usCostLine(deployedUsd) {
  var c = U.costs, b = deployedUsd * c.brokerage, g = b * c.gst;
  var lo = b + g + deployedUsd * c.fxBand[0], hi = b + g + deployedUsd * c.fxBand[1];
  return {lo: lo, hi: hi, pctLo: deployedUsd ? lo / deployedUsd : 0, pctHi: deployedUsd ? hi / deployedUsd : 0};
}

function renderHeadlineUs() {
  var el = document.getElementById("headline");
  el.className = "headline us";
  if (!U) {
    el.innerHTML = '<div class="hl-kicker">US board</div><div class="hl-line"><span>US panel not built yet.</span></div>' +
      '<div class="hl-alt">The first run of <code>scripts/us_build.py</code> writes <code>us.js</code>, and this board renders from it. Until then the other four boards are unaffected.</div>';
    return;
  }
  var h = '<div class="hl-kicker">US board, high risk high reward, ' + fmtDate(U.date) + ", " + sessionLabel(U.session) + "</div>";
  var N = U.dayNumber, G = U.gateDays || 7;

  if (N <= G) {
    h += '<div class="hl-line"><span>Day</span><span class="hl-amt" style="color:var(--us)">' + N + " of " + G +
      '</span><span class="hl-in">recording and predicting only</span></div>';
    var strip = "";
    for (var i = 0; i < G; i++) strip += "<i" + (i < N ? ' class="on"' : "") + "></i>";
    h += '<div class="days" aria-label="Day ' + N + ' of ' + G + '">' + strip + "</div>";
    h += '<div class="hl-alt">Every forecast is being recorded and scored, and the recommendation engine is running in the shadows ' +
      "so the paper track record starts on day one. From day " + (G + 1) + " the order lines appear here, provided the data integrity checks pass. " +
      "Nothing is buyable until then.</div>";
    h += '<div class="hl-note"><span>&#8226;</span><span>' + esc(U.notes.sevenDays) + "</span></div>";
    el.innerHTML = h; return;
  }

  if (!U.gateOpen) {
    h += '<div class="hl-line"><span>Day ' + N + '</span><span class="hl-in">recommendations are gated</span></div>';
    h += '<div class="hl-alt" style="color:var(--warn)"><b style="color:var(--warn)">Not buyable today.</b> The data integrity check failed:' +
      '<ul style="margin:6px 0 0;padding-left:18px">' + (U.gateReasons || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul>" +
      (U.recs && U.recs.etf && U.recs.etf.picks && U.recs.etf.picks.length
        ? "The last recommendation is still shown below with a data degraded banner, for reference, not for action." : "") + "</div>";
    h += '<div class="hl-note"><span>&#8226;</span><span>' + esc(U.notes.sevenDays) + "</span></div>";
    el.innerHTML = h; return;
  }

  var totalDeployed = 0, totalHeld = 0;
  ["etf", "stock"].forEach(function (sl) {
    var r = scaledRec(sl);
    if (!r) return;
    h += '<div class="hl-sub">' + (sl === "etf" ? "ETF sleeve" : "Stock sleeve") + ", " + usdW(r.budget) + " a month</div>";
    if (!r.picks.length) h += '<div class="hl-line hl-c"><span>Hold</span><span class="hl-amt">' + usdW(r.budget) + '</span><span class="hl-in">nothing qualified this month</span></div>';
    r.picks.forEach(function (p) {
      h += '<div class="hl-line hl-u"><span>Buy</span><span class="hl-amt">' + usdW(p.usd) + "</span>" +
        '<span class="hl-in">of</span><span class="hl-fund">' + esc(p.sym) + "</span>" +
        '<span class="hl-in">' + esc(p.name || "") + (p.lev > 1 ? ' <span class="tag us">' + p.lev + "x</span>" : "") +
        ((p.flags || []).indexOf("cryptoAdjacent") >= 0 ? ' <span class="tag cfm">confirm in app</span>' : "") +
        ", about " + toInr(p.usd) + "</span></div>";
    });
    totalDeployed += r.deployed;
    /* a sleeve with no picks already printed its own hold line, so it must not be counted again below */
    if (r.picks.length) totalHeld += r.held;
  });
  if (totalHeld > 0) {
    var fr = U.recs.etf ? U.recs.etf.deployFrac : 1;
    h += '<div class="hl-line hl-c" style="margin-top:12px"><span>Hold</span><span class="hl-amt">' + usdW(totalHeld) + "</span>" +
      '<span class="hl-in">this month in the INDmoney cash balance' + (fr < 1 ? ", regime deploys " + pu(fr, 0) + " only" : ", the vol target did not need it") +
      ", it rolls into next month</span></div>";
  }
  var c = usCostLine(totalDeployed);
  h += '<div class="hl-alt">Costs on these orders: <b>' + usd(c.lo) + " to " + usd(c.hi) + "</b> (" + pu(c.pctLo, 2) + " to " + pu(c.pctHi, 2) +
    "), brokerage " + pu(U.costs.brokerage, 2) + " plus " + pu(U.costs.gst, 0) + " GST on it and a " + pu(U.costs.fxBand[0], 1) + " to " +
    pu(U.costs.fxBand[1], 1) + " FX markup. Fund the account once a month so the markup is paid once. Issued " + fmtDate(U.recs.etf ? U.recs.etf.date : U.date) +
    ", regime <b>" + esc(U.recs.etf ? U.recs.etf.regime : U.market.regime.label) + "</b>, next issue " + fmtDate(U.recs.nextIssue) + ".</div>";
  h += '<div class="hl-note"><span>&#8226;</span><span>' + esc(U.notes.sevenDays) + "</span></div>";
  el.innerHTML = h;
}

function renderUsBudget() {
  document.getElementById("usEtf").value = S.usEtf;
  document.getElementById("usStock").value = S.usStock;
  document.getElementById("usFx").textContent = usdW(S.usEtf + S.usStock) + " a month is about " + toInr(S.usEtf + S.usStock) +
    " at " + D.fx.usdinr.toFixed(2) + " to the dollar.";
}

function renderUsReadings() {
  var m = U.market, el = document.getElementById("usReadings");
  document.getElementById("usReadTitle").textContent = U.session === "pre" ? "This morning's readings" : "Today's readings";
  function px(sym, mk) {
    var t = usTicker(sym);
    var pre = U.session === "pre" && t && t.price.pre !== null;
    return driverRow(sym, usd(pre ? t.price.pre : mk.close), (pre ? "pre market, " : "close, ") + pf(mk.ret1d) + " on the day, " +
      (mk.above200 ? "above" : "below") + " the 200 day");
  }
  var h = px("SPY", m.spy) + px("QQQ", m.qqq);
  h += driverRow("VIX", (+m.vix).toFixed(2), "VIX3M " + (+m.vix3m).toFixed(2) + ", VIX9D " + (+m.vix9d).toFixed(2));
  h += driverRow("VIX / VIX3M", (+m.vixRatio).toFixed(3), m.backwardation ? "backwardation, risk off" : "contango, normal");
  h += driverRow("13 week bill", pu(m.tbill, 2), "recorded daily as the risk free rate, display only, no rule uses it");
  h += driverRow("Regime", esc(m.regime.label), "SPY vol at the " + pu(m.regime.volPct, 0) + " percentile of 500 days");
  h += driverRow("HMM p(high vol)", pu(m.hmm.pHigh, 0), "display only, never used for sizing, fit " + fmtDate(m.hmm.fitDate));
  var last = (U.runs || [])[U.runs.length - 1];
  if (last) h += driverRow("Last run", esc(last.session) + " " + fmtDate(last.date),
    (last.degraded ? "degraded, " : "clean, ") + (last.lateMin ? last.lateMin + " min late" : "on time"));
  el.innerHTML = h;
}

function wbar(name, w, label) {
  return '<div class="wrow"><span class="wk">' + esc(label || name) + '</span><span class="wb"><i style="width:' +
    Math.max(0, Math.min(100, w * 100)).toFixed(1) + '%"></i></span><span class="wv">' + pu(w, 0) + "</span></div>";
}
var EXPERT_LABEL = {rw_drift: "RW + drift", gjr_zero: "GJR GARCH", har_dens: "HAR RV", trend_regime: "trend regime", hmm_mix: "HMM mix",
                    ewma: "EWMA", gjr: "GJR GARCH", har: "HAR RV", combo: "combo"};
function renderUsWeights() {
  var W = U.weights, el = document.getElementById("usWeights"), h = "";
  h += '<div class="hl-sub" style="margin-top:0">Density experts, Hedge on CRPS</div>';
  Object.keys(W.density || {}).forEach(function (k) { h += wbar(k, W.density[k], EXPERT_LABEL[k] || k); });
  h += '<div class="hl-sub">Variance experts, Hedge on QLIKE</div>';
  Object.keys(W.variance || {}).forEach(function (k) { h += wbar(k, W.variance[k], EXPERT_LABEL[k] || k); });
  h += '<div class="note" style="margin-top:12px">Weights are shrunk toward equal by min(0.5, t/250) with t = ' + (+W.t) +
    " scored days, so for the first year the engine trusts no expert much more than the others. That is deliberate: a week of data " +
    "cannot tell five models apart.</div>";
  el.innerHTML = h;
}

/* ---- predictions table */
var PRED_COLS = [
  {key: "sym",   label: "Ticker",   left: true},
  {key: "sleeve", label: "Sleeve"},
  {key: "price", label: "Last"},
  {key: "d1",    label: "1d"},
  {key: "mom",   label: "12-1 mom"},
  {key: "vol",   label: "Vol ann"},
  {key: "mu",    label: "Forecast"},
  {key: "pUp",   label: "P(up)"},
  {key: "band",  label: "90% band"},
  {key: "hit",   label: "Yesterday"},
  {key: "trend", label: "Trend"}
];
function predRows() {
  return Object.keys(U.tickers).map(function (sym) {
    var t = U.tickers[sym], pre = U.session === "pre" && t.price.pre !== null;
    var f = t.forecast || {};
    return {
      sym: sym, t: t, sleeve: t.sleeve, price: pre ? t.price.pre : t.price.close, pre: pre,
      d1: t.ret.d1, mom: t.ret.mom12_1, vol: t.vol.ann, mu: f.mu, pUp: f.pUp, lo: f.lo90, hi: f.hi90,
      band: f.hi90 !== undefined && f.lo90 !== undefined && f.hi90 !== null ? f.hi90 - f.lo90 : null,
      hit: t.lastScore ? t.lastScore.hit : null, trend: {up: 2, mixed: 1, down: 0}[t.trend.state]
    };
  });
}
function renderUsPred() {
  var rows = predRows(), k = S.usSort.key, dir = S.usSort.dir;
  rows.sort(function (a, b) {
    var x = a[k], y = b[k];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return x < y ? -dir : x > y ? dir : 0;
    return (x - y) * dir;
  });
  document.getElementById("usPredLede").textContent =
    "Forecast for " + fmtDate(rows[0].t.forecast ? rows[0].t.forecast.target : U.date) + ", made " + sessionLabel(U.session) +
    (U.session === "pre" ? " with the overnight gap folded in. " : ". ") +
    "Mean and 90% conformal band are next day log returns. Yesterday is whether the previous forecast got the sign right. Click a column to sort.";
  var h = "<thead><tr>" + PRED_COLS.map(function (c) {
    var on = c.key === k;
    return '<th class="sortable" data-key="' + c.key + '"' + (on ? ' aria-sort="' + (dir > 0 ? "ascending" : "descending") + '"' : "") + ">" +
      c.label + (on ? (dir > 0 ? " &#9650;" : " &#9660;") : "") + "</th>";
  }).join("") + "</tr></thead><tbody>";
  rows.forEach(function (r) {
    var t = r.t, tags = "";
    if (t.lev > 1) tags += ' <span class="tag us">' + t.lev + "x</span>";
    if ((t.flags || []).indexOf("cryptoAdjacent") >= 0) tags += ' <span class="tag cfm">confirm in app</span>';
    if ((t.flags || []).indexOf("thin") >= 0) tags += ' <span class="tag mid">thin</span>';
    if (t.earnings) tags += ' <span class="tag mid">earnings ' + esc(fmtDate(t.earnings).replace(/ \d{4}$/, "")) + "</span>";
    var hit = r.hit === null ? '<span class="dim">new</span>' : r.hit ? '<span class="hitok">&#10003;</span>' : '<span class="hitno">&#10007;</span>';
    h += "<tr><td><b>" + esc(r.sym) + "</b>" + tags +
      '<span style="display:block;color:var(--tx3);font-size:11px;white-space:normal">' + esc(t.name) + "</span></td>" +
      '<td class="dim">' + esc(r.sleeve) + "</td>" +
      "<td>" + usd(r.price) + (r.pre ? ' <span class="dim" style="font-size:10px">pre</span>' : "") + "</td>" +
      "<td" + (r.d1 < 0 ? ' style="color:var(--bad)"' : "") + ">" + pf(r.d1) + "</td>" +
      "<td>" + pf(r.mom, 0) + "</td>" +
      "<td>" + pu(r.vol, 0) + "</td>" +
      "<td>" + pf(r.mu, 2) + "</td>" +
      "<td>" + pu(r.pUp, 0) + "</td>" +
      '<td class="dim">' + pf(r.lo, 1) + " to " + pf(r.hi, 1) + "</td>" +
      "<td>" + hit + "</td>" +
      '<td><span class="chip ' + esc(t.trend.state) + '">' + esc(t.trend.state) + "</span></td></tr>";
  });
  h += "</tbody>";
  var tbl = document.getElementById("usPredTbl");
  tbl.innerHTML = h;
  Array.prototype.forEach.call(tbl.querySelectorAll("th.sortable"), function (th) {
    th.addEventListener("click", function () {
      var key = th.getAttribute("data-key");
      if (S.usSort.key === key) S.usSort.dir = -S.usSort.dir;
      else S.usSort = {key: key, dir: key === "sym" || key === "sleeve" ? 1 : -1};
      renderUsPred();
    });
  });
  var lev = Object.keys(U.tickers).filter(function (s) { return U.tickers[s].lev > 1; }).length;
  var cry = Object.keys(U.tickers).filter(function (s) { return (U.tickers[s].flags || []).indexOf("cryptoAdjacent") >= 0; }).length;
  document.getElementById("usPredNote").innerHTML =
    '<div class="note"><b>Read the band, not the mean.</b> ' + esc(U.notes.direction) + "</div>" +
    '<div class="note warn"><b>' + lev + " leveraged rows, " + cry + " crypto adjacent rows.</b> " + esc(U.notes.leverage) +
    " The crypto adjacent names can be withdrawn from INDmoney without notice, confirm in the app before counting on one.</div>";
}

/* ---- scorecard */
function tile(label, big, sub, cls) {
  return '<div class="tile' + (cls ? " " + cls : "") + '"><div class="lab">' + label + '</div><div class="big">' + big + '</div><div class="sub">' + sub + "</div></div>";
}
function skillCls(x) { return x === null || x === undefined ? "" : x > 0.01 ? "good" : x < -0.01 ? "poor" : "flat"; }
function renderUsScore() {
  var sc = U.scorecard, el = document.getElementById("usScoreTiles");
  var ciLo = sc.hitCI ? sc.hitCI[0] : null, ciHi = sc.hitCI ? sc.hitCI[1] : null;
  var dirCls = ciLo !== null && ciLo > 0.5 ? "good" : ciHi !== null && ciHi < 0.5 ? "poor" : "flat";
  document.getElementById("usScoreLede").textContent =
    sc.days + " trading days, " + (+sc.n).toLocaleString("en-IN") + " scored forecasts across " + Object.keys(U.tickers).length +
    " tickers. Every skill number sits next to its baseline and its sample size, because without those it is a story.";
  /* Every one of these is null until something has been scored. A null must read as n/a, never as 0.00. */
  var has = function (x) { return x !== null && x !== undefined; };
  var nd = function (x, dp) { return has(x) ? (+x).toFixed(dp) : "n/a"; };
  var noScores = "no scored forecasts yet";
  var h = '<div class="tiles">' +
    tile("Hit rate", pu(sc.hitRate), sc.n ? "95% CI " + pu(ciLo, 0) + " to " + pu(ciHi, 0) + ", n " + (+sc.n).toLocaleString("en-IN") : noScores + ", n 0", dirCls) +
    tile("Brier skill", pf(sc.brierSkill, 2), has(sc.brier) ? "vs climatology " + nd(sc.brierClim, 4) + ", model " + nd(sc.brier, 4) : noScores, skillCls(sc.brierSkill)) +
    tile("CRPS skill", pf(sc.crpsSkill, 2), has(sc.crps) ? "vs random walk with drift, " + nd(sc.crpsBase, 5) + " to " + nd(sc.crps, 5) : noScores, skillCls(sc.crpsSkill)) +
    tile("90% coverage", pu(sc.cov90), has(sc.cov90) ? "target 90, and the 50% band covers " + pu(sc.cov50, 0) : noScores,
      !has(sc.cov90) ? "" : sc.cov90 >= 0.85 && sc.cov90 <= 0.95 ? "good" : sc.cov90 < 0.8 || sc.cov90 > 0.97 ? "poor" : "flat") +
    tile("Variance skill", pf(sc.varSkill.combo, 2), has(sc.varSkill.combo) ? "combo vs EWMA on QLIKE; GJR " + pf(sc.varSkill.gjr, 1) + ", HAR " + pf(sc.varSkill.har, 1) : noScores, skillCls(sc.varSkill.combo)) +
    tile("DM t stat", nd(sc.dmT, 2), "Diebold-Mariano on CRPS, Newey-West lag 5, needs 1.96", has(sc.dmT) && Math.abs(sc.dmT) >= 1.96 ? "good" : "flat") +
    tile("Min track record", has(sc.minTrlDays) ? (+sc.minTrlDays).toLocaleString("en-IN") + " d" : "n/a",
      has(sc.minTrlDays) ? "days needed before this paper Sharpe means anything, about " + (sc.minTrlDays / 252).toFixed(1) + " years" : "needs ten paper days before it can be computed", "flat") +
    tile("Paper Sharpe", nd(sc.sharpePaper, 2), "shadow portfolio since day one, annualised, vs its baseline", "flat") +
    "</div>";
  var bs = sc.bySession || {}, bl = sc.bySleeve || {};
  h += driverRow("By session", "pre " + pu(bs.pre && bs.pre.hitRate, 0) + " / post " + pu(bs.post && bs.post.hitRate, 0),
    "hit rate, n " + (bs.pre ? bs.pre.n : 0) + " and " + (bs.post ? bs.post.n : 0) + "; CRPS skill " + pf(bs.pre && bs.pre.crpsSkill) + " and " + pf(bs.post && bs.post.crpsSkill));
  h += driverRow("By sleeve", "ETF " + pu(bl.etf && bl.etf.hitRate, 0) + " / stock " + pu(bl.stock && bl.stock.hitRate, 0),
    "hit rate, n " + (bl.etf ? bl.etf.n : 0) + " and " + (bl.stock ? bl.stock.n : 0) + "; CRPS skill " + pf(bl.etf && bl.etf.crpsSkill) + " and " + pf(bl.stock && bl.stock.crpsSkill));
  h += driverRow("Log score skill", pf(sc.logSkill, 2), "vs random walk with drift");
  el.innerHTML = h;

  var coin = ciLo !== null && ciLo <= 0.5 && ciHi >= 0.5;
  var sentence;
  if (!sc.n) {
    sentence = "<b>What this says.</b> Nothing yet. A forecast is scored by the post run on its target day, so the first scores arrive with the first post run after a forecast was published, and every tile above fills in from there.";
  } else {
    sentence = "<b>What this says.</b> After " + sc.days + " days and " + (+sc.n).toLocaleString("en-IN") + " forecasts the hit rate is " +
      pu(sc.hitRate) + " and its interval " + (coin ? "contains" : "excludes") + " 50%, so direction " +
      (coin ? "is not distinguishable from a coin flip yet" : (sc.hitRate > 0.5 ? "is ahead of a coin flip, which at this sample size still proves little" : "is behind a coin flip")) +
      ". CRPS skill of " + pf(sc.crpsSkill, 1) + " against a random walk with drift has " +
      (has(sc.dmT) ? "a DM t of " + nd(sc.dmT, 2) + (Math.abs(sc.dmT) >= 1.96 ? ", which clears the usual 1.96" : ", short of the usual 1.96") : "no DM t yet") +
      ". The 90% band covered " + pu(sc.cov90, 0) + " of outcomes" +
      (!has(sc.cov90) ? "" : sc.cov90 >= 0.85 && sc.cov90 <= 0.95 ? ", about right" : sc.cov90 < 0.85 ? ", too narrow" : ", too wide") +
      ", and the variance combination " + (has(sc.varSkill.combo) ? (sc.varSkill.combo >= 0 ? "beats" : "trails") + " EWMA by " + pf(Math.abs(sc.varSkill.combo), 1) + " on QLIKE" : "has no QLIKE comparison against EWMA yet") +
      ", which is where the skill is expected to live. " +
      (has(sc.minTrlDays) ? "The minimum track record length is " + (+sc.minTrlDays).toLocaleString("en-IN") + " days, so nothing about the paper portfolio is evidence yet."
        : "The minimum track record length needs ten paper days before it can be computed, so nothing about the paper portfolio is evidence yet.");
  }
  document.getElementById("usScoreText").innerHTML = '<div class="note">' + sentence + "</div>";
  renderUsScoreChart();
  document.getElementById("usPit").innerHTML = svgHist(sc.pitHist || [], sc.n) +
    '<div class="clegend"><span>Probability integral transform of every scored outcome in ten bins. Flat is calibrated, a U shape means the bands are too narrow, a hump means too wide. Dashed line is the uniform expectation.</span></div>';
}
function renderUsScoreChart() {
  var daily = S.usDaily || U.scorecard.daily || [];
  document.getElementById("usScoreChart").innerHTML = svgLine(daily) +
    '<div class="clegend"><span><i style="background:var(--us)"></i>hit rate, 20 day rolling, weighted by forecasts</span>' +
    '<span><i style="background:var(--accent)"></i>CRPS skill vs random walk with drift, daily</span>' +
    "<span>" + daily.length + " days" + (S.usDaily ? ", from the ledger Worker" : ", from the last build") + "</span></div>";
}

/* ---- inline SVG charts. Plain polylines in a viewBox, so they scale with the card. */
function svgLine(daily) {
  var W = 600, H = 220, L = 40, R = 12, T = 10, gap = 18, ph = (H - T - gap - 22) / 2;
  if (!daily.length) return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '"><text x="' + (W / 2) + '" y="' + (H / 2) + '" text-anchor="middle">no scored days yet</text></svg>';
  var hit = [], crp = [];
  for (var i = 0; i < daily.length; i++) {
    var hs = 0, ns = 0;
    for (var j = Math.max(0, i - 19); j <= i; j++) { hs += (daily[j].hitRate || 0) * (daily[j].n || 0); ns += daily[j].n || 0; }
    hit.push(ns ? hs / ns : null); crp.push(daily[i].crpsSkill);
  }
  var xs = function (i) { return L + (daily.length === 1 ? (W - L - R) / 2 : i / (daily.length - 1) * (W - L - R)); };
  function panel(vals, top, lo, hi, ref, cls, fmt) {
    var ys = function (v) { return top + ph - (v - lo) / (hi - lo) * ph; };
    var s = '<line class="axis" x1="' + L + '" y1="' + top + '" x2="' + L + '" y2="' + (top + ph) + '"/>';
    [lo, (lo + hi) / 2, hi].forEach(function (g) {
      s += '<line class="grid" x1="' + L + '" y1="' + ys(g).toFixed(1) + '" x2="' + (W - R) + '" y2="' + ys(g).toFixed(1) + '"/>' +
        '<text x="' + (L - 5) + '" y="' + (ys(g) + 3.5).toFixed(1) + '" text-anchor="end">' + fmt(g) + "</text>";
    });
    if (ref >= lo && ref <= hi) s += '<line class="ref" x1="' + L + '" y1="' + ys(ref).toFixed(1) + '" x2="' + (W - R) + '" y2="' + ys(ref).toFixed(1) + '"/>';
    var pts = vals.map(function (v, i) { return v === null || v === undefined ? null : xs(i).toFixed(1) + "," + ys(Math.max(lo, Math.min(hi, v))).toFixed(1); }).filter(Boolean);
    s += '<polyline class="' + cls + '" points="' + pts.join(" ") + '"/>';
    if (pts.length === 1) s += '<circle cx="' + pts[0].split(",")[0] + '" cy="' + pts[0].split(",")[1] + '" r="3" class="' + cls + '"/>';
    return s;
  }
  var cmin = Math.min.apply(null, crp.concat([-0.05])), cmax = Math.max.apply(null, crp.concat([0.05]));
  var cr = Math.max(Math.abs(cmin), Math.abs(cmax)); cr = Math.ceil(cr * 20) / 20;
  var s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Rolling hit rate and CRPS skill">';
  s += panel(hit, T, 0.3, 0.7, 0.5, "l1", function (g) { return Math.round(g * 100) + "%"; });
  s += panel(crp, T + ph + gap, -cr, cr, 0, "l2", function (g) { return (g > 0 ? "+" : "") + Math.round(g * 100) + "%"; });
  /* month ticks along the bottom */
  var seen = {}, y0 = H - 6, lastLabelX = -1e9;
  daily.forEach(function (d, i) {
    var m = (d.date || "").slice(0, 7);
    if (!m || seen[m]) return;
    seen[m] = 1;
    s += '<line class="axis" x1="' + xs(i).toFixed(1) + '" y1="' + (y0 - 14) + '" x2="' + xs(i).toFixed(1) + '" y2="' + (y0 - 10) + '"/>';
    /* a label every month, unless two month starts sit within a label's width of each other (the first weeks of a record) */
    if (xs(i) - lastLabelX < 70) return;
    lastLabelX = xs(i);
    s += '<text x="' + xs(i).toFixed(1) + '" y="' + y0 + '" text-anchor="' + (i === 0 ? "start" : "middle") + '">' + esc(fmtDate(d.date).replace(/^\d+ /, "")) + "</text>";
  });
  return s + "</svg>";
}
function svgHist(bins, n) {
  var W = 600, H = 190, L = 36, R = 12, T = 12, B = 26;
  var mx = Math.max.apply(null, bins.concat([1])), ref = n ? n / bins.length : 0;
  mx = Math.max(mx, ref) * 1.15;
  var bw = (W - L - R) / Math.max(1, bins.length);
  var ys = function (v) { return T + (H - T - B) - v / mx * (H - T - B); };
  var s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="PIT histogram">';
  s += '<line class="axis" x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) + '"/>';
  bins.forEach(function (v, i) {
    var x = L + i * bw + 3, y = ys(v);
    s += '<rect class="hbar" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + (bw - 6).toFixed(1) + '" height="' + (H - B - y).toFixed(1) + '" rx="2"/>' +
      '<text x="' + (x + (bw - 6) / 2).toFixed(1) + '" y="' + (y - 3).toFixed(1) + '" text-anchor="middle">' + v + "</text>" +
      '<text x="' + (L + i * bw).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + (i / bins.length).toFixed(1) + "</text>";
  });
  s += '<text x="' + (W - R) + '" y="' + (H - 8) + '" text-anchor="middle">1.0</text>';
  if (ref) s += '<line class="ref" x1="' + L + '" y1="' + ys(ref).toFixed(1) + '" x2="' + (W - R) + '" y2="' + ys(ref).toFixed(1) + '"/>' +
    '<text x="' + (L - 4) + '" y="' + (ys(ref) + 3.5).toFixed(1) + '" text-anchor="end">' + Math.round(ref) + "</text>";
  return s + "</svg>";
}
function svgFan(f, title) {
  var W = 600, H = 230, L = 46, R = 14, T = 12, B = 28;
  var hs = [21, 63, 126, 252], q = f.q || {};
  var pts = [[0, [0, 0, 0, 0, 0]]].concat(hs.filter(function (h) { return q[String(h)]; }).map(function (h) { return [h, q[String(h)]]; }));
  if (pts.length < 2) return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '"><text x="' + (W / 2) + '" y="' + (H / 2) + '" text-anchor="middle">no fan recorded</text></svg>';
  var lo = Math.min.apply(null, pts.map(function (p) { return p[1][0]; })), hi = Math.max.apply(null, pts.map(function (p) { return p[1][4]; }));
  lo = Math.floor(lo * 10) / 10 - 0.0001; hi = Math.ceil(hi * 10) / 10 + 0.0001;
  var xs = function (d) { return L + d / 252 * (W - L - R); }, ys = function (v) { return T + (H - T - B) - (v - lo) / (hi - lo) * (H - T - B); };
  function band(a, b) {
    var up = pts.map(function (p) { return xs(p[0]).toFixed(1) + "," + ys(p[1][b]).toFixed(1); });
    var dn = pts.slice().reverse().map(function (p) { return xs(p[0]).toFixed(1) + "," + ys(p[1][a]).toFixed(1); });
    return up.concat(dn).join(" ");
  }
  var s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(title) + ' twelve month fan">';
  var step = (hi - lo) > 1.2 ? 0.25 : (hi - lo) > 0.6 ? 0.2 : 0.1;
  for (var g = Math.ceil(lo / step) * step; g <= hi + 1e-9; g += step) {
    s += '<line class="' + (Math.abs(g) < 1e-9 ? "ref" : "grid") + '" x1="' + L + '" y1="' + ys(g).toFixed(1) + '" x2="' + (W - R) + '" y2="' + ys(g).toFixed(1) + '"/>' +
      '<text x="' + (L - 5) + '" y="' + (ys(g) + 3.5).toFixed(1) + '" text-anchor="end">' + (g > 0 ? "+" : "") + Math.round(g * 100) + "%</text>";
  }
  s += '<polygon class="b95" points="' + band(0, 4) + '"/><polygon class="b75" points="' + band(1, 3) + '"/>';
  s += '<polyline class="med" points="' + pts.map(function (p) { return xs(p[0]).toFixed(1) + "," + ys(p[1][2]).toFixed(1); }).join(" ") + '"/>';
  for (var m = 1; m <= 12; m++) {
    var x = xs(m * 21);
    s += '<line class="axis" x1="' + x.toFixed(1) + '" y1="' + (H - B) + '" x2="' + x.toFixed(1) + '" y2="' + (H - B + 4) + '"/>';
    if (m % 3 === 0) s += '<text x="' + x.toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + m + " mo</text>";
  }
  s += '<line class="axis" x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) + '"/>';
  return s + "</svg>";
}
function fanCard(key, title, sub) {
  var f = U.fans && U.fans[key];
  if (!f) return card(title, '<div class="note">No fan recorded for ' + esc(key) + " yet.</div>");
  var y = f.q && f.q["252"];
  return card(title, svgFan(f, title) +
    '<div class="clegend"><span><i style="background:var(--us);opacity:.3"></i>5 to 95</span><span><i style="background:var(--us);opacity:.6"></i>25 to 75</span>' +
    '<span><i style="background:var(--us)"></i>median</span></div>' +
    (y ? driverRow("One year", pf(y[2], 0) + " median", pf(y[0], 0) + " to " + pf(y[4], 0) + " covers 90% of paths") : "") +
    driverRow("Drift used", pf(f.drift, 1), "w " + (+f.w).toFixed(2) + " on the sample, " + (1 - f.w).toFixed(2) + " on the " + pf(f.driftLR, 1) + " long run average, as of " + fmtDate(f.asOf)) +
    (sub ? '<div class="note" style="margin-top:10px">' + sub + "</div>" : ""));
}
function renderUsFans() {
  document.getElementById("usFans").innerHTML =
    fanCard("SPY", "SPY, twelve months", "The centre of this fan is the 98 year average, not a prediction. The width is the part that carries information: it is the last five years of daily moves, resampled in blocks, with today's volatility scaling the first sixty days.") +
    fanCard("QQQ", "QQQ, twelve months", "Same construction. Fans are recorded on the first post run of each month so that a year from now the realised path can be scored against this one with pinball loss.");
}

/* ---- recommendation cards */
function renderUsRecs() {
  var sec = document.getElementById("usRecSec"), el = document.getElementById("usRecs"), banner = document.getElementById("usRecBanner");
  var have = U.recs && U.recs.etf && U.recs.etf.picks;
  var show = have && U.dayNumber > (U.gateDays || 7);
  sec.hidden = !show;
  if (!show) { el.innerHTML = ""; banner.innerHTML = ""; return; }
  banner.innerHTML = U.gateOpen ? "" :
    '<div class="usgate"><b>Data degraded, gate closed.</b> This is the last recommendation issued, shown for reference. ' +
    "Do not act on it until the checks pass again.<ul>" + (U.gateReasons || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul></div>";
  document.getElementById("usRecLede").textContent =
    "Issued " + fmtDate(U.recs.etf.date) + " for " + esc(U.recs.etf.month) + ", held until " + fmtDate(U.recs.nextIssue) +
    ". Trend filter, 12-1 momentum ranked per unit of volatility, inverse vol weights, sleeve vol target (ETF 25%, stock 50%), 3x line capped at $10. The lines scale with the budget boxes above.";
  el.innerHTML = recCard("etf") + recCard("stock");
}
function recCard(sleeve) {
  var r = scaledRec(sleeve);
  if (!r) return card(sleeve === "etf" ? "ETF sleeve" : "Stock sleeve", '<div class="note">No recommendation recorded.</div>');
  var rec = r.rec, h = "";
  /* the empty case is a note, not a table row: the 520px table scrolls on a phone and would clip the sentence */
  if (!r.picks.length) h += '<div class="note">Nothing qualified this month: no line survived the trend filter, the earnings skip and the sizing caps at the $3 minimum.</div>';
  else h += '<div class="tblwrap"><table class="picks" style="min-width:520px"><thead><tr><th>Pick</th><th>$</th><th>Weight</th><th>12-1 mom</th><th>Vol</th></tr></thead><tbody>';
  r.picks.forEach(function (p) {
    h += "<tr><td><b>" + esc(p.sym) + "</b>" + (p.lev > 1 ? ' <span class="tag us">' + p.lev + "x</span>" : "") +
      ((p.flags || []).indexOf("cryptoAdjacent") >= 0 ? ' <span class="tag cfm">confirm in app</span>' : "") +
      '<span style="display:block;color:var(--tx3);font-size:11px;white-space:normal">' + esc(p.name || "") + ". " + esc(p.why || "") + "</span></td>" +
      "<td><b>" + usdW(p.usd) + "</b></td><td>" + pu(r.deployed ? p.usd / r.deployed : 0, 0) + "</td><td>" + pf(p.mom, 0) + "</td><td>" + pu(p.sigma, 0) + "</td></tr>";
  });
  if (r.picks.length) h += "</tbody></table></div>";
  if (rec.skipped && rec.skipped.length) {
    h += '<div class="note warn" style="margin-top:10px"><b>Skipped, not banned:</b> ' + rec.skipped.map(function (s) { return esc(s.sym) + " (" + esc(s.why) + ")"; }).join("; ") + "</div>";
  }
  var c = usCostLine(r.deployed);
  h += '<div style="margin-top:10px">';
  h += driverRow("Deployed", usdW(r.deployed) + " of " + usdW(r.budget), "regime " + esc(rec.regime) + ", VIX ratio " + (+rec.vixRatio).toFixed(2) + ", deploy fraction " + pu(rec.deployFrac, 0));
  h += driverRow("Held", usdW(r.held), r.held > 0 ? "rolls into next month's hold bucket" : "nothing held back");
  h += driverRow("Baseline", (rec.baseline || []).map(function (b) { return usdW(b.usd * r.f) + " " + esc(b.sym); }).join(" + "), "what the paper record is measured against");
  h += driverRow("Costs", usd(c.lo) + " to " + usd(c.hi), pu(c.pctLo, 2) + " to " + pu(c.pctHi, 2) + ", brokerage plus GST plus FX markup band");
  if (rec.paper && !rec.paper.since) {
    h += driverRow("Paper", "unfilled", "the paper book fills at the next trading day's open after the recommendation" + (rec.shadow ? ", shadow" : ""));
  } else if (rec.paper) {
    var p = rec.paper;
    h += driverRow("Paper, since " + fmtDate(p.since), pf(p.ret),
      "baseline " + pf(p.baselineRet) + ": " + usd(p.value) + " vs " + usd(p.baselineValue) + " on " + usd(p.invested) + " invested, filled at the next open, marked at adjusted close" + (rec.shadow ? ", shadow" : ""));
  }
  h += "</div>";
  var fk = sleeve + "Basket";
  if (U.fans && U.fans[fk]) {
    h += '<div class="hl-sub" style="margin-top:14px">Basket, twelve months</div>' + svgFan(U.fans[fk], sleeve + " basket") +
      '<div class="clegend"><span><i style="background:var(--us);opacity:.3"></i>5 to 95</span><span><i style="background:var(--us);opacity:.6"></i>25 to 75</span><span><i style="background:var(--us)"></i>median</span></div>';
  }
  return card((sleeve === "etf" ? "ETF sleeve, " : "Stock sleeve, ") + usdW(r.budget) + " a month", h);
}

/* ---- diagnostics */
function renderUsDiag() {
  var el = document.getElementById("usDiag"), h = "";
  ["SPY", "QQQ"].forEach(function (sym) {
    var d = U.diagnostics && U.diagnostics[sym];
    if (!d) { h += card(sym, '<div class="note">No diagnostics recorded.</div>'); return; }
    var b = "";
    Object.keys(d.vr || {}).forEach(function (q) {
      var v = d.vr[q];
      b += driverRow("Variance ratio, q " + esc(q), (+v.vr).toFixed(3), "robust z " + (v.z >= 0 ? "+" : "") + (+v.z).toFixed(2) + (Math.abs(v.z) < 1.96 ? ", random walk not rejected" : ", rejects random walk"));
    });
    if (d.hurst) b += driverRow("Hurst, Lo modified R/S", (+d.hurst.h).toFixed(3), "noise band " + (+d.hurst.lo).toFixed(2) + " to " + (+d.hurst.hi).toFixed(2) +
      (d.hurst.h >= 0.45 && d.hurst.h <= 0.55 ? ", reads as noise" : d.hurst.h > 0.55 ? ", mild persistence" : ", mild mean reversion"));
    if (d.overnight) b += driverRow("Overnight share of variance", pu(d.overnight.share, 0), "mean gap " + pf(d.overnight.meanGap, 3) + ", mean open to close " + pf(d.overnight.meanIntraday, 3));
    b += driverRow("Autocorrelation", "lag 1 " + (+d.ac1).toFixed(3) + ", lag 5 " + (+d.ac5).toFixed(3), "of daily log returns");
    h += card(sym, b + '<div class="note" style="margin-top:10px">Display only. If any of these ever looked exploitable the honest response is suspicion, not a position.</div>');
  });
  el.innerHTML = h;
}

/* Long score history from the ledger Worker. The page must behave exactly the same when
   the Worker is missing, so every failure path is silent and the build's daily list stays. */
function fetchUsHistory() {
  if (!U || S.usFetched || typeof fetch !== "function") return;
  S.usFetched = true;
  var from = new Date(Date.now() - 400 * 86400000).toISOString().slice(0, 10);
  try {
    fetch(LEDGER + "/ledger?kind=score&from=" + from + "&limit=5000", {mode: "cors"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.rows || !j.rows.length) return;
        var by = {};
        j.rows.forEach(function (s) {
          if (!s || !s.date) return;
          var d = by[s.date] || (by[s.date] = {date: s.date, n: 0, hit: 0, crps: 0, crpsB: 0, cov: 0, brier: 0, brierC: 0});
          d.n += 1; d.hit += s.hit ? 1 : 0; d.crps += +s.crps || 0; d.cov += s.cov90 ? 1 : 0; d.brier += +s.brier || 0;
          d.crpsB += s.base ? +s.base.crps || 0 : 0; d.brierC += s.base ? +s.base.brierClim || 0 : 0;
        });
        var daily = Object.keys(by).sort().map(function (k) {
          var d = by[k];
          return {date: d.date, n: d.n, hitRate: d.hit / d.n, crpsSkill: d.crpsB ? 1 - d.crps / d.crpsB : 0,
                  cov90: d.cov / d.n, brierSkill: d.brierC ? 1 - d.brier / d.brierC : 0};
        });
        if (daily.length <= ((U.scorecard || {}).daily || []).length) return;
        S.usDaily = daily;
        if (isUs()) renderUsScoreChart();
      })
      .catch(function () {});
  } catch (e) {}
}

function renderUsAll() {
  if (!U) { document.getElementById("usOnly").hidden = true; return; }
  renderUsBudget(); renderUsReadings(); renderUsWeights(); renderUsPred();
  renderUsScore(); renderUsRecs(); renderUsFans(); renderUsDiag();
  fetchUsHistory();
}

function renderAll() {
  renderStaleness();
  /* A board whose data source failed in the last build must not render as an empty page. */
  if (isMulti() && !B()) S.board = "signal";
  BOARDS.forEach(function (b) {
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

  var multi = isMulti(), us = isUs();
  document.getElementById("multiOnly").hidden = !multi;
  document.getElementById("metalOnly").hidden = multi || us;
  document.getElementById("gaugewrap").hidden = multi || us;
  /* The daily cadence is a gold and silver idea. Six sleeves cannot be bought daily at
     any sane ticket size, so the option is hidden rather than left to fail quietly. */
  document.getElementById("mDay").hidden = multi || us;
  if ((multi || us) && S.mode === "day") S.mode = "month";
  /* The US board is dollars and two sleeves: the rupee amount modes and the split card
     do not apply, so its own section replaces them. */
  document.getElementById("modes").hidden = us;
  document.getElementById("splitgrid").hidden = us;
  document.getElementById("usOnly").hidden = !us || !U;

  renderTicker(); renderHeadline();
  if (!us) { renderSplit(); renderDrivers(); }
  if (us) renderUsAll();
  else if (multi) { renderSleeves(); renderCorr(); renderBacktest(); renderRoute(); }
  else { renderInstruments(); renderIndicators(); renderPrices(); }
  renderMethod();
  document.getElementById("footmeta").innerHTML =
    "Rebuilt " + esc(D.generatedIst) + " from LBMA benchmark fixings" +
    (spliced() ? " to " + esc(D.history.lbmaTo) + " and daily spot closes to " + esc(D.asOfFix) : " (" + esc(D.asOfFix) + ")") +
    ", AMFI daily NAVs and ECB reference rates. Rules and charges last reviewed " + esc(I.updated) + "." +
    (us && U ? " US panel rebuilt " + esc(U.generatedIst) + " (" + esc(U.session) + " run) from " + esc(U.sources.history) + ", " +
      esc(U.sources.quotes) + ", " + esc(U.sources.vix) + " and " + esc(U.sources.tbill) + "." : "");
}

/* ---------------------------------------------------------------- wire up */
function on(id, fn) { document.getElementById(id).addEventListener("click", fn); }
BOARDS.forEach(function (b) {
  on("tab" + b.charAt(0).toUpperCase() + b.slice(1), function () {
    S.board = b; localStorage.setItem("mgf.board", b); renderAll();
  });
});
function applyUsBudget() {
  var e = Math.round(+document.getElementById("usEtf").value), s = Math.round(+document.getElementById("usStock").value);
  if (e >= 1 && e <= 100000) { S.usEtf = e; localStorage.setItem("mgf.usEtf", e); }
  if (s >= 1 && s <= 100000) { S.usStock = s; localStorage.setItem("mgf.usStock", s); }
  renderAll();
}
on("usBudgetGo", applyUsBudget);
["usEtf", "usStock"].forEach(function (id) {
  document.getElementById(id).addEventListener("keydown", function (e) { if (e.key === "Enter") applyUsBudget(); });
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

/* screenshot hooks: #shot=signal|safe|growth|offshore|us&theme=light|dark&mode=month|day */
(function () {
  var m = /shot=(\w+)/.exec(location.hash);
  if (m && BOARDS.indexOf(m[1]) >= 0) { S.board = m[1]; }
  var t = /theme=(\w+)/.exec(location.hash);
  if (t) setTheme(t[1] === "light" ? "light" : "dark");
  var d = /mode=(\w+)/.exec(location.hash);
  if (d) S.mode = d[1];
  if (m || t || d) renderAll();
})();

})();
