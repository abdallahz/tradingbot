"""ETF metadata for conflict detection, deduplication, and position sizing.

This module provides:
  - A set of known ETF symbols
  - A map of leveraged ETFs → leverage factor
  - A map of ETF families (same underlying index)
  - Helper functions for conflict detection and classification
"""
from __future__ import annotations

# ── Known ETF symbols (broad set encountered in screener results) ─────
# This isn't exhaustive but covers the most-traded ETFs the bot sees.
KNOWN_ETFS: set[str] = {
    # Broad market
    "SPY", "VOO", "IVV",           # S&P 500
    "QQQ", "QQQM",                 # Nasdaq-100
    "DIA",                          # Dow Jones
    "IWM", "IWO", "IWN",           # Russell 2000
    "VTI", "ITOT",                  # Total US Market
    # Leveraged / Inverse – broad
    "TQQQ", "SQQQ",                # 3x Nasdaq
    "QLD", "QID",                   # 2x Nasdaq
    "PSQ",                          # -1x Nasdaq
    "SPXL", "SPXS",                # 3x S&P 500
    "SSO", "SDS",                   # 2x S&P 500
    "UPRO", "SPXU",                # 3x S&P 500 (alt)
    "SH", "SPDN",                   # -1x S&P 500
    "TNA", "TZA",                   # 3x Russell 2000
    "UWM", "TWM",                   # 2x Russell 2000
    "RWM", "SRTY",                  # -1x / -3x Russell 2000
    "UDOW", "SDOW",                # 3x Dow Jones
    "DOG",                          # -1x Dow Jones
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLC", "XLB", "XLRE",
    "SMH", "SOXX",                  # Semiconductors
    "SOXL", "SOXS",                # 3x Semiconductors
    "XBI", "IBB", "LABU", "LABD",  # Biotech
    "HDGE",                         # Active bear fund
    "GDX", "GDXJ", "NUGT", "DUST", # Gold miners
    "XOP", "OIH",                   # Oil & gas
    "KRE", "DPST",                  # Regional banks
    "ARKK", "ARKW", "ARKG",        # ARK Innovation
    # iShares sector ETFs
    "IYM", "IYW", "IYH", "IYF", "IYJ", "IYE", "IYC", "IYZ", "IYK", "IYG", "IYR",
    # Other sector / thematic ETFs
    "VNQ", "HACK", "TAN", "LIT", "JETS", "BUZZ", "MSOS", "YOLO", "WEED",
    "DFEN", "DUSL", "FAS", "FAZ", "ERX", "ERY", "CURE", "NAIL", "DRN", "DRV",
    # Commodities / rates
    "GLD", "SLV", "IAU",           # Gold, Silver
    "USO", "UCO", "SCO",           # Oil
    "TLT", "TMF", "TMV",           # Treasuries
    "UNG",                          # Natural Gas
    # Volatility
    "VXX", "UVXY", "UVIX", "SVXY",  # VIX
    # International
    "EEM", "FXI", "KWEB", "EWZ",   # Emerging markets, China, Brazil
    # Single-stock ETFs (leveraged / inverse bets on individual names)
    "NVD", "NVDL", "NVDS", "NVDD",  # NVIDIA single-stock
    "TSDD", "TSLL", "TSLQ",         # Tesla single-stock
    "AAPD", "AAPU",                  # Apple single-stock
    "MSFD", "MSFU",                  # Microsoft single-stock
    "AMZD", "AMZU",                  # Amazon single-stock
    "METD", "METU",                  # Meta single-stock
    "GOOGD", "GOOU",                  # Google single-stock
    "CONL", "CONY",                  # Coinbase single-stock
    # Crypto / Digital assets ETFs
    "BITO", "BITX", "BITI",         # Bitcoin Strategy
    "ETHE", "ETHU",                  # Ethereum
    "GBTC", "IBIT", "FBTC",         # Bitcoin spot
    # Bonds / Fixed income ETFs
    "HYG", "JNK", "LQD", "BND",     # Corporate / high yield bonds
    "AGG", "VCIT", "VCSH",          # Aggregate / investment grade
    "SHY", "IEF",                    # Treasury short / intermediate
}

# ── Leveraged ETF → leverage factor ──────────────────────────────────
# Positive = bull, negative = inverse (bear).
# The absolute value is used for position sizing adjustment.
LEVERAGED_ETFS: dict[str, int] = {
    # Nasdaq-100
    "TQQQ": 3, "SQQQ": -3, "QLD": 2, "QID": -2, "PSQ": -1,
    # S&P 500
    "SPXL": 3, "SPXS": -3, "UPRO": 3, "SPXU": -3,
    "SSO": 2, "SDS": -2, "SH": -1, "SPDN": -1,
    # Dow Jones
    "UDOW": 3, "SDOW": -3, "DOG": -1,
    # Russell 2000
    "TNA": 3, "TZA": -3, "UWM": 2, "TWM": -2, "RWM": -1, "SRTY": -3,
    # Semiconductors
    "SOXL": 3, "SOXS": -3,
    # Biotech
    "LABU": 3, "LABD": -3,
    # Gold miners
    "NUGT": 2, "DUST": -2,
    # Oil
    "UCO": 2, "SCO": -2,
    # Treasuries
    "TMF": 3, "TMV": -3,
    # Volatility
    "UVXY": 2, "UVIX": 2, "SVXY": -1,  # SVXY is -0.5x technically, we just use -1
    # Bear / hedge funds
    "HDGE": -1,
    # Single-stock ETFs — NVIDIA
    "NVD": -2, "NVDL": 2, "NVDS": -2, "NVDD": -1,
    # Single-stock ETFs — Tesla
    "TSDD": -2, "TSLL": 2, "TSLQ": -2,
    # Single-stock ETFs — Apple
    "AAPD": -2, "AAPU": 2,
    # Single-stock ETFs — Microsoft
    "MSFD": -2, "MSFU": 2,
    # Single-stock ETFs — Amazon
    "AMZD": -2, "AMZU": 2,
    # Single-stock ETFs — Meta
    "METD": -2, "METU": 2,
    # Single-stock ETFs — Coinbase
    "CONL": 2, "CONY": 2,
    # Crypto
    "BITX": 2, "BITI": -1,
    # Ethereum
    "ETHU": 2,
}


# ── ETF Families (same underlying index/sector) ─────────────────────
# Key = family name, Value = set of tickers that track the same thing.
# Within a family, the bot should only alert ONE representative.
ETF_FAMILIES: dict[str, set[str]] = {
    "sp500":        {"SPY", "VOO", "IVV", "SPXL", "SPXS", "UPRO", "SPXU", "SSO", "SDS", "SH", "SPDN"},
    "nasdaq100":    {"QQQ", "QQQM", "TQQQ", "SQQQ", "QLD", "QID", "PSQ"},
    "dow":          {"DIA", "UDOW", "SDOW", "DOG"},
    "russell2000":  {"IWM", "IWO", "IWN", "TNA", "TZA", "UWM", "TWM", "RWM", "SRTY"},
    "semis":        {"SMH", "SOXX", "SOXL", "SOXS"},
    "biotech":      {"XBI", "IBB", "LABU", "LABD"},
    "gold_miners":  {"GDX", "GDXJ", "NUGT", "DUST"},
    "oil":          {"USO", "UCO", "SCO", "XOP", "OIH"},
    "treasuries":   {"TLT", "TMF", "TMV"},
    "gold":         {"GLD", "IAU"},
    "silver":       {"SLV"},
    "vix":          {"VXX", "UVXY", "UVIX", "SVXY"},
    "nat_gas":      {"UNG"},
    # Single-stock ETF families
    "nvidia_single":   {"NVD", "NVDL", "NVDS", "NVDD"},
    "tesla_single":    {"TSDD", "TSLL", "TSLQ"},
    "apple_single":    {"AAPD", "AAPU"},
    "msft_single":     {"MSFD", "MSFU"},
    "amzn_single":     {"AMZD", "AMZU"},
    "meta_single":     {"METD", "METU"},
    "coinbase_single": {"CONL", "CONY"},
    # Crypto / digital asset ETFs
    "bitcoin":      {"BITO", "BITX", "BITI", "GBTC", "IBIT", "FBTC"},
    "ethereum":     {"ETHE", "ETHU"},
    # Fixed income
    "high_yield":   {"HYG", "JNK"},
    "invest_grade": {"LQD", "VCIT", "VCSH"},
    "agg_bonds":    {"BND", "AGG"},
    "treasury_short": {"SHY", "IEF"},
}

# Pre-compute reverse lookup: symbol → family name
_SYMBOL_TO_FAMILY: dict[str, str] = {}
for _family, _members in ETF_FAMILIES.items():
    for _sym in _members:
        _SYMBOL_TO_FAMILY[_sym] = _family


# ── Helper functions ─────────────────────────────────────────────────

def is_etf(symbol: str) -> bool:
    """Return True if the symbol is a known ETF."""
    return symbol in KNOWN_ETFS


def get_leverage_factor(symbol: str) -> int:
    """Return the leverage factor for a symbol.

    Positive = bull, negative = inverse.
    Returns 1 for non-leveraged ETFs and individual stocks.
    """
    return LEVERAGED_ETFS.get(symbol, 1)


def is_leveraged_etf(symbol: str) -> bool:
    """Return True if the symbol is a leveraged ETF (bull or inverse).

    Leveraged bull ETFs (TQQQ 3×, SOXL 3×, etc.) inflate gap_pct by their
    leverage factor — a 1% move in the underlying becomes 3% in the ETF,
    gaming the ranker's gap score.  Going long on these in a momentum-gap
    strategy adds layer risk without real edge.
    """
    lev = LEVERAGED_ETFS.get(symbol, 1)
    return abs(lev) > 1


def get_etf_family(symbol: str) -> str | None:
    """Return the family name for an ETF, or None if not in any family."""
    return _SYMBOL_TO_FAMILY.get(symbol)


def are_conflicting(symbol_a: str, symbol_b: str) -> bool:
    """Return True if two symbols are in the same ETF family (duplicative bets).

    This covers both inverse conflicts (SOXL + SOXS) and overlap
    (QQQ + TQQQ = same directional bet doubled).
    """
    fam_a = get_etf_family(symbol_a)
    fam_b = get_etf_family(symbol_b)
    if fam_a is None or fam_b is None:
        return False
    return fam_a == fam_b
