#!/usr/bin/env python3
"""
mygoldfund US board: the maths. Pure functions over plain lists of floats, no I/O.

Stdlib only, on purpose (the Action needs no pip install). Everything here is a daily log
return r[t] = ln(adj[t] / adj[t-1]) unless the docstring says otherwise. Sigmas are daily
fractions (0.01 = 1% a day) unless the name says "ann".

Sections, in order:
  constants, numeric helpers, returns and range estimators, volatility experts (EWMA, GJR
  GARCH, HAR), Hedge combination, density experts and the combined density, the pre market
  gap adjustment, adaptive conformal intervals, scores and baselines, signals and regime,
  diagnostics, the recommendation helpers, the 12 month bootstrap fan.

Papers cited inline: RiskMetrics (JP Morgan 1996), Glosten Jagannathan Runkle (1993),
Corsi (2009), Yang and Zhang (2000), Garman and Klass (1980), Freund and Schapire (1997),
Gibbs and Candes (2021), Gneiting and Raftery (2007), Diebold and Mariano (1995), Newey and
West (1987), Bailey and Lopez de Prado (2012), Wilson (1927), Lo and MacKinlay (1988), Lo (1991),
Barroso and Santa-Clara (2015), Politis and Romano (1994), Faber (2007), Jegadeesh and Titman (1993).
"""

import math
import random
import zlib

# ---------------------------------------------------------------- constants (one place)

MU_LR_ANNUAL     = 0.1008   # Damodaran, S&P 500 geometric mean 1928 to 2025, the long run anchor
EWMA_LAMBDA      = 0.94     # RiskMetrics daily decay
ACI_GAMMA        = 0.03     # adaptive conformal step size (Gibbs and Candes 2021 use 0.005 to 0.05)
HEDGE_SHRINK_CAP = 0.5      # Hedge weights are never trusted beyond 50/50 with uniform
VOL_TARGET       = 0.12     # size_sleeve default (Barroso and Santa-Clara 2015); the sleeves override it
VOL_TARGET_ETF   = 0.25     # ETF sleeve: about the Nasdaq-100's long run realised vol
VOL_TARGET_STOCK = 0.50     # stock sleeve: one concentrated three name growth basket
KELLY_FRACTION   = 0.25     # quarter Kelly (kelly_cap is kept for the tests; neither sleeve uses it, on
                            # 60% vol names it lands under the $3 minimum line and the sleeve holds cash)
LEV_CAP_USD      = 10       # at most this many dollars a month into a leveraged ETF
BUDGET_ETF       = 30
BUDGET_STOCK     = 20
BROKERAGE        = 0.0025   # INDmoney, per order
GST              = 0.18     # on the brokerage
FX_BAND          = (0.005, 0.012)   # INDmoney GIFT City FX markup band
GATE_DAYS        = 7

TRADING_DAYS     = 252
MU_LR_DAILY      = MU_LR_ANNUAL / TRADING_DAYS            # 0.0004, the rw_drift expert's mean
MU_LR_DAILY_LOG  = math.log(1 + MU_LR_ANNUAL) / TRADING_DAYS   # the fan's long run log drift
ACI_WINDOW       = 250      # scores kept per tracker
ACI_WARMUP       = 30       # Gaussian quantile until this many scores exist
ACI_ALPHA_MIN    = 0.001
ACI_ALPHA_MAX    = 0.999
GJR_MAX_OBS      = 1500
GJR_PERSIST_MAX  = 0.999    # alpha + beta + gamma/2 must stay below this
HAR_PRIORS       = (0.372, 0.343, 0.224)   # Corsi (2009) Table 3, S&P 500 futures, daily horizon
HAR_PRIOR_SCALE  = 250.0    # shrink weight 1 / (1 + n / 250)
HMM_ITERS        = 60
HEDGE_HORIZON    = 250
DM_LAG           = 5
CLIM_WINDOW      = 504      # trailing two years of up day fraction
OVERNIGHT_WINDOW = 120
OVERNIGHT_CLIP   = (0.1, 0.7)
VOL_REGIME_WINDOW = 500
VOL_REGIME_HIGH  = 0.70
MIN_LINE_USD     = 3
PINBALL_TAUS     = (0.05, 0.25, 0.5, 0.75, 0.95)
TREND_MU         = {"up": 0.0004, "mixed": 0.0, "down": -0.0002}

# ---------------------------------------------------------------- numeric helpers

SQRT2 = math.sqrt(2.0)
SQRT2PI = math.sqrt(2.0 * math.pi)
INV_SQRT_PI = 1.0 / math.sqrt(math.pi)

def norm_cdf(x):
    """Standard normal CDF through math.erf."""
    return 0.5 * (1.0 + math.erf(x / SQRT2))

def norm_pdf(x):
    """Standard normal density."""
    return math.exp(-0.5 * x * x) / SQRT2PI

def norm_ppf(p):
    """Standard normal quantile by bisection on [-12, 12] (60 halvings, 3e-18 wide)."""
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")
    lo, hi = -12.0, 12.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)

PINBALL_Z = tuple(norm_ppf(t) for t in PINBALL_TAUS)

def mean(v):
    return sum(v) / len(v) if v else 0.0

def variance(v):
    """Sample variance (n - 1)."""
    n = len(v)
    if n < 2:
        return 0.0
    m = mean(v)
    return sum((x - m) ** 2 for x in v) / (n - 1)

def stdev(v):
    return math.sqrt(variance(v))

def quantile(xs, p):
    """Empirical quantile with linear interpolation (Hyndman and Fan type 7, numpy default)."""
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    if n == 1:
        return s[0]
    pos = p * (n - 1)
    i = int(math.floor(pos))
    if i >= n - 1:
        return s[-1]
    if i < 0:
        return s[0]
    f = pos - i
    return s[i] + f * (s[i + 1] - s[i])

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

# ---------------------------------------------------------------- returns, range estimators

def log_returns(adj):
    """r[t] = ln(adj[t] / adj[t-1]), one shorter than the input."""
    return [math.log(adj[i] / adj[i - 1]) for i in range(1, len(adj)) if adj[i] > 0 and adj[i - 1] > 0]

def _ohlc(row):
    """A history row is [date, o, h, l, c, adj, v] (list) or a dict with those keys."""
    if isinstance(row, dict):
        return (float(row["open"]), float(row["high"]), float(row["low"]),
                float(row["close"]), float(row.get("adjclose", row.get("adjClose", row["close"]))))
    return float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])

def gap_returns(rows):
    """Overnight log return ln(open[t] / close[t-1]) on adjusted prices (open scaled by adj/close
    so a split or dividend does not show up as a gap). One shorter than the input."""
    out = []
    for i in range(1, len(rows)):
        o, h, l, c, adj = _ohlc(rows[i])
        o0, h0, l0, c0, adj0 = _ohlc(rows[i - 1])
        if o > 0 and c > 0 and adj0 > 0:
            out.append(math.log(o * adj / c / adj0))
    return out

def intraday_returns(rows):
    """Open to close log return ln(close / open) per bar."""
    out = []
    for row in rows:
        o, h, l, c, adj = _ohlc(row)
        if o > 0 and c > 0:
            out.append(math.log(c / o))
    return out

def garman_klass(o, h, l, c):
    """Garman and Klass (1980) per bar variance, 0.5 ln(h/l)^2 - (2 ln 2 - 1) ln(c/o)^2. Lists in,
    list out. About 7x more efficient than close to close on a single bar but blind to the gap."""
    k = 2.0 * math.log(2.0) - 1.0
    out = []
    for i in range(len(c)):
        if h[i] > 0 and l[i] > 0 and o[i] > 0 and c[i] > 0:
            hl = math.log(h[i] / l[i])
            co = math.log(c[i] / o[i])
            out.append(0.5 * hl * hl - k * co * co)
        else:
            out.append(0.0)
    return out

def yang_zhang_series(rows, window=20):
    """Yang and Zhang (2000) daily variance on a rolling window: sigma^2 = V_overnight + k V_openclose
    + (1 - k) V_rogers_satchell with k = 0.34 / (1.34 + (n + 1) / (n - 1)), the minimum variance
    choice in the paper. Rolling sums, O(N). Aligned to rows[window:] (first value is for the bar at
    index `window`); returns a list of length len(rows) - window, or [] if too short."""
    n = len(rows)
    if n <= window:
        return []
    ov, cc, rs = [], [], []
    for i in range(1, n):
        o, h, l, c, adj = _ohlc(rows[i])
        o0, h0, l0, c0, adj0 = _ohlc(rows[i - 1])
        if min(o, h, l, c, adj, adj0) <= 0:
            ov.append(0.0); cc.append(0.0); rs.append(0.0)
            continue
        ov.append(math.log(o * adj / c / adj0))
        cc.append(math.log(c / o))
        rs.append(math.log(h / o) * math.log(h / c) + math.log(l / o) * math.log(l / c))
    k = 0.34 / (1.34 + (window + 1.0) / (window - 1.0))
    out = []
    s_o = s_o2 = s_c = s_c2 = s_rs = 0.0
    for i in range(len(ov)):
        s_o += ov[i]; s_o2 += ov[i] * ov[i]
        s_c += cc[i]; s_c2 += cc[i] * cc[i]
        s_rs += rs[i]
        if i >= window:
            j = i - window
            s_o -= ov[j]; s_o2 -= ov[j] * ov[j]
            s_c -= cc[j]; s_c2 -= cc[j] * cc[j]
            s_rs -= rs[j]
        if i >= window - 1:
            v_o = max(0.0, (s_o2 - s_o * s_o / window) / (window - 1))
            v_c = max(0.0, (s_c2 - s_c * s_c / window) / (window - 1))
            v_rs = max(0.0, s_rs / window)
            out.append(max(1e-12, v_o + k * v_c + (1 - k) * v_rs))
    return out

def yang_zhang(rows, window=20):
    """Latest Yang-Zhang daily variance over the last `window` bars (see yang_zhang_series)."""
    s = yang_zhang_series(rows[-(window + 1):], window)
    return s[-1] if s else 0.0

# ---------------------------------------------------------------- volatility experts

def ewma_var(r, lam=EWMA_LAMBDA):
    """RiskMetrics (1996) EWMA variance for the next day, lambda 0.94, the zero parameter baseline
    every other variance expert is scored against. Seeded with the mean square of the first 50 obs
    (r[0]^2 alone can be a 1 in 1000 day and needs ~100 days to wash out)."""
    n = len(r)
    if n == 0:
        return 0.0
    k = min(50, n)
    v = sum(x * x for x in r[:k]) / k
    for x in r[k:]:
        v = lam * v + (1 - lam) * x * x
    return v

def ewma_var_series(r, lam=EWMA_LAMBDA):
    """The EWMA variance after each observation (same seeding as ewma_var), length len(r)."""
    n = len(r)
    if n == 0:
        return []
    k = min(50, n)
    v = sum(x * x for x in r[:k]) / k
    out = [v] * k
    for x in r[k:]:
        v = lam * v + (1 - lam) * x * x
        out.append(v)
    return out

def _nelder_mead(f, x0, step, max_iter=600, ftol=1e-9, xtol=1e-7):
    """Nelder and Mead (1965) simplex, standard coefficients (reflect 1, expand 2, contract 0.5,
    shrink 0.5). Returns (x_best, f_best, iterations)."""
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        x = list(x0)
        x[i] += step[i]
        simplex.append(x)
    fs = [f(x) for x in simplex]
    it = 0
    while it < max_iter:
        it += 1
        order = sorted(range(n + 1), key=lambda i: fs[i])
        simplex = [simplex[i] for i in order]
        fs = [fs[i] for i in order]
        spread = max(abs(simplex[j][i] - simplex[0][i]) for j in range(1, n + 1) for i in range(n))
        if fs[-1] - fs[0] < ftol and spread < xtol:
            break
        cen = [sum(simplex[j][i] for j in range(n)) / n for i in range(n)]
        worst = simplex[-1]
        xr = [cen[i] + (cen[i] - worst[i]) for i in range(n)]
        fr = f(xr)
        if fr < fs[0]:
            xe = [cen[i] + 2.0 * (cen[i] - worst[i]) for i in range(n)]
            fe = f(xe)
            if fe < fr:
                simplex[-1], fs[-1] = xe, fe
            else:
                simplex[-1], fs[-1] = xr, fr
        elif fr < fs[-2]:
            simplex[-1], fs[-1] = xr, fr
        else:
            if fr < fs[-1]:
                xc = [cen[i] + 0.5 * (xr[i] - cen[i]) for i in range(n)]
            else:
                xc = [cen[i] - 0.5 * (cen[i] - worst[i]) for i in range(n)]
            fc = f(xc)
            if fc < min(fr, fs[-1]):
                simplex[-1], fs[-1] = xc, fc
            else:
                best = simplex[0]
                for j in range(1, n + 1):
                    simplex[j] = [best[i] + 0.5 * (simplex[j][i] - best[i]) for i in range(n)]
                    fs[j] = f(simplex[j])
    i0 = min(range(n + 1), key=lambda i: fs[i])
    return simplex[i0], fs[i0], it

def _gjr_params(params):
    if isinstance(params, dict):
        return params["omega"], params["alpha"], params["gamma"], params["beta"]
    return params[0], params[1], params[2], params[3]

def _gjr_nll(p, r2, neg, h0):
    """Gaussian negative log likelihood (up to the constant) of GJR on pct returns, with a linear
    penalty outside the feasible set so the simplex is pushed back rather than walled off."""
    omega, alpha, gamma, beta = p
    pen = 0.0
    if omega <= 1e-8:
        pen += 1e-8 - omega; omega = 1e-8
    if alpha < 0:
        pen -= alpha; alpha = 0.0
    if gamma < 0:
        pen -= gamma; gamma = 0.0
    if beta < 0:
        pen -= beta; beta = 0.0
    persist = alpha + beta + 0.5 * gamma
    if persist >= GJR_PERSIST_MAX:
        pen += persist - GJR_PERSIST_MAX + 1e-6
        scale = (GJR_PERSIST_MAX - 1e-6) / persist
        alpha *= scale; gamma *= scale; beta *= scale
    h = h0
    nll = math.log(h) + r2[0] / h
    ag = alpha + gamma
    log = math.log
    for t in range(1, len(r2)):
        h = omega + (ag if neg[t - 1] else alpha) * r2[t - 1] + beta * h
        nll += log(h) + r2[t] / h
    return 0.5 * nll + 1e4 * pen

def gjr_garch_fit(r, init=None, max_obs=GJR_MAX_OBS):
    """GJR GARCH(1,1) (Glosten, Jagannathan and Runkle 1993): h[t] = omega + (alpha + gamma 1{r<0})
    r[t-1]^2 + beta h[t-1], Gaussian quasi likelihood, Nelder-Mead by hand on the last `max_obs`
    returns. Fitted on returns x100 so the four parameters live on comparable scales (omega is
    converted back to fraction units in the result). Constraints omega > 0, alpha, gamma, beta >= 0,
    alpha + beta + gamma / 2 < 0.999 by penalty. One restart from the first solution with a smaller
    simplex, which is the cheapest cure for Nelder-Mead's habit of stalling on a ridge.
    Returns a JSON friendly dict {omega, alpha, gamma, beta, nll, n, iters}. `init` may be a previous
    result (warm start) or None."""
    r = r[-max_obs:]
    n = len(r)
    if n < 100:
        v = variance(r) if n > 1 else 1e-4
        return {"omega": v * 0.05, "alpha": 0.02, "gamma": 0.10, "beta": 0.88, "nll": None, "n": n, "iters": 0}
    rp = [x * 100.0 for x in r]
    r2 = [x * x for x in rp]
    neg = [1 if x < 0 else 0 for x in rp]
    v = sum(r2) / n
    h0 = v
    if init is not None:
        o, a, g, b = _gjr_params(init)
        x0 = [o * 1e4, a, g, b]
        if x0[0] <= 0:
            x0[0] = v * 0.05
    else:
        x0 = [v * 0.05, 0.02, 0.10, 0.88]
    f = lambda p: _gjr_nll(p, r2, neg, h0)
    step = [0.5 * x0[0], 0.02, 0.05, 0.05]
    x, fx, it1 = _nelder_mead(f, x0, step)
    step = [0.1 * max(x[0], 1e-6), 0.005, 0.01, 0.01]
    x, fx, it2 = _nelder_mead(f, x, step)
    omega, alpha, gamma, beta = x
    omega = max(omega, 1e-8); alpha = max(alpha, 0.0); gamma = max(gamma, 0.0); beta = max(beta, 0.0)
    persist = alpha + beta + 0.5 * gamma
    if persist >= GJR_PERSIST_MAX:
        s = (GJR_PERSIST_MAX - 1e-6) / persist
        alpha *= s; gamma *= s; beta *= s
    return {"omega": omega / 1e4, "alpha": alpha, "gamma": gamma, "beta": beta,
            "nll": fx, "n": n, "iters": it1 + it2}

def gjr_garch_forecast(r, params, max_obs=GJR_MAX_OBS):
    """Run the GJR recursion over the last `max_obs` returns with fixed params and return the next
    day variance h[T+1]. Seeded at the sample mean square, same as the fit."""
    omega, alpha, gamma, beta = _gjr_params(params)
    r = r[-max_obs:]
    if not r:
        return omega / max(1e-9, 1 - alpha - beta - 0.5 * gamma)
    h = sum(x * x for x in r) / len(r)
    for x in r:
        h = omega + (alpha + (gamma if x < 0 else 0.0)) * x * x + beta * h
    return h

def _solve3(A, b):
    """Gaussian elimination with partial pivoting for a 3x3 system."""
    M = [list(A[i]) + [b[i]] for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda i: abs(M[i][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-14:
            return None
        for i in range(c + 1, 3):
            f = M[i][c] / M[c][c]
            for j in range(c, 4):
                M[i][j] -= f * M[c][j]
    x = [0.0] * 3
    for i in (2, 1, 0):
        x[i] = (M[i][3] - sum(M[i][j] * x[j] for j in range(i + 1, 3))) / M[i][i]
    return x

def har_fit(rv_series):
    """Log HAR (Corsi 2009): ln rv[t+1] = a + b_d ln rv[t] + b_w mean(ln rv[t-4..t]) + b_m
    mean(ln rv[t-21..t]). OLS by hand: centre everything, solve the 3x3 normal equations, recover
    the intercept. The slopes are then shrunk toward Corsi's published daily horizon estimates
    (0.372, 0.343, 0.224) with weight 1 / (1 + n / 250), so 250 obs trust the data half way and
    1500 obs about 86%. Returns {a, bd, bw, bm, n, residVar, bd0...} or None if under 60 obs."""
    lv = [math.log(max(1e-12, x)) for x in rv_series]
    n_all = len(lv)
    if n_all < 60:
        return None
    X, y = [], []
    cw = cm = 0.0
    for t in range(21, n_all - 1):
        d = lv[t]
        w = sum(lv[t - 4:t + 1]) / 5.0
        m = sum(lv[t - 21:t + 1]) / 22.0
        X.append((d, w, m))
        y.append(lv[t + 1])
    n = len(y)
    xb = [sum(x[i] for x in X) / n for i in range(3)]
    yb = sum(y) / n
    S = [[0.0] * 3 for _ in range(3)]
    c = [0.0] * 3
    for k in range(n):
        dx = [X[k][i] - xb[i] for i in range(3)]
        dy = y[k] - yb
        for i in range(3):
            c[i] += dx[i] * dy
            for j in range(3):
                S[i][j] += dx[i] * dx[j]
    for i in range(3):
        S[i][i] += 1e-10
    b_ols = _solve3(S, c)
    if b_ols is None:
        b_ols = list(HAR_PRIORS)
    w = 1.0 / (1.0 + n / HAR_PRIOR_SCALE)
    b = [w * HAR_PRIORS[i] + (1 - w) * b_ols[i] for i in range(3)]
    a = yb - sum(b[i] * xb[i] for i in range(3))
    resid = [y[k] - a - sum(b[i] * X[k][i] for i in range(3)) for k in range(n)]
    rvar = sum(e * e for e in resid) / max(1, n - 4)
    return {"a": a, "bd": b[0], "bw": b[1], "bm": b[2], "n": n, "residVar": rvar,
            "ols": {"bd": b_ols[0], "bw": b_ols[1], "bm": b_ols[2]}, "shrink": w}

def har_rv(rv_series, fit=None):
    """Next day realised variance from the log HAR. The log model forecasts the median of a
    lognormal, so half the residual variance is added back to get the mean (Corsi 2009, section 4).
    Falls back to the last value when there is too little history to fit."""
    if fit is None:
        fit = har_fit(rv_series)
    if fit is None:
        return rv_series[-1] if rv_series else 0.0
    lv = [math.log(max(1e-12, x)) for x in rv_series[-22:]]
    d = lv[-1]
    w = sum(lv[-5:]) / len(lv[-5:])
    m = sum(lv) / len(lv)
    return math.exp(fit["a"] + fit["bd"] * d + fit["bw"] * w + fit["bm"] * m + 0.5 * fit["residVar"])

# ---------------------------------------------------------------- Hedge combination

def hedge_eta(n_experts, horizon=HEDGE_HORIZON):
    """Freund and Schapire (1997) learning rate sqrt(8 ln N / T), the regret optimal choice for a
    known horizon T, here 250 trading days."""
    return math.sqrt(8.0 * math.log(max(2, n_experts)) / horizon)

def hedge_weights(cum_losses, eta):
    """Exponential weights w_i proportional to exp(-eta L_i) over cumulative losses (Freund and
    Schapire 1997, Hedge). Losses must be in comparable, dimensionless units: for the density
    experts pass CRPS divided by the EWMA sigma (a CRPS of 0.003 on raw returns would never move
    the weights), for the variance experts QLIKE is already scale free (its differences are)."""
    if not cum_losses:
        return []
    m = min(cum_losses)
    w = [math.exp(-eta * (L - m)) for L in cum_losses]
    s = sum(w)
    return [x / s for x in w]

def shrink_weights(w, t, cap=HEDGE_SHRINK_CAP, horizon=HEDGE_HORIZON):
    """w_final = lam w_hedge + (1 - lam) / N with lam = min(cap, t / horizon): uniform on day one,
    half trusted after a year, never more than half trusted."""
    n = len(w)
    if n == 0:
        return []
    lam = min(cap, t / float(horizon))
    return [lam * x + (1 - lam) / n for x in w]

# ---------------------------------------------------------------- density experts

def rw_drift(r):
    """Random walk with the long run drift (Damodaran 10.08% a year over 252 days = 0.04% a day)
    and the EWMA sigma. The baseline every density is scored against."""
    return MU_LR_DAILY, math.sqrt(ewma_var(r))

def gjr_zero(r, params):
    """Zero mean, GJR GARCH sigma."""
    return 0.0, math.sqrt(max(1e-12, gjr_garch_forecast(r, params)))

def har_dens(rv_series, fit=None):
    """Zero mean, HAR sigma (sqrt of the forecast daily variance)."""
    return 0.0, math.sqrt(max(1e-12, har_rv(rv_series, fit)))

def trend_regime(adj, sigma, dates=None):
    """Faber (2007) style trend prior: +4bp a day above both the 200 day and the 10 month SMA,
    -2bp below both, 0 when mixed. Sigma is the caller's combined variance expert."""
    st = trend_state(adj, dates)
    return TREND_MU[st["state"]], sigma

def _hmm_init(r, init):
    if init is not None:
        return ([float(x) for x in init["mu"]], [float(x) for x in init["sd"]],
                [[float(x) for x in row] for row in init["A"]], [float(x) for x in init["pi"]])
    m = mean(r)
    s = max(1e-6, stdev(r))
    return [m, m], [0.6 * s, 1.6 * s], [[0.97, 0.03], [0.05, 0.95]], [0.5, 0.5]

def _hmm_emis(r, mu, sd):
    """Gaussian emission densities for both states, two lists."""
    out = []
    for k in range(2):
        s = max(1e-6, sd[k])
        m = mu[k]
        c = 1.0 / (s * SQRT2PI)
        inv = -0.5 / (s * s)
        out.append([c * math.exp(inv * (x - m) * (x - m)) for x in r])
    return out

def _hmm_forward(b0, b1, A, pi):
    """Scaled forward pass (Rabiner 1989). Returns (alpha_hat list of (a0, a1), scales, loglik)."""
    n = len(b0)
    a00, a01, a10, a11 = A[0][0], A[0][1], A[1][0], A[1][1]
    x0 = pi[0] * b0[0]
    x1 = pi[1] * b1[0]
    c = x0 + x1
    if c <= 0:
        c = 1e-300
    x0 /= c; x1 /= c
    alph = [(x0, x1)]
    ll = math.log(c)
    for t in range(1, n):
        y0 = (x0 * a00 + x1 * a10) * b0[t]
        y1 = (x0 * a01 + x1 * a11) * b1[t]
        c = y0 + y1
        if c <= 0:
            c = 1e-300
        x0 = y0 / c; x1 = y1 / c
        alph.append((x0, x1))
        ll += math.log(c)
    return alph, ll

def hmm_fit(r, init=None, iters=HMM_ITERS):
    """Two state Gaussian hidden Markov model by scaled Baum-Welch (Rabiner 1989), 60 iterations,
    warm start from `init` (a previous result) when given. States are ordered so index 1 is the
    high volatility state. Returns {mu:[2], sd:[2], A:[[2],[2]], pi:[2], loglik, n, iters}."""
    n = len(r)
    if n < 100:
        mu, sd, A, pi = _hmm_init(r, init)
        return {"mu": mu, "sd": sd, "A": A, "pi": pi, "loglik": None, "n": n, "iters": 0}
    mu, sd, A, pi = _hmm_init(r, init)
    ll_prev = None
    done = 0
    for it in range(iters):
        b0, b1 = _hmm_emis(r, mu, sd)
        alph, ll = _hmm_forward(b0, b1, A, pi)
        a00, a01, a10, a11 = A[0][0], A[0][1], A[1][0], A[1][1]
        # backward, scaled to sum to one each step (only ratios matter once we normalise gamma/xi)
        bt0 = bt1 = 1.0
        g0 = [0.0] * n; g1 = [0.0] * n
        x00 = x01 = x10 = x11 = 0.0
        f0, f1 = alph[n - 1]
        g0[n - 1] = f0; g1[n - 1] = f1
        for t in range(n - 2, -1, -1):
            e0 = b0[t + 1] * bt0
            e1 = b1[t + 1] * bt1
            n0 = a00 * e0 + a01 * e1
            n1 = a10 * e0 + a11 * e1
            c = n0 + n1
            if c <= 0:
                c = 1e-300
            f0, f1 = alph[t]
            # xi before normalising beta so the four cells share one scale
            q00 = f0 * a00 * e0; q01 = f0 * a01 * e1
            q10 = f1 * a10 * e0; q11 = f1 * a11 * e1
            qs = q00 + q01 + q10 + q11
            if qs > 0:
                x00 += q00 / qs; x01 += q01 / qs; x10 += q10 / qs; x11 += q11 / qs
            bt0 = n0 / c; bt1 = n1 / c
            p0 = f0 * bt0; p1 = f1 * bt1
            ps = p0 + p1
            if ps > 0:
                g0[t] = p0 / ps; g1[t] = p1 / ps
            else:
                g0[t] = f0; g1[t] = f1
        s0 = sum(g0); s1 = sum(g1)
        pi = [max(1e-6, g0[0]), max(1e-6, g1[0])]
        r0 = x00 + x01; r1 = x10 + x11
        if r0 > 0:
            A[0] = [clamp(x00 / r0, 1e-4, 1 - 1e-4), clamp(x01 / r0, 1e-4, 1 - 1e-4)]
        if r1 > 0:
            A[1] = [clamp(x10 / r1, 1e-4, 1 - 1e-4), clamp(x11 / r1, 1e-4, 1 - 1e-4)]
        if s0 > 1e-9:
            m0 = sum(g0[t] * r[t] for t in range(n)) / s0
            v0 = sum(g0[t] * (r[t] - m0) ** 2 for t in range(n)) / s0
            mu[0] = m0; sd[0] = math.sqrt(max(1e-10, v0))
        if s1 > 1e-9:
            m1 = sum(g1[t] * r[t] for t in range(n)) / s1
            v1 = sum(g1[t] * (r[t] - m1) ** 2 for t in range(n)) / s1
            mu[1] = m1; sd[1] = math.sqrt(max(1e-10, v1))
        done = it + 1
        if ll_prev is not None and abs(ll - ll_prev) < 1e-7 * max(1.0, abs(ll)):
            ll_prev = ll
            break
        ll_prev = ll
    if sd[0] > sd[1]:
        mu = [mu[1], mu[0]]; sd = [sd[1], sd[0]]
        A = [[A[1][1], A[1][0]], [A[0][1], A[0][0]]]
        pi = [pi[1], pi[0]]
    return {"mu": mu, "sd": sd, "A": [list(A[0]), list(A[1])], "pi": pi,
            "loglik": ll_prev, "n": n, "iters": done}

def hmm_filter_path(r, params):
    """Filtered P(high vol state | r[1..t]) for every t (forward pass only), length len(r)."""
    if not r:
        return []
    b0, b1 = _hmm_emis(r, params["mu"], params["sd"])
    alph, ll = _hmm_forward(b0, b1, params["A"], params["pi"])
    return [a[1] for a in alph]

def hmm_filter(r, params):
    """Filtered P(high vol state | r[1..T]) for the last observation."""
    if not r:
        return float(params["pi"][1])
    return hmm_filter_path(r, params)[-1]

def hmm_mix(r, params):
    """(mu, sigma) of tomorrow's predictive mixture: filtered state probabilities pushed one step
    through the transition matrix, then the mixture mean and sd (law of total variance)."""
    p_hi = hmm_filter(r, params)
    A = params["A"]
    q0 = (1 - p_hi) * A[0][0] + p_hi * A[1][0]
    q1 = 1.0 - q0
    mu = q0 * params["mu"][0] + q1 * params["mu"][1]
    m2 = q0 * (params["sd"][0] ** 2 + params["mu"][0] ** 2) + q1 * (params["sd"][1] ** 2 + params["mu"][1] ** 2)
    return mu, math.sqrt(max(1e-12, m2 - mu * mu))

def combine_density(experts, weights):
    """Linear pool of Gaussians summarised by its first two moments: mu = sum w mu_i,
    sigma^2 = sum w (sigma_i^2 + mu_i^2) - mu^2. `experts` is a list of (mu, sigma)."""
    s = sum(weights)
    if s <= 0 or not experts:
        return 0.0, 0.0
    w = [x / s for x in weights]
    mu = sum(w[i] * experts[i][0] for i in range(len(experts)))
    m2 = sum(w[i] * (experts[i][1] ** 2 + experts[i][0] ** 2) for i in range(len(experts)))
    return mu, math.sqrt(max(1e-12, m2 - mu * mu))

def combine_variance(hs, weights):
    """Linear pool of variance forecasts."""
    s = sum(weights)
    if s <= 0 or not hs:
        return 0.0
    return sum(weights[i] * hs[i] for i in range(len(hs))) / s

def p_up(mu, sigma):
    """P(r > 0) under N(mu, sigma^2)."""
    if sigma <= 0:
        return 1.0 if mu > 0 else 0.0
    return 1.0 - norm_cdf(-mu / sigma)

# ---------------------------------------------------------------- pre market gap adjustment

def overnight_share(gaps, rets, window=OVERNIGHT_WINDOW, clip=OVERNIGHT_CLIP):
    """Share of close to close variance that happens overnight: var(gap) / var(close to close) over
    the last `window` days, clipped to [0.1, 0.7] (US large caps sit near 0.3 to 0.5)."""
    g = gaps[-window:]
    c = rets[-window:]
    vg, vc = variance(g), variance(c)
    if vc <= 0 or len(g) < 20:
        return clip[0]
    return clamp(vg / vc, clip[0], clip[1])

def pre_adjust(mu, sigma, gap, share):
    """Condition the close to close density on a known overnight move of log size `gap`: the gap
    is realised, so mu_pre = gap + (1 - s) mu and sigma_pre = sigma sqrt(1 - s), where s is the
    overnight variance share. With gap None the post run forecast is returned unchanged."""
    if gap is None:
        return mu, sigma
    s = clamp(share, 0.0, 0.95)
    return gap + (1 - s) * mu, sigma * math.sqrt(1 - s)

# ---------------------------------------------------------------- adaptive conformal intervals

def aci_quantile(scores, alpha, warmup=ACI_WARMUP, window=ACI_WINDOW):
    """Conformal quantile of the last `window` nonconformity scores |r - mu| / sigma at level
    1 - alpha: the ceil((n + 1)(1 - alpha)) th smallest (Gibbs and Candes 2021, eq. 2). Before
    `warmup` scores exist the Gaussian two sided quantile (1.645 at 90%, 0.674 at 50%) is used."""
    s = scores[-window:]
    n = len(s)
    if n < warmup:
        return norm_ppf(1 - alpha / 2.0)
    srt = sorted(s)
    k = int(math.ceil((n + 1) * (1 - alpha)))
    k = max(1, min(n, k))
    return srt[k - 1]

def aci_update(alpha_t, err, target_alpha, gamma=ACI_GAMMA):
    """Adaptive conformal inference step: alpha_{t+1} = alpha_t + gamma (target - err), err = 1 if
    the outcome fell outside the band. Clipped to [0.001, 0.999]."""
    return clamp(alpha_t + gamma * (target_alpha - err), ACI_ALPHA_MIN, ACI_ALPHA_MAX)

def aci_band(mu, sigma, q):
    """(lo, hi) = mu -/+ q sigma."""
    return mu - q * sigma, mu + q * sigma

# ---------------------------------------------------------------- scores

def hit(r, mu):
    """1 if sign(r) == sign(mu); a zero return counts as a hit when mu >= 0."""
    if r == 0:
        return 1 if mu >= 0 else 0
    return 1 if (r > 0) == (mu > 0) else 0

def brier(pup, r):
    """(p_up - 1{r > 0})^2."""
    return (pup - (1.0 if r > 0 else 0.0)) ** 2

def logscore(r, mu, sigma):
    """-ln N(r; mu, sigma^2). Lower is better."""
    sigma = max(1e-12, sigma)
    z = (r - mu) / sigma
    return 0.5 * math.log(2 * math.pi) + math.log(sigma) + 0.5 * z * z

def crps_gauss(r, mu, sigma):
    """CRPS of a Gaussian, closed form (Gneiting and Raftery 2007, eq. 21):
    sigma [z (2 Phi(z) - 1) + 2 phi(z) - 1 / sqrt(pi)], z = (r - mu) / sigma. Same units as r."""
    sigma = max(1e-12, sigma)
    z = (r - mu) / sigma
    return sigma * (z * (2 * norm_cdf(z) - 1) + 2 * norm_pdf(z) - INV_SQRT_PI)

def pinball(r, mu, sigma, taus=PINBALL_TAUS):
    """Pinball (quantile) loss at each tau on the Gaussian quantiles mu + sigma Phi^-1(tau)."""
    out = []
    for i, tau in enumerate(taus):
        z = PINBALL_Z[i] if taus is PINBALL_TAUS else norm_ppf(tau)
        q = mu + sigma * z
        d = r - q
        out.append(tau * d if d >= 0 else (tau - 1) * d)
    return out

def coverage(r, lo, hi):
    """1 if lo <= r <= hi."""
    return 1 if lo <= r <= hi else 0

def pit(r, mu, sigma):
    """Probability integral transform Phi((r - mu) / sigma); uniform on [0, 1] if calibrated."""
    return norm_cdf((r - mu) / max(1e-12, sigma))

def qlike(r, h):
    """QLIKE loss ln h + r^2 / h (Patton 2011), the robust loss for variance forecasts."""
    h = max(1e-14, h)
    return math.log(h) + r * r / h

def score_forecast(r, mu, sigma, pup, lo90, hi90, lo50, hi50):
    """All per forecast scores in one dict (keys match the ledger's score row)."""
    return {"hit": hit(r, mu), "brier": brier(pup, r), "logs": logscore(r, mu, sigma),
            "crps": crps_gauss(r, mu, sigma), "pin": pinball(r, mu, sigma),
            "cov90": coverage(r, lo90, hi90), "cov50": coverage(r, lo50, hi50), "pit": pit(r, mu, sigma)}

# ---------------------------------------------------------------- baselines and skill

def climatology(r, window=CLIM_WINDOW):
    """Trailing up day fraction over the last `window` returns (two years), the P(up) baseline."""
    s = r[-window:]
    if not s:
        return 0.5
    return sum(1 for x in s if x > 0) / float(len(s))

def skill(score_model, score_base):
    """1 - model / baseline for a negatively oriented score. Positive is good. None if the
    baseline is zero or either input is missing."""
    if score_model is None or score_base is None or score_base == 0:
        return None
    return 1.0 - score_model / score_base

def dm_tstat(loss_model, loss_base, lag=DM_LAG):
    """Diebold and Mariano (1995) t statistic on d = loss_model - loss_base with a Newey-West (1987)
    long run variance, Bartlett weights to `lag`. Negative means the model is better. None under
    10 observations or when the long run variance is zero."""
    n = min(len(loss_model), len(loss_base))
    if n < 10:
        return None
    d = [loss_model[-n + i] - loss_base[-n + i] for i in range(n)]
    m = mean(d)
    e = [x - m for x in d]
    g0 = sum(x * x for x in e) / n
    s = g0
    for k in range(1, min(lag, n - 1) + 1):
        gk = sum(e[t] * e[t - k] for t in range(k, n)) / n
        s += 2.0 * (1.0 - k / (lag + 1.0)) * gk
    if s <= 0:
        return None
    return m / math.sqrt(s / n)

def min_trl(returns, sr_target=0.0, confidence=0.95):
    """Bailey and Lopez de Prado (2012) minimum track record length, in observations:
    1 + [1 - g3 SR + (g4 - 1) / 4 SR^2] (z / (SR - SR*))^2 with SR the per period Sharpe, g3 skew,
    g4 kurtosis (not excess). None when SR <= SR* (no finite record proves nothing) or under 10 obs."""
    n = len(returns)
    if n < 10:
        return None
    m = mean(returns)
    s = stdev(returns)
    if s <= 0:
        return None
    sr = m / s
    if sr <= sr_target:
        return None
    g3 = sum(((x - m) / s) ** 3 for x in returns) / n
    g4 = sum(((x - m) / s) ** 4 for x in returns) / n
    z = norm_ppf(confidence)
    return 1.0 + (1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr) * (z / (sr - sr_target)) ** 2

def binom_ci(hits, n, z=1.959964):
    """Wilson (1927) 95% score interval for a proportion. (0, 1) when n is 0."""
    if n <= 0:
        return 0.0, 1.0
    p = hits / float(n)
    z2 = z * z
    den = 1.0 + z2 / n
    cen = (p + z2 / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4.0 * n * n)) / den
    return max(0.0, cen - half), min(1.0, cen + half)

# ---------------------------------------------------------------- signals and regime

def mom_12_1(adj):
    """Jegadeesh and Titman (1993) 12-1 momentum: adj[-22] / adj[-252] - 1, skipping the last
    month (short term reversal). None under 252 bars."""
    if len(adj) < 252 or adj[-252] <= 0:
        return None
    return adj[-22] / adj[-252] - 1.0

def mom_6(adj):
    """Six month return adj[-1] / adj[-127] - 1. None under 127 bars."""
    if len(adj) < 127 or adj[-127] <= 0:
        return None
    return adj[-1] / adj[-127] - 1.0

def ret_1m(adj):
    """One month return adj[-1] / adj[-22] - 1. None under 22 bars."""
    if len(adj) < 22 or adj[-22] <= 0:
        return None
    return adj[-1] / adj[-22] - 1.0

def hi52_ratio(close):
    """Last close over the highest close of the last 252 bars (1.0 means at the 52 week high)."""
    s = close[-252:]
    if not s:
        return None
    hi = max(s)
    return close[-1] / hi if hi > 0 else None

def _month_end_closes(adj, dates, k=10):
    """Last close of each of the last k calendar months (the current month counts with its latest
    close, Faber 2007 style). Without dates, every 21st bar back from the end."""
    if dates and len(dates) == len(adj):
        out = []
        last_m = None
        for i in range(len(adj) - 1, -1, -1):
            m = str(dates[i])[:7]
            if m != last_m:
                out.append(adj[i])
                last_m = m
                if len(out) == k:
                    break
        return out
    return [adj[-1 - 21 * i] for i in range(k) if 1 + 21 * i <= len(adj)]

def trend_state(adj, dates=None):
    """above200 = close > SMA(200); above10m = close > mean of the last 10 month end closes
    (Faber 2007). state: up (both), down (neither), mixed. With fewer than 200 bars the SMA is over
    what exists (callers gate on history length separately)."""
    if not adj:
        return {"above200": False, "above10m": False, "state": "mixed", "sma200": None, "sma10m": None}
    c = adj[-1]
    sma200 = mean(adj[-200:])
    me = _month_end_closes(adj, dates)
    sma10m = mean(me)
    a200 = c > sma200
    a10 = c > sma10m
    state = "up" if (a200 and a10) else "down" if (not a200 and not a10) else "mixed"
    return {"above200": a200, "above10m": a10, "state": state, "sma200": sma200, "sma10m": sma10m}

def vol_regime(sigma_series, window=VOL_REGIME_WINDOW, high=VOL_REGIME_HIGH):
    """Percentile of today's EWMA sigma within the last `window` values (inclusive); high if above
    0.70. Returns {pct, regime}."""
    s = sigma_series[-window:]
    if not s:
        return {"pct": None, "regime": "low"}
    x = s[-1]
    pct = sum(1 for v in s if v <= x) / float(len(s))
    return {"pct": pct, "regime": "high" if pct > high else "low"}

def beta_60(r_sym, r_spy, window=60):
    """OLS beta of the symbol on SPY over the last `window` days. None under 20 common days."""
    n = min(len(r_sym), len(r_spy), window)
    if n < 20:
        return None
    a = r_sym[-n:]
    b = r_spy[-n:]
    ma, mb = mean(a), mean(b)
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    vb = sum((x - mb) ** 2 for x in b)
    return cov / vb if vb > 0 else None

def ann_sigma(sigma_daily):
    return sigma_daily * math.sqrt(TRADING_DAYS)

# ---------------------------------------------------------------- diagnostics (display only)

def autocorr(r, k):
    """Sample autocorrelation at lag k."""
    n = len(r)
    if n <= k + 1:
        return None
    m = mean(r)
    den = sum((x - m) ** 2 for x in r)
    if den <= 0:
        return None
    return sum((r[t] - m) * (r[t - k] - m) for t in range(k, n)) / den

def variance_ratio(r, q):
    """Lo and MacKinlay (1988) variance ratio VR(q) = var(q day return) / (q var(1 day)), overlapping
    q sums, with their heteroskedasticity robust z (their eq. 24, the one that survives GARCH).
    VR > 1 trends, VR < 1 mean reverts, |z| < 2 says nothing. Returns {vr, z} or Nones under 10q obs."""
    n = len(r)
    if n < 10 * q or q < 2:
        return {"vr": None, "z": None}
    m = mean(r)
    e = [x - m for x in r]
    s1 = sum(x * x for x in e) / (n - 1)
    rq = [sum(r[t - q + 1:t + 1]) - q * m for t in range(q - 1, n)]
    sq = sum(x * x for x in rq) / (q * (n - q + 1)) * (n / float(n - q))  # small sample scaled
    if s1 <= 0:
        return {"vr": None, "z": None}
    vr = sq / s1
    den = sum(x * x for x in e) ** 2
    theta = 0.0
    for j in range(1, q):
        dj = sum(e[t] * e[t] * e[t - j] * e[t - j] for t in range(j, n)) / den
        theta += (2.0 * (q - j) / q) ** 2 * dj
    z = (vr - 1.0) / math.sqrt(theta) if theta > 0 else None
    return {"vr": vr, "z": z}

def _modified_rs(x, q):
    """Lo (1991) modified rescaled range: range of the partial sums of deviations over the
    Newey-West standard deviation with q Bartlett weighted autocovariances."""
    n = len(x)
    m = mean(x)
    e = [v - m for v in x]
    s = 0.0
    mx = mn = 0.0
    for v in e:
        s += v
        if s > mx:
            mx = s
        if s < mn:
            mn = s
    g0 = sum(v * v for v in e) / n
    var = g0
    for j in range(1, q + 1):
        gj = sum(e[t] * e[t - j] for t in range(j, n)) / n
        var += 2.0 * (1.0 - j / (q + 1.0)) * gj
    if var <= 0:
        var = g0 if g0 > 0 else 1e-18
    return (mx - mn) / math.sqrt(var)

def _anis_lloyd(n):
    """Anis and Lloyd (1976) expected R/S of n iid normals, with Peters (1994) (n - 0.5) / n small
    sample factor: E[R/S_n] = (n - 0.5) / n Gamma((n - 1) / 2) / (sqrt(pi) Gamma(n / 2))
    sum_{i=1}^{n-1} sqrt((n - i) / i). Through lgamma so it never overflows."""
    s = sum(math.sqrt((n - i) / float(i)) for i in range(1, n))
    lg = math.lgamma(0.5 * (n - 1)) - math.lgamma(0.5 * n) - 0.5 * math.log(math.pi)
    return (n - 0.5) / n * math.exp(lg) * s

def _hurst_point(r, scales):
    """H = 0.5 + slope of ln(mean R/S_m / E[R/S_m]) on ln m across block sizes m (Mandelbrot and
    Wallis 1969 regression, Lo's correction inside each block, Anis and Lloyd expected value
    subtracted so an iid series reads 0.5 rather than the ~0.56 the raw statistic gives at these
    block sizes)."""
    rho1 = autocorr(r, 1) or 0.0
    xs, ys = [], []
    for m in scales:
        nb = len(r) // m
        if nb < 2:
            continue
        # Andrews (1991) data driven lag for an AR(1), as Lo (1991) uses, capped at m / 4
        if abs(rho1) < 1e-9 or abs(rho1) >= 1:
            q = 0
        else:
            a = 2 * rho1 / (1 - rho1 * rho1)
            q = int((1.5 * m) ** (1.0 / 3.0) * (a * a) ** (1.0 / 3.0))   # (a^2)^(1/3) keeps it real
            q = max(0, min(q, m // 4))
        vals = [_modified_rs(r[b * m:(b + 1) * m], q) for b in range(nb)]
        vals = [v for v in vals if v > 0]
        if not vals:
            continue
        xs.append(math.log(m))
        ys.append(math.log(mean(vals)) - math.log(_anis_lloyd(m)))
    if len(xs) < 2:
        return 0.5
    xb, yb = mean(xs), mean(ys)
    den = sum((x - xb) ** 2 for x in xs)
    return 0.5 + (sum((xs[i] - xb) * (ys[i] - yb) for i in range(len(xs))) / den if den > 0 else 0.0)

def _stationary_bootstrap_indices(n, length, mean_block, rng):
    """Politis and Romano (1994) stationary bootstrap index path: geometric block lengths with
    mean `mean_block`, wrapping around the sample."""
    p = 1.0 / mean_block
    idx = rng.randrange(n)
    out = [idx]
    for _ in range(length - 1):
        if rng.random() < p:
            idx = rng.randrange(n)
        else:
            idx = idx + 1 if idx + 1 < n else 0
        out.append(idx)
    return out

def hurst_rs(r, n_boot=100, seed=7, mean_block=20):
    """Hurst exponent from Lo (1991) modified R/S over block sizes 32 to n/4 (geometric grid), plus a
    stationary block bootstrap 95% interval (n_boot resamples, seeded, deterministic). 0.5 is a random
    walk; the UI labels 0.45 to 0.55 as noise. Returns {h, lo, hi} (lo/hi None if n_boot is 0)."""
    n = len(r)
    if n < 128:
        return {"h": None, "lo": None, "hi": None}
    scales = []
    m = 32
    while m <= n // 4:
        scales.append(m)
        m = int(m * 1.5)
    h = _hurst_point(r, scales)
    if n_boot <= 0:
        return {"h": h, "lo": None, "hi": None}
    rng = random.Random(seed)
    hs = []
    for _ in range(n_boot):
        idx = _stationary_bootstrap_indices(n, n, mean_block, rng)
        hs.append(_hurst_point([r[i] for i in idx], scales))
    return {"h": h, "lo": quantile(hs, 0.025), "hi": quantile(hs, 0.975)}

def overnight_decomposition(rows, window=250):
    """Mean overnight (close to open) and intraday (open to close) log return per day and the
    overnight share of close to close variance, over the last `window` bars."""
    g = gap_returns(rows[-(window + 1):])
    d = intraday_returns(rows[-window:])
    n = min(len(g), len(d))
    if n < 20:
        return {"share": None, "meanGap": None, "meanIntraday": None, "n": n}
    g, d = g[-n:], d[-n:]
    cc = [g[i] + d[i] for i in range(n)]
    vc = variance(cc)
    return {"share": (variance(g) / vc) if vc > 0 else None, "meanGap": mean(g), "meanIntraday": mean(d), "n": n}

# ---------------------------------------------------------------- recommendation helpers

def rank_etf(cands):
    """ETF sleeve ranking. `cands` is a list of dicts {sym, mom (12-1), sigmaAnn, trend}; keeps
    trend up or mixed with a momentum reading, sorts by vol scaled momentum mom / sigmaAnn
    descending. Returns the kept dicts with a `score` key added, best first."""
    kept = []
    for c in cands:
        if c.get("trend") not in ("up", "mixed") or c.get("mom") is None or not c.get("sigmaAnn"):
            continue
        d = dict(c)
        d["score"] = c["mom"] / c["sigmaAnn"]
        kept.append(d)
    kept.sort(key=lambda d: -d["score"])
    return kept

def rank_stock(cands, min_bars=300, hi52_bonus=0.95):
    """Stock sleeve ranking. `cands` dicts {sym, mom, trend, bars, hi52, earningsSoon}. Requires
    trend up or mixed and at least 300 bars; names near their 52 week high (ratio >= 0.95) get a
    one place bonus; earningsSoon names are set aside, not banned. Returns (ranked, skipped) where
    skipped is [{sym, why}]."""
    kept, skipped = [], []
    for c in cands:
        if c.get("mom") is None or (c.get("bars") or 0) < min_bars:
            skipped.append({"sym": c.get("sym"), "why": "under %d bars of history" % min_bars})
            continue
        if c.get("trend") not in ("up", "mixed"):
            skipped.append({"sym": c.get("sym"), "why": "down trend"})
            continue
        if c.get("earningsSoon"):
            skipped.append({"sym": c.get("sym"), "why": "earnings soon, skipped"})
            continue
        kept.append(dict(c))
    kept.sort(key=lambda d: -d["mom"])
    for i, d in enumerate(kept):
        d["nearHigh"] = (d.get("hi52") or 0) >= hi52_bonus
        d["rank"] = i - (1 if d["nearHigh"] else 0)
    # a name lifted one place sits above the name whose place it now shares
    kept.sort(key=lambda d: (d["rank"], 0 if d["nearHigh"] else 1, -d["mom"]))
    return kept, skipped

def kelly_cap(sigma_ann, mu_ann=MU_LR_ANNUAL, fraction=KELLY_FRACTION):
    """Quarter Kelly fraction f = 0.25 mu / sigma^2 with the shrunk long run mean, capped at 1."""
    if not sigma_ann or sigma_ann <= 0:
        return 1.0
    return min(1.0, fraction * mu_ann / (sigma_ann * sigma_ann))

def _largest_remainder(shares, total):
    """Whole units summing exactly to `total`, allocated by Hamilton's largest remainder: floors
    first, then one unit each to the largest fractional parts (the same fix build_data.py's
    round_weights() applies, rounding lines independently never adds up)."""
    if total <= 0 or not shares:
        return [0] * len(shares)
    s = sum(shares)
    if s <= 0:
        return [0] * len(shares)
    raw = [total * x / s for x in shares]
    out = [int(math.floor(x + 1e-9)) for x in raw]
    left = total - sum(out)
    order = sorted(range(len(shares)), key=lambda i: -(raw[i] - out[i]))
    for i in order[:max(0, left)]:
        out[i] += 1
    return out

def size_sleeve(picks, budget, deploy_frac=1.0, kelly=False, vol_target=VOL_TARGET,
                lev_cap=LEV_CAP_USD, min_line=MIN_LINE_USD):
    """Dollar sizing of one sleeve. `picks` best first, dicts {sym, sigmaAnn, lev (optional)}.
    1. inverse vol weights 1 / sigma_i (Barroso and Santa-Clara 2015 use the same for momentum);
    2. scale so the sleeve's annualised vol hits `vol_target`, with the conservative full correlation
       bound sigma_p = sum w_i sigma_i, never above 100% deployed; what the target leaves unspent
       is held, not pushed into the top pick;
    3. whole dollars by largest remainder on budget x deploy_frac x scale;
    4. caps: a leveraged line at lev_cap, and with `kelly` every line at floor(deploy x f_i) where
       f_i = 0.25 mu_LR / sigma_i^2 is the quarter Kelly fraction of the sleeve;
    5. lines under min_line are dropped; only the money freed by caps and drops goes to the top
       pick, up to its own cap; whatever is left after that is held.
    Returns {lines: [{sym, usd, weight}], deployUsd, heldUsd, budgetUsd, volScale, sleeveVol} with
    weight = usd / budget. Never exceeds budget x deploy_frac."""
    budget = int(round(budget))
    deploy = int(round(budget * clamp(deploy_frac, 0.0, 1.0)))
    out = {"lines": [], "deployUsd": 0, "heldUsd": budget, "budgetUsd": budget, "volScale": 0.0, "sleeveVol": 0.0}
    ok = [p for p in picks if p.get("sigmaAnn") and p["sigmaAnn"] > 0]
    if not ok or deploy <= 0:
        return out
    n = len(ok)
    inv = [1.0 / p["sigmaAnn"] for p in ok]
    s = sum(inv)
    w = [x / s for x in inv]
    vol_full = sum(w[i] * ok[i]["sigmaAnn"] for i in range(n))
    scale = min(1.0, vol_target / vol_full) if vol_full > 0 else 1.0
    target = int(round(deploy * scale))               # dollars the vol target allows
    usd = _largest_remainder(w, target)
    cap_usd = [int(math.floor(deploy * kelly_cap(p["sigmaAnn"]) + 1e-9)) if kelly else deploy for p in ok]
    for i, p in enumerate(ok):
        if p.get("lev"):
            cap_usd[i] = min(cap_usd[i], int(lev_cap))
    usd = [min(usd[i], cap_usd[i]) for i in range(n)]
    usd = [x if x >= min_line else 0 for x in usd]
    # money freed by caps and drops goes to the top pick, up to its own cap; the rest is held
    freed = target - sum(usd)
    if freed > 0:
        usd[0] = min(cap_usd[0], usd[0] + freed)
        if usd[0] < min_line:
            usd[0] = 0
    lines = [{"sym": ok[i]["sym"], "usd": usd[i], "weight": usd[i] / float(budget)}
             for i in range(n) if usd[i] > 0]
    dep = sum(l["usd"] for l in lines)
    out.update({"lines": lines, "deployUsd": dep, "heldUsd": budget - dep, "volScale": scale,
                "sleeveVol": vol_full * scale})
    return out

def deploy_fraction(spy_trend, vix_ratio, vol_regime_label):
    """1.0 when SPY's trend is up AND VIX/VIX3M < 1 AND the vol regime is low, else 0.5 (never 0:
    the unspent half is held, not abandoned). Returns (fraction, [failed conditions])."""
    fails = []
    if spy_trend != "up":
        fails.append("SPY trend is %s, not up" % spy_trend)
    if vix_ratio is None or vix_ratio >= 1.0:
        fails.append("VIX term structure in backwardation" if vix_ratio is not None else "VIX ratio unavailable")
    if vol_regime_label != "low":
        fails.append("vol regime is high")
    return (1.0 if not fails else 0.5), fails

def cost_line(usd):
    """INDmoney cost stack on `usd` of orders: brokerage 0.25% + 18% GST on it, FX markup 0.5% to
    1.2% (the band is shown, not a point). Dollars and fractions of the amount."""
    usd = float(usd)
    b = usd * BROKERAGE
    g = b * GST
    lo = usd * FX_BAND[0]
    hi = usd * FX_BAND[1]
    tl, th = b + g + lo, b + g + hi
    return {"brokerage": b, "gst": g, "fxLo": lo, "fxHi": hi, "totalLo": tl, "totalHi": th,
            "pctLo": (tl / usd) if usd > 0 else 0.0, "pctHi": (th / usd) if usd > 0 else 0.0}

# ---------------------------------------------------------------- 12 month fan

FAN_HORIZONS = (21, 63, 126, 252)
FAN_PCTS = (0.05, 0.25, 0.5, 0.75, 0.95)

def fan_seed(sym, month):
    """Deterministic seed for (sym, month). Python's hash() of a str is salted per process, so it
    cannot be used for a reproducible rerun; CRC32 can."""
    return zlib.crc32(("%s|%s" % (sym, month)).encode("utf-8")) & 0x7FFFFFFF

def block_bootstrap_fan(r, n_paths=2000, horizon=252, mean_block=20, seed=0, sigma_now=None,
                        years_cap=5, fade_days=60):
    """Stationary block bootstrap (Politis and Romano 1994) fan of cumulative simple return.
    Sample = the last `years_cap` years of daily log returns, demeaned; drift re added as
    mu* = w mu_sample + (1 - w) mu_LR, w = years / (years + 98) (a 98 year prior, the length of the
    Damodaran record, so 5 years of data move the centre only 5%), mu_LR = ln(1.1008) / 252.
    Residuals are scaled by sigma_now / sigma_sample on day 1 fading linearly to 1 by `fade_days`
    (vol clusters, drift does not). Seeded with random.Random(seed): the same inputs give the same
    bytes. Returns {q: {"21": [p5, p25, p50, p75, p95], ...}, drift, driftLR, w, n, sigmaScale}."""
    r = r[-(years_cap * TRADING_DAYS):]
    n = len(r)
    if n < 60:
        return {"q": {str(h): [None] * 5 for h in FAN_HORIZONS}, "drift": None, "driftLR": MU_LR_DAILY_LOG,
                "w": None, "n": n, "sigmaScale": None}
    mu_s = mean(r)
    years = n / float(TRADING_DAYS)
    w = years / (years + 98.0)
    mu_star = w * mu_s + (1 - w) * MU_LR_DAILY_LOG
    e = [x - mu_s for x in r]
    sig_s = stdev(r)
    s0 = (sigma_now / sig_s) if (sigma_now and sig_s > 0) else 1.0
    scale = [s0 + (1.0 - s0) * min(1.0, t / float(fade_days)) for t in range(horizon)]
    rng = random.Random(seed)
    p = 1.0 / mean_block
    sums = {h: [] for h in FAN_HORIZONS if h <= horizon}
    hset = set(sums.keys())
    randrange, rand = rng.randrange, rng.random
    for _ in range(n_paths):
        idx = randrange(n)
        acc = 0.0
        for t in range(horizon):
            if t > 0:
                if rand() < p:
                    idx = randrange(n)
                else:
                    idx = idx + 1 if idx + 1 < n else 0
            acc += mu_star + scale[t] * e[idx]
            if (t + 1) in hset:
                sums[t + 1].append(acc)
    q = {}
    for h in FAN_HORIZONS:
        if h in sums:
            srt = sorted(sums[h])
            q[str(h)] = [math.exp(quantile(srt, pc)) - 1.0 for pc in FAN_PCTS]
        else:
            q[str(h)] = [None] * 5
    return {"q": q, "drift": mu_star, "driftLR": MU_LR_DAILY_LOG, "w": w, "n": n, "sigmaScale": s0}
