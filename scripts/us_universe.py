#!/usr/bin/env python3
"""
The US board's universe. Constants only, no I/O, importable from anywhere.

40 stocks, 16 ETFs, 2 benchmarks, 4 indices = 62 Yahoo symbols. Yahoo symbol == ticker
for every one of them (the four indices carry Yahoo's leading caret).

Excluded on purpose, so nobody re-adds them without reading this:
  IBIT, WGMI    INDmoney blocks them under IFSCA's crypto exclusion (Aug 2025 circular)
  FNGU          an ETN (BMO unsecured credit), recycled ticker, history only from Feb 2025
  BITQ          ADV about 105k shares, too thin
  SOXX, BOTZ    duplicates of SMH and AIQ
  LLY, MSFT, AAPL   low beta anchors, not this board's job
"""

BENCH = ("SPY", "QQQ")                        # benchmarks and leverage proxies, never recommended
INDEX = ("^VIX", "^VIX3M", "^VIX9D", "^IRX")  # Yahoo symbols

# sym, name, kind, lev, proxy, er (net, fraction), flags
ETFS = (
    ("VOO",  "Vanguard S&P 500",               "broad",  1, None,  0.0003, ()),
    ("QQQM", "Invesco Nasdaq 100",             "broad",  1, None,  0.0015, ()),
    ("IWM",  "iShares Russell 2000",           "broad",  1, None,  0.0019, ()),
    ("SMH",  "VanEck Semiconductor",           "sector", 1, None,  0.0035, ()),
    ("XBI",  "SPDR S&P Biotech",               "sector", 1, None,  0.0035, ()),
    ("ARKK", "ARK Innovation",                 "theme",  1, None,  0.0075, ()),
    ("URA",  "Global X Uranium",               "theme",  1, None,  0.0069, ()),
    ("ICLN", "iShares Global Clean Energy",    "theme",  1, None,  0.0039, ()),
    ("TAN",  "Invesco Solar",                  "theme",  1, None,  0.0070, ()),
    ("AIQ",  "Global X AI & Technology",       "theme",  1, None,  0.0068, ()),
    ("QTUM", "Defiance Quantum",               "theme",  1, None,  0.0040, ("thin",)),
    ("KWEB", "KraneShares China Internet",     "theme",  1, None,  0.0069, ()),
    ("GDXJ", "VanEck Junior Gold Miners",      "theme",  1, None,  0.0052, ()),
    ("TQQQ", "ProShares UltraPro QQQ",         "lev",    3, "QQQ", 0.0082, ("leveraged",)),
    ("SOXL", "Direxion Semiconductor Bull 3X", "lev",    3, "SMH", 0.0075, ("leveraged",)),
    ("UPRO", "ProShares UltraPro S&P 500",     "lev",    3, "SPY", 0.0089, ("leveraged",)),
)

# sym, name, sector, flags
# "cryptoAdjacent": INDmoney may withdraw these without notice, confirm in the app before buying.
STOCKS = (
    ("NVDA",  "Nvidia",                     "semis", ()),
    ("AMD",   "Advanced Micro Devices",     "semis", ()),
    ("AVGO",  "Broadcom",                   "semis", ()),
    ("MU",    "Micron Technology",          "semis", ()),
    ("TSM",   "Taiwan Semiconductor",       "semis", ()),
    ("ARM",   "Arm Holdings",               "semis", ()),
    ("MRVL",  "Marvell Technology",         "semis", ()),
    ("SMCI",  "Super Micro Computer",       "semis", ()),
    ("TSLA",  "Tesla",                      "growth/fintech", ()),
    ("PLTR",  "Palantir",                   "growth/fintech", ()),
    ("HOOD",  "Robinhood",                  "growth/fintech", ()),
    ("SOFI",  "SoFi Technologies",          "growth/fintech", ()),
    ("AFRM",  "Affirm",                     "growth/fintech", ()),
    ("UPST",  "Upstart",                    "growth/fintech", ()),
    ("COIN",  "Coinbase",                   "crypto adjacent", ("cryptoAdjacent",)),
    ("MSTR",  "Strategy (MicroStrategy)",   "crypto adjacent", ("cryptoAdjacent",)),
    ("MARA",  "MARA Holdings",              "crypto adjacent", ("cryptoAdjacent",)),
    ("RIOT",  "Riot Platforms",             "crypto adjacent", ("cryptoAdjacent",)),
    ("CLSK",  "CleanSpark",                 "crypto adjacent", ("cryptoAdjacent",)),
    ("RKLB",  "Rocket Lab",                 "space/quantum/nuclear/power", ()),
    ("ASTS",  "AST SpaceMobile",            "space/quantum/nuclear/power", ()),
    ("IONQ",  "IonQ",                       "space/quantum/nuclear/power", ()),
    ("RGTI",  "Rigetti Computing",          "space/quantum/nuclear/power", ()),
    ("QBTS",  "D-Wave Quantum",             "space/quantum/nuclear/power", ()),
    ("OKLO",  "Oklo",                       "space/quantum/nuclear/power", ()),
    ("VST",   "Vistra",                     "space/quantum/nuclear/power", ()),
    ("CEG",   "Constellation Energy",       "space/quantum/nuclear/power", ()),
    ("CRWD",  "CrowdStrike",                "software/AI/health", ()),
    ("NET",   "Cloudflare",                 "software/AI/health", ()),
    ("APP",   "AppLovin",                   "software/AI/health", ()),
    ("SNOW",  "Snowflake",                  "software/AI/health", ()),
    ("DDOG",  "Datadog",                    "software/AI/health", ()),
    ("SHOP",  "Shopify",                    "software/AI/health", ()),
    ("META",  "Meta Platforms",             "software/AI/health", ()),
    ("AMZN",  "Amazon",                     "software/AI/health", ()),
    ("GOOGL", "Alphabet",                   "software/AI/health", ()),
    ("NBIS",  "Nebius Group",               "software/AI/health", ()),
    ("CRWV",  "CoreWeave",                  "software/AI/health", ()),
    ("HIMS",  "Hims & Hers Health",         "software/AI/health", ()),
    ("TEM",   "Tempus AI",                  "software/AI/health", ()),
)

EXCLUDED = {
    "IBIT": "INDmoney blocks it under IFSCA's crypto exclusion (Aug 2025 circular)",
    "WGMI": "INDmoney blocks it under IFSCA's crypto exclusion (Aug 2025 circular)",
    "FNGU": "ETN (BMO unsecured credit), recycled ticker, history only from Feb 2025",
    "BITQ": "ADV about 105k shares, too thin",
    "SOXX": "duplicate of SMH",
    "BOTZ": "duplicate of AIQ",
    "LLY":  "low beta anchor, not this board's job",
    "MSFT": "low beta anchor, not this board's job",
    "AAPL": "low beta anchor, not this board's job",
}

_ETF_KEYS = ("sym", "name", "kind", "lev", "proxy", "er", "flags")
_STOCK_KEYS = ("sym", "name", "sector", "flags")

_ETF_BY_SYM = {row[0]: dict(zip(_ETF_KEYS, row)) for row in ETFS}
_STOCK_BY_SYM = {row[0]: dict(zip(_STOCK_KEYS, row)) for row in STOCKS}


def etf_symbols():
    return tuple(row[0] for row in ETFS)


def stock_symbols():
    return tuple(row[0] for row in STOCKS)


def all_symbols():
    """All 62 Yahoo symbols in a stable order: benchmarks, indices, ETFs, stocks."""
    return tuple(BENCH) + tuple(INDEX) + etf_symbols() + stock_symbols()


def etf_meta(sym):
    """Dict with sym, name, kind, lev, proxy, er, flags (flags as a list). None if not an ETF."""
    m = _ETF_BY_SYM.get(sym)
    if m is None:
        return None
    m = dict(m)
    m["flags"] = list(m["flags"])
    return m


def stock_meta(sym):
    """Dict with sym, name, sector, flags (flags as a list). None if not a stock."""
    m = _STOCK_BY_SYM.get(sym)
    if m is None:
        return None
    m = dict(m)
    m["flags"] = list(m["flags"])
    return m


def is_leveraged(sym):
    m = _ETF_BY_SYM.get(sym)
    return bool(m) and m["lev"] > 1


def proxy_of(sym):
    """The symbol every signal for a leveraged ETF is computed on. The symbol itself otherwise."""
    m = _ETF_BY_SYM.get(sym)
    if m and m["proxy"]:
        return m["proxy"]
    return sym


def sleeve_of(sym):
    """'etf', 'stock', 'bench' or 'index'. None if the symbol is not in the universe."""
    if sym in _ETF_BY_SYM:
        return "etf"
    if sym in _STOCK_BY_SYM:
        return "stock"
    if sym in BENCH:
        return "bench"
    if sym in INDEX:
        return "index"
    return None


if __name__ == "__main__":
    syms = all_symbols()
    assert len(syms) == 62, len(syms)
    assert len(set(syms)) == 62, "duplicate symbol"
    assert len(ETFS) == 16 and len(STOCKS) == 40
    assert sum(1 for s in etf_symbols() if is_leveraged(s)) == 3
    assert proxy_of("TQQQ") == "QQQ" and proxy_of("NVDA") == "NVDA"
    for s in syms:
        assert s not in EXCLUDED, s
    print("us_universe: %d symbols (%d etf, %d stock, %d bench, %d index), ok"
          % (len(syms), len(ETFS), len(STOCKS), len(BENCH), len(INDEX)))
