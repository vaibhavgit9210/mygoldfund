#!/usr/bin/env python3
"""
mygoldfund US board: fetch layer, history cache, NYSE calendar, ET clock.

Stdlib only, on purpose (the Action needs no pip install). Importing this module does
nothing; every network call sits behind a function.

Sources (all keyless, all verified from a GitHub runner on 2026-10-13)
  Yahoo v8 chart        daily OHLCV + adjusted close, 10 years
  Yahoo v7 quote        pre/post market prints, needs the cookie + crumb dance
  Cboe                  VIX, VIX3M, VIX9D daily history CSVs
  US Treasury           daily bill rates CSV (13 week coupon equivalent)

Per host User-Agent is mandatory and never rotated:
  Yahoo      exactly "Mozilla/5.0". Python's default UA gets 429 on every request and full
             browser strings are rejected intermittently.
  FRED       "curl/8.7.1". A Mozilla UA hangs (see build_data.py).
  elsewhere  a plain descriptive string.

A 429 from Yahoo is a block, not a hiccup: sleep 60 s, retry once, then try the curl binary
with the same UA, then give up on that symbol and serve the cache. Once a block is confirmed
the rest of the run skips straight to the next fallback instead of sleeping per symbol.

History cache: us/history/<SYM>.csv with columns date,open,high,low,close,adjclose,volume.
Index symbols drop Yahoo's caret in the file name (^VIX -> _VIX.csv). The Cboe CSVs are
cached as us/history/VIX.csv, VIX3M.csv, VIX9D.csv (date,open,high,low,close).

Adjusted close is what every return uses. If a fresh pull's adjclose disagrees with the
cache by more than ADJ_TOL on any overlapping date, a new split or dividend has shifted the
whole adjustment, so the cached file is replaced by a full pull and the event is logged.
"""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(ROOT, "us", "history")

YAHOO_UA = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
FRED_UA = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}
PLAIN_UA = {"User-Agent": "mygoldfund/1.0 (+https://github.com/vaibhavgit9210/mygoldfund)", "Accept": "*/*"}

HISTORY_YEARS = 10
OVERLAP_DAYS = 7          # incremental pulls start this many days before the last cached bar
ADJ_TOL = 1e-4            # adjclose disagreement that triggers a full cache replacement; two full
                          # pulls agree to 1e-6, a quarterly dividend shifts VOO by 0.3%, so 0.5%
                          # would splice old and new basis and fake a 0.3% return every quarter
PAUSE = 0.4               # seconds between Yahoo calls
BLOCK_SLEEP = 60          # seconds to wait after the first 429
QUOTE_BATCH = 60
TIMEOUT = 30

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d&events=div%%2Csplit"
YAHOO_QUOTE = "https://query2.finance.yahoo.com/v7/finance/quote?symbols=%s&crumb=%s"
YAHOO_CRUMB = "https://query2.finance.yahoo.com/v1/test/getcrumb"
YAHOO_COOKIE = "https://fc.yahoo.com"
CBOE_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv"
TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/%d/all?type=daily_treasury_bill_rates&field_tdr_date_value=%d&page&_format=csv")
TBILL_COLUMN = "13 WEEKS COUPON EQUIVALENT"

QUOTE_FIELDS = ("marketState", "preMarketPrice", "preMarketTime", "postMarketPrice", "postMarketTime",
                "regularMarketPrice", "regularMarketTime", "regularMarketOpen", "regularMarketDayHigh",
                "regularMarketDayLow", "regularMarketVolume", "regularMarketPreviousClose",
                "earningsTimestamp", "fiftyTwoWeekHigh", "netExpenseRatio")

CSV_COLUMNS = ("date", "open", "high", "low", "close", "adjclose", "volume")


def log(msg):
    sys.stderr.write("us_data: %s\n" % msg)
    sys.stderr.flush()


# ---------------------------------------------------------------- HTTP

def _headers_for(url):
    host = urllib.parse.urlsplit(url).netloc
    if host.endswith("yahoo.com"):
        return YAHOO_UA
    if host.endswith("stlouisfed.org"):
        return FRED_UA
    return PLAIN_UA


def http(url, timeout=TIMEOUT, opener=None):
    """GET url with the User-Agent this host needs. Raises urllib.error.HTTPError on 4xx/5xx."""
    req = urllib.request.Request(url, headers=_headers_for(url))
    if opener is not None:
        with opener.open(req, timeout=timeout) as f:
            return f.read()
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read()


class YahooBlocked(Exception):
    pass


# Shared across one process so a confirmed block is not re-discovered 62 times.
_BLOCK = {"urllib": False, "curl": False}


def _curl(url, cookie_jar=None, timeout=TIMEOUT):
    """The curl binary with the Yahoo UA. Returns bytes, raises on any failure."""
    cmd = ["curl", "-sS", "-f", "-L", "-m", str(timeout), "-A", "Mozilla/5.0"]
    if cookie_jar:
        cmd += ["-b", cookie_jar, "-c", cookie_jar]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    if out.returncode != 0:
        raise RuntimeError("curl exit %d: %s" % (out.returncode, out.stderr.decode(errors="replace").strip()[:120]))
    return out.stdout


def yahoo_get(url, opener=None):
    """GET a Yahoo URL. Returns (bytes, src) with src 'yahoo' or 'curl'.
    429 handling: sleep BLOCK_SLEEP, retry once, then curl, then YahooBlocked."""
    if not _BLOCK["urllib"]:
        for attempt in (0, 1):
            try:
                return http(url, opener=opener), "yahoo"
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                if attempt == 0:
                    log("429 from Yahoo, sleeping %d s then retrying once" % BLOCK_SLEEP)
                    time.sleep(BLOCK_SLEEP)
                else:
                    log("429 again, Yahoo is blocking urllib for this run, falling through to curl")
                    _BLOCK["urllib"] = True
    if not _BLOCK["curl"]:
        try:
            data = _curl(url)
            if data:
                return data, "curl"
        except Exception as e:  # noqa: BLE001
            log("curl fallback failed: %s" % e)
            if "429" in str(e):
                _BLOCK["curl"] = True
    raise YahooBlocked(url)


# ---------------------------------------------------------------- ET clock

def _nth_sunday(year, month, n):
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _dst_bounds(year):
    """US DST window in UTC: second Sunday of March 02:00 EST to first Sunday of November 02:00 EDT."""
    start = datetime.combine(_nth_sunday(year, 3, 2), datetime.min.time(), timezone.utc) + timedelta(hours=7)
    end = datetime.combine(_nth_sunday(year, 11, 1), datetime.min.time(), timezone.utc) + timedelta(hours=6)
    return start, end


def et_offset(utc_dt):
    """timedelta of Eastern Time from UTC at the given aware UTC datetime."""
    start, end = _dst_bounds(utc_dt.year)
    return timedelta(hours=-4) if start <= utc_dt < end else timedelta(hours=-5)


def to_et(dt):
    """Aware datetime in Eastern Time. Naive input is taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    off = et_offset(utc)
    return utc.astimezone(timezone(off, "EDT" if off == timedelta(hours=-4) else "EST"))


def et_now():
    return to_et(datetime.now(timezone.utc))


def et_date(ts):
    """'YYYY-MM-DD' in Eastern Time for an epoch second (UTC)."""
    return to_et(datetime.fromtimestamp(ts, timezone.utc)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- NYSE calendar

HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}
EARLY_CLOSES = {"2026-11-27", "2026-12-24", "2027-11-26"}   # 13:00 ET
CALENDAR_YEARS = (2026, 2027)


def _as_date(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def is_trading_day(d):
    """Weekday and not an NYSE closure. Only 2026 and 2027 closures are known; other years are
    weekday only."""
    d = _as_date(d)
    if d.weekday() >= 5:
        return False
    if d.year not in CALENDAR_YEARS:
        log("calendar: no NYSE closures loaded for %d, weekday rule only" % d.year)
    return d.isoformat() not in HOLIDAYS


def is_early_close(d):
    return _as_date(d).isoformat() in EARLY_CLOSES


def next_trading_day(d):
    """'YYYY-MM-DD' of the first trading day strictly after d."""
    d = _as_date(d)
    while True:
        d += timedelta(days=1)
        if is_trading_day(d):
            return d.isoformat()


def prev_trading_day(d):
    """'YYYY-MM-DD' of the last trading day strictly before d."""
    d = _as_date(d)
    while True:
        d -= timedelta(days=1)
        if is_trading_day(d):
            return d.isoformat()


def session_now(now=None):
    """('pre'|'post'|'closed', et_datetime). pre if 04:00 <= ET < 09:30, post if ET >= 16:15,
    closed otherwise and on every non trading day."""
    et = to_et(now) if now is not None else et_now()
    if not is_trading_day(et.date()):
        return "closed", et
    hm = et.hour * 60 + et.minute
    if 4 * 60 <= hm < 9 * 60 + 30:
        return "pre", et
    if hm >= 16 * 60 + 15:
        return "post", et
    return "closed", et


# ---------------------------------------------------------------- history cache

def cache_path(sym):
    return os.path.join(HIST_DIR, sym.replace("^", "_") + ".csv")


def _fnum(s):
    return None if s in ("", None) else float(s)


def load_history(sym):
    """Cached bars for sym, oldest first. Each row is a dict with keys
    date, open, high, low, close, adjclose, volume (volume int, prices float or None)."""
    path = cache_path(sym)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for rec in csv.DictReader(f):
            rows.append({
                "date": rec["date"],
                "open": _fnum(rec["open"]), "high": _fnum(rec["high"]), "low": _fnum(rec["low"]),
                "close": _fnum(rec["close"]), "adjclose": _fnum(rec["adjclose"]),
                "volume": int(float(rec["volume"])) if rec["volume"] not in ("", None) else 0,
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def save_history(sym, rows):
    os.makedirs(HIST_DIR, exist_ok=True)
    path = cache_path(sym)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in sorted(rows, key=lambda r: r["date"]):
            w.writerow([r["date"]] + ["" if r[k] is None else repr(round(r[k], 6)) for k in CSV_COLUMNS[1:6]]
                       + [int(r["volume"] or 0)])
    os.replace(tmp, path)


def parse_chart(payload):
    """Yahoo v8 chart JSON -> (rows, meta). Bars with a null close are dropped. Bar dates are the
    exchange's local date, converted with meta.gmtoffset so they stay right across DST."""
    j = json.loads(payload)
    chart = j.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError("yahoo chart error: %s" % json.dumps(chart["error"])[:200])
    res = (chart.get("result") or [None])[0]
    if not res:
        raise RuntimeError("yahoo chart: empty result")
    meta = res.get("meta") or {}
    tz = timezone(timedelta(seconds=int(meta.get("gmtoffset", -4 * 3600))))
    stamps = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose")
    rows = []
    for i, ts in enumerate(stamps):
        c = q.get("close", [None] * len(stamps))[i]
        if c is None:
            continue
        a = adj[i] if adj and i < len(adj) and adj[i] is not None else c
        rows.append({
            "date": datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d"),
            "open": q.get("open", [None] * len(stamps))[i],
            "high": q.get("high", [None] * len(stamps))[i],
            "low": q.get("low", [None] * len(stamps))[i],
            "close": c, "adjclose": a,
            "volume": q.get("volume", [None] * len(stamps))[i] or 0,
        })
    # Yahoo occasionally returns two stamps for the same date; keep the later one.
    by_date = {}
    for r in rows:
        by_date[r["date"]] = r
    return [by_date[d] for d in sorted(by_date)], meta


def fetch_chart(sym, period1, period2):
    """(rows, src) for one symbol between two epoch seconds. Raises YahooBlocked or RuntimeError."""
    url = YAHOO_CHART % (urllib.parse.quote(sym), int(period1), int(period2))
    data, src = yahoo_get(url)
    rows, _meta = parse_chart(data)
    return rows, src


def _full_window(now_ts):
    return now_ts - HISTORY_YEARS * 365.25 * 86400, now_ts + 86400


def adj_mismatch(old_rows, new_rows, before_date=None):
    """First (date, old_adj, new_adj) where the two series disagree by more than ADJ_TOL on an
    overlapping date, or None. Dates on or after before_date are ignored (a bar still forming)."""
    old = {r["date"]: r["adjclose"] for r in old_rows if r["adjclose"]}
    for r in new_rows:
        d = r["date"]
        if before_date and d >= before_date:
            continue
        o = old.get(d)
        if o and r["adjclose"] and abs(r["adjclose"] / o - 1) > ADJ_TOL:
            return d, o, r["adjclose"]
    return None


def refresh_history(syms, full=False, pause=PAUSE):
    """Refresh the cache for each symbol. Returns {sym: {"rows": [...], "src": "yahoo"|"curl"|"cache",
    "ok": bool, "replaced": bool, "err": str|None}}. rows come from the merged cache after the refresh
    (or the untouched cache when Yahoo could not be reached); ok is False when nothing fresh arrived."""
    out = {}
    now_ts = time.time()
    today_et = et_now().strftime("%Y-%m-%d")
    for i, sym in enumerate(syms):
        cached = load_history(sym)
        rec = {"rows": cached, "src": "cache", "ok": False, "replaced": False, "err": None}
        try:
            if full or not cached:
                p1, p2 = _full_window(now_ts)
            else:
                last = datetime.strptime(cached[-1]["date"], "%Y-%m-%d") - timedelta(days=OVERLAP_DAYS)
                p1, p2 = last.replace(tzinfo=timezone.utc).timestamp(), now_ts + 86400
            fresh, src = fetch_chart(sym, p1, p2)
            if not fresh:
                raise RuntimeError("no bars returned")
            if cached and not full:
                mm = adj_mismatch(cached, fresh, before_date=today_et)
                if mm:
                    log("%s: adjclose on %s moved %.6g -> %.6g (>%.1f%%), replacing the cached file with a full pull"
                        % (sym, mm[0], mm[1], mm[2], ADJ_TOL * 100))
                    time.sleep(pause)
                    p1, p2 = _full_window(now_ts)
                    fresh, src = fetch_chart(sym, p1, p2)
                    if not fresh:
                        raise RuntimeError("no bars returned on full re-pull")
                    cached = []
                    rec["replaced"] = True
            elif cached and full:
                mm = adj_mismatch(cached, fresh, before_date=today_et)
                if mm:
                    log("%s: full pull adjclose on %s differs from cache %.6g -> %.6g, cache replaced"
                        % (sym, mm[0], mm[1], mm[2]))
                    rec["replaced"] = True
                cached = []
            merged = {r["date"]: r for r in cached}
            for r in fresh:
                merged[r["date"]] = r
            rows = [merged[d] for d in sorted(merged)]
            save_history(sym, rows)
            rec.update(rows=rows, src=src, ok=True)
        except YahooBlocked:
            rec["err"] = "yahoo blocked"
            log("%s: Yahoo unreachable, serving %d cached bars" % (sym, len(cached)))
        except Exception as e:  # noqa: BLE001
            rec["err"] = str(e)[:200]
            log("%s: %s, serving %d cached bars" % (sym, rec["err"], len(cached)))
        out[sym] = rec
        if i + 1 < len(syms):
            time.sleep(pause)
    return out


def bootstrap_from_json(sym, path):
    """Seed the cache from a scratch JSON {rows: [[date,o,h,l,c,adj,v], ...]} if no cache exists.
    Returns the number of rows written (0 if the cache already existed or the file is missing)."""
    if os.path.exists(cache_path(sym)) or not os.path.exists(path):
        return 0
    with open(path) as f:
        j = json.load(f)
    rows = []
    for d, o, h, l, c, a, v in j["rows"]:
        if c is None:
            continue
        rows.append({"date": d, "open": o, "high": h, "low": l, "close": c, "adjclose": a if a is not None else c,
                     "volume": v or 0})
    save_history(sym, rows)
    return len(rows)


# ---------------------------------------------------------------- quotes

_QUOTE = {"opener": None, "crumb": None}


def _quote_session():
    """One cookie carrying opener plus its crumb, built once per process."""
    if _QUOTE["crumb"]:
        return _QUOTE["opener"], _QUOTE["crumb"]
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        http(YAHOO_COOKIE, opener=opener)
    except urllib.error.HTTPError:
        pass                       # 404 is expected, the cookie is what we came for
    crumb, _src = yahoo_get(YAHOO_CRUMB, opener=opener)
    crumb = crumb.decode().strip()
    if not crumb or crumb.startswith("{") or "<" in crumb:
        raise RuntimeError("bad crumb: %r" % crumb[:60])
    _QUOTE["opener"], _QUOTE["crumb"] = opener, crumb
    return opener, crumb


def _parse_quotes(payload, src):
    j = json.loads(payload)
    qr = j.get("quoteResponse") or {}
    if qr.get("error"):
        raise RuntimeError("yahoo quote error: %s" % json.dumps(qr["error"])[:200])
    out = {}
    for q in qr.get("result") or []:
        rec = {k: q.get(k) for k in QUOTE_FIELDS}
        rec["src"] = src
        out[q["symbol"]] = rec
    return out


def _curl_quotes(symbols):
    """Whole cookie + crumb + quote flow through the curl binary."""
    fd, jar = tempfile.mkstemp(prefix="yck", suffix=".txt")
    os.close(fd)
    try:
        try:
            _curl(YAHOO_COOKIE, cookie_jar=jar)
        except Exception:  # noqa: BLE001
            pass                   # 404 (curl -f exits 22), the cookie jar is still written
        crumb = _curl(YAHOO_CRUMB, cookie_jar=jar).decode().strip()
        url = YAHOO_QUOTE % (",".join(urllib.parse.quote(s) for s in symbols), urllib.parse.quote(crumb))
        return _parse_quotes(_curl(url, cookie_jar=jar), "curl")
    finally:
        try:
            os.unlink(jar)
        except OSError:
            pass


def fetch_quotes(syms, pause=PAUSE):
    """{sym: {marketState, preMarketPrice, preMarketTime, postMarketPrice, postMarketTime,
    regularMarketPrice, regularMarketTime, regularMarketOpen, regularMarketDayHigh, regularMarketDayLow,
    regularMarketVolume, regularMarketPreviousClose, earningsTimestamp, fiftyTwoWeekHigh, netExpenseRatio,
    src}}. Timestamps are epoch seconds UTC; absent fields are None; symbols Yahoo did not return
    are absent from the dict. Batches of QUOTE_BATCH symbols through one reused opener."""
    syms = list(syms)
    out = {}
    for b in range(0, len(syms), QUOTE_BATCH):
        batch = syms[b:b + QUOTE_BATCH]
        if b:
            time.sleep(pause)
        got = None
        if not _BLOCK["urllib"]:
            for attempt in (0, 1):
                try:
                    opener, crumb = _quote_session()
                    url = YAHOO_QUOTE % (",".join(urllib.parse.quote(s) for s in batch), urllib.parse.quote(crumb))
                    data, src = yahoo_get(url, opener=opener)
                    got = _parse_quotes(data, src)
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403) and attempt == 0:
                        log("quote %d: crumb rejected, rebuilding the session" % e.code)
                        _QUOTE["opener"] = _QUOTE["crumb"] = None
                        continue
                    log("quote batch %d: HTTP %d" % (b // QUOTE_BATCH, e.code))
                    break
                except YahooBlocked:
                    break
                except Exception as e:  # noqa: BLE001
                    log("quote batch %d: %s" % (b // QUOTE_BATCH, str(e)[:160]))
                    break
        if got is None and not _BLOCK["curl"]:
            try:
                got = _curl_quotes(batch)
            except Exception as e:  # noqa: BLE001
                log("quote batch %d via curl failed: %s" % (b // QUOTE_BATCH, str(e)[:160]))
        if got:
            out.update(got)
    return out


# ---------------------------------------------------------------- VIX family (Cboe)

VIX_NAMES = ("VIX", "VIX3M", "VIX9D")


def _cboe_path(name):
    return os.path.join(HIST_DIR, name + ".csv")


def _read_cboe_cache(name):
    path = _cboe_path(name)
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(f) if r.get("close")}


def _parse_cboe(payload):
    rows = []
    for rec in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
        d = (rec.get("DATE") or "").strip()
        if not d:
            continue
        try:
            iso = datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d")
            rows.append((iso, float(rec["OPEN"]), float(rec["HIGH"]), float(rec["LOW"]), float(rec["CLOSE"])))
        except (ValueError, KeyError):
            continue
    rows.sort()
    return rows


def fetch_vix_history():
    """{"VIX": {date: close}, "VIX3M": {...}, "VIX9D": {...}} from Cboe, each CSV cached whole to
    us/history/<NAME>.csv (date,open,high,low,close). Falls back to the cache per index."""
    out = {}
    for name in VIX_NAMES:
        try:
            rows = _parse_cboe(http(CBOE_CSV % name, timeout=60))
            if len(rows) < 100:
                raise RuntimeError("only %d rows" % len(rows))
            os.makedirs(HIST_DIR, exist_ok=True)
            tmp = _cboe_path(name) + ".tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(("date", "open", "high", "low", "close"))
                w.writerows(rows)
            os.replace(tmp, _cboe_path(name))
            out[name] = {r[0]: r[4] for r in rows}
        except Exception as e:  # noqa: BLE001
            cached = _read_cboe_cache(name)
            log("%s from Cboe failed (%s), serving %d cached rows" % (name, str(e)[:120], len(cached)))
            out[name] = cached
    return out


# ---------------------------------------------------------------- risk free

def fetch_tbill():
    """(rate, date, src): the 13 week bill coupon equivalent yield as a fraction (0.0416 for 4.16%),
    its 'YYYY-MM-DD', and 'treasury'|'yahoo'|'cache'. Falls back to Yahoo ^IRX, then to the ^IRX cache."""
    year = et_now().year
    for y in (year, year - 1):
        try:
            text = http(TREASURY_CSV % (y, y), timeout=60).decode("utf-8-sig")
            best = None
            for rec in csv.DictReader(io.StringIO(text)):
                v = (rec.get(TBILL_COLUMN) or "").strip()
                d = (rec.get("Date") or "").strip()
                if not v or not d or v.upper() in ("N/A", "NA"):
                    continue
                iso = datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d")
                if best is None or iso > best[1]:
                    best = (float(v) / 100.0, iso)
            if best:
                return best[0], best[1], "treasury"
            log("treasury %d: no %s rows yet" % (y, TBILL_COLUMN))
        except Exception as e:  # noqa: BLE001
            log("treasury %d failed: %s" % (y, str(e)[:120]))
    try:
        q = fetch_quotes(["^IRX"]).get("^IRX")
        if q and q.get("regularMarketPrice") is not None:
            return q["regularMarketPrice"] / 100.0, et_date(q["regularMarketTime"]), "yahoo"
    except Exception as e:  # noqa: BLE001
        log("^IRX quote failed: %s" % str(e)[:120])
    rows = load_history("^IRX")
    if rows:
        return rows[-1]["close"] / 100.0, rows[-1]["date"], "cache"
    raise RuntimeError("no bill rate from Treasury, Yahoo or cache")


# ---------------------------------------------------------------- CLI

def _verdict(name, ok, detail):
    print("%-8s %s  %s" % (name, "OK  " if ok else "FAIL", detail))
    return ok


def selftest():
    ok = True
    for sym in ("SPY", "NVDA", "TQQQ"):
        r = refresh_history([sym])[sym]
        rows = r["rows"]
        ok &= _verdict(sym, r["ok"], "%d bars %s..%s src=%s%s%s" % (
            len(rows), rows[0]["date"] if rows else "-", rows[-1]["date"] if rows else "-", r["src"],
            " replaced" if r["replaced"] else "", (" err=" + r["err"]) if r["err"] else ""))
        time.sleep(PAUSE)
    syms = ["SPY", "QQQ", "^VIX", "^VIX3M", "NVDA", "TQQQ"]
    q = fetch_quotes(syms)
    spy = q.get("SPY") or {}
    ok &= _verdict("quotes", len(q) == len(syms), "%d/%d symbols, SPY state %s pre %s post %s reg %s at %s src=%s" % (
        len(q), len(syms), spy.get("marketState"), spy.get("preMarketPrice"), spy.get("postMarketPrice"),
        spy.get("regularMarketPrice"),
        et_date(spy["regularMarketTime"]) if spy.get("regularMarketTime") else "-", spy.get("src")))
    v = fetch_vix_history()
    parts = []
    good = True
    for name in VIX_NAMES:
        s = v.get(name) or {}
        last = max(s) if s else "-"
        good &= len(s) > 100 and last >= prev_trading_day(prev_trading_day(et_now().date()))
        parts.append("%s %d rows to %s (%s)" % (name, len(s), last, s.get(last)))
    ok &= _verdict("vix", good, "; ".join(parts))
    try:
        rate, d, src = fetch_tbill()
        ok &= _verdict("tbill", src != "cache", "13w %.4f on %s src=%s" % (rate, d, src))
    except Exception as e:  # noqa: BLE001
        ok &= _verdict("tbill", False, str(e))
    s, et = session_now()
    print("session  %s  %s (next trading day %s)" % (s, et.strftime("%Y-%m-%d %H:%M %Z"), next_trading_day(et.date())))
    return ok


def seed(bootstrap_dir=None):
    """Full 10 year pull for every symbol in the universe. With bootstrap_dir, symbols whose
    Yahoo pull fails are seeded from <dir>/<SYM>.json (caret -> underscore) instead."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from us_universe import all_symbols
    syms = all_symbols()
    res = refresh_history(syms, full=True)
    failed = []
    for sym in syms:
        r = res[sym]
        if not r["ok"] and bootstrap_dir:
            n = bootstrap_from_json(sym, os.path.join(bootstrap_dir, sym.replace("^", "_") + ".json"))
            if n:
                r = {"rows": load_history(sym), "src": "bootstrap", "ok": True, "replaced": False, "err": r["err"]}
                res[sym] = r
        rows = r["rows"]
        print("%-7s %-5s %5d bars %s..%s%s" % (
            sym, r["src"], len(rows), rows[0]["date"] if rows else "-", rows[-1]["date"] if rows else "-",
            ("  err=" + r["err"]) if r["err"] else ""))
        if not r["ok"]:
            failed.append(sym)
    print("seeded %d/%d symbols%s" % (len(syms) - len(failed), len(syms),
                                       (", failed: " + " ".join(failed)) if failed else ""))
    return not failed


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.exit(0 if selftest() else 1)
    if "--seed" in args:
        bdir = None
        if "--bootstrap" in args:
            bdir = args[args.index("--bootstrap") + 1]
        sys.exit(0 if seed(bdir) else 1)
    print("usage: us_data.py --selftest | --seed [--bootstrap DIR]")
    sys.exit(2)
