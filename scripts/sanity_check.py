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

# The multi asset boards. These are solved numerically rather than by a closed form, so the
# checks below are mostly "did the optimiser actually do its job", which a formula never needs.
for name in ("growth", "offshore"):
    b = d.get(name)
    if not b:
        fails.append("%s board missing entirely" % name)
        continue
    lo, hi = b["bounds"]
    ws = [s["weight"] for s in b["sleeves"]]
    need(len(ws) >= 3, "%s has only %d sleeves" % (name, len(ws)))
    need(abs(sum(ws) - 1) < 1e-6, "%s weights sum to %.6f, not 1" % (name, sum(ws)))
    for sl in b["sleeves"]:
        need(lo - 1e-6 <= sl["weight"] <= hi + 1e-6,
             "%s sleeve %s weight %.4f outside bounds %s" % (name, sl["id"], sl["weight"], b["bounds"]))
        need(2 < sl["vol"] < 150, "%s sleeve %s volatility implausible: %s" % (name, sl["id"], sl["vol"]))
    # Equal risk contribution is the whole claim of these boards. If the solver did not
    # converge the risk shares drift apart, and that must never ship silently.
    free = [s["riskShare"] for s in b["sleeves"] if lo + 1e-6 < s["weight"] < hi - 1e-6]
    if len(free) > 1:
        need(max(free) - min(free) < 0.02,
             "%s risk contributions not equal: %.4f to %.4f" % (name, min(free), max(free)))
    need(1 < b["portVol"] < 100, "%s portfolio vol implausible: %s" % (name, b["portVol"]))
    n = len(b["sleeves"])
    C = b["corr"]
    for i in range(n):
        need(abs(C[i][i] - 1) < 1e-6, "%s correlation diagonal not 1" % name)
        for j in range(n):
            need(-1.0001 <= C[i][j] <= 1.0001, "%s correlation out of range" % name)
            need(abs(C[i][j] - C[j][i]) < 1e-9, "%s correlation matrix not symmetric" % name)
    bt = b.get("backtest")
    need(bool(bt), "%s has no backtest" % name)
    if bt:
        need(bt["sips"] >= 24, "%s backtest only %d SIPs" % (name, bt["sips"]))
        need(bt["board"]["multiple"] > 0, "%s backtest multiple non positive" % name)
        need(bt["board"]["maxDrawdown"] <= 0, "%s backtest drawdown should be negative" % name)

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
for name in ("growth", "offshore"):
    b = d.get(name)
    if b:
        print("    %-9s vol %5.1f%%  %s" % (name, b["portVol"],
              "  ".join("%s %.0f%%" % (s["short"], s["weight"] * 100) for s in b["sleeves"])))
