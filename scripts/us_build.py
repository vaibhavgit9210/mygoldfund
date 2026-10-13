#!/usr/bin/env python3
"""
mygoldfund US board: the orchestrator. Runs twice a trading day from .github/workflows/us.yml.

  python3 scripts/us_build.py --session auto|pre|post [--force] [--offline]
  python3 scripts/us_build.py --rebuild-usjs

Post run (16:25 ET), in order: refresh history -> quotes -> outcomes for the day -> score every
forecast in the ledger whose target has a close and is not yet scored -> update ACI / Hedge
state -> refit the weekly models when due -> forecasts for the next trading day -> signals,
regime, recommendation (issued on the first post run of each month, shadow until the gate opens),
fills at the next open, paper portfolio -> fans -> scorecard over the whole ledger -> gate ->
write us.js, us/state.json, append us/ledger.ndjson -> POST the new rows to the Worker -> exit 0.

Pre run (08:35 ET): quotes only (history is refreshed only for a symbol whose cache is stale),
the post run's forecast for today is conditioned on the overnight gap, readings and us.js with
session "pre", ledger append, POST.

Idempotent: a `run` row for (date, session) already in the ledger means exit 0 unless --force.
--offline makes no network call at all (cached history, no quotes) and is the test mode.
--rebuild-usjs regenerates us.js from the ledger + cache + state without fetching anything.

The ledger is append only. A row that has to change (a rec gaining its fill) is appended again
with the same natural key; readers take the last row per key and the Worker's INSERT OR REPLACE
does the same. Set US_OUT=<dir> to write ledger/state/us.js somewhere else (tests).

Stdlib only, on purpose.
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone, time as dtime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import us_models as M          # noqa: E402
import us_data as DL           # noqa: E402
import us_universe as UV       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("US_OUT") or ROOT
US_DIR = os.path.join(OUT, "us")
LEDGER_PATH = os.path.join(US_DIR, "ledger.ndjson")
STATE_PATH = os.path.join(US_DIR, "state.json")
USJS_PATH = os.path.join(OUT, "us.js")

DENSITY = ("rw_drift", "gjr_zero", "har_dens", "trend_regime", "hmm_mix")
VARIANCE = ("ewma", "gjr", "har")
REFIT_DAYS = 7
SCHED = {"pre": (8, 35), "post": (16, 25)}
SESSION_ORDER = {"pre": 0, "post": 1}
KEYS = {
    "run": ("date", "session"), "reading": ("date", "session", "sym"),
    "forecast": ("target", "made", "session", "sym"), "score": ("date", "session", "sym"),
    "rec": ("month", "sleeve", "date"), "fan": ("date", "sym"), "paper": ("date", "sleeve"),
}
DEGRADED_FALLBACKS = 5     # symbols from cache/curl that make a run "degraded"
MIN_QUOTED = 50            # fewer quoted symbols than this = the quote call failed
EARNINGS_DAYS = 5
BASELINE = {"etf": [{"sym": "VOO", "usd": 15}, {"sym": "QQQM", "usd": 15}], "stock": [{"sym": "SPY", "usd": 20}]}
USJS_KEYS = ("generatedAt", "generatedIst", "session", "date", "dayNumber", "gateDays", "gateOpen", "gateReasons",
             "runs", "market", "universe", "tickers", "scorecard", "weights", "recs", "paper", "fans",
             "diagnostics", "costs", "constants", "sources", "notes")

SOURCES = {"history": "Yahoo Finance v8 chart, adjusted close",
           "quotes": "Yahoo Finance v7 quote, pre and post market",
           "vix": "Cboe daily index CSVs, latest from Yahoo",
           "tbill": "US Treasury daily bill rates, 13 week coupon equivalent"}
NOTES = {
    "sevenDays": ("Seven days of scores cannot validate a direction call: the confidence interval on a seven day "
                  "hit rate is plus or minus 37 points. The recommendations do not come from the daily predictions. "
                  "They come from rules with decades of evidence: a trend filter, 12-1 momentum, inverse vol "
                  "sizing to a sleeve vol target (25% ETFs, 50% stocks), and VIX term structure risk off."),
    "direction": ("Daily direction is close to a coin flip in every honest study. The daily forecast is a full "
                  "density (mean, sigma, probability up, conformal band) scored every day against a random walk "
                  "with drift and against climatology. Volatility has real measurable skill; direction mostly does "
                  "not, and the scorecard says which is which."),
    "leverage": ("A 3x ETF resets daily, so its return over a month is not three times the index return. It decays "
                 "in choppy markets and it can lose most of its value in a crash. It is only ever one capped line, "
                 "buy and hold, never rebalanced."),
}

_T0 = time.time()


def log(msg):
    sys.stderr.write("us_build: %s\n" % msg)
    sys.stderr.flush()


class Stage:
    """Prints the wall time of each stage, the run must stay under four minutes."""
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()
        return self

    def __exit__(self, *a):
        log("stage %-22s %6.2f s  (total %5.1f s)" % (self.name, time.time() - self.t, time.time() - _T0))


# ---------------------------------------------------------------- small helpers

def g6(x):
    """Six significant figures, the ledger does not need more and it halves the file."""
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return float("%.6g" % x)
    return x


def compact(o):
    if isinstance(o, dict):
        return {k: compact(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [compact(v) for v in o]
    return g6(o)


def iso_utc(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ist_label(dt_utc):
    ist = dt_utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return "%d %s, %s IST" % (ist.day, ist.strftime("%b %Y"), ist.strftime("%H:%M"))


def days_between(a, b):
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def month_of(d):
    return d[:7]


def first_trading_day_of_next_month(d):
    y, m = int(d[:4]), int(d[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    first = "%04d-%02d-01" % (y, m)
    return first if DL.is_trading_day(first) else DL.next_trading_day(first)


def mean(v):
    return sum(v) / len(v) if v else None


def safe_div(a, b):
    return a / b if (a is not None and b) else None


# ---------------------------------------------------------------- ledger and state

class Ledger:
    """The ndjson ledger in memory: every row by (kind, natural key), last row per key wins, plus
    the rows added in this run (what gets appended and POSTed)."""

    def __init__(self, path):
        self.path = path
        self.by = {k: {} for k in KEYS}
        self.new = []
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        log("ledger: skipping an unparsable line")
                        continue
                    self._put(row)

    @staticmethod
    def key(row):
        return tuple(row.get(k) for k in KEYS[row["kind"]])

    def _put(self, row):
        kind = row.get("kind")
        if kind not in KEYS:
            return
        self.by[kind][self.key(row)] = row

    def add(self, row):
        row = compact(row)
        self._put(row)
        self.new.append(row)

    def rows(self, kind):
        return list(self.by[kind].values())

    def get(self, kind, *key):
        return self.by[kind].get(tuple(key))

    def flush(self):
        if not self.new:
            return 0
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a") as f:
            for row in self.new:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        return len(self.new)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            try:
                return json.load(f)
            except ValueError as e:
                sys.exit("%s is not valid JSON (%s); repair it or restore it from git, a missing file "
                         "would silently reset every learner" % (STATE_PATH, e))
    return {"dayNumber": 0, "firstDate": None, "runs": [], "tickers": {}}


def save_state(state):
    os.makedirs(US_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(compact(state), f, separators=(",", ":"))
    os.replace(tmp, STATE_PATH)


def ticker_state(state, sym):
    return state["tickers"].setdefault(sym, {
        "gjr": None, "hmm": None,
        "hedge": {"density": {k: 0.0 for k in DENSITY}, "variance": {k: 0.0 for k in VARIANCE}, "t": 0},
        "aci": {"alpha90": 0.10, "alpha50": 0.50, "scores": []},
        "overnightShare": None, "earnings": None,
    })


# ---------------------------------------------------------------- dates and sessions

def pick_target(session, et):
    """The trading day a run is about and how late it is against its cron slot. Pre: today if the
    close has not happened, else the next trading day. Post: today once the close has happened,
    else the last completed trading day."""
    today = et.date()
    hm = et.hour * 60 + et.minute
    trading = DL.is_trading_day(today)
    close_hm = (13 if DL.is_early_close(today) else 16) * 60
    if session == "pre":
        D = today.isoformat() if (trading and hm < close_hm) else DL.next_trading_day(today)
    else:
        D = today.isoformat() if (trading and hm >= close_hm) else DL.prev_trading_day(today)
    h, m = SCHED[session]
    sched = datetime.combine(datetime.strptime(D, "%Y-%m-%d").date(), dtime(h, m)).replace(tzinfo=et.tzinfo)
    late = int((et - sched).total_seconds() // 60)
    return D, max(0, late)


def history_cut(D, session):
    """Last bar date a run may use: the day itself after the close, the previous trading day before."""
    return D if session == "post" else DL.prev_trading_day(D)


def rows_upto(rows, cut):
    return [r for r in rows if r["date"] <= cut and r.get("close") and r.get("adjclose")]


def ohlc_rows(rows):
    """Bars with a full open/high/low, what the range estimators and gap returns need."""
    return [r for r in rows if r.get("open") and r.get("high") and r.get("low")]


# ---------------------------------------------------------------- per ticker maths

def signals(sym, rows, spy_rows):
    """Everything the view and the recommendation engine read off one history (rows <= cut)."""
    adj = [r["adjclose"] for r in rows]
    close = [r["close"] for r in rows]
    dates = [r["date"] for r in rows]
    r = M.log_returns(adj)
    out = {"bars": len(rows), "asOf": dates[-1] if dates else None}
    out["d1"] = (adj[-1] / adj[-2] - 1.0) if len(adj) >= 2 else None
    out["m1"] = M.ret_1m(adj)
    out["m6"] = M.mom_6(adj)
    out["mom"] = M.mom_12_1(adj)
    ytd = None
    if dates:
        y = dates[-1][:4]
        prev = [a for a, d in zip(adj, dates) if d < y + "-01-01"]
        if prev and prev[-1] > 0:
            ytd = adj[-1] / prev[-1] - 1.0
    out["ytd"] = ytd
    ts = M.trend_state(adj, dates)
    out["trend"] = {"above200": ts["above200"], "above10m": ts["above10m"], "state": ts["state"],
                    "sma200": ts["sma200"], "sma10m": ts["sma10m"]}
    out["hi52"] = {"high": max(close[-252:]) if close else None, "ratio": M.hi52_ratio(close)}
    out["sigmaAnn"] = M.ann_sigma(math.sqrt(M.ewma_var(r[-60:]))) if len(r) >= 20 else None
    es = M.ewma_var_series(r)
    vr = M.vol_regime([math.sqrt(v) for v in es]) if es else {"pct": None, "regime": "low"}
    out["volPct"], out["volRegime"] = vr["pct"], vr["regime"]
    gaps = M.gap_returns(ohlc_rows(rows))
    out["overnightShare"] = M.overnight_share(gaps, r) if len(r) >= 20 and len(gaps) >= 20 else None
    # beta on SPY over the last 60 common dates
    beta = None
    if spy_rows is not None and len(rows) > 61 and UV.sleeve_of(sym) != "index":
        spy_adj = {x["date"]: x["adjclose"] for x in spy_rows if x["adjclose"] > 0}
        a = {d: v for d, v in zip(dates, adj) if v > 0}
        common = [d for d in dates if d in spy_adj and d in a][-61:]
        if len(common) >= 61:
            rs = [math.log(a[common[i]] / a[common[i - 1]]) for i in range(1, len(common))]
            rb = [math.log(spy_adj[common[i]] / spy_adj[common[i - 1]]) for i in range(1, len(common))]
            beta = M.beta_60(rs, rb)
    out["beta60"] = beta
    return out


def refit_due(entry, D):
    return entry is None or not entry.get("params") or days_between(entry["fitDate"], D) >= REFIT_DAYS


def model_ticker(sym, rows, tst, D):
    """One ticker's variance experts, density experts, Hedge weights, combined density and conformal
    bands as of the last bar in rows. Refits GJR and the HMM when a week has passed. Returns the
    pieces the forecast row and the view need."""
    adj = [r["adjclose"] for r in rows]
    dates = [r["date"] for r in rows]
    r = M.log_returns(adj)
    if len(r) < 30:
        return None
    h_ewma = M.ewma_var(r)
    if refit_due(tst.get("gjr"), D):
        init = (tst.get("gjr") or {}).get("params")
        tst["gjr"] = {"params": M.gjr_garch_fit(r, init=init), "fitDate": D}
    h_gjr = M.gjr_garch_forecast(r, tst["gjr"]["params"])
    yz = M.yang_zhang_series(ohlc_rows(rows), 20)
    h_har = M.har_rv(yz, M.har_fit(yz)) if len(yz) >= 60 else h_ewma
    rh = r[-M.GJR_MAX_OBS:]
    if refit_due(tst.get("hmm"), D):
        init = (tst.get("hmm") or {}).get("params")
        tst["hmm"] = {"params": M.hmm_fit(rh, init=init), "fitDate": D}
    hp = tst["hmm"]["params"]
    p_high = M.hmm_filter(rh, hp)
    mu_h, sd_h = M.hmm_mix(rh, hp)

    hedge = tst["hedge"]
    t = hedge["t"]
    vw = M.shrink_weights(M.hedge_weights([hedge["variance"].get(k, 0.0) for k in VARIANCE], M.hedge_eta(len(VARIANCE))), t)
    hs = [h_ewma, h_gjr, h_har]
    h_combo = M.combine_variance(hs, vw)
    s_combo = math.sqrt(max(1e-12, h_combo))
    dens = {
        "rw_drift": (M.MU_LR_DAILY, math.sqrt(max(1e-12, h_ewma))),
        "gjr_zero": (0.0, math.sqrt(max(1e-12, h_gjr))),
        "har_dens": (0.0, math.sqrt(max(1e-12, h_har))),
        "trend_regime": M.trend_regime(adj, s_combo, dates),
        "hmm_mix": (mu_h, sd_h),
    }
    dw = M.shrink_weights(M.hedge_weights([hedge["density"].get(k, 0.0) for k in DENSITY], M.hedge_eta(len(DENSITY))), t)
    mu, sigma = M.combine_density([dens[k] for k in DENSITY], dw)
    aci = tst["aci"]
    q90 = M.aci_quantile(aci["scores"], aci["alpha90"])
    q50 = M.aci_quantile(aci["scores"], aci["alpha50"])
    lo90, hi90 = M.aci_band(mu, sigma, q90)
    lo50, hi50 = M.aci_band(mu, sigma, q50)
    return {
        "mu": mu, "sigma": sigma, "pUp": M.p_up(mu, sigma),
        "lo90": lo90, "hi90": hi90, "lo50": lo50, "hi50": hi50,
        "alpha90": aci["alpha90"], "alpha50": aci["alpha50"], "q90": q90, "q50": q50,
        "experts": {k: {"mu": dens[k][0], "sigma": dens[k][1], "w": dw[i]} for i, k in enumerate(DENSITY)},
        "varExperts": {k: {"sigma": math.sqrt(max(1e-12, hs[i])), "w": vw[i]} for i, k in enumerate(VARIANCE)},
        "sigmaCombo": s_combo, "pHigh": p_high, "t": t,
    }


def forecast_row(sym, target, made, session, mdl):
    return {"kind": "forecast", "target": target, "made": made, "session": session, "sym": sym,
            "mu": mdl["mu"], "sigma": mdl["sigma"], "pUp": mdl["pUp"],
            "lo90": mdl["lo90"], "hi90": mdl["hi90"], "lo50": mdl["lo50"], "hi50": mdl["hi50"],
            "alpha90": mdl["alpha90"], "alpha50": mdl["alpha50"],
            "experts": mdl["experts"], "varExperts": mdl["varExperts"]}


def pre_forecast(base, gap, share, q90, q50):
    """The post run's forecast for today conditioned on the overnight gap (every density expert and
    the pool), with the same conformal multipliers on the reduced sigma. gap None = unchanged."""
    f = json.loads(json.dumps(base))
    f["session"] = "pre"
    if gap is None:
        return f
    s = share if share is not None else M.OVERNIGHT_CLIP[0]
    for k, e in f["experts"].items():
        e["mu"], e["sigma"] = M.pre_adjust(e["mu"], e["sigma"], gap, s)
    f["mu"], f["sigma"] = M.pre_adjust(base["mu"], base["sigma"], gap, s)
    f["pUp"] = M.p_up(f["mu"], f["sigma"])
    f["lo90"], f["hi90"] = M.aci_band(f["mu"], f["sigma"], q90)
    f["lo50"], f["hi50"] = M.aci_band(f["mu"], f["sigma"], q50)
    return f


def outcome(rows_by_date, dates_sorted, T):
    """(r, gap, intraday, rvYz) for the bar dated T, or None when the bar or its predecessor is missing."""
    if T not in rows_by_date:
        return None
    i = dates_sorted.index(T)
    if i == 0:
        return None
    b, p = rows_by_date[T], rows_by_date[dates_sorted[i - 1]]
    if not (b.get("adjclose") and p.get("adjclose") and b.get("close")):
        return None
    r = math.log(b["adjclose"] / p["adjclose"])
    gap = math.log(b["open"] * b["adjclose"] / b["close"] / p["adjclose"]) if b.get("open") else None
    intra = math.log(b["close"] / b["open"]) if b.get("open") else None
    window = ohlc_rows([rows_by_date[d] for d in dates_sorted[max(0, i - 20):i + 1]])
    rv = M.yang_zhang(window, 20) if len(window) >= 21 else None
    return r, gap, intra, rv


def score_row(f, r, gap, intra, rv, p_clim):
    sc = M.score_forecast(r, f["mu"], f["sigma"], f["pUp"], f["lo90"], f["hi90"], f["lo50"], f["hi50"])
    ve = f.get("varExperts") or {}
    ql = {k: M.qlike(r, v["sigma"] ** 2) for k, v in ve.items()}
    if ve:
        h_combo = M.combine_variance([v["sigma"] ** 2 for v in ve.values()], [v["w"] for v in ve.values()])
        ql["combo"] = M.qlike(r, h_combo)
    rw = (f.get("experts") or {}).get("rw_drift")
    base = {"brier": None, "logs": None, "crps": None, "brierClim": M.brier(p_clim, r)}
    if rw:
        base["brier"] = M.brier(M.p_up(rw["mu"], rw["sigma"]), r)
        base["logs"] = M.logscore(r, rw["mu"], rw["sigma"])
        base["crps"] = M.crps_gauss(r, rw["mu"], rw["sigma"])
    row = {"kind": "score", "date": f["target"], "sym": f["sym"], "session": f["session"],
           "r": r, "gap": gap, "intraday": intra, "rvYz": rv}
    row.update(sc)
    row["qlike"] = ql
    row["base"] = base
    return row


def learn(tst, f, r):
    """ACI and Hedge updates from one scored post run forecast."""
    aci = tst["aci"]
    aci["alpha90"] = M.aci_update(aci["alpha90"], 0 if f["lo90"] <= r <= f["hi90"] else 1, 0.10)
    aci["alpha50"] = M.aci_update(aci["alpha50"], 0 if f["lo50"] <= r <= f["hi50"] else 1, 0.50)
    aci["scores"] = (aci["scores"] + [abs(r - f["mu"]) / max(1e-12, f["sigma"])])[-M.ACI_WINDOW:]
    hedge = tst["hedge"]
    s_ewma = (f.get("varExperts") or {}).get("ewma", {}).get("sigma") or f["sigma"]
    for k, e in (f.get("experts") or {}).items():
        hedge["density"][k] = hedge["density"].get(k, 0.0) + M.crps_gauss(r, e["mu"], e["sigma"]) / max(1e-12, s_ewma)
    for k, e in (f.get("varExperts") or {}).items():
        hedge["variance"][k] = hedge["variance"].get(k, 0.0) + M.qlike(r, e["sigma"] ** 2)
    hedge["t"] = hedge.get("t", 0) + 1


# ---------------------------------------------------------------- readings

def _before_open(ts):
    et = DL.to_et(datetime.fromtimestamp(ts, timezone.utc))
    return et.hour * 60 + et.minute < 9 * 60 + 30


def reading_row(sym, D, session, bar, prev, q, src):
    pre = post = pre_t = post_t = None
    if q:
        if session == "pre" and q.get("preMarketPrice") and q.get("preMarketTime") and DL.et_date(q["preMarketTime"]) == D:
            pre, pre_t = q["preMarketPrice"], iso_utc(q["preMarketTime"])
        if session == "pre" and sym.startswith("^") and q.get("regularMarketPrice") and q.get("regularMarketTime") \
                and DL.et_date(q["regularMarketTime"]) == D and _before_open(q["regularMarketTime"]):
            pre, pre_t = q["regularMarketPrice"], iso_utc(q["regularMarketTime"])   # an index quotes live pre market
        if session == "post" and q.get("postMarketPrice") and q.get("postMarketTime") and DL.et_date(q["postMarketTime"]) == D:
            post, post_t = q["postMarketPrice"], iso_utc(q["postMarketTime"])
    return {"kind": "reading", "date": D, "session": session, "sym": sym,
            "pre": pre, "preTime": pre_t, "post": post, "postTime": post_t,
            "open": bar.get("open") if bar else None, "high": bar.get("high") if bar else None,
            "low": bar.get("low") if bar else None, "close": bar.get("close") if bar else None,
            "adjClose": bar.get("adjclose") if bar else None, "vol": bar.get("volume") if bar else None,
            "prevClose": prev.get("close") if prev else None, "src": src,
            "barDate": bar.get("date") if bar else None}


def bar_from_quote(q, D):
    """A post run whose history pull failed can still take the day's bar from the quote, as long
    as the quote is about D (never the next session's live print)."""
    if not q or not q.get("regularMarketPrice") or not q.get("regularMarketTime"):
        return None
    if DL.et_date(q["regularMarketTime"]) != D:
        return None
    return {"date": D, "open": q.get("regularMarketOpen"), "high": q.get("regularMarketDayHigh"),
            "low": q.get("regularMarketDayLow"), "close": q["regularMarketPrice"],
            "adjclose": q["regularMarketPrice"], "volume": q.get("regularMarketVolume") or 0}


def read_cboe(name):
    path = os.path.join(DL.HIST_DIR, name + ".csv")
    out = {}
    if not os.path.exists(path):
        return out
    import csv
    with open(path, newline="") as f:
        for rec in csv.DictReader(f):
            if rec.get("close"):
                out[rec["date"]] = float(rec["close"])
    return out


def last_on_or_before(series, d):
    ks = [k for k in series if k <= d]
    return series[max(ks)] if ks else None


# ---------------------------------------------------------------- recommendation engine

def why_line(mom, trend, sigma_ann, near_high=False):
    s = "12-1 momentum %+.0f%%, trend %s, vol %.0f%%" % (mom * 100, trend, sigma_ann * 100)
    if near_high:
        s += ", near its 52 week high"
    return s + ", ranked by momentum per unit of vol"


def issue_recs(D, sig, market, state_day, gate_open, earnings):
    """Both sleeves for the month of D. Returns ({sleeve: rec_row}, deploy fraction, failed conditions)."""
    spy = sig["SPY"]
    frac, fails = M.deploy_fraction(spy["trend"]["state"], market["vixRatio"], spy["volRegime"])
    shadow = state_day < M.GATE_DAYS + 1 or not gate_open
    regime = market["regime"]["label"]
    out = {}

    # ETF sleeve
    cands, skipped = [], []
    for sym in UV.etf_symbols():
        if UV.is_leveraged(sym):
            continue
        s = sig.get(sym)
        if not s or s["mom"] is None or not s["sigmaAnn"]:
            skipped.append({"sym": sym, "why": "under 252 bars of history"})
            continue
        cands.append({"sym": sym, "mom": s["mom"], "sigmaAnn": s["sigmaAnn"], "trend": s["trend"]["state"]})
    ranked = M.rank_etf(cands)
    kept = {c["sym"] for c in ranked}
    skipped += [{"sym": c["sym"], "why": "down trend"} for c in cands if c["sym"] not in kept]
    top = ranked[:3]
    # at most one leveraged line: proxy trend up, VIX ratio under 0.95, proxy vol regime low. The
    # gate signals are the proxy's; the line is sized on its own 3x vol (so the sleeve vol target is
    # real) and capped at $10. A proxy that is already one of the two kept picks loses to one that
    # is not, so the sleeve does not end up 4x in one sector.
    lev = None
    kept2 = {c["sym"] for c in top[:2]}
    for sym in UV.etf_symbols():
        if not UV.is_leveraged(sym):
            continue
        proxy = UV.proxy_of(sym)
        p = sig.get(proxy)
        own = sig.get(sym)
        if not p or not own or p["mom"] is None or not p["sigmaAnn"] or not own["sigmaAnn"]:
            continue
        ok = p["trend"]["state"] == "up" and market["vixRatio"] is not None and market["vixRatio"] < 0.95 and p["volRegime"] == "low"
        if not ok:
            continue
        cand = {"sym": sym, "score": p["mom"] / p["sigmaAnn"], "dup": proxy in kept2, "mom": p["mom"],
                "sigmaAnn": p["sigmaAnn"], "ownSigma": own["sigmaAnn"], "proxy": proxy,
                "trend": p["trend"]["state"], "volRegime": p["volRegime"]}
        if lev is None or (lev["dup"], -lev["score"]) > (cand["dup"], -cand["score"]):
            lev = cand
    picks_in = [{"sym": c["sym"], "sigmaAnn": c["sigmaAnn"], "lev": False, "mom": c["mom"], "trend": c["trend"],
                 "show": c["sigmaAnn"]} for c in top]
    if lev:
        picks_in = picks_in[:2] + [{"sym": lev["sym"], "sigmaAnn": lev["ownSigma"], "lev": True, "mom": lev["mom"],
                                    "trend": lev["trend"], "show": lev["ownSigma"]}]
    sized = M.size_sleeve(picks_in, M.BUDGET_ETF, deploy_frac=frac, kelly=False, vol_target=M.VOL_TARGET_ETF)
    byline = {l["sym"]: l for l in sized["lines"]}
    picks = []
    for p in picks_in:
        l = byline.get(p["sym"])
        if not l:
            continue
        if p["lev"]:
            why = ("buy and hold, never rebalance, 3x daily reset, capped at $%d. Signals computed on %s: trend %s, "
                   "VIX ratio %.3f, %s vol, own vol %.0f%%"
                   % (M.LEV_CAP_USD, lev["proxy"], lev["trend"], market["vixRatio"], lev["volRegime"], lev["ownSigma"] * 100))
        else:
            why = why_line(p["mom"], p["trend"], p["sigmaAnn"])
        picks.append({"sym": p["sym"], "usd": l["usd"], "weight": l["weight"], "why": why,
                      "mom": p["mom"], "sigma": p["show"], "lev": 3 if p["lev"] else 1})
    out["etf"] = {"kind": "rec", "month": month_of(D), "date": D, "sleeve": "etf", "shadow": shadow,
                  "budgetUsd": M.BUDGET_ETF, "deployFrac": frac, "regime": regime, "vixRatio": market["vixRatio"],
                  "picks": picks, "heldUsd": sized["heldUsd"], "deployUsd": sized["deployUsd"],
                  "baseline": BASELINE["etf"], "cost": M.cost_line(sized["deployUsd"]), "fill": None,
                  "skipped": skipped, "deployFails": fails}

    # stock sleeve
    cands = []
    for sym in UV.stock_symbols():
        s = sig.get(sym)
        if not s:
            continue
        soon = False
        e = earnings.get(sym)
        if e and D < e:
            d, k = D, 0
            while k < EARNINGS_DAYS:
                d = DL.next_trading_day(d)
                k += 1
                if d == e:
                    soon = True
                    break
        cands.append({"sym": sym, "mom": s["mom"], "trend": s["trend"]["state"], "bars": s["bars"],
                      "hi52": s["hi52"]["ratio"], "earningsSoon": soon, "sigmaAnn": s["sigmaAnn"]})
    ranked, skipped_all = M.rank_stock(cands)
    top = ranked[:3]
    picks_in = [{"sym": c["sym"], "sigmaAnn": c["sigmaAnn"], "lev": False, "mom": c["mom"], "trend": c["trend"],
                 "nearHigh": c.get("nearHigh", False)} for c in top if c["sigmaAnn"]]
    sized = M.size_sleeve(picks_in, M.BUDGET_STOCK, deploy_frac=frac, kelly=False, vol_target=M.VOL_TARGET_STOCK)
    byline = {l["sym"]: l for l in sized["lines"]}
    picks = []
    for p in picks_in:
        l = byline.get(p["sym"])
        if not l:
            continue
        picks.append({"sym": p["sym"], "usd": l["usd"], "weight": l["weight"],
                      "why": why_line(p["mom"], p["trend"], p["sigmaAnn"], p["nearHigh"]),
                      "mom": p["mom"], "sigma": p["sigmaAnn"], "lev": 1})
    # earnings skips shown only when the name would have made the top three
    floor_mom = top[-1]["mom"] if len(top) >= 3 else -1e9
    skipped = [s for s in skipped_all if s["why"].startswith("earnings")
               and (next((c["mom"] for c in cands if c["sym"] == s["sym"]), None) or -1e9) > floor_mom]
    out["stock"] = {"kind": "rec", "month": month_of(D), "date": D, "sleeve": "stock", "shadow": shadow,
                    "budgetUsd": M.BUDGET_STOCK, "deployFrac": frac, "regime": regime, "vixRatio": market["vixRatio"],
                    "picks": picks, "heldUsd": sized["heldUsd"], "deployUsd": sized["deployUsd"],
                    "baseline": BASELINE["stock"], "cost": M.cost_line(sized["deployUsd"]), "fill": None,
                    "skipped": skipped, "deployFails": fails}
    return out, frac, fails


def current_rec(ledger, sleeve, D):
    """The rec in force on D: the latest issued on or before D (the month's, or the last one held over)."""
    rows = [r for r in ledger.rows("rec") if r["sleeve"] == sleeve and r["date"] <= D]
    if not rows:
        return None
    rows.sort(key=lambda r: r["date"])
    return rows[-1]


def fill_recs(ledger, hist_full, D):
    """Append a copy of every unfilled rec with its fill at the next trading day's open, once that
    bar exists for every pick and baseline line."""
    n = 0
    for rec in ledger.rows("rec"):
        if rec.get("fill"):
            continue
        fd = DL.next_trading_day(rec["date"])
        if fd > D:
            continue
        syms = [p["sym"] for p in rec["picks"]] + [b["sym"] for b in rec["baseline"]]
        prices = {}
        for sym in syms:
            bar = next((b for b in hist_full.get(sym, []) if b["date"] == fd), None)
            if not bar or not bar.get("open"):
                prices = None
                break
            prices[sym] = bar["open"]
        if prices is None:
            log("rec %s %s: fill bar %s not available yet" % (rec["month"], rec["sleeve"], fd))
            continue
        new = dict(rec)
        new["fill"] = {"date": fd, "prices": prices}
        ledger.add(new)
        n += 1
    return n


def adj_factor(hist_full, sym, d):
    """adjclose / close on date d (what turns a raw open into an adjusted open)."""
    bar = next((b for b in hist_full.get(sym, []) if b["date"] == d), None)
    if not bar or not bar.get("close") or not bar.get("adjclose"):
        return None
    return bar["adjclose"] / bar["close"]


def paper_value(ledger, hist, sleeve, D):
    """Mark every filled rec of the sleeve at D's adjusted close. Returns the paper row fields."""
    value = invested = bval = binv = 0.0
    since = None
    last = {sym: (rows[-1]["adjclose"] if rows else None) for sym, rows in hist.items()}
    for rec in ledger.rows("rec"):
        if rec["sleeve"] != sleeve or not rec.get("fill") or rec["fill"]["date"] > D:
            continue
        fd = rec["fill"]["date"]
        since = fd if since is None or fd < since else since
        for lines, is_base in ((rec["picks"], False), (rec["baseline"], True)):
            for p in lines:
                sym = p["sym"]
                fac = adj_factor(hist, sym, fd)
                px = rec["fill"]["prices"].get(sym)
                now = last.get(sym)
                if not (fac and px and now):
                    continue
                v = p["usd"] * now / (px * fac)
                if is_base:
                    bval += v
                    binv += p["usd"]
                else:
                    value += v
                    invested += p["usd"]
    return {"value": value, "invested": invested, "baselineValue": bval, "baselineInvested": binv, "since": since}


# ---------------------------------------------------------------- fans

def basket_returns(hist, picks):
    """Daily log return of a usd weighted basket of picks over their common dates (last 5 years)."""
    tot = sum(p["usd"] for p in picks)
    if tot <= 0:
        return []
    series = {}
    for p in picks:
        rows = hist.get(p["sym"], [])
        series[p["sym"]] = {r["date"]: r["adjclose"] for r in rows}
    common = None
    for s in series.values():
        common = set(s) if common is None else common & set(s)
    if not common:
        return []
    dates = sorted(common)[-(5 * M.TRADING_DAYS + 1):]
    out = []
    for i in range(1, len(dates)):
        g = 0.0
        for p in picks:
            s = series[p["sym"]]
            g += p["usd"] / tot * (s[dates[i]] / s[dates[i - 1]] - 1.0)
        out.append(math.log(1.0 + g))
    return out


def fan_for(ledger, D, sym, r, sigma_now):
    """The month's fan for sym: reuse the ledger's if one exists this month, else compute and record."""
    month = month_of(D)
    have = [f for f in ledger.rows("fan") if f["sym"] == sym and month_of(f["date"]) == month]
    if have:
        return sorted(have, key=lambda f: f["date"])[0]
    if len(r) < 60:
        return None
    f = M.block_bootstrap_fan(r, seed=M.fan_seed(sym, month), sigma_now=sigma_now)
    row = {"kind": "fan", "date": D, "sym": sym, "q": f["q"],
           "drift": (math.exp(f["drift"] * M.TRADING_DAYS) - 1.0) if f["drift"] is not None else None,
           "driftLR": M.MU_LR_ANNUAL, "w": f["w"]}
    ledger.add(row)
    return ledger.get("fan", D, sym)


# ---------------------------------------------------------------- scorecard, gate

def _agg(scores):
    n = len(scores)
    if n == 0:
        return {"n": 0, "hitRate": None, "crpsSkill": None}
    hits = sum(1 for s in scores if s["hit"])
    cr = [s["crps"] for s in scores]
    cb = [s["base"]["crps"] for s in scores if s["base"].get("crps") is not None]
    return {"n": n, "hitRate": hits / n, "crpsSkill": M.skill(mean(cr), mean(cb)) if cb else None}


def qlike_nonneg(q, r):
    """Patton's non negative QLIKE r^2/h - ln(r^2/h) - 1 from the stored ln h + r^2/h, so a ratio
    skill is meaningful (the stored form is negative and a ratio of negatives means nothing)."""
    if q is None or r == 0:
        return None
    return q - math.log(r * r) - 1.0


def ticker_stats(scores):
    n = len(scores)
    if n == 0:
        return {"n": 0, "hitRate": None, "brierSkill": None, "crpsSkill": None, "cov90": None, "cov50": None}
    bc = [s["base"]["brierClim"] for s in scores]
    cb = [s["base"]["crps"] for s in scores if s["base"].get("crps") is not None]
    return {"n": n, "hitRate": sum(s["hit"] for s in scores) / n,
            "brierSkill": M.skill(mean([s["brier"] for s in scores]), mean(bc)),
            "crpsSkill": M.skill(mean([s["crps"] for s in scores]), mean(cb)) if cb else None,
            "cov90": sum(s["cov90"] for s in scores) / n, "cov50": sum(s["cov50"] for s in scores) / n}


def scorecard(ledger, paper_daily):
    scores = sorted(ledger.rows("score"), key=lambda s: (s["date"], SESSION_ORDER.get(s["session"], 0), s["sym"]))
    n = len(scores)
    days = sorted({s["date"] for s in scores})
    sc = {"days": len(days), "n": n, "hitRate": None, "hitCI": [0.0, 1.0], "brier": None, "brierBase": None,
          "brierClim": None, "brierSkill": None, "logSkill": None, "crps": None, "crpsBase": None, "crpsSkill": None,
          "cov90": None, "cov50": None, "pitHist": [0] * 10, "varSkill": {"gjr": None, "har": None, "combo": None},
          "dmT": None, "minTrlDays": None, "sharpePaper": None,
          "bySession": {"pre": _agg([]), "post": _agg([])}, "bySleeve": {"etf": _agg([]), "stock": _agg([])},
          "daily": []}
    if n:
        hits = sum(1 for s in scores if s["hit"])
        sc["hitRate"] = hits / n
        sc["hitCI"] = list(M.binom_ci(hits, n))
        sc["brier"] = mean([s["brier"] for s in scores])
        sc["brierClim"] = mean([s["base"]["brierClim"] for s in scores])
        bb = [s["base"]["brier"] for s in scores if s["base"].get("brier") is not None]
        sc["brierBase"] = mean(bb) if bb else None
        sc["brierSkill"] = M.skill(sc["brier"], sc["brierClim"])
        sc["crps"] = mean([s["crps"] for s in scores])
        cb = [s["base"]["crps"] for s in scores if s["base"].get("crps") is not None]
        sc["crpsBase"] = mean(cb) if cb else None
        sc["crpsSkill"] = M.skill(sc["crps"], sc["crpsBase"])
        ld = [s["base"]["logs"] - s["logs"] for s in scores if s["base"].get("logs") is not None]
        sc["logSkill"] = (math.exp(mean(ld)) - 1.0) if ld else None     # geometric mean likelihood ratio minus one
        sc["cov90"] = sum(s["cov90"] for s in scores) / n
        sc["cov50"] = sum(s["cov50"] for s in scores) / n
        for s in scores:
            sc["pitHist"][min(9, int(s["pit"] * 10))] += 1
        for k in ("gjr", "har", "combo"):
            pairs = [(qlike_nonneg(s["qlike"].get(k), s["r"]), qlike_nonneg(s["qlike"].get("ewma"), s["r"]))
                     for s in scores if s.get("qlike")]
            pairs = [p for p in pairs if p[0] is not None and p[1] is not None]
            if pairs:
                sc["varSkill"][k] = M.skill(mean([p[0] for p in pairs]), mean([p[1] for p in pairs]))
        # DM on one loss differential per day (the mean over tickers and sessions): the 58 tickers
        # share a market factor, so pooling them as 116 observations a day rejected a true null
        # 53% of the time at |t| > 1.96 in simulation; the daily series with Newey-West lag 5 is
        # what Diebold and Mariano assume
        by_day = {}
        for s in scores:
            if s["base"].get("crps") is not None:
                by_day.setdefault(s["date"], []).append(s["crps"] - s["base"]["crps"])
        dd = [mean(by_day[d]) for d in sorted(by_day)]
        sc["dmT"] = M.dm_tstat(dd, [0.0] * len(dd)) if dd else None
        for ses in ("pre", "post"):
            sc["bySession"][ses] = _agg([s for s in scores if s["session"] == ses])
        for sl in ("etf", "stock"):
            sc["bySleeve"][sl] = _agg([s for s in scores if UV.sleeve_of(s["sym"]) == sl])
        for d in days:
            ds = [s for s in scores if s["date"] == d]
            a = _agg(ds)
            bc = mean([s["base"]["brierClim"] for s in ds])
            sc["daily"].append({"date": d, "n": a["n"], "hitRate": a["hitRate"], "crpsSkill": a["crpsSkill"],
                                "cov90": sum(s["cov90"] for s in ds) / len(ds),
                                "brierSkill": M.skill(mean([s["brier"] for s in ds]), bc)})
    # paper track record: active daily return of the whole paper book against its baseline
    act = []
    prev = None
    for d in paper_daily:
        v, b = d["etf"] + d["stock"], d["etfBase"] + d["stockBase"]
        inv, binv = d["invested"], d["baselineInvested"]
        if prev and prev[0] > 0 and prev[1] > 0:
            vin = prev[0] + (inv - prev[2])
            bin_ = prev[1] + (binv - prev[3])
            if vin > 0 and bin_ > 0:
                act.append(v / vin - b / bin_)
        prev = (v, b, inv, binv)
    if len(act) >= 10:
        sd = M.stdev(act)
        sc["sharpePaper"] = (M.mean(act) / sd * math.sqrt(M.TRADING_DAYS)) if sd > 0 else None
        sc["minTrlDays"] = M.min_trl(act, 0.0)
        if sc["minTrlDays"] is not None:
            sc["minTrlDays"] = int(math.ceil(sc["minTrlDays"]))
    return sc


def gate(ledger, state, D, session, run_row, sc):
    reasons = []
    dn = state["dayNumber"]
    if dn < M.GATE_DAYS + 1:
        reasons.append("day %d of %d, the recording period is not over" % (dn, M.GATE_DAYS))
    # both sessions on every recent trading day (a day with one run allowed at most twice in the last 10)
    runs = ledger.rows("run")
    if state.get("firstDate"):
        d = D if session == "post" else DL.prev_trading_day(D)
        window = []
        while d >= state["firstDate"] and len(window) < 10:
            window.append(d)
            d = DL.prev_trading_day(d)
        have = {}
        for r in runs:
            have.setdefault(r["date"], set()).add(r["session"])
        partial = [d for d in window if len(have.get(d, ())) < 2]
        if len(partial) > 2:
            reasons.append("%d of the last %d trading days are missing a pre or post run (%s)"
                           % (len(partial), len(window), ", ".join(sorted(partial)[-3:])))
    if run_row.get("degraded"):
        reasons.append("today's run is degraded: %s" % (run_row.get("degradedWhy") or "data fallback"))
    if sc["n"] < 100:
        reasons.append("only %d scored forecasts, 100 needed" % sc["n"])
    scores = sorted(ledger.rows("score"), key=lambda s: s["date"])
    days = sorted({s["date"] for s in scores})[-60:]
    recent = [s for s in scores if s["date"] in set(days)]
    if recent:
        c = sum(s["cov90"] for s in recent) / len(recent)
        if not 0.80 <= c <= 0.97:
            reasons.append("90%% band covered %.0f%% of the last %d scored days, outside 80 to 97" % (c * 100, len(days)))
    else:
        reasons.append("no scored forecasts yet")
    return not reasons, reasons


# ---------------------------------------------------------------- the view (us.js)

def build_view(ledger, state, hist, hist_full, D, session, sig, diag, run_row, sc, gate_open, reasons, tbill):
    cut = history_cut(D, session)
    readings = {r["sym"]: r for r in ledger.rows("reading") if r["date"] == D and r["session"] == session}
    forecasts = {}
    for f in ledger.rows("forecast"):
        if f["session"] == session and f["made"] == D:
            forecasts[f["sym"]] = f
    scores_by_sym = {}
    for s in ledger.rows("score"):
        scores_by_sym.setdefault(s["sym"], []).append(s)
    tickers = {}
    dw_acc = {k: [] for k in DENSITY}
    vw_acc = {k: [] for k in VARIANCE}
    tmax = 0
    for sym in UV.all_symbols():
        sl = UV.sleeve_of(sym)
        if sl == "index":
            continue
        em, sm = UV.etf_meta(sym), UV.stock_meta(sym)
        meta = em or sm or {"name": {"SPY": "SPDR S&P 500", "QQQ": "Invesco QQQ"}.get(sym, sym), "flags": []}
        s = sig.get(sym) or {}
        rd = readings.get(sym) or {}
        f = forecasts.get(sym)
        tst = state["tickers"].get(sym) or {}
        ve = (f or {}).get("varExperts") or {}
        combo = None
        if ve:
            combo = math.sqrt(M.combine_variance([v["sigma"] ** 2 for v in ve.values()], [v["w"] for v in ve.values()]))
            for k in DENSITY:
                if k in f["experts"]:
                    dw_acc[k].append(f["experts"][k]["w"])
            for k in VARIANCE:
                if k in ve:
                    vw_acc[k].append(ve[k]["w"])
        tmax = max(tmax, (tst.get("hedge") or {}).get("t", 0))
        post_scores = sorted([x for x in scores_by_sym.get(sym, []) if x["session"] == "post"], key=lambda x: x["date"])
        ls = post_scores[-1] if post_scores else None
        tickers[sym] = {
            "name": meta["name"], "sleeve": sl, "lev": em["lev"] if em else 1, "proxy": em["proxy"] if em else None,
            "flags": list(meta.get("flags") or []),
            "price": {"prevClose": rd.get("prevClose"), "close": rd.get("close"), "pre": rd.get("pre"),
                      "preTime": rd.get("preTime"), "post": rd.get("post"), "postTime": rd.get("postTime"),
                      "open": rd.get("open"), "high": rd.get("high"), "low": rd.get("low"), "vol": rd.get("vol"),
                      "adjClose": rd.get("adjClose"), "asOf": rd.get("barDate") or s.get("asOf")},
            "ret": {"d1": s.get("d1"), "m1": s.get("m1"), "m6": s.get("m6"), "mom12_1": s.get("mom"), "ytd": s.get("ytd")},
            "vol": {"ewma": (ve.get("ewma") or {}).get("sigma"), "gjr": (ve.get("gjr") or {}).get("sigma"),
                    "har": (ve.get("har") or {}).get("sigma"), "combo": combo,
                    "ann": M.ann_sigma(combo) if combo else s.get("sigmaAnn")},
            "hi52": s.get("hi52") or {"high": None, "ratio": None},
            "beta60": s.get("beta60"),
            "trend": {"above200": (s.get("trend") or {}).get("above200", False),
                      "above10m": (s.get("trend") or {}).get("above10m", False),
                      "state": (s.get("trend") or {}).get("state", "mixed")},
            "overnightShare": tst.get("overnightShare", s.get("overnightShare")),
            "earnings": tst.get("earnings"),
            "forecast": ({"target": f["target"], "made": f["made"], "session": f["session"], "mu": f["mu"],
                          "sigma": f["sigma"], "pUp": f["pUp"], "lo90": f["lo90"], "hi90": f["hi90"],
                          "lo50": f["lo50"], "hi50": f["hi50"], "experts": f["experts"]} if f else None),
            "lastScore": ({"date": ls["date"], "r": ls["r"], "hit": ls["hit"], "brier": ls["brier"],
                           "crps": ls["crps"], "cov90": ls["cov90"]} if ls else None),
            "stats": ticker_stats(scores_by_sym.get(sym, [])),
        }

    def mk(sym):
        s = sig.get(sym) or {}
        rd = readings.get(sym) or {}
        t = s.get("trend") or {}
        return {"close": rd.get("close"), "prevClose": rd.get("prevClose"), "ret1d": s.get("d1"),
                "sma200": t.get("sma200"), "sma10m": t.get("sma10m"), "above200": t.get("above200", False),
                "above10m": t.get("above10m", False), "trend": t.get("state", "mixed")}

    def idx(sym):
        rd = readings.get(sym) or {}
        if session == "pre" and rd.get("pre") is not None:
            return rd["pre"]
        return rd.get("close")

    vix, vix3m, vix9d = idx("^VIX"), idx("^VIX3M"), idx("^VIX9D")
    ratio = safe_div(vix, vix3m)
    spy = sig.get("SPY") or {}
    spy_state = state["tickers"].get("SPY") or {}
    hp = (spy_state.get("hmm") or {}).get("params") or {}
    spy_rows = hist.get("SPY", [])
    p_high = None
    if hp and spy_rows:
        r_spy = M.log_returns([x["adjclose"] for x in spy_rows])[-M.GJR_MAX_OBS:]
        p_high = M.hmm_filter(r_spy, hp)
    market = {
        "spy": mk("SPY"), "qqq": mk("QQQ"),
        "vix": vix, "vix3m": vix3m, "vix9d": vix9d, "vixRatio": ratio,
        "backwardation": (ratio > 1.0) if ratio is not None else False,
        "tbill": tbill,
        "regime": {"trend": (spy.get("trend") or {}).get("state", "mixed"), "vol": spy.get("volRegime", "low"),
                   "volPct": spy.get("volPct"),
                   "label": "%s trend, %s vol" % ((spy.get("trend") or {}).get("state", "mixed"), spy.get("volRegime", "low"))},
        "hmm": {"pHigh": p_high, "sdLo": (hp.get("sd") or [None, None])[0], "sdHi": (hp.get("sd") or [None, None])[1],
                "fitDate": (spy_state.get("hmm") or {}).get("fitDate")},
    }

    # recs in force
    recs = {"etf": None, "stock": None, "nextIssue": first_trading_day_of_next_month(D)}
    for sl in ("etf", "stock"):
        rec = current_rec(ledger, sl, D)
        if not rec:
            continue
        pv = paper_value(ledger, hist, sl, D)
        picks = []
        for p in rec["picks"]:
            em, sm = UV.etf_meta(p["sym"]), UV.stock_meta(p["sym"])
            meta = em or sm or {"name": p["sym"], "flags": []}
            q = dict(p)
            q["name"] = meta["name"]
            q["flags"] = list(meta.get("flags") or [])
            picks.append(q)
        recs[sl] = {"month": rec["month"], "date": rec["date"], "shadow": rec["shadow"], "budgetUsd": rec["budgetUsd"],
                    "deployFrac": rec["deployFrac"], "deployUsd": rec.get("deployUsd", sum(p["usd"] for p in rec["picks"])),
                    "heldUsd": rec["heldUsd"], "regime": rec["regime"], "vixRatio": rec["vixRatio"], "picks": picks,
                    "baseline": rec["baseline"], "cost": rec["cost"], "skipped": rec.get("skipped") or [],
                    "paper": {"invested": pv["invested"], "value": pv["value"],
                              "ret": (pv["value"] / pv["invested"] - 1.0) if pv["invested"] else None,
                              "baselineValue": pv["baselineValue"],
                              "baselineRet": (pv["baselineValue"] / pv["baselineInvested"] - 1.0) if pv["baselineInvested"] else None,
                              "since": pv["since"]}}

    # paper daily
    pd = {}
    for p in ledger.rows("paper"):
        d = pd.setdefault(p["date"], {"date": p["date"], "etf": 0.0, "etfBase": 0.0, "stock": 0.0, "stockBase": 0.0})
        d[p["sleeve"]] = p["value"]
        d[p["sleeve"] + "Base"] = p["baselineValue"]
    paper = {"daily": [pd[k] for k in sorted(pd)]}

    # fans: this month's rows, else the latest per symbol
    fans = {}
    for f in sorted(ledger.rows("fan"), key=lambda f: f["date"]):
        if f["date"] <= D:
            fans[f["sym"]] = {"q": f["q"], "drift": f["drift"], "driftLR": f["driftLR"], "w": f["w"], "asOf": f["date"]}

    wd = {k: (mean(dw_acc[k]) if dw_acc[k] else 1.0 / len(DENSITY)) for k in DENSITY}
    wv = {k: (mean(vw_acc[k]) if vw_acc[k] else 1.0 / len(VARIANCE)) for k in VARIANCE}

    runs = sorted(ledger.rows("run"), key=lambda r: (r["date"], SESSION_ORDER.get(r["session"], 0)))[-14:]
    now = datetime.now(timezone.utc)
    view = {
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "generatedIst": ist_label(now),
        "session": session, "date": D, "dayNumber": state["dayNumber"], "gateDays": M.GATE_DAYS,
        "gateOpen": gate_open, "gateReasons": reasons,
        "runs": [{"date": r["date"], "session": r["session"], "ranAt": r["ranAt"], "lateMin": r["lateMin"],
                  "degraded": r["degraded"]} for r in runs],
        "market": market,
        "universe": {"etf": [UV.etf_meta(s) for s in UV.etf_symbols()], "stock": [UV.stock_meta(s) for s in UV.stock_symbols()]},
        "tickers": tickers,
        "scorecard": sc,
        "weights": {"density": wd, "variance": wv, "t": tmax},
        "recs": recs,
        "paper": paper,
        "fans": fans,
        "diagnostics": diag,
        "costs": {"brokerage": M.BROKERAGE, "gst": M.GST, "fxBand": list(M.FX_BAND)},
        "constants": {"muLR": M.MU_LR_ANNUAL, "ewmaLambda": M.EWMA_LAMBDA, "aciGamma": M.ACI_GAMMA,
                      "volTargetEtf": M.VOL_TARGET_ETF, "volTargetStock": M.VOL_TARGET_STOCK,
                      "levCapUsd": M.LEV_CAP_USD, "gateDays": M.GATE_DAYS},
        "sources": SOURCES,
        "notes": NOTES,
    }
    assert tuple(view.keys()) == USJS_KEYS
    return view


def write_usjs(view):
    tmp = USJS_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write("// generated by scripts/us_build.py, do not edit (window.MGF_US, DESIGN.md section 7)\n")
        f.write("window.MGF_US = ")
        f.write(json.dumps(compact(view), indent=1))
        f.write(";\n")
    os.replace(tmp, USJS_PATH)


def diagnostics(hist):
    out = {}
    for sym in ("SPY", "QQQ"):
        rows = hist.get(sym, [])
        r = M.log_returns([x["adjclose"] for x in rows])[-5 * M.TRADING_DAYS:]
        if len(r) < 200:
            out[sym] = {"vr": {str(q): {"vr": None, "z": None} for q in (2, 5, 10, 20)},
                        "hurst": {"h": None, "lo": None, "hi": None},
                        "overnight": {"share": None, "meanGap": None, "meanIntraday": None}, "ac1": None, "ac5": None}
            continue
        od = M.overnight_decomposition(ohlc_rows(rows), 250)
        out[sym] = {"vr": {str(q): M.variance_ratio(r, q) for q in (2, 5, 10, 20)},
                    "hurst": M.hurst_rs(r),
                    "overnight": {"share": od["share"], "meanGap": od["meanGap"], "meanIntraday": od["meanIntraday"]},
                    "ac1": M.autocorr(r, 1), "ac5": M.autocorr(r, 5)}
    return out


# ---------------------------------------------------------------- Worker

def post_ledger(rows):
    key, url = os.environ.get("LEDGER_KEY"), os.environ.get("LEDGER_URL")
    if not key or not url or not rows:
        log("worker POST skipped (%s)" % ("no new rows" if not rows else "LEDGER_KEY/LEDGER_URL unset"))
        return
    url = url.rstrip("/") + "/record"
    for i in range(0, len(rows), 1500):
        chunk = rows[i:i + 1500]
        body = json.dumps({"rows": chunk}, separators=(",", ":")).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                              "User-Agent": "mygoldfund-us-build"})
        try:
            with urllib.request.urlopen(req, timeout=30) as f:
                log("worker POST %d rows: %s" % (len(chunk), f.read().decode()[:160]))
        except Exception as e:  # noqa: BLE001
            log("worker POST failed (%d rows), the ledger file is still the source of truth: %s" % (len(chunk), str(e)[:160]))
            return


# ---------------------------------------------------------------- the run

def run(session, force, offline):
    sess_now, et = DL.session_now()
    ledger = Ledger(LEDGER_PATH)
    if session == "auto":
        if sess_now == "closed":
            # a pre cron that GitHub fired late (after 09:30 ET) still runs as that day's pre run,
            # with lateMin recorded; without this the whole session is lost for the day
            hm = et.hour * 60 + et.minute
            late_pre = (DL.is_trading_day(et.date()) and 9 * 60 + 30 <= hm < 16 * 60 + 15
                        and ledger.get("run", et.date().isoformat(), "pre") is None)
            if not late_pre:
                print("market closed at %s ET, nothing to do" % et.strftime("%Y-%m-%d %H:%M"))
                return 0
            sess_now = "pre"
        session = sess_now
    D, late = pick_target(session, et)
    cut = history_cut(D, session)
    state = load_state()
    if ledger.get("run", D, session) and not force:
        print("%s run for %s already in the ledger, use --force to redo it" % (session, D))
        return 0
    log("%s run for %s (now %s ET, %d min after the %02d:%02d slot, history cut %s%s)"
        % (session, D, et.strftime("%Y-%m-%d %H:%M"), late, SCHED[session][0], SCHED[session][1], cut,
           ", offline" if offline else ""))
    syms = UV.all_symbols()

    # 1. history
    hist_full, src = {}, {}
    replaced = []
    with Stage("history"):
        if offline:
            for s in syms:
                hist_full[s] = DL.load_history(s)
                src[s] = "cache"
        else:
            todo = list(syms)
            if session == "pre":
                todo = [s for s in syms if not DL.load_history(s) or DL.load_history(s)[-1]["date"] < cut]
            res = DL.refresh_history(todo) if todo else {}
            for s in syms:
                if s in res:
                    hist_full[s] = res[s]["rows"]
                    src[s] = res[s]["src"]
                    if res[s].get("replaced"):
                        replaced.append(s)
                else:
                    hist_full[s] = DL.load_history(s)
                    src[s] = "cache"
            if session == "pre" and not todo:
                src = {}
    hist = {s: rows_upto(hist_full[s], cut) for s in syms}
    stale = [s for s in syms if not hist[s] or hist[s][-1]["date"] != cut]
    if stale:
        log("no bar for %s on %d symbols: %s" % (cut, len(stale), " ".join(stale[:8])))

    # 2. quotes, VIX, bill
    quotes, tbill = {}, None
    with Stage("quotes"):
        if not offline:
            try:
                quotes = DL.fetch_quotes(syms)
            except Exception as e:  # noqa: BLE001
                log("quotes failed: %s" % str(e)[:160])
            try:
                DL.fetch_vix_history()
            except Exception as e:  # noqa: BLE001
                log("cboe failed: %s" % str(e)[:160])
            try:
                tbill = DL.fetch_tbill()
            except Exception as e:  # noqa: BLE001
                log("tbill failed: %s" % str(e)[:160])
        if tbill is None:
            irx = DL.load_history("^IRX")
            irx = [x for x in irx if x["date"] <= cut and x.get("close")]
            tbill = (irx[-1]["close"] / 100.0, irx[-1]["date"], "cache") if irx else (None, None, "none")
        for s in syms:
            q = quotes.get(s)
            if q and q.get("earningsTimestamp"):
                e = DL.et_date(q["earningsTimestamp"])
                ticker_state(state, s)["earnings"] = e if e >= D else None
            elif q:
                ticker_state(state, s)["earnings"] = None
    quote_ok = len(quotes) >= MIN_QUOTED

    # 3. readings
    n_read = 0
    with Stage("readings"):
        cboe = {n: read_cboe(n) for n in DL.VIX_NAMES}
        for s in syms:
            rows = hist[s]
            bar = rows[-1] if rows else None
            prev = rows[-2] if len(rows) >= 2 else None
            rsrc = src.get(s, "cache")
            if session == "post" and (not bar or bar["date"] != D):
                qb = bar_from_quote(quotes.get(s), D)
                if qb:
                    bar, prev, rsrc = qb, (rows[-1] if rows else None), "quote"
                    hist[s] = rows + [qb]
            if s in ("^VIX", "^VIX3M", "^VIX9D") and (not bar or bar["date"] != cut):
                v = last_on_or_before(cboe[s[1:]], cut)
                if v is not None:
                    bar = {"date": cut, "open": None, "high": None, "low": None, "close": v, "adjclose": v, "volume": 0}
                    rsrc = "cboe"
            ledger.add(reading_row(s, D, session, bar, prev, quotes.get(s), rsrc))
            n_read += 1 if bar else 0

    # 4. score what can be scored (post run: today's close; both: any catch up)
    n_scored = 0
    with Stage("scoring"):
        done = ledger.by["score"]
        for f in sorted(ledger.rows("forecast"), key=lambda f: (f["target"], SESSION_ORDER.get(f["session"], 0))):
            if f["target"] > cut or (f["target"], f["session"], f["sym"]) in done:
                continue
            rows = hist.get(f["sym"]) or []
            by = {r["date"]: r for r in rows}
            dates = [r["date"] for r in rows]
            oc = outcome(by, dates, f["target"])
            if oc is None:
                continue
            r, gap, intra, rv = oc
            i = dates.index(f["target"])
            p_clim = M.climatology(M.log_returns([x["adjclose"] for x in rows[:i]]))
            ledger.add(score_row(f, r, gap, intra, rv, p_clim))
            n_scored += 1
            if f["session"] == "post":
                learn(ticker_state(state, f["sym"]), f, r)
        log("scored %d forecasts" % n_scored)

    # 5. run row and day count
    run_dates = {r["date"] for r in ledger.rows("run")} | {D}
    state["dayNumber"] = len(run_dates)
    state["firstDate"] = min(run_dates)
    counts = {"yahoo": 0, "curl": 0, "cache": 0}
    for s in syms:
        v = src.get(s)
        if v in counts:
            counts[v] += 1
    fallback = counts["curl"] + counts["cache"]
    why = []
    if not quote_ok:
        why.append("quote call failed" if not offline else "offline, no quotes")
    if session == "post" and fallback >= DEGRADED_FALLBACKS:
        why.append("%d symbols from cache or curl" % fallback)
    if session == "post" and len(stale) >= DEGRADED_FALLBACKS:
        why.append("%d symbols without a bar for %s" % (len(stale), D))
    run_row = {"kind": "run", "date": D, "session": session, "ranAt": now_iso(), "etTime": et.strftime("%Y-%m-%d %H:%M %Z"),
               "lateMin": late, "degraded": bool(why), "degradedWhy": "; ".join(why) or None,
               "srcCounts": counts, "nSyms": n_read, "dayNumber": state["dayNumber"],
               "replaced": replaced, "tbill": {"rate": tbill[0], "date": tbill[1], "src": tbill[2]},
               "offline": offline}
    ledger.add(run_row)

    # 6. signals for everything
    with Stage("signals"):
        sig = {s: signals(s, hist[s], hist.get("SPY")) for s in syms if hist[s]}
        for s in syms:
            if s in sig and sig[s]["overnightShare"] is not None:
                ticker_state(state, s)["overnightShare"] = sig[s]["overnightShare"]

    # 7. forecasts
    with Stage("forecasts"):
        if session == "post":
            T = DL.next_trading_day(D)
            for s in syms:
                if UV.sleeve_of(s) == "index" or not hist[s]:
                    continue
                mdl = model_ticker(s, hist[s], ticker_state(state, s), D)
                if mdl:
                    ledger.add(forecast_row(s, T, D, "post", mdl))
        else:
            rd = {r["sym"]: r for r in ledger.rows("reading") if r["date"] == D and r["session"] == "pre"}
            for s in syms:
                if UV.sleeve_of(s) == "index" or not hist[s]:
                    continue
                tst = ticker_state(state, s)
                base = ledger.get("forecast", D, DL.prev_trading_day(D), "post", s)
                if base is None:
                    cands = [f for f in ledger.rows("forecast") if f["sym"] == s and f["session"] == "post" and f["target"] == D]
                    base = cands[0] if cands else None
                if base is None:
                    mdl = model_ticker(s, hist[s], tst, cut)
                    if not mdl:
                        continue
                    base = forecast_row(s, D, cut, "post", mdl)
                    log("%s: no post forecast for %s in the ledger, computed one from history" % (s, D))
                r = rd.get(s) or {}
                gap = None
                # the gap needs the previous trading day's close exactly: the cached bar only when it
                # is dated `cut`, else the post run's recorded close for `cut` (a stale cache once
                # turned a +1.45% gap into -1.54% by comparing against the close two days back)
                prev_close = r.get("close") if r.get("barDate") == cut else None
                if prev_close is None:
                    pr = ledger.get("reading", cut, "post", s)
                    if pr and pr.get("barDate") == cut and pr.get("close"):
                        prev_close = pr["close"]
                if r.get("pre") and prev_close:
                    gap = math.log(r["pre"] / prev_close)
                q90 = M.aci_quantile(tst["aci"]["scores"], tst["aci"]["alpha90"])
                q50 = M.aci_quantile(tst["aci"]["scores"], tst["aci"]["alpha50"])
                f = pre_forecast(base, gap, tst.get("overnightShare"), q90, q50)
                f["made"] = D
                f["target"] = D
                ledger.add(f)

    # 8. scorecard and gate (before the rec, its shadow flag needs them)
    paper_daily = []
    sc = scorecard(ledger, paper_daily)
    gate_open, reasons = gate(ledger, state, D, session, run_row, sc)

    # 9. recommendation, fills, paper, fans (post runs only)
    vix_rd = {r["sym"]: r for r in ledger.rows("reading") if r["date"] == D and r["session"] == session}
    vix, vix3m = (vix_rd.get("^VIX") or {}).get("close"), (vix_rd.get("^VIX3M") or {}).get("close")
    ratio = safe_div(vix, vix3m)
    spy = sig.get("SPY") or {}
    market = {"vixRatio": ratio, "regime": {"label": "%s trend, %s vol" % ((spy.get("trend") or {}).get("state", "mixed"),
                                                                           spy.get("volRegime", "low"))}}
    if session == "post":
        with Stage("recommendation"):
            earnings = {s: (state["tickers"].get(s) or {}).get("earnings") for s in syms}
            month = month_of(D)
            have = {r["sleeve"] for r in ledger.rows("rec") if r["month"] == month}
            if len(have) < 2:
                recs, frac, fails = issue_recs(D, sig, market, state["dayNumber"], gate_open, earnings)
                for sl, rec in recs.items():
                    if sl not in have:
                        ledger.add(rec)
                        log("rec %s %s: %s, deploy %.0f%% (%s)" % (month, sl,
                            ", ".join("%s $%d" % (p["sym"], p["usd"]) for p in rec["picks"]) or "nothing",
                            frac * 100, "; ".join(fails) or "calm, up"))
            nf = fill_recs(ledger, hist, D)
            if nf:
                log("filled %d rec rows" % nf)
            for sl in ("etf", "stock"):
                pv = paper_value(ledger, hist, sl, D)
                ledger.add({"kind": "paper", "date": D, "sleeve": sl, "value": pv["value"], "invested": pv["invested"],
                            "baselineValue": pv["baselineValue"], "baselineInvested": pv["baselineInvested"]})
        with Stage("fans"):
            want = ["SPY", "QQQ"]
            for sl in ("etf", "stock"):
                rec = current_rec(ledger, sl, D)
                if rec:
                    want += [p["sym"] for p in rec["picks"]]
            for s in want:
                r = M.log_returns([x["adjclose"] for x in hist.get(s, [])])
                fan_for(ledger, D, s, r, math.sqrt(M.ewma_var(r)) if r else None)
            for sl in ("etf", "stock"):
                rec = current_rec(ledger, sl, D)
                if rec and rec["picks"]:
                    rb = basket_returns(hist, rec["picks"])
                    fan_for(ledger, D, sl + "Basket", rb, math.sqrt(M.ewma_var(rb)) if rb else None)

    # 10. final scorecard (paper rows now exist), diagnostics, view
    with Stage("scorecard"):
        pdaily = {}
        for p in ledger.rows("paper"):
            d = pdaily.setdefault(p["date"], {"date": p["date"], "etf": 0.0, "etfBase": 0.0, "stock": 0.0, "stockBase": 0.0,
                                              "invested": 0.0, "baselineInvested": 0.0})
            d[p["sleeve"]] = p["value"]
            d[p["sleeve"] + "Base"] = p["baselineValue"]
            d["invested"] += p["invested"]
            d["baselineInvested"] += p.get("baselineInvested", 0.0)
        sc = scorecard(ledger, [pdaily[k] for k in sorted(pdaily)])
        gate_open, reasons = gate(ledger, state, D, session, run_row, sc)
    with Stage("diagnostics"):
        diag = diagnostics(hist)
    with Stage("us.js"):
        view = build_view(ledger, state, hist, hist_full, D, session, sig, diag, run_row, sc, gate_open, reasons, tbill[0])
        write_usjs(view)

    # 11. persist
    with Stage("write"):
        state["runs"] = (state.get("runs") or [])
        state["runs"] = [r for r in state["runs"] if not (r["date"] == D and r["session"] == session)]
        state["runs"].append({"date": D, "session": session, "ranAt": run_row["ranAt"], "lateMin": late, "degraded": run_row["degraded"]})
        state["runs"] = state["runs"][-60:]
        save_state(state)
        n = ledger.flush()
        log("appended %d ledger rows" % n)
    with Stage("worker"):
        # new rows plus everything from the last three trading days: a POST that failed on an
        # earlier run (Worker down, key unset) is healed by the next one, the Worker upserts
        since = D
        for _ in range(3):
            since = DL.prev_trading_day(since)
        new_keys = {(r["kind"], Ledger.key(r)) for r in ledger.new}
        recent = [r for k in KEYS for r in ledger.rows(k)
                  if r.get("target" if k == "forecast" else "date", "") >= since
                  and (k, Ledger.key(r)) not in new_keys]
        post_ledger(ledger.new + recent)
    print("%s run for %s done: day %d, %d readings, %d scored, %d forecasts, gate %s%s, %.1f s"
          % (session, D, state["dayNumber"], n_read, n_scored,
             sum(1 for f in ledger.new if f["kind"] == "forecast"), "open" if gate_open else "closed",
             (" (" + "; ".join(reasons) + ")") if reasons else "", time.time() - _T0))
    return 0


def rebuild():
    ledger = Ledger(LEDGER_PATH)
    state = load_state()
    runs = sorted(ledger.rows("run"), key=lambda r: (r["date"], SESSION_ORDER.get(r["session"], 0)))
    if not runs:
        print("no run rows in the ledger, nothing to rebuild")
        return 1
    last = runs[-1]
    D, session = last["date"], last["session"]
    cut = history_cut(D, session)
    syms = UV.all_symbols()
    hist_full = {s: DL.load_history(s) for s in syms}
    hist = {s: rows_upto(hist_full[s], cut) for s in syms}
    with Stage("signals"):
        sig = {s: signals(s, hist[s], hist.get("SPY")) for s in syms if hist[s]}
    with Stage("scorecard"):
        pdaily = {}
        for p in ledger.rows("paper"):
            d = pdaily.setdefault(p["date"], {"date": p["date"], "etf": 0.0, "etfBase": 0.0, "stock": 0.0, "stockBase": 0.0,
                                              "invested": 0.0, "baselineInvested": 0.0})
            d[p["sleeve"]] = p["value"]
            d[p["sleeve"] + "Base"] = p["baselineValue"]
            d["invested"] += p["invested"]
            d["baselineInvested"] += p.get("baselineInvested", 0.0)
        sc = scorecard(ledger, [pdaily[k] for k in sorted(pdaily)])
        gate_open, reasons = gate(ledger, state, D, session, last, sc)
    with Stage("diagnostics"):
        diag = diagnostics(hist)
    with Stage("us.js"):
        tb = (last.get("tbill") or {}).get("rate")
        view = build_view(ledger, state, hist, hist_full, D, session, sig, diag, last, sc, gate_open, reasons, tb)
        write_usjs(view)
    print("us.js rebuilt from the ledger for the %s run of %s (day %d, gate %s)" % (session, D, state["dayNumber"],
                                                                                   "open" if gate_open else "closed"))
    return 0


def main():
    ap = argparse.ArgumentParser(description="mygoldfund US board builder")
    ap.add_argument("--session", choices=("auto", "pre", "post"), default="auto")
    ap.add_argument("--force", action="store_true", help="redo a (date, session) already in the ledger")
    ap.add_argument("--offline", action="store_true", help="no network: cached history, no quotes")
    ap.add_argument("--rebuild-usjs", action="store_true", help="regenerate us.js from the ledger without fetching")
    a = ap.parse_args()
    if a.rebuild_usjs:
        return rebuild()
    return run(a.session, a.force, a.offline)


if __name__ == "__main__":
    sys.exit(main())
