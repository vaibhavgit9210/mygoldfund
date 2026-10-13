#!/usr/bin/env python3
"""
Unit tests for scripts/us_models.py. Plain script, no pytest: one line per test, exit 1 on any failure.

    python3 scripts/test_us_models.py            # synthetic tests
    python3 scripts/test_us_models.py --spy PATH # also print real numbers from a {"rows": [[date,o,h,l,c,adj,v],...]} file

Every synthetic series is seeded, so a failure is reproducible.
"""

import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import us_models as M

FAILS = []
COUNT = [0]

def check(name, cond, detail=""):
    COUNT[0] += 1
    tag = "ok  " if cond else "FAIL"
    print("%s %-62s %s" % (tag, name, detail))
    if not cond:
        FAILS.append(name)

def gauss_series(n, mu, sd, seed):
    rng = random.Random(seed)
    return [mu + sd * rng.gauss(0, 1) for _ in range(n)]

# ---------------------------------------------------------------- numeric helpers

check("norm_cdf(0) = 0.5", abs(M.norm_cdf(0) - 0.5) < 1e-12)
check("norm_ppf(0.95) = 1.6449", abs(M.norm_ppf(0.95) - 1.6448536) < 1e-6, "%.7f" % M.norm_ppf(0.95))
check("norm_ppf(0.75) = 0.6745", abs(M.norm_ppf(0.75) - 0.6744898) < 1e-6)
check("norm_ppf inverts norm_cdf", all(abs(M.norm_ppf(M.norm_cdf(x)) - x) < 1e-7 for x in (-3, -1, 0.3, 2.5)))
check("norm_pdf(0) = 0.3989", abs(M.norm_pdf(0) - 0.3989423) < 1e-6)
check("quantile type 7", M.quantile([3, 1, 2, 4], 0.5) == 2.5 and M.quantile([1, 2, 3, 4, 5], 0.25) == 2.0)

# ---------------------------------------------------------------- range estimators

def make_bars(n, sd_day, seed, gap_sd=None, steps=100):
    """Bars from an intraday random walk (steps per day) plus an overnight jump."""
    rng = random.Random(seed)
    rows, px = [], 100.0
    gap_sd = sd_day * 0.5 if gap_sd is None else gap_sd
    for d in range(n):
        px *= math.exp(gap_sd * rng.gauss(0, 1))
        o = px
        hi = lo = px
        for _ in range(steps):
            px *= math.exp(sd_day / math.sqrt(steps) * rng.gauss(0, 1))
            hi = max(hi, px); lo = min(lo, px)
        rows.append(["2020-%02d-%02d" % (1 + (d // 28) % 12, 1 + d % 28), o, hi, lo, px, px, 1000])
    return rows

bars = make_bars(600, 0.01, 11, gap_sd=0.005)
true_var = 0.01 ** 2 + 0.005 ** 2
yz = M.yang_zhang_series(bars, 20)
check("yang_zhang_series length = n - window", len(yz) == 580, str(len(yz)))
yz_mean = M.mean(yz)
check("yang_zhang mean within 20% of true daily variance", abs(yz_mean / true_var - 1) < 0.2, "ratio %.3f" % (yz_mean / true_var))
check("yang_zhang(rows) equals last of series", abs(M.yang_zhang(bars, 20) - yz[-1]) < 1e-15)
gk = M.garman_klass([b[1] for b in bars], [b[2] for b in bars], [b[3] for b in bars], [b[4] for b in bars])
check("garman_klass mean within 20% of intraday variance", abs(M.mean(gk) / 0.01 ** 2 - 1) < 0.2, "ratio %.3f" % (M.mean(gk) / 1e-4))
od = M.overnight_decomposition(bars, window=500)
check("overnight share recovers 0.2 (gap var / total)", abs(od["share"] - 0.2) < 0.06, "share %.3f" % od["share"])
g = M.gap_returns(bars); cc = M.log_returns([b[5] for b in bars])
check("overnight_share clipped to [0.1, 0.7]", 0.1 <= M.overnight_share(g, cc) <= 0.7, "%.3f" % M.overnight_share(g, cc))

# ---------------------------------------------------------------- EWMA

r_iid = gauss_series(2000, 0.0, 0.01, 1)
ev = M.ewma_var(r_iid)
check("ewma_var near true variance on iid", abs(ev / 1e-4 - 1) < 0.4, "ratio %.3f" % (ev / 1e-4))
check("ewma_var_series length and last matches", len(M.ewma_var_series(r_iid)) == 2000 and abs(M.ewma_var_series(r_iid)[-1] - ev) < 1e-18)

# ---------------------------------------------------------------- GJR GARCH

def sim_gjr(n, omega, alpha, gamma, beta, seed):
    rng = random.Random(seed)
    h = omega / (1 - alpha - beta - gamma / 2)
    out, prev = [], 0.0
    for t in range(n + 500):
        if t:
            h = omega + (alpha + (gamma if prev < 0 else 0.0)) * prev * prev + beta * h
        prev = math.sqrt(h) * rng.gauss(0, 1)
        out.append(prev)
    return out[500:]

TRUE = dict(omega=2e-6, alpha=0.03, gamma=0.10, beta=0.88)
r_gjr = sim_gjr(3000, TRUE["omega"], TRUE["alpha"], TRUE["gamma"], TRUE["beta"], 3)
t0 = time.time(); fit = M.gjr_garch_fit(r_gjr, max_obs=3000); dt = time.time() - t0
check("GJR alpha within 0.05 on 3000 obs", abs(fit["alpha"] - TRUE["alpha"]) < 0.05, "alpha %.3f (true 0.03)" % fit["alpha"])
check("GJR beta within 0.05 on 3000 obs", abs(fit["beta"] - TRUE["beta"]) < 0.05, "beta %.3f (true 0.88)" % fit["beta"])
check("GJR gamma within 0.08 on 3000 obs", abs(fit["gamma"] - TRUE["gamma"]) < 0.08, "gamma %.3f (true 0.10)" % fit["gamma"])
check("GJR constraints hold", fit["omega"] > 0 and fit["alpha"] >= 0 and fit["gamma"] >= 0 and fit["beta"] >= 0
      and fit["alpha"] + fit["beta"] + fit["gamma"] / 2 < 0.999)
t0 = time.time(); fit1500 = M.gjr_garch_fit(r_gjr); dt1500 = time.time() - t0
check("GJR fit under 0.5 s on 1500 obs", dt1500 < 0.5, "%.3f s, %d iters" % (dt1500, fit1500["iters"]))
t0 = time.time(); fitw = M.gjr_garch_fit(r_gjr, init=fit1500); dtw = time.time() - t0
check("GJR warm start lands within 0.02 of cold fit", abs(fitw["beta"] - fit1500["beta"]) < 0.02, "%.3f s" % dtw)
h_next = M.gjr_garch_forecast(r_gjr, fit)
uncond = TRUE["omega"] / (1 - TRUE["alpha"] - TRUE["beta"] - TRUE["gamma"] / 2)
check("GJR forecast within factor 3 of unconditional variance", uncond / 3 < h_next < uncond * 3, "sigma %.4f" % math.sqrt(h_next))
check("GJR forecast accepts a tuple", abs(M.gjr_garch_forecast(r_gjr, (fit["omega"], fit["alpha"], fit["gamma"], fit["beta"])) - h_next) < 1e-18)
mu0, s0 = M.gjr_zero(r_gjr, fit)
check("gjr_zero density has mu 0 and sigma sqrt(h)", mu0 == 0.0 and abs(s0 - math.sqrt(h_next)) < 1e-12)

# ---------------------------------------------------------------- HAR

def sim_har(n, a, bd, bw, bm, sd, seed):
    rng = random.Random(seed)
    lv = [-9.0] * 22
    for t in range(21, n + 21):
        d = lv[t]; w = M.mean(lv[t - 4:t + 1]); m = M.mean(lv[t - 21:t + 1])
        lv.append(a + bd * d + bw * w + bm * m + sd * rng.gauss(0, 1))
    return [math.exp(x) for x in lv[22:]]

rv = sim_har(2500, -0.6, 0.37, 0.34, 0.22, 0.4, 5)
hf = M.har_fit(rv)
check("HAR OLS recovers slopes within 0.12", all(abs(hf["ols"][k] - v) < 0.12 for k, v in (("bd", 0.37), ("bw", 0.34), ("bm", 0.22))),
      "ols bd %.2f bw %.2f bm %.2f" % (hf["ols"]["bd"], hf["ols"]["bw"], hf["ols"]["bm"]))
check("HAR shrink weight = 1/(1+n/250)", abs(hf["shrink"] - 1 / (1 + hf["n"] / 250.0)) < 1e-12)
check("HAR forecast positive and near scale", 0.2 * M.mean(rv[-22:]) < M.har_rv(rv, hf) < 5 * M.mean(rv[-22:]))
check("HAR with little data falls back to last rv", M.har_rv(rv[:30]) == rv[29])
check("HAR tiny sample shrinks hard to Corsi priors", abs(M.har_fit(rv[:80])["shrink"] - 1 / (1 + M.har_fit(rv[:80])["n"] / 250.0)) < 1e-12)

# ---------------------------------------------------------------- Hedge

rng = random.Random(8)
L = [0.0, 0.0]
eta = M.hedge_eta(2)
for _ in range(250):
    L[0] += 0.45 + 0.1 * rng.gauss(0, 1)
    L[1] += 0.55 + 0.1 * rng.gauss(0, 1)
w = M.hedge_weights(L, eta)
check("hedge eta = sqrt(8 ln 2 / 250)", abs(eta - math.sqrt(8 * math.log(2) / 250)) < 1e-12, "%.4f" % eta)
check("Hedge > 80% on the better expert after 250 steps", w[0] > 0.8, "w = %.3f" % w[0])
check("hedge_weights sum to 1", abs(sum(w) - 1) < 1e-12)
check("shrink_weights uniform at t=0", M.shrink_weights(w, 0) == [0.5, 0.5])
ws = M.shrink_weights(w, 250)
check("shrink_weights lam capped at 0.5 by t=250", abs(ws[0] - (0.5 * w[0] + 0.25)) < 1e-12)
check("shrink_weights never trusts more than 50%", abs(M.shrink_weights(w, 10000)[0] - ws[0]) < 1e-12)
check("hedge_weights handles huge losses without overflow", M.hedge_weights([1e6, 1e6 + 5], 0.15)[0] > 0.5)

# ---------------------------------------------------------------- density experts and combination

mu_c, s_c = M.combine_density([(0.01, 0.01), (-0.01, 0.01)], [1, 1])
check("combine_density mixes mean and spread", abs(mu_c) < 1e-15 and abs(s_c - math.sqrt(2e-4)) < 1e-12, "sigma %.5f" % s_c)
check("combine_density with one expert is identity", M.combine_density([(0.001, 0.02)], [3.0]) == (0.001, 0.02) or abs(M.combine_density([(0.001, 0.02)], [3.0])[1] - 0.02) < 1e-9)
check("combine_variance linear pool", abs(M.combine_variance([1.0, 3.0], [0.5, 0.5]) - 2.0) < 1e-12)
check("p_up(0, s) = 0.5", abs(M.p_up(0.0, 0.01) - 0.5) < 1e-12)
check("p_up(mu = sigma) = 0.8413", abs(M.p_up(0.01, 0.01) - 0.8413447) < 1e-6)
mu_rw, s_rw = M.rw_drift(r_iid)
check("rw_drift mu = 0.1008/252 and sigma = EWMA", abs(mu_rw - 0.1008 / 252) < 1e-15 and abs(s_rw - math.sqrt(ev)) < 1e-15)
up = [100 * 1.001 ** t for t in range(600)]
check("trend_regime +4bp above both SMAs", M.trend_regime(up, 0.01) == (0.0004, 0.01))
check("trend_regime -2bp below both SMAs", M.trend_regime(up[::-1], 0.01) == (-0.0002, 0.01))

# ---------------------------------------------------------------- HMM

def sim_hmm(n, sds, A, seed):
    rng = random.Random(seed)
    s, out, states = 0, [], []
    for _ in range(n):
        if rng.random() < A[s][1 - s]:
            s = 1 - s
        states.append(s)
        out.append(sds[s] * rng.gauss(0, 1))
    return out, states

r_hmm, st = sim_hmm(1500, (0.007, 0.02), [[0.98, 0.02], [0.04, 0.96]], 21)
t0 = time.time(); hp = M.hmm_fit(r_hmm); dth = time.time() - t0
check("HMM fit under 3 s on 1500 obs", dth < 3.0, "%.2f s, %d iters" % (dth, hp["iters"]))
check("HMM state 1 is the high vol state", hp["sd"][0] < hp["sd"][1], "sd %.4f / %.4f" % tuple(hp["sd"]))
check("HMM low sd within 20% of 0.7%", abs(hp["sd"][0] / 0.007 - 1) < 0.2, "%.4f" % hp["sd"][0])
check("HMM high sd within 20% of 2%", abs(hp["sd"][1] / 0.02 - 1) < 0.2, "%.4f" % hp["sd"][1])
check("HMM transitions persistent", hp["A"][0][0] > 0.9 and hp["A"][1][1] > 0.85, "A00 %.3f A11 %.3f" % (hp["A"][0][0], hp["A"][1][1]))
path = M.hmm_filter_path(r_hmm, hp)
acc = M.mean([1.0 if (path[t] > 0.5) == (st[t] == 1) else 0.0 for t in range(len(st))])
check("HMM filtered path matches the true regime > 85%", acc > 0.85, "accuracy %.3f" % acc)
check("hmm_filter equals last of path", M.hmm_filter(r_hmm, hp) == path[-1])
mu_h, s_h = M.hmm_mix(r_hmm, hp)
check("hmm_mix sigma between the two state sds", hp["sd"][0] <= s_h <= hp["sd"][1] * 1.01, "sigma %.4f" % s_h)
hp2 = M.hmm_fit(r_hmm, init=hp, iters=5)
check("HMM warm start stays put", abs(hp2["sd"][1] - hp["sd"][1]) < 0.001)
check("HMM on 60 iterations is capped", M.hmm_fit(r_hmm, iters=60)["iters"] <= 60)

# ---------------------------------------------------------------- pre adjustment and ACI

check("pre_adjust with no gap is identity", M.pre_adjust(0.0004, 0.01, None, 0.4) == (0.0004, 0.01))
mp, sp = M.pre_adjust(0.0004, 0.01, 0.01, 0.5)
check("pre_adjust mu = gap + (1-s) mu, sigma = sigma sqrt(1-s)", abs(mp - 0.0102) < 1e-12 and abs(sp - 0.01 * math.sqrt(0.5)) < 1e-12)
check("aci_quantile Gaussian before warmup (90%)", abs(M.aci_quantile([1.0] * 10, 0.10) - 1.6448536) < 1e-6)
check("aci_quantile Gaussian before warmup (50%)", abs(M.aci_quantile([1.0] * 10, 0.50) - 0.6744898) < 1e-6)
check("aci_quantile conformal rank", M.aci_quantile(list(range(1, 101)), 0.10) == 91)
check("aci_update moves toward target and clips", abs(M.aci_update(0.10, 1, 0.10) - 0.073) < 1e-12 and M.aci_update(0.0005, 1, 0.1) == 0.001
      and abs(M.aci_update(0.10, 0, 0.10) - 0.103) < 1e-12)

def run_aci(target, seed):
    rng = random.Random(seed)
    scores, alpha, inside = [], target, []
    for t in range(2000):
        sd = 1.0 if t < 1000 else 2.5
        r = sd * rng.gauss(0, 1)
        mu, sigma = 0.0, 1.0      # a deliberately wrong, constant sigma forecaster
        q = M.aci_quantile(scores, alpha)
        lo, hi = M.aci_band(mu, sigma, q)
        err = 1 - M.coverage(r, lo, hi)
        inside.append(1 - err)
        alpha = M.aci_update(alpha, err, target)
        scores.append(abs(r - mu) / sigma)
    return M.mean(inside)

cov90 = run_aci(0.10, 31)
check("ACI 90% coverage within 3pp over 2000 steps with a scale shift", abs(cov90 - 0.90) < 0.03, "%.3f" % cov90)
cov50 = run_aci(0.50, 32)
check("ACI 50% coverage within 3pp over 2000 steps with a scale shift", abs(cov50 - 0.50) < 0.03, "%.3f" % cov50)

# ---------------------------------------------------------------- scores

def crps_numeric(r, mu, sigma):
    lo, hi, n = mu - 12 * sigma, mu + 12 * sigma, 60000
    h = (hi - lo) / n
    tot = 0.0
    for i in range(n):
        x = lo + (i + 0.5) * h
        F = M.norm_cdf((x - mu) / sigma)
        tot += (F - (1.0 if x >= r else 0.0)) ** 2
    return tot * h

ok = True
worst = 0.0
for r_, mu_, s_ in ((0.3, 0.0, 1.0), (-1.7, 0.2, 1.0), (2.5, 0.0, 2.0), (0.0, 0.0, 0.5)):
    d = abs(M.crps_gauss(r_, mu_, s_) - crps_numeric(r_, mu_, s_))
    worst = max(worst, d)
    ok = ok and d < 1e-4
check("crps_gauss matches numerical integration to 1e-4", ok, "worst diff %.2e" % worst)
check("crps_gauss scales with sigma", abs(M.crps_gauss(0.003, 0.0, 0.01) - 0.01 * M.crps_gauss(0.3, 0.0, 1.0)) < 1e-15)
check("crps_gauss at the mean = sigma (sqrt(2) - 1) / sqrt(pi)", abs(M.crps_gauss(0, 0, 1) - (math.sqrt(2) - 1) / math.sqrt(math.pi)) < 1e-12)
check("logscore = -ln pdf", abs(M.logscore(0.5, 0, 1) + math.log(M.norm_pdf(0.5))) < 1e-12)
check("hit rules (zero return counts with mu >= 0)", M.hit(0, 0.1) == 1 and M.hit(0, -0.1) == 0 and M.hit(-0.01, 0.0004) == 0 and M.hit(0.02, 0.001) == 1)
check("brier = (p_up - 1{r > 0})^2", abs(M.brier(0.7, 0.01) - 0.09) < 1e-12 and abs(M.brier(0.7, -0.01) - 0.49) < 1e-12 and abs(M.brier(0.7, 0.0) - 0.49) < 1e-12)
pb = M.pinball(0.0, 0.0, 1.0)
check("pinball at tau 0.5 = 0.5|r - mu| and 5 values", len(pb) == 5 and abs(pb[2]) < 1e-12 and abs(M.pinball(1.0, 0.0, 1.0)[2] - 0.5) < 1e-12)
check("pinball all non negative", all(x >= 0 for x in M.pinball(-0.03, 0.001, 0.012)))
check("coverage inclusive", M.coverage(1, 1, 2) == 1 and M.coverage(3, 1, 2) == 0)
check("pit of the mean is 0.5", abs(M.pit(0.001, 0.001, 0.01) - 0.5) < 1e-12)
check("qlike minimised at h = r^2", M.qlike(0.01, 1e-4) < M.qlike(0.01, 2e-4) and M.qlike(0.01, 1e-4) < M.qlike(0.01, 0.5e-4))
sc = M.score_forecast(0.004, 0.0004, 0.01, 0.52, -0.016, 0.017, -0.006, 0.007)
check("score_forecast keys", set(sc) == {"hit", "brier", "logs", "crps", "pin", "cov90", "cov50", "pit"} and sc["hit"] == 1 and sc["cov90"] == 1)

# ---------------------------------------------------------------- baselines, skill, DM, MinTRL, Wilson

check("climatology up fraction", abs(M.climatology([0.01, -0.01, 0.02, 0.03]) - 0.75) < 1e-12 and M.climatology([]) == 0.5)
check("skill = 1 - model/base", abs(M.skill(0.8, 1.0) - 0.2) < 1e-12 and M.skill(1.0, 0.0) is None and M.skill(None, 1.0) is None)
rng = random.Random(41)
base = [1.0 + 0.3 * rng.gauss(0, 1) for _ in range(500)]
model = [b - 0.1 + 0.05 * rng.gauss(0, 1) for b in base]
dm = M.dm_tstat(model, base)
check("dm_tstat strongly negative when the model is better", dm is not None and dm < -5, "t = %.2f" % dm)
same = M.dm_tstat(base, base)
check("dm_tstat None on identical losses", same is None)
noise_m = [b + 0.3 * rng.gauss(0, 1) for b in base]
check("dm_tstat near 0 on equal skill", abs(M.dm_tstat(noise_m, base)) < 3, "t = %.2f" % M.dm_tstat(noise_m, base))
pnl = gauss_series(1000, 0.001, 0.01, 44)
sr = M.mean(pnl) / M.stdev(pnl)
mt = M.min_trl(pnl)
check("min_trl finite and near (1.645/SR)^2 for Gaussian P&L", mt is not None and abs(mt / ((1.645 / sr) ** 2 * (1 + sr * sr / 2) + 1) - 1) < 0.05, "%.0f days at SR %.3f" % (mt, sr))
check("min_trl None when SR <= target", M.min_trl(gauss_series(300, -0.001, 0.01, 45)) is None)
lo, hi = M.binom_ci(7, 7)
check("Wilson 7/7 lower bound 0.646", abs(lo - 0.6457) < 0.001 and hi == 1.0, "[%.3f, %.3f]" % (lo, hi))
lo, hi = M.binom_ci(5, 10)
check("Wilson 5/10 symmetric around 0.5", abs((lo + hi) / 2 - 0.5) < 1e-9 and abs(lo - 0.2366) < 0.001, "[%.3f, %.3f]" % (lo, hi))
check("Wilson n=0 is (0,1)", M.binom_ci(0, 0) == (0.0, 1.0))

# ---------------------------------------------------------------- signals and regime

down = up[::-1]
check("mom_12_1 on a monotone rise = adj[-22]/adj[-252] - 1", abs(M.mom_12_1(up) - (up[-22] / up[-252] - 1)) < 1e-12 and M.mom_12_1(up) > 0)
check("mom_12_1 None under 252 bars", M.mom_12_1(up[:200]) is None)
check("mom_6, ret_1m signs", M.mom_6(up) > 0 and M.ret_1m(up) > 0 and M.mom_6(down) < 0 and M.ret_1m(down) < 0)
check("hi52_ratio = 1 at the high, < 1 falling", M.hi52_ratio(up) == 1.0 and M.hi52_ratio(down) < 0.8)
ts = M.trend_state(up)
check("trend_state up on a monotone rise", ts["state"] == "up" and ts["above200"] and ts["above10m"])
ts = M.trend_state(down)
check("trend_state down on a monotone fall", ts["state"] == "down" and not ts["above200"] and not ts["above10m"])
dates = ["%04d-%02d-%02d" % (2020 + d // 240, 1 + (d // 20) % 12, 1 + d % 20) for d in range(600)]
ts = M.trend_state(up, dates)
check("trend_state with dates uses month end closes", ts["state"] == "up" and abs(ts["sma10m"] - M.mean([up[-1 - 20 * i] for i in range(10)])) < 1e-9)
# above the 200 day average but below the 10 month average: a flat series whose month end bars
# (every 21st bar back from the end without dates) printed high, with today's close in between
mixed = [50.0] * 400
for i in range(1, 10):
    mixed[-1 - 21 * i] = 100.0
mixed[-1] = 70.0
tsm = M.trend_state(mixed)
check("trend_state mixed when above 200d but below 10m SMA", tsm["state"] == "mixed" and tsm["above200"] and not tsm["above10m"],
      "%s sma200 %.1f sma10m %.1f" % (tsm["state"], tsm["sma200"], tsm["sma10m"]))
mixed2 = [100.0] * 400
for i in range(1, 10):
    mixed2[-1 - 21 * i] = 50.0
mixed2[-1] = 80.0
check("trend_state mixed when below 200d but above 10m SMA", M.trend_state(mixed2)["state"] == "mixed", M.trend_state(mixed2)["state"])
check("vol_regime high at the top, low at the bottom", M.vol_regime([0.01 + 1e-5 * t for t in range(600)])["regime"] == "high"
      and M.vol_regime([0.02 - 1e-5 * t for t in range(600)])["regime"] == "low" and M.vol_regime([0.01 + 1e-5 * t for t in range(600)])["pct"] == 1.0)
r_spy = gauss_series(300, 0, 0.01, 51)
rng = random.Random(52)
r_sym = [1.5 * x + 0.003 * rng.gauss(0, 1) for x in r_spy]
check("beta_60 recovers 1.5", abs(M.beta_60(r_sym, r_spy) - 1.5) < 0.15, "%.3f" % M.beta_60(r_sym, r_spy))
check("beta_60 None on short input", M.beta_60(r_sym[:10], r_spy[:10]) is None)

# ---------------------------------------------------------------- diagnostics

ok = True
detail = []
for q in (2, 5, 10, 20):
    v = M.variance_ratio(r_iid, q)
    detail.append("q%d %.2f z%.1f" % (q, v["vr"], v["z"]))
    ok = ok and abs(v["vr"] - 1) < 0.15 and abs(v["z"]) < 2
check("variance_ratio near 1 with |z| < 2 on iid normal", ok, " ".join(detail))
rng = random.Random(61)
ar = [0.0]
for _ in range(2000):
    ar.append(0.5 * ar[-1] + 0.01 * rng.gauss(0, 1))
check("variance_ratio > 1 with z > 2 on an AR(1) 0.5", M.variance_ratio(ar, 2)["vr"] > 1.3 and M.variance_ratio(ar, 2)["z"] > 2)
check("variance_ratio None on short input", M.variance_ratio(r_iid[:15], 2)["vr"] is None)
t0 = time.time(); hr = M.hurst_rs(r_iid[:1500]); dthr = time.time() - t0
check("hurst_rs near 0.5 on iid (Anis-Lloyd corrected) with CI containing 0.5", 0.42 < hr["h"] < 0.60 and hr["lo"] <= 0.5 <= hr["hi"],
      "h %.3f [%.3f, %.3f] %.2f s" % (hr["h"], hr["lo"], hr["hi"], dthr))
hs_iid = [M.hurst_rs(gauss_series(1500, 0, 0.01, 300 + k), n_boot=0)["h"] for k in range(8)]
check("hurst_rs averages 0.5 over 8 iid draws (no small sample bias)", abs(M.mean(hs_iid) - 0.5) < 0.03, "mean %.3f" % M.mean(hs_iid))
check("hurst_rs deterministic under the same seed", M.hurst_rs(r_iid[:600], n_boot=20) == M.hurst_rs(r_iid[:600], n_boot=20))
check("hurst_rs on an AR(1) 0.5 stays near 0.5 (Lo's lag absorbs short memory)", 0.40 < M.hurst_rs(ar, n_boot=0)["h"] < 0.60, "%.3f" % M.hurst_rs(ar, n_boot=0)["h"])
rng = random.Random(62)
comps = [0.0] * 4
lm = []
for _ in range(1500):
    for k, phi in enumerate((0.5, 0.9, 0.98, 0.995)):
        comps[k] = phi * comps[k] + math.sqrt(1 - phi * phi) * rng.gauss(0, 1)
    lm.append(sum(comps))
check("hurst_rs above 0.55 on an aggregated long memory series", M.hurst_rs(lm, n_boot=0)["h"] > 0.55, "%.3f" % M.hurst_rs(lm, n_boot=0)["h"])
check("autocorr of iid near 0, of AR(1) near 0.5", abs(M.autocorr(r_iid, 1)) < 0.06 and abs(M.autocorr(ar, 1) - 0.5) < 0.08)

# ---------------------------------------------------------------- recommendation helpers

etfs = [{"sym": "A", "mom": 0.30, "sigmaAnn": 0.30, "trend": "up"},
        {"sym": "B", "mom": 0.25, "sigmaAnn": 0.20, "trend": "mixed"},
        {"sym": "C", "mom": 0.50, "sigmaAnn": 0.60, "trend": "down"},
        {"sym": "D", "mom": None, "sigmaAnn": 0.20, "trend": "up"},
        {"sym": "E", "mom": 0.10, "sigmaAnn": 0.10, "trend": "up"}]
rk = M.rank_etf(etfs)
check("rank_etf drops down trend and missing mom, sorts by mom/sigma", [d["sym"] for d in rk] == ["B", "A", "E"], str([d["sym"] for d in rk]))
stocks = [{"sym": "S1", "mom": 0.9, "trend": "up", "bars": 400, "hi52": 0.8, "earningsSoon": False},
          {"sym": "S2", "mom": 0.8, "trend": "up", "bars": 400, "hi52": 0.97, "earningsSoon": False},
          {"sym": "S3", "mom": 1.5, "trend": "up", "bars": 200, "hi52": 1.0, "earningsSoon": False},
          {"sym": "S4", "mom": 1.2, "trend": "down", "bars": 400, "hi52": 0.5, "earningsSoon": False},
          {"sym": "S5", "mom": 1.1, "trend": "up", "bars": 400, "hi52": 0.99, "earningsSoon": True},
          {"sym": "S6", "mom": 0.3, "trend": "mixed", "bars": 400, "hi52": 0.9, "earningsSoon": False}]
rk, sk = M.rank_stock(stocks)
check("rank_stock: 52w high bonus lifts S2 over S1, filters and skips", [d["sym"] for d in rk] == ["S2", "S1", "S6"]
      and sorted(s["sym"] for s in sk) == ["S3", "S4", "S5"] and any(s["why"] == "earnings soon, skipped" for s in sk),
      "%s / %s" % ([d["sym"] for d in rk], [(s["sym"], s["why"]) for s in sk]))

picks = [{"sym": "A", "sigmaAnn": 0.20}, {"sym": "B", "sigmaAnn": 0.30}, {"sym": "C", "sigmaAnn": 0.40}]
sz = M.size_sleeve(picks, 30, 1.0)
tot = sum(l["usd"] for l in sz["lines"])
check("size_sleeve vol target scales below budget, holds the rest", tot + sz["heldUsd"] == 30 and tot < 30 and abs(sz["sleeveVol"] - 0.12) < 1e-9,
      "lines %s held %d" % ([(l["sym"], l["usd"]) for l in sz["lines"]], sz["heldUsd"]))
check("size_sleeve whole dollars and weights = usd/budget", all(isinstance(l["usd"], int) and abs(l["weight"] - l["usd"] / 30.0) < 1e-12 for l in sz["lines"]))
sz = M.size_sleeve(picks, 30, 1.0, vol_target=10.0)
check("size_sleeve deploys 100% when the target does not bind", sum(l["usd"] for l in sz["lines"]) == 30 and sz["heldUsd"] == 0,
      str([(l["sym"], l["usd"]) for l in sz["lines"]]))
sz = M.size_sleeve(picks, 30, 0.5, vol_target=10.0)
check("size_sleeve deploy_frac 0.5 spends 15 and holds 15", sum(l["usd"] for l in sz["lines"]) == 15 and sz["heldUsd"] == 15)
lev = [{"sym": "A", "sigmaAnn": 0.20}, {"sym": "B", "sigmaAnn": 0.20}, {"sym": "TQQQ", "sigmaAnn": 0.10, "lev": True}]
sz = M.size_sleeve(lev, 30, 1.0, vol_target=10.0)
lv = [l for l in sz["lines"] if l["sym"] == "TQQQ"][0]
check("size_sleeve leveraged line capped at $10, excess to the top pick, total 30", lv["usd"] == 10 and sz["lines"][0]["usd"] == 13 and sum(l["usd"] for l in sz["lines"]) == 30,
      str([(l["sym"], l["usd"]) for l in sz["lines"]]))
sz = M.size_sleeve([{"sym": "A", "sigmaAnn": 0.20}, {"sym": "TQQQ", "sigmaAnn": 0.10, "lev": True}], 30, 1.0, vol_target=10.0)
check("size_sleeve leveraged cap: 1/3 + 2/3 of 30 becomes 20 + 10", [(l["sym"], l["usd"]) for l in sz["lines"]] == [("A", 20), ("TQQQ", 10)], str([(l["sym"], l["usd"]) for l in sz["lines"]]))
check("largest remainder sums exactly", M._largest_remainder([0.25, 0.25, 0.5], 30) == [8, 7, 15] and sum(M._largest_remainder([1, 1, 1], 20)) == 20 and M._largest_remainder([1, 1], 0) == [0, 0])
sz = M.size_sleeve([{"sym": "A", "sigmaAnn": 0.25}, {"sym": "B", "sigmaAnn": 0.6}, {"sym": "C", "sigmaAnn": 0.7}], 20, 1.0, kelly=True, vol_target=10.0)
check("size_sleeve quarter Kelly caps each line (0.25*0.1008/sigma^2)", all(l["usd"] <= math.floor(20 * M.kelly_cap(p["sigmaAnn"]) + 1e-9)
      for l in sz["lines"] for p in [{"A": {"sigmaAnn": 0.25}, "B": {"sigmaAnn": 0.6}, "C": {"sigmaAnn": 0.7}}[l["sym"]]]),
      "lines %s held %d" % ([(l["sym"], l["usd"]) for l in sz["lines"]], sz["heldUsd"]))
check("size_sleeve drops lines under $3", all(l["usd"] >= 3 for l in sz["lines"]))
check("size_sleeve empty picks holds everything", M.size_sleeve([], 30)["heldUsd"] == 30 and M.size_sleeve([], 30)["lines"] == [])
rng = random.Random(77)
ok = True
for _ in range(500):
    n = rng.randint(1, 4)
    pk = [{"sym": "P%d" % i, "sigmaAnn": rng.uniform(0.05, 1.2), "lev": rng.random() < 0.2} for i in range(n)]
    b = rng.choice([5, 10, 20, 30, 50, 100])
    fr = rng.choice([0.5, 1.0])
    s = M.size_sleeve(pk, b, fr, kelly=rng.random() < 0.5, vol_target=rng.choice([0.12, 0.3, 10.0]))
    t_ = sum(l["usd"] for l in s["lines"])
    ok = ok and t_ <= round(b * fr) and t_ + s["heldUsd"] == b and all(isinstance(l["usd"], int) and l["usd"] >= 3 for l in s["lines"])
    ok = ok and all(l["usd"] <= M.LEV_CAP_USD for l in s["lines"] if l["sym"] in {p["sym"] for p in pk if p["lev"]})
check("size_sleeve fuzz: never over budget, whole dollars >= 3, lev <= 10", ok)
check("deploy_fraction 1.0 only when all three hold", M.deploy_fraction("up", 0.9, "low") == (1.0, []) and M.deploy_fraction("mixed", 0.9, "low")[0] == 0.5
      and M.deploy_fraction("up", 1.05, "high")[0] == 0.5 and len(M.deploy_fraction("down", 1.05, "high")[1]) == 3)
cl = M.cost_line(100)
check("cost_line 0.25% + 18% GST + FX 0.5 to 1.2%", abs(cl["brokerage"] - 0.25) < 1e-12 and abs(cl["gst"] - 0.045) < 1e-12
      and abs(cl["totalLo"] - 0.795) < 1e-12 and abs(cl["totalHi"] - 1.495) < 1e-12 and abs(cl["pctHi"] - 0.01495) < 1e-12)

# ---------------------------------------------------------------- bootstrap fan

r_fan = gauss_series(1260, 0.0004, 0.01, 90)
t0 = time.time(); f1 = M.block_bootstrap_fan(r_fan, seed=123); dtf = time.time() - t0
f2 = M.block_bootstrap_fan(r_fan, seed=123)
check("block_bootstrap_fan same seed is bit identical", f1 == f2, "%.2f s" % dtf)
check("block_bootstrap_fan different seed differs", M.block_bootstrap_fan(r_fan, seed=124) != f1)
expect = math.exp(f1["drift"] * 252) - 1
check("fan 12 month median within 3pp of exp(drift*252)-1 on iid", abs(f1["q"]["252"][2] - expect) < 0.03, "median %.3f vs %.3f" % (f1["q"]["252"][2], expect))
check("fan w = years/(years+98) and drift between sample and LR", abs(f1["w"] - 5 / 103.0) < 1e-12 and min(M.mean(r_fan), M.MU_LR_DAILY_LOG) <= f1["drift"] <= max(M.mean(r_fan), M.MU_LR_DAILY_LOG))
check("fan percentiles ordered and horizons widen", all(f1["q"][h][i] < f1["q"][h][i + 1] for h in ("21", "63", "126", "252") for i in range(4))
      and f1["q"]["252"][4] - f1["q"]["252"][0] > f1["q"]["21"][4] - f1["q"]["21"][0])
spread_iid = f1["q"]["21"][4] - f1["q"]["21"][0]
f3 = M.block_bootstrap_fan(r_fan, seed=123, sigma_now=0.02)
check("fan sigma_now scaling widens the near horizon", f3["q"]["21"][4] - f3["q"]["21"][0] > 1.4 * spread_iid, "%.3f vs %.3f" % (f3["q"]["21"][4] - f3["q"]["21"][0], spread_iid))
check("fan_seed deterministic and distinct", M.fan_seed("SPY", "2026-10") == M.fan_seed("SPY", "2026-10") and M.fan_seed("SPY", "2026-10") != M.fan_seed("QQQ", "2026-10"))
check("fan short input returns nulls", M.block_bootstrap_fan(r_fan[:30], seed=1)["q"]["252"] == [None] * 5)

# ---------------------------------------------------------------- performance of the daily per ticker update

def daily_update(rows, r, gjr, har_fit, scores, alpha):
    h_e = M.ewma_var(r[-1500:])
    h_g = M.gjr_garch_forecast(r, gjr)
    yz_ = M.yang_zhang_series(rows[-1500:], 20)
    h_h = M.har_rv(yz_, har_fit)
    mu, sigma = M.combine_density([(M.MU_LR_DAILY, math.sqrt(h_e)), (0.0, math.sqrt(h_g)), (0.0, math.sqrt(h_h))], [1, 1, 1])
    q = M.aci_quantile(scores, alpha)
    lo, hi = M.aci_band(mu, sigma, q)
    return M.score_forecast(r[-1], mu, sigma, M.p_up(mu, sigma), lo, hi, lo / 2, hi / 2)

rows_perf = make_bars(1600, 0.01, 5)
r_perf = M.log_returns([b[5] for b in rows_perf])
gjr_perf = M.gjr_garch_fit(r_perf)
har_perf = M.har_fit(M.yang_zhang_series(rows_perf, 20))
scores_perf = [abs(x) / 0.01 for x in r_perf[-250:]]
daily_update(rows_perf, r_perf, gjr_perf, har_perf, scores_perf, 0.1)
t0 = time.time()
for _ in range(20):
    daily_update(rows_perf, r_perf, gjr_perf, har_perf, scores_perf, 0.1)
per = (time.time() - t0) / 20
check("per ticker daily update under 20 ms", per < 0.020, "%.1f ms" % (per * 1000))

# ---------------------------------------------------------------- real data (optional)

def load_rows(path):
    """Rows [date, o, h, l, c, adj, v] from a {"rows": [...]} JSON file or a us/history CSV."""
    if path.endswith(".json"):
        with open(path) as fh:
            return json.load(fh)["rows"]
    rows = []
    with open(path) as fh:
        for line in fh.read().splitlines()[1:]:
            p = line.split(",")
            if len(p) >= 7:
                rows.append([p[0]] + [float(x) for x in p[1:7]])
    return rows

def real_data(path):
    rows = load_rows(path)
    adj = [x[5] for x in rows]
    dates = [x[0] for x in rows]
    r = M.log_returns(adj)
    print("\n-- real data: %s, %d bars to %s" % (path, len(rows), dates[-1]))
    t0 = time.time(); g = M.gjr_garch_fit(r); dtg = time.time() - t0
    print("GJR params  omega %.3e alpha %.4f gamma %.4f beta %.4f  (persistence %.4f, %d iters, %.2f s)" % (
        g["omega"], g["alpha"], g["gamma"], g["beta"], g["alpha"] + g["beta"] + g["gamma"] / 2, g["iters"], dtg))
    yz_ = M.yang_zhang_series(rows, 20)
    hf_ = M.har_fit(yz_)
    print("HAR coefs   a %.3f bd %.3f bw %.3f bm %.3f (OLS bd %.3f bw %.3f bm %.3f, shrink %.3f, n %d)" % (
        hf_["a"], hf_["bd"], hf_["bw"], hf_["bm"], hf_["ols"]["bd"], hf_["ols"]["bw"], hf_["ols"]["bm"], hf_["shrink"], hf_["n"]))
    t0 = time.time(); hm = M.hmm_fit(r[-1500:]); dthm = time.time() - t0
    ph = M.hmm_filter(r[-1500:], hm)
    print("HMM         sd low %.4f sd high %.4f  A00 %.3f A11 %.3f  p_high today %.3f  (%d iters, %.2f s)" % (
        hm["sd"][0], hm["sd"][1], hm["A"][0][0], hm["A"][1][1], ph, hm["iters"], dthm))
    s_e = math.sqrt(M.ewma_var(r[-1500:])); s_g = math.sqrt(M.gjr_garch_forecast(r, g)); s_h = math.sqrt(M.har_rv(yz_, hf_))
    s_combo = math.sqrt(M.combine_variance([s_e ** 2, s_g ** 2, s_h ** 2], [1, 1, 1]))
    experts = [M.rw_drift(r[-1500:]), M.gjr_zero(r, g), M.har_dens(yz_, hf_), M.trend_regime(adj, s_combo, dates), M.hmm_mix(r[-1500:], hm)]
    mu, sigma = M.combine_density(experts, [1] * 5)
    q90 = M.aci_quantile([], 0.10); q50 = M.aci_quantile([], 0.50)
    print("sigmas      ewma %.4f gjr %.4f har %.4f hmm %.4f" % (s_e, s_g, s_h, experts[4][1]))
    print("one day     mu %+.5f sigma %.4f pUp %.3f band90 [%+.4f, %+.4f] band50 [%+.4f, %+.4f] trend %s" % (
        mu, sigma, M.p_up(mu, sigma), mu - q90 * sigma, mu + q90 * sigma, mu - q50 * sigma, mu + q50 * sigma, M.trend_state(adj, dates)["state"]))
    sig_e = [math.sqrt(v) for v in M.ewma_var_series(r)]
    vr = M.vol_regime(sig_e)
    print("signals     mom12_1 %+.3f mom6 %+.3f ret1m %+.3f hi52 %.3f vol pct %.2f (%s) hurst %.3f" % (
        M.mom_12_1(adj), M.mom_6(adj), M.ret_1m(adj), M.hi52_ratio([x[4] for x in rows]), vr["pct"], vr["regime"], M.hurst_rs(r[-1500:], n_boot=0)["h"]))
    fan = M.block_bootstrap_fan(r, seed=M.fan_seed("SPY", dates[-1][:7]), sigma_now=sigma)
    print("12m fan     p5 %+.3f p25 %+.3f p50 %+.3f p75 %+.3f p95 %+.3f (drift %.2e/day, w %.3f)" % tuple(fan["q"]["252"] + [fan["drift"], fan["w"]]))
    check("real data: daily sigma between 0.4% and 1.5%", 0.004 <= sigma <= 0.015, "%.4f" % sigma)
    check("real data: every expert sigma between 0.3% and 2%", all(0.003 <= e[1] <= 0.02 for e in experts))
    check("real data: GJR persistence below 0.999 and fit under 0.5 s", g["alpha"] + g["beta"] + g["gamma"] / 2 < 0.999 and dtg < 0.5)
    check("real data: HMM p_high in [0, 1] and fit under 3 s", 0 <= ph <= 1 and dthm < 3.0)
    check("real data: 12 month fan median between -10% and +30%", -0.10 < fan["q"]["252"][2] < 0.30)

if "--spy" in sys.argv:
    real_data(sys.argv[sys.argv.index("--spy") + 1])
else:
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "us", "history", "SPY.csv")
    if os.path.exists(default) and len(load_rows(default)) > 600:
        real_data(default)

print("\n%d checks, %d failed" % (COUNT[0], len(FAILS)))
if FAILS:
    for f in FAILS:
        print("  FAIL", f)
    sys.exit(1)
