#!/usr/bin/env python3
"""
Publication gate for us.js (the US board). Run after scripts/us_build.py; exit 1 stops the
commit, so the page keeps serving the last good file.

Checks: us.js parses as JSON once the `window.MGF_US =` prefix is stripped and carries exactly
the section 7 keys; all 62 universe symbols have a reading with a price for the run and the 58
buyable plus benchmark tickers each have a price in us.js; no |1 day return| above 30% unless the
run replaced that symbol's cache for a split; every forecast sigma in (0.001, 0.25) daily and
pUp in (0, 1); each sleeve's picks sum to deployUsd within $1 and never above the budget; gate
reasons are strings; dayNumber is an integer >= 1; no em or en dashes in any string.

Stdlib only. Set US_OUT to check a build written elsewhere.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("US_OUT") or ROOT
USJS = os.path.join(OUT, "us.js")
LEDGER = os.path.join(OUT, "us", "ledger.ndjson")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from us_universe import all_symbols, sleeve_of   # noqa: E402

KEYS = ("generatedAt", "generatedIst", "session", "date", "dayNumber", "gateDays", "gateOpen", "gateReasons",
        "runs", "market", "universe", "tickers", "scorecard", "weights", "recs", "paper", "fans",
        "diagnostics", "costs", "constants", "sources", "notes")
MAX_D1 = 0.30
SIGMA_LO, SIGMA_HI = 0.001, 0.25

fails = []


def check(ok, msg):
    if not ok:
        fails.append(msg)
    return ok


def strings(o, path="$"):
    if isinstance(o, str):
        yield path, o
    elif isinstance(o, dict):
        for k, v in o.items():
            for x in strings(v, path + "." + str(k)):
                yield x
    elif isinstance(o, list):
        for i, v in enumerate(o):
            for x in strings(v, "%s[%d]" % (path, i)):
                yield x


def main():
    if not os.path.exists(USJS):
        print("us_sanity: %s missing" % USJS)
        return 1
    text = open(USJS).read()
    marker = "window.MGF_US ="
    if not check(marker in text, "us.js does not assign window.MGF_US"):
        print("\n".join("FAIL " + f for f in fails))
        return 1
    body = text[text.index(marker) + len(marker):].strip().rstrip(";")
    try:
        d = json.loads(body)
    except ValueError as e:
        print("FAIL us.js is not JSON after the prefix: %s" % e)
        return 1
    check(tuple(d.keys()) == KEYS, "top level keys differ from section 7: %s" % (sorted(set(d) ^ set(KEYS)),))
    check(d.get("session") in ("pre", "post"), "session is %r" % d.get("session"))
    check(isinstance(d.get("dayNumber"), int) and not isinstance(d.get("dayNumber"), bool) and d["dayNumber"] >= 1,
          "dayNumber is %r, must be an integer >= 1" % d.get("dayNumber"))
    check(isinstance(d.get("gateReasons"), list) and all(isinstance(r, str) for r in d["gateReasons"]),
          "gateReasons must be a list of strings")
    check(isinstance(d.get("gateOpen"), bool), "gateOpen must be a boolean")
    check(d.get("gateDays") == 7, "gateDays must be 7")

    # the run's readings: every one of the 62 symbols with a price
    replaced = set()
    readings = {}
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("date") != d.get("date") or row.get("session") != d.get("session"):
                    continue
                if row.get("kind") == "reading":
                    readings[row["sym"]] = row
                elif row.get("kind") == "run":
                    replaced = set(row.get("replaced") or [])
    missing = [s for s in all_symbols() if not readings.get(s) or not readings[s].get("close")]
    check(not missing, "readings without a price for %s %s: %s" % (d.get("date"), d.get("session"), " ".join(missing)))

    tickers = d.get("tickers") or {}
    want = [s for s in all_symbols() if sleeve_of(s) != "index"]
    missing = [s for s in want if s not in tickers]
    check(not missing, "tickers missing: %s" % " ".join(missing))
    check(len(tickers) == len(want), "%d tickers, expected %d" % (len(tickers), len(want)))
    for sym, t in tickers.items():
        px = (t.get("price") or {}).get("close")
        check(isinstance(px, (int, float)) and px > 0, "%s has no close price" % sym)
        d1 = (t.get("ret") or {}).get("d1")
        if d1 is not None and abs(d1) > MAX_D1 and sym not in replaced:
            check(False, "%s moved %.1f%% in a day and its cache was not replaced for a split" % (sym, d1 * 100))
        f = t.get("forecast")
        if check(isinstance(f, dict), "%s has no forecast" % sym):
            sg, pu = f.get("sigma"), f.get("pUp")
            check(isinstance(sg, (int, float)) and SIGMA_LO < sg < SIGMA_HI, "%s forecast sigma %r outside (%g, %g)" % (sym, sg, SIGMA_LO, SIGMA_HI))
            check(isinstance(pu, (int, float)) and 0 < pu < 1, "%s forecast pUp %r outside (0, 1)" % (sym, pu))
            check(f.get("lo90") is not None and f.get("hi90") is not None and f["lo90"] < f["hi90"], "%s 90%% band is not ordered" % sym)
            for name, e in (f.get("experts") or {}).items():
                check(SIGMA_LO < (e.get("sigma") or 0) < SIGMA_HI, "%s expert %s sigma %r out of range" % (sym, name, e.get("sigma")))
        check(isinstance((t.get("trend") or {}).get("state"), str), "%s trend state missing" % sym)
        for k in ("name", "sleeve", "lev", "proxy", "flags", "price", "ret", "vol", "hi52", "beta60", "trend",
                  "overnightShare", "earnings", "forecast", "lastScore", "stats"):
            check(k in t, "%s lacks key %s" % (sym, k))

    recs = d.get("recs") or {}
    for sl in ("etf", "stock"):
        r = recs.get(sl)
        if r is None:
            continue
        total = sum(int(p.get("usd") or 0) for p in r.get("picks") or [])
        check(abs(total - (r.get("deployUsd") or 0)) <= 1, "%s picks sum to $%d, deployUsd is %r" % (sl, total, r.get("deployUsd")))
        check(total <= (r.get("budgetUsd") or 0), "%s picks $%d exceed the budget $%r" % (sl, total, r.get("budgetUsd")))
        check((r.get("deployUsd") or 0) + (r.get("heldUsd") or 0) == r.get("budgetUsd"),
              "%s deployUsd + heldUsd != budgetUsd" % sl)
        for p in r.get("picks") or []:
            check(p.get("usd", 0) >= 1 and float(p["usd"]).is_integer(), "%s pick %s usd %r not a whole dollar >= 1" % (sl, p.get("sym"), p.get("usd")))
            if p.get("lev", 1) > 1:
                check(p["usd"] <= d["constants"]["levCapUsd"], "%s leveraged pick %s over the cap" % (sl, p["sym"]))
            check(sleeve_of(p.get("sym")) == sl, "%s pick %s is not in that sleeve" % (sl, p.get("sym")))

    m = d.get("market") or {}
    for k in ("vix", "vix3m", "vixRatio"):
        v = m.get(k)
        check(v is None or (isinstance(v, (int, float)) and v > 0), "market.%s is %r" % (k, v))
    sc = d.get("scorecard") or {}
    check(isinstance(sc.get("pitHist"), list) and len(sc["pitHist"]) == 10, "scorecard.pitHist must have 10 bins")
    check(isinstance(sc.get("n"), int), "scorecard.n must be an integer")
    for k in ("cov90", "cov50", "hitRate"):
        v = sc.get(k)
        check(v is None or 0 <= v <= 1, "scorecard.%s is %r" % (k, v))
    w = d.get("weights") or {}
    for grp in ("density", "variance"):
        tot = sum((w.get(grp) or {}).values())
        check(abs(tot - 1.0) < 1e-3, "weights.%s sum to %.4f" % (grp, tot))

    for path, s in strings(d):
        if chr(0x2014) in s or chr(0x2013) in s:
            check(False, "em or en dash at %s" % path)
            break

    if fails:
        print("\n".join("FAIL " + f for f in fails))
        print("us_sanity: %d problems" % len(fails))
        return 1
    print("us_sanity: ok (%s %s run, day %d, %d tickers, gate %s)" % (
        d["date"], d["session"], d["dayNumber"], len(tickers), "open" if d["gateOpen"] else "closed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
