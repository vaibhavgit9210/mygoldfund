#!/usr/bin/env python3
"""Refuse to publish obviously broken data. Runs in CI after every rebuild."""
import json, sys, os, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(ROOT, "data.json")))
fails = []

def need(cond, msg):
    if not cond:
        fails.append(msg)

need(d["history"]["days"] > 5000, "history too short: %s days" % d["history"]["days"])
need(200 < d["gold"]["usdOz"] < 100000, "gold price implausible: %s" % d["gold"]["usdOz"])
need(1 < d["silver"]["usdOz"] < 5000, "silver price implausible: %s" % d["silver"]["usdOz"])
need(10 < d["gsr"]["value"] < 200, "GSR implausible: %s" % d["gsr"]["value"])
need(20 < d["fx"]["usdinr"] < 300, "USDINR implausible: %s" % d["fx"]["usdinr"])
need(0 <= d["safe"]["deploy"] <= 1, "deploy out of range: %s" % d["safe"]["deploy"])
for b in ("signal", "safe"):
    w = d[b]["wGold"]
    lo, hi = d[b]["bounds"]
    need(lo - 1e-9 <= w <= hi + 1e-9, "%s gold weight %s outside bounds %s" % (b, w, d[b]["bounds"]))
    need(abs(d[b]["wGold"] + d[b]["wSilver"] - 1) < 1e-6, "%s weights do not sum to 1" % b)

# The India landed price must be internally consistent with spot and FX.
implied = d["gold"]["liveUsdOz"] / d["constants"]["troyOzG"] * d["fx"]["usdinr"]
need(abs(implied - d["gold"]["inr"]["parity"]) < 1.0,
     "India parity does not reconcile: %.2f vs %.2f" % (implied, d["gold"]["inr"]["parity"]))

age = (datetime.date.today() - datetime.date.fromisoformat(d["asOfFix"])).days
need(age <= 7, "LBMA fix is %d days stale (%s)" % (age, d["asOfFix"]))

if fails:
    for f in fails:
        sys.stderr.write("FAIL: %s\n" % f)
    sys.exit(1)

print("OK  GSR %.2f  signal %.0f/%.0f  safe %.0f/%.0f deploy %.0f%%  fix %s (%dd old)  funds %d" % (
    d["gsr"]["value"], d["signal"]["wGold"] * 100, d["signal"]["wSilver"] * 100,
    d["safe"]["wGold"] * 100, d["safe"]["wSilver"] * 100, d["safe"]["deploy"] * 100,
    d["asOfFix"], age, len(d["navs"])))
