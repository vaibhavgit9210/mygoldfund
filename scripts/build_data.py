#!/usr/bin/env python3
"""
mygoldfund data builder.

Pulls keyless public data, computes every indicator and both dashboard allocations
server side, and writes data.js for the static page.

Dependency free on purpose: stdlib only, so the GitHub Action needs no pip install
and no API keys. Every source below is public and keyless.

Sources
  LBMA benchmark fixings   prices.lbma.org.uk    daily since 1968, the industry benchmark
  Live spot                api.gold-api.com      intraday, no key
  USD/INR                  api.frankfurter.dev   ECB reference rates (FRED DEXINUS as fallback)
  Indian fund NAVs         portal.amfiindia.com  official AMFI daily NAV file
"""

import json, math, os, sys, csv, io, time, bisect
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (compatible; mygoldfund/1.0; +https://github.com/vaibhavgit9210/mygoldfund)"}
# FRED does not reject our normal User-Agent, it just never answers: the connection hangs
# until the socket times out. That is why the DEXINUS fallback below had never actually run.
# Requests to FRED therefore go out with a plain curl style header instead.
FRED_UA = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}

# ---------------------------------------------------------------- model constants
# Every number here is either measured from the LBMA history in this script or
# sourced in instruments.js / README. Change these in one place only.

ERC_VOL_WINDOW   = 2520     # 10y of trading days, for the equal risk contribution base weight
GSR_WINDOW_YEARS = 30       # percentile lookback. 30y had the best walk forward win rate (83-86%)
GSR_TILT_K       = 0.25     # max +/- 25pp shift in gold weight at the extremes of the percentile
SIGNAL_BOUNDS    = (0.40, 0.90)   # never an all in bet either way
SAFE_BOUNDS      = (0.10, 0.90)   # position bounds on min variance, per Michaud (1989)
EWMA_LAMBDA      = 0.94     # RiskMetrics daily decay factor
EWMA_WINDOW      = 500
CORR_WINDOW      = 60
VOL_TARGET       = 0.15     # annualised, the Safe dashboard's portfolio vol budget

# ---- multi asset boards (Growth, Offshore) ----
# These two boards deploy the whole budget across several asset classes instead of two
# metals. With more than two assets equal risk contribution stops having a closed form,
# so it is solved numerically below. Nothing here is tilted or timed: the tilt was tested
# and rejected, see README.
MONTHLY_BUDGET   = 5000     # the default monthly amount the whole dashboard is written around
MULTI_BOUNDS     = (0.05, 0.35)   # per sleeve weight bounds. 5% of 5,000 is 250, the smallest
                                  # ticket a monthly SIP can still place at a real fund minimum.
MULTI_EW_BLEND   = 0.50     # weight on EWMA variance vs full window variance in the vol estimate.
                            # Pure EWMA at lambda 0.94 has an 11 day half life, far too twitchy
                            # for a mix you set once a month across six asset classes.
MULTI_CORR_SHRINK= 0.20     # shrink correlations toward their average (Ledoit and Wolf style)
MULTI_WARMUP     = 500      # observations required before the backtest places its first SIP

# India landed price. Verified constants live in instruments.js; these mirror them.
# India raised the effective import duty on gold and silver from 6% to 15% with effect
# from 13 May 2026 (Basic Customs Duty 10% + AIDC 5%). Getting this wrong understates the
# Indian price by about 8.5%, so it is the single most important constant in this file.
IMPORT_DUTY = 0.15          # total effective customs duty on gold and silver, from 13 May 2026
GST_BULLION = 0.03          # GST on investment grade bullion (unchanged in GST 2.0, 56th Council)

TROY_OZ_G = 31.1034768

# ---------------------------------------------------------------- fetch helpers

def http(url, timeout=90, tries=3):
    hdr = FRED_UA if "stlouisfed.org" in url else UA
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:                      # noqa: BLE001
            last = e
            sys.stderr.write("  retry %d/%d %s: %s\n" % (i + 1, tries, url[:60], e))
            time.sleep(2 + i * 3)
    raise last

def jget(url, **kw):
    return json.loads(http(url, **kw))

# ---------------------------------------------------------------- math helpers
# Written out rather than imported so the script stays stdlib only.

def mean(v):
    return sum(v) / len(v)

def stdev(v):
    if len(v) < 2:
        return 0.0
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))

def median(v):
    s = sorted(v)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

def pctile(sorted_v, p):
    if not sorted_v:
        return 0.0
    k = (len(sorted_v) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return sorted_v[lo]
    return sorted_v[lo] * (hi - k) + sorted_v[hi] * (k - lo)

def pct_rank(hist, x):
    """Fraction of history strictly below x. Non parametric, robust to the fat tails
    that make a plain z score misleading on this series."""
    if not hist:
        return 0.5
    return sum(1 for h in hist if h < x) / len(hist)

def robust_z(hist, x):
    """Median/MAD z score. MAD is scaled by 1.4826 so it estimates sigma for a normal."""
    if len(hist) < 30:
        return 0.0
    m = median(hist)
    mad = median([abs(h - m) for h in hist]) * 1.4826
    if mad <= 0:
        return 0.0
    return (x - m) / mad

def logret(series):
    return [math.log(series[i] / series[i - 1]) for i in range(1, len(series))
            if series[i] > 0 and series[i - 1] > 0]

def ann_vol(rets, n=None):
    r = rets[-n:] if n else rets
    return stdev(r) * math.sqrt(252)

def ewma_vol(rets, lam=EWMA_LAMBDA, window=EWMA_WINDOW):
    r = rets[-window:]
    if len(r) < 50:
        return ann_vol(rets)
    v = r[0] ** 2
    for x in r[1:]:
        v = lam * v + (1 - lam) * x * x
    return math.sqrt(v * 252)

def corr(a, b):
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    if n < 3:
        return 0.0
    ma, mb = mean(a), mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da and db else 0.0

def sma(series, n):
    return mean(series[-n:]) if len(series) >= n else mean(series)

def rsi(series, n=14):
    """Wilder's RSI, the standard smoothing. 70/30 are the conventional thresholds."""
    if len(series) < n + 1:
        return 50.0
    d = [series[i] - series[i - 1] for i in range(1, len(series))]
    gains = [max(x, 0.0) for x in d]
    losses = [max(-x, 0.0) for x in d]
    ag, al = mean(gains[:n]), mean(losses[:n])
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)

def erc_weight(vol_a, vol_b):
    """Equal risk contribution for two assets. For N=2 this is exactly the inverse
    volatility weight and is independent of correlation (Maillard, Roncalli,
    Teiletche 2010). Verified numerically against the true ERC solver: 0.0pp apart."""
    if vol_a <= 0 or vol_b <= 0:
        return 0.5
    return (1 / vol_a) / ((1 / vol_a) + (1 / vol_b))

def minvar_weight(vol_a, vol_b, rho):
    """Markowitz two asset minimum variance closed form."""
    den = vol_a ** 2 + vol_b ** 2 - 2 * rho * vol_a * vol_b
    if abs(den) < 1e-12:
        return 0.5
    return (vol_b ** 2 - rho * vol_a * vol_b) / den

def port_vol(w, vol_a, vol_b, rho):
    return math.sqrt((w * vol_a) ** 2 + ((1 - w) * vol_b) ** 2 + 2 * w * (1 - w) * rho * vol_a * vol_b)

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

# ---------------------------------------------------------------- data sources

def fetch_lbma(name):
    url = "https://prices.lbma.org.uk/json/%s.json" % name
    rows = jget(url)
    out = {}
    for r in rows:
        v = r.get("v") or []
        if v and v[0]:
            try:
                p = float(v[0])
                if p > 0:
                    out[r["d"]] = p
            except (TypeError, ValueError):
                pass
    return out

def fetch_spot():
    """Live intraday spot. Nice to have; the LBMA fix is the source of truth for signals."""
    out = {}
    for sym, key in (("XAU", "gold"), ("XAG", "silver")):
        try:
            d = jget("https://api.gold-api.com/price/%s" % sym, timeout=30)
            out[key] = {"usd_oz": float(d["price"]), "at": d.get("updatedAt")}
        except Exception as e:                      # noqa: BLE001
            sys.stderr.write("  spot %s unavailable: %s\n" % (sym, e))
    return out

def fetch_usdinr_history():
    """ECB reference rates via frankfurter. Falls back to FRED DEXINUS."""
    try:
        d = jget("https://api.frankfurter.dev/v1/2015-01-01..?base=USD&symbols=INR", timeout=90)
        h = {k: float(v["INR"]) for k, v in d["rates"].items() if v.get("INR")}
        if len(h) > 500:
            return h, "frankfurter.dev (ECB)"
    except Exception as e:                          # noqa: BLE001
        sys.stderr.write("  frankfurter failed: %s\n" % e)
    txt = http("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXINUS").decode()
    h = {}
    for row in csv.DictReader(io.StringIO(txt)):
        vals = list(row.values())
        try:
            h[vals[0]] = float(vals[1])
        except (TypeError, ValueError):
            pass
    return h, "FRED DEXINUS"

def fetch_amfi_navs():
    """Official AMFI daily NAV file. Field layout is
    code;isin1;isin2;name;plan;option;nav;date  (NAV is index 6, not 5).
    We ingest the whole gold/silver universe and let instruments.js decide what to
    recommend, so a new fund launching does not need a code change here."""
    try:
        txt = http("https://portal.amfiindia.com/spages/NAVAll.txt", timeout=120).decode("utf-8", "replace")
    except Exception as e:                          # noqa: BLE001
        sys.stderr.write("  AMFI unavailable: %s\n" % e)
        return {}

    def categorise(n):
        n = n.lower()
        if "mining" in n or "world gold" in n:      # equity funds, not metal
            return None
        gold, silver = "gold" in n, "silver" in n
        fof = ("fof" in n or "fund of fund" in n or "passive fund of funds" in n
               or n.endswith(" fund") or "gold fund" in n or "silver fund" in n)
        etf = ("etf" in n or "exchange traded" in n or "bees" in n) and not fof
        if gold and silver:
            return "combo_fof" if fof else None
        if gold:
            return "gold_etf" if etf else ("gold_fof" if fof else None)
        if silver:
            return "silver_etf" if etf else ("silver_fof" if fof else None)
        return None

    out = {}
    for line in txt.splitlines():
        parts = line.split(";")
        if len(parts) < 8:
            continue
        name, plan, option = parts[3].strip(), parts[4].strip(), parts[5].strip()
        if not name:
            continue
        ol = option.lower()
        if "idcw" in ol or "income distribution" in ol or "dividend" in ol:
            continue
        if plan and "direct" not in plan.lower():   # direct plans only, lower TER
            continue
        cat = categorise(name)
        if not cat:
            continue
        try:
            nav = float(parts[6])
        except (TypeError, ValueError):
            continue
        key = name
        if key not in out:
            out[key] = {"nav": round(nav, 4), "cat": cat, "date": parts[7].strip()}
    return out


# ---------------------------------------------------------------- India local premium
# Two AMCs per metal, so the two answers can be cross checked against each other.
PREMIUM_SCHEMES = {
    "gold":   [("GOLDBEES", 140088), ("ICICI Gold ETF", 113076)],
    "silver": [("Nippon Silver ETF", 149758), ("ICICI Silver ETF", 149464)],
}

def fetch_nav_history(code):
    """Daily NAV history from mfapi.in, a keyless mirror of the AMFI archive."""
    try:
        d = jget("https://api.mfapi.in/mf/%d" % code, timeout=60)
    except Exception as e:                          # noqa: BLE001
        sys.stderr.write("  navhist %s unavailable: %s\n" % (code, e))
        return {}
    out = {}
    for r in d.get("data", []):
        try:
            dd = r["date"].split("-")
            out["%s-%s-%s" % (dd[2], dd[1], dd[0])] = float(r["nav"])
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return out

def india_premium(metal_px, fx_hist, schemes):
    """How far the Indian price sits from import parity, as a percentage.

    An Indian metal ETF's NAV is (grams per unit) x (domestic price per gram). We do not
    know grams per unit, but it is a constant, so NAV / import-parity is proportional to
    the local premium. Normalising that series by its own median cancels the constant and
    leaves a clean 'versus its own normal' reading. That is the number that matters: it is
    what told you Indian silver ETFs were 5% to 12% rich during the October 2025 squeeze."""
    fxd = sorted(fx_hist)
    def fx_on(d):
        i = bisect.bisect_right(fxd, d) - 1
        return fx_hist[fxd[i]] if i >= 0 else None

    results = []
    for name, code in schemes:
        navs = fetch_nav_history(code)
        days = sorted(set(navs) & set(metal_px))
        series = []
        for d in days:
            f = fx_on(d)
            if not f:
                continue
            parity = metal_px[d] / TROY_OZ_G * f
            if parity > 0:
                series.append((d, navs[d] / parity))
        if len(series) < 250:
            continue
        vals = [v for _, v in series]
        med = median(vals)
        if med <= 0:
            continue
        norm = [v / med for v in vals]
        cur = norm[-1]
        results.append({
            "source": name,
            "premium": round((cur - 1) * 100, 2),
            "percentile": round(pct_rank(norm[:-1], cur), 4),
            "avg90": round((mean(norm[-90:]) - 1) * 100, 2),
            "days": len(norm),
            "from": series[0][0],
        })
    if not results:
        return None
    prem = mean([r["premium"] for r in results])
    return {
        "premium": round(prem, 2),
        "percentile": round(mean([r["percentile"] for r in results]), 4),
        "avg90": round(mean([r["avg90"] for r in results]), 2),
        "spread": round(max(r["premium"] for r in results) - min(r["premium"] for r in results), 2),
        "sources": results,
    }

# ---------------------------------------------------------------- multi asset sleeves
# Every sleeve is something buyable as a MUTUAL FUND (an index fund, a fund of funds, or a
# US listed ETF), never a single stock and never a coin held directly. The fund that
# implements each sleeve on each route lives in instruments.js; this file only measures.
#
# Two price sources per sleeve are avoided on purpose. Where an Indian feeder fund exists
# its NAV is used directly, because a NAV is the return actually available to a unit holder
# net of the fund's own costs, which an index level is not.

SLEEVES = [
    {"id": "nifty50",   "name": "India large cap",   "short": "Nifty 50",
     "src": ("mf", [120716, 119063, 120620]),
     "what": "The Nifty 50, through an index fund. Measured off UTI's direct plan NAV."},
    {"id": "midcap150", "name": "India midcap",      "short": "Nifty Midcap 150",
     "src": ("mf", [147621, 148726, 149389, 150673]),
     "what": "The Nifty Midcap 150. Higher beta than the Nifty and the usual way an Indian portfolio reaches for growth at home."},
    {"id": "sp500",     "name": "US large cap",      "short": "S&P 500",
     "src": ("fred", "SP500"),
     "what": "The S&P 500, converted to rupees. The single most widely held equity index on earth."},
    {"id": "ndx100",    "name": "US tech",           "short": "Nasdaq 100",
     "src": ("fred", "NASDAQ100"),
     "what": "The Nasdaq 100, converted to rupees. Concentrated in a handful of very large technology companies."},
    {"id": "emxi",      "name": "Emerging ex India", "short": "EM ex India",
     "src": ("mf", [119779, 140327, 138456]),
     "what": "China, Taiwan, Korea and Brazil, through an Indian feeder fund. Cheap on valuation for years, and still cheap."},
    {"id": "gold",      "name": "Gold",              "short": "Gold",
     "src": ("gold", None),
     "what": "The same LBMA gold the other two boards are built on, in rupees per gram."},
    {"id": "btc",       "name": "Bitcoin",           "short": "Bitcoin",
     "src": ("btc", None),
     "what": "Bitcoin, held through a US listed spot Bitcoin ETF. Offshore only: no Indian mutual fund may hold crypto."},
]

# Which sleeves each board is allowed to use, and why the two lists differ.
#   growth   : everything an Indian resident can buy as a domestic mutual fund, no LRS.
#              Bitcoin is absent because no Indian mutual fund is permitted to hold it.
#   offshore : what a US brokerage account reached through the Liberalised Remittance
#              Scheme can buy. Indian equity is absent because remitting money abroad to
#              buy India back is strictly worse than buying it at home.
GROWTH_KEYS   = ["nifty50", "midcap150", "sp500", "ndx100", "emxi", "gold"]
OFFSHORE_KEYS = ["sp500", "ndx100", "emxi", "gold", "btc"]

def fetch_fred(series):
    """Daily index level from FRED. Keyless CSV. See FRED_UA above for the header trap."""
    txt = http("https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s" % series).decode()
    out = {}
    for row in csv.reader(io.StringIO(txt)):
        if len(row) < 2:
            continue
        try:
            v = float(row[1])
            if v > 0:
                out[row[0]] = v
        except ValueError:                          # header row and "." for market holidays
            pass
    return out

def fetch_btc_usd():
    """Daily BTC/USD back to 2010 from blockchain.info's public chart API. Keyless."""
    d = jget("https://api.blockchain.info/charts/market-price?timespan=all&format=json&sampled=false",
             timeout=90)
    out = {}
    for p in d.get("values", []):
        if p.get("y", 0) > 0:
            out[datetime.fromtimestamp(p["x"], timezone.utc).strftime("%Y-%m-%d")] = p["y"]
    return out

def fetch_first_nav(codes, need=250):
    """First scheme code that answers with enough history. mfapi returns a 502 for a given
    code often enough that a single hardcoded code is not safe to build a board on."""
    for c in codes:
        h = fetch_nav_history(c)
        if len(h) >= need:
            return h, c
    return {}, None

def to_inr(series, fx_hist):
    """A dollar series becomes a rupee series. Risk is measured in rupees throughout,
    because rupees are the currency being spent: for an Indian investor the currency leg
    of a US index fund is part of the risk, not a footnote to it."""
    fxd = sorted(fx_hist)
    out = {}
    for d, v in series.items():
        i = bisect.bisect_right(fxd, d) - 1
        if i >= 0:
            out[d] = v * fx_hist[fxd[i]]
    return out

# ---------------------------------------------------------------- N asset risk model

def corr_matrix(R):
    n, T = len(R), len(R[0])
    m = [mean(r) for r in R]
    s = [stdev(r) for r in R]
    C = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if s[i] <= 0 or s[j] <= 0:
                continue
            c = sum((R[i][t] - m[i]) * (R[j][t] - m[j]) for t in range(T)) / ((T - 1) * s[i] * s[j])
            C[i][j] = C[j][i] = c
    return C

def shrink_corr(C, delta=MULTI_CORR_SHRINK):
    """Pull every correlation toward the average correlation. A sample correlation matrix
    estimated from a few years of data is the noisiest input in the whole model, and this
    is the cheap standard defence (Ledoit and Wolf 2003)."""
    n = len(C)
    if n < 2:
        return C
    off = [C[i][j] for i in range(n) for j in range(n) if i != j]
    a = mean(off)
    return [[1.0 if i == j else (1 - delta) * C[i][j] + delta * a for j in range(n)] for i in range(n)]

def ewma_var_daily(rets, lam=EWMA_LAMBDA, window=750):
    """Per observation EWMA variance. Deliberately NOT annualised here: these boards are
    sampled on the days every market in the mix was open, about 229 a year rather than 252,
    so the annualisation factor is measured from the data instead of assumed."""
    r = rets[-window:]
    if len(r) < 50:
        r = rets
    v = r[0] ** 2
    for x in r[1:]:
        v = lam * v + (1 - lam) * x * x
    return v

def estimate_cov(px_cols, ann):
    """Annualised vols (EWMA blended with the full window) and a shrunk correlation matrix,
    combined into a daily covariance matrix."""
    R = [logret(p) for p in px_cols]
    v_ew = [math.sqrt(ewma_var_daily(r) * ann) for r in R]
    v_lr = [stdev(r) * math.sqrt(ann) for r in R]
    vol = [math.sqrt(MULTI_EW_BLEND * v_ew[i] ** 2 + (1 - MULTI_EW_BLEND) * v_lr[i] ** 2)
           for i in range(len(R))]
    C = shrink_corr(corr_matrix(R))
    S = [[vol[i] * vol[j] * C[i][j] / ann for j in range(len(R))] for i in range(len(R))]
    return vol, v_ew, v_lr, C, S

def port_vol_n(w, S, ann):
    q = sum(w[i] * w[j] * S[i][j] for i in range(len(w)) for j in range(len(w)))
    return math.sqrt(max(0.0, q)) * math.sqrt(ann)

def risk_budget_weights(S, b, iters=500):
    """Long only weights whose risk contributions match the budget b, by cyclical coordinate
    descent on the convex log barrier problem (Spinu 2013). With b equal for every sleeve
    this is exactly equal risk contribution, the N asset generalisation of the two asset
    formula the Signal board uses."""
    n = len(b)
    w = [1.0 / n] * n
    for _ in range(iters):
        mx = 0.0
        for i in range(n):
            if S[i][i] <= 0:
                continue
            c = sum(S[i][j] * w[j] for j in range(n) if j != i)
            ni = (-c + math.sqrt(c * c + 4 * S[i][i] * b[i])) / (2 * S[i][i])
            mx = max(mx, abs(ni - w[i]))
            w[i] = ni
        if mx < 1e-13:
            break
    t = sum(w)
    return [x / t for x in w]

def bound_weights(w, lo, hi):
    """Clip to the per sleeve bounds and push the excess onto whatever is still free."""
    w = list(w)
    for _ in range(100):
        w = [min(hi, max(lo, x)) for x in w]
        t = sum(w)
        if abs(t - 1.0) < 1e-12:
            break
        free = [i for i in range(len(w)) if lo < w[i] < hi]
        if not free:
            return [x / t for x in w]
        excess = t - 1.0
        fs = sum(w[i] for i in free)
        for i in free:
            w[i] -= excess * w[i] / fs
    return w

def erc_weights(S, n, bounds=MULTI_BOUNDS):
    return bound_weights(risk_budget_weights(S, [1.0 / n] * n), *bounds)

def round_weights(w, dp=4):
    """Round for publication but keep the sum exactly 1. Rounding each weight independently
    lets the total drift to 1.0001, and the page turns weights into rupees, so a split that
    does not add up to the budget is a visible bug rather than a rounding detail."""
    r = [round(x, dp) for x in w]
    big = max(range(len(r)), key=lambda i: r[i])
    r[big] = round(r[big] + (1.0 - sum(r)), dp)
    return r

def risk_contributions(w, S):
    """Share of portfolio variance each sleeve is responsible for. The point of ERC is that
    these come out roughly equal, and showing them is the only way to prove the solver worked."""
    n = len(w)
    tot = sum(w[i] * w[j] * S[i][j] for i in range(n) for j in range(n))
    if tot <= 0:
        return [1.0 / n] * n
    return [w[i] * sum(S[i][j] * w[j] for j in range(n)) / tot for i in range(n)]

def obs_per_year(days):
    span = (datetime.strptime(days[-1], "%Y-%m-%d") - datetime.strptime(days[0], "%Y-%m-%d")).days / 365.25
    return len(days) / span if span > 0 else 252.0

def backtest_board(keys, cols, days, ann, bounds=MULTI_BOUNDS, warm=MULTI_WARMUP):
    """Walk forward monthly SIP with no lookahead: at each rebalance the covariance is
    re estimated from data available on that date only. Reports the money weighted multiple
    on everything invested and the worst peak to trough fall of the accumulated pot.

    Benchmarks matter more than the headline here, so every single sleeve is run on its own
    alongside the mix. If one of them wins, the page says so."""
    reb, seen = [], set()
    for i, d in enumerate(days):
        if i < warm:
            continue
        if d[:7] not in seen:
            seen.add(d[:7])
            reb.append(i)
    if len(reb) < 24:
        return None

    n = len(keys)
    strategies = {"board": [0.0] * n, "equal": [0.0] * n}
    for k in range(n):
        strategies["solo_" + keys[k]] = [0.0] * n
    invested = 0.0
    curves = {k: [] for k in strategies}

    for m, i in enumerate(reb):
        sub = [c[:i + 1] for c in cols]
        _, _, _, _, S = estimate_cov(sub, ann)
        w_board = erc_weights(S, n, bounds)
        invested += MONTHLY_BUDGET
        for name, units in strategies.items():
            if name == "board":
                w = w_board
            elif name == "equal":
                w = [1.0 / n] * n
            else:
                j = keys.index(name[5:])
                w = [1.0 if q == j else 0.0 for q in range(n)]
            for j in range(n):
                if w[j] > 0:
                    units[j] += MONTHLY_BUDGET * w[j] / cols[j][i]
        end = reb[m + 1] if m + 1 < len(reb) else len(days)
        for t in range(i, end):
            for name, units in strategies.items():
                curves[name].append(sum(units[j] * cols[j][t] for j in range(n)))

    def summarise(vals):
        peak, dd = -1e18, 0.0
        for x in vals:
            peak = max(peak, x)
            dd = min(dd, x / peak - 1)
        return {"multiple": round(vals[-1] / invested, 4), "maxDrawdown": round(dd * 100, 2)}

    out = {"sips": len(reb), "from": days[reb[0]], "to": days[-1], "invested": round(invested),
           "monthly": MONTHLY_BUDGET,
           "board": summarise(curves["board"]), "equal": summarise(curves["equal"]),
           "solo": {keys[k]: summarise(curves["solo_" + keys[k]]) for k in range(n)}}
    best = max(out["solo"], key=lambda k: out["solo"][k]["multiple"])
    out["bestSolo"] = best
    out["boardBeatEqual"] = out["board"]["multiple"] > out["equal"]["multiple"]
    out["boardBeatEverySoloDrawdown"] = all(
        out["board"]["maxDrawdown"] > out["solo"][k]["maxDrawdown"] for k in out["solo"])
    return out

def build_multi_board(keys, inr_series, meta_by_id, bounds=MULTI_BOUNDS):
    """One board: align the sleeves onto the days every one of them actually traded, estimate,
    solve equal risk contribution, and backtest. No forward filling anywhere, so a public
    holiday in one market never invents a zero return in another."""
    common = None
    for k in keys:
        ds = set(inr_series[k])
        common = ds if common is None else (common & ds)
    days = sorted(common)
    if len(days) < 750:
        sys.stderr.write("  board %s: only %d common days, skipping\n" % (",".join(keys), len(days)))
        return None

    cols = [[inr_series[k][d] for d in days] for k in keys]
    ann = obs_per_year(days)
    vol, v_ew, v_lr, C, S = estimate_cov(cols, ann)
    n = len(keys)
    w = erc_weights(S, n, bounds)
    rc = risk_contributions(w, S)
    w = round_weights(w)
    step = max(1, int(round(ann / 12)))

    sleeves = []
    for i, k in enumerate(keys):
        p = cols[i]
        avg_corr = mean([C[i][j] for j in range(n) if j != i]) if n > 1 else 0.0
        sleeves.append({
            "id": k,
            "name": meta_by_id[k]["name"], "short": meta_by_id[k]["short"],
            "what": meta_by_id[k]["what"],
            "weight": w[i],
            "riskShare": round(rc[i], 4),
            "vol": round(vol[i] * 100, 2),
            "volEwma": round(v_ew[i] * 100, 2),
            "volLong": round(v_lr[i] * 100, 2),
            "avgCorr": round(avg_corr, 3),
            "ret1y": round((p[-1] / p[-int(ann)] - 1) * 100, 2) if len(p) > ann else None,
            "mom12_1": round((p[-step] / p[-13 * step] - 1) * 100, 2) if len(p) > 13 * step else None,
            "ddFromHigh": round((p[-1] / max(p) - 1) * 100, 2),
        })

    return {
        "keys": keys,
        "sleeves": sleeves,
        "corr": [[round(C[i][j], 3) for j in range(n)] for i in range(n)],
        "portVol": round(port_vol_n(w, S, ann) * 100, 2),
        "equalWeightVol": round(port_vol_n([1.0 / n] * n, S, ann) * 100, 2),
        "bounds": list(bounds),
        "deploy": 1.0,
        "window": {"from": days[0], "to": days[-1], "days": len(days),
                   "obsPerYear": round(ann, 1),
                   "years": round(len(days) / ann, 2)},
        "backtest": backtest_board(keys, cols, days, ann, bounds),
    }

# ---------------------------------------------------------------- main build

def build():
    log = sys.stderr.write
    log("fetching LBMA gold...\n");   gold = fetch_lbma("gold_pm")
    log("fetching LBMA silver...\n"); silver = fetch_lbma("silver")
    days = sorted(set(gold) & set(silver))
    if len(days) < 5000:
        raise SystemExit("LBMA history too short (%d rows), refusing to build" % len(days))

    g = [gold[d] for d in days]
    s = [silver[d] for d in days]
    gsr = [g[i] / s[i] for i in range(len(days))]

    log("fetching USD/INR...\n"); fx_hist, fx_src = fetch_usdinr_history()
    fx_days = sorted(fx_hist)
    usdinr = fx_hist[fx_days[-1]]
    fx_date = fx_days[-1]

    log("fetching live spot...\n"); spot = fetch_spot()
    log("computing India local premium...\n")
    prem = {}
    for metal, px in (("gold", gold), ("silver", silver)):
        r = india_premium(px, fx_hist, PREMIUM_SCHEMES[metal])
        if r:
            prem[metal] = r
            log("  %s local premium %+.2f%% (pctile %.0f%%, %d AMCs agree within %.2fpp)\n"
                % (metal, r["premium"], r["percentile"] * 100, len(r["sources"]), r["spread"]))

    log("fetching AMFI NAVs...\n")
    navs = fetch_amfi_navs()
    log("  matched %d funds\n" % len(navs))

    # ---------------- multi asset sleeves, all converted to rupees
    log("fetching growth sleeves...\n")
    meta_by_id = {x["id"]: x for x in SLEEVES}
    inr_series, sleeve_src = {}, {}
    for sl in SLEEVES:
        kind, arg = sl["src"]
        try:
            if kind == "mf":
                h, code = fetch_first_nav(arg)
                if not h:
                    raise RuntimeError("no scheme code answered from %s" % (arg,))
                inr_series[sl["id"]] = h
                sleeve_src[sl["id"]] = "AMFI NAV via mfapi.in, scheme %s" % code
            elif kind == "fred":
                inr_series[sl["id"]] = to_inr(fetch_fred(arg), fx_hist)
                sleeve_src[sl["id"]] = "FRED %s, converted to rupees" % arg
            elif kind == "gold":
                inr_series[sl["id"]] = to_inr({d: v / TROY_OZ_G for d, v in gold.items()}, fx_hist)
                sleeve_src[sl["id"]] = "LBMA gold PM fix, rupees per gram"
            elif kind == "btc":
                inr_series[sl["id"]] = to_inr(fetch_btc_usd(), fx_hist)
                sleeve_src[sl["id"]] = "blockchain.info market price, converted to rupees"
            log("  %-10s %5d points\n" % (sl["id"], len(inr_series[sl["id"]])))
        except Exception as e:                      # noqa: BLE001
            sys.stderr.write("  sleeve %s unavailable: %s\n" % (sl["id"], e))

    def board_if_possible(keys):
        have = [k for k in keys if len(inr_series.get(k, {})) > 750]
        if len(have) < 3:
            sys.stderr.write("  too few sleeves for board %s\n" % keys)
            return None
        if have != keys:
            sys.stderr.write("  board running without %s\n" % [k for k in keys if k not in have])
        return build_multi_board(have, inr_series, meta_by_id)

    growth = board_if_possible(GROWTH_KEYS)
    offshore = board_if_possible(OFFSHORE_KEYS)
    for nm, b in (("growth", growth), ("offshore", offshore)):
        if b:
            log("  %s: vol %.1f%%  %s\n" % (nm, b["portVol"],
                "  ".join("%s %.0f%%" % (x["short"], x["weight"] * 100) for x in b["sleeves"])))
        for k in (b or {}).get("keys", []):
            b["sleeves"][b["keys"].index(k)]["source"] = sleeve_src.get(k, "")

    # ---------------- returns and volatility
    rg, rs = logret(g), logret(s)
    vol_g_ewma, vol_s_ewma = ewma_vol(rg), ewma_vol(rs)
    vol_g_10y, vol_s_10y = ann_vol(rg, ERC_VOL_WINDOW), ann_vol(rs, ERC_VOL_WINDOW)
    rho60 = corr(rg[-CORR_WINDOW:], rs[-CORR_WINDOW:])
    rho_long = corr(rg[-1260:], rs[-1260:])

    # ---------------- GSR signal
    win = int(GSR_WINDOW_YEARS * 252)
    hist = gsr[-win:-1] if len(gsr) > win else gsr[:-1]
    cur_gsr = gsr[-1]
    gsr_pct = pct_rank(hist, cur_gsr)
    gsr_rz = robust_z([math.log(x) for x in hist], math.log(cur_gsr))
    hs = sorted(hist)
    gsr_bands = {"p10": pctile(hs, .10), "p25": pctile(hs, .25), "p50": pctile(hs, .50),
                 "p75": pctile(hs, .75), "p90": pctile(hs, .90),
                 "min": hs[0], "max": hs[-1], "median": median(hist)}

    gsr_windows = {}
    for yrs in (5, 10, 20, 30):
        w = int(yrs * 252)
        if len(gsr) > w + 10:
            h = gsr[-w:-1]
            gsr_windows[str(yrs)] = {
                "pct": round(pct_rank(h, cur_gsr), 4),
                "rz": round(robust_z([math.log(x) for x in h], math.log(cur_gsr)), 3),
                "median": round(median(h), 2),
            }

    # ---------------- DASHBOARD A: signal
    base_w = erc_weight(vol_g_10y, vol_s_10y)
    tilt = -GSR_TILT_K * (gsr_pct - 0.5) * 2.0        # high percentile => silver cheap => tilt to silver
    wA = clamp(base_w + tilt, *SIGNAL_BOUNDS)

    # ---------------- DASHBOARD B: safe
    mv_raw = minvar_weight(vol_g_ewma, vol_s_ewma, rho60)
    wB = clamp(mv_raw, *SAFE_BOUNDS)
    pvB = port_vol(wB, vol_g_ewma, vol_s_ewma, rho60)
    deploy = clamp(VOL_TARGET / pvB, 0.0, 1.0) if pvB > 0 else 1.0

    # ---------------- trend and momentum context
    def ctx(series, rets):
        last = series[-1]
        s50, s200 = sma(series, 50), sma(series, 200)
        ath = max(series)
        mom12_1 = (series[-22] / series[-274] - 1) if len(series) > 280 else None
        return {
            "last": round(last, 4),
            "sma50": round(s50, 2), "sma200": round(s200, 2),
            "vs50": round((last / s50 - 1) * 100, 2),
            "vs200": round((last / s200 - 1) * 100, 2),
            "rsi14": round(rsi(series[-400:]), 1),
            "ath": round(ath, 2),
            "ddFromAth": round((last / ath - 1) * 100, 2),
            "ret1m": round((last / series[-22] - 1) * 100, 2) if len(series) > 22 else None,
            "ret1y": round((last / series[-252] - 1) * 100, 2) if len(series) > 252 else None,
            "ret5yCagr": round(((last / series[-1260]) ** (1 / 5) - 1) * 100, 2) if len(series) > 1260 else None,
            "mom12_1": round(mom12_1 * 100, 2) if mom12_1 is not None else None,
            "volEwma": None,
        }
    cg, cs = ctx(g, rg), ctx(s, rs)
    cg["volEwma"] = round(vol_g_ewma * 100, 1)
    cs["volEwma"] = round(vol_s_ewma * 100, 1)

    # ---------------- India landed price, derived transparently
    def india(usd_oz):
        parity = usd_oz / TROY_OZ_G * usdinr        # per gram, import parity
        duty = parity * (1 + IMPORT_DUTY)
        return {"parity": round(parity, 2),
                "withDuty": round(duty, 2),
                "withGst": round(duty * (1 + GST_BULLION), 2)}
    gold_live = spot.get("gold", {}).get("usd_oz") or g[-1]
    silver_live = spot.get("silver", {}).get("usd_oz") or s[-1]

    # ---------------- sparkline series, downsampled to keep data.js small
    def spark(series, n=180):
        step = max(1, len(series) // n)
        idx = list(range(len(series) - 1, -1, -step))[::-1]
        return [[days[i], round(series[i], 3)] for i in idx]

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generatedIst": (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST"),
        "asOfFix": days[-1],
        "history": {"from": days[0], "days": len(days)},
        "fx": {"usdinr": round(usdinr, 4), "date": fx_date, "source": fx_src},
        "gold": {"usdOz": round(g[-1], 2), "liveUsdOz": round(gold_live, 2), "inr": india(gold_live), **cg},
        "silver": {"usdOz": round(s[-1], 4), "liveUsdOz": round(silver_live, 4), "inr": india(silver_live), **cs},
        "gsr": {
            "value": round(cur_gsr, 3),
            "liveValue": round(gold_live / silver_live, 3) if silver_live else None,
            "windowYears": GSR_WINDOW_YEARS,
            "percentile": round(gsr_pct, 4),
            "robustZ": round(gsr_rz, 3),
            "bands": {k: round(v, 2) for k, v in gsr_bands.items()},
            "byWindow": gsr_windows,
            "spark": spark([round(x, 3) for x in gsr], 200),
        },
        "vol": {
            "goldEwma": round(vol_g_ewma * 100, 2), "silverEwma": round(vol_s_ewma * 100, 2),
            "gold10y": round(vol_g_10y * 100, 2), "silver10y": round(vol_s_10y * 100, 2),
            "rho60": round(rho60, 3), "rho5y": round(rho_long, 3),
            "lambda": EWMA_LAMBDA,
        },
        "signal": {
            "baseWeight": round(base_w, 4), "tilt": round(tilt, 4),
            "wGold": round(wA, 4), "wSilver": round(1 - wA, 4),
            "bounds": list(SIGNAL_BOUNDS), "k": GSR_TILT_K,
            "portVol": round(port_vol(wA, vol_g_ewma, vol_s_ewma, rho60) * 100, 2),
        },
        "safe": {
            "minVarRaw": round(mv_raw, 4),
            "wGold": round(wB, 4), "wSilver": round(1 - wB, 4),
            "bounds": list(SAFE_BOUNDS),
            "portVol": round(pvB * 100, 2),
            "volTarget": VOL_TARGET * 100,
            "deploy": round(deploy, 4),
            "park": round(1 - deploy, 4),
        },
        "indiaPremium": prem,
        "growth": growth,
        "offshore": offshore,
        "navs": navs,
        "spark": {"gold": spark(g), "silver": spark(s)},
        "constants": {"importDuty": IMPORT_DUTY, "gstBullion": GST_BULLION, "troyOzG": TROY_OZ_G},
    }
    return out

if __name__ == "__main__":
    data = build()
    js = ("// Generated by scripts/build_data.py. Do not edit by hand.\n"
          "window.MGF_DATA = " + json.dumps(data, indent=1, sort_keys=False) + ";\n")
    open(os.path.join(ROOT, "data.js"), "w").write(js)
    json.dump(data, open(os.path.join(ROOT, "data.json"), "w"), indent=1)
    sys.stderr.write("wrote data.js (%.1f KB)\n" % (len(js) / 1024))
    d = data
    sys.stderr.write("  GSR %.2f  pct %.1f%%  ->  SIGNAL %.0f/%.0f   SAFE %.0f/%.0f deploy %.0f%%\n" % (
        d["gsr"]["value"], d["gsr"]["percentile"] * 100,
        d["signal"]["wGold"] * 100, d["signal"]["wSilver"] * 100,
        d["safe"]["wGold"] * 100, d["safe"]["wSilver"] * 100, d["safe"]["deploy"] * 100))
