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
    "SPXL", "SPXS",                # 3x S&P 500
    "SSO", "SDS",                   # 2x S&P 500
    "UPRO", "SPXU",                # 3x S&P 500 (alt)
    "TNA", "TZA",                   # 3x Russell 2000
    "UWM", "TWM",                   # 2x Russell 2000
    "UDOW", "SDOW",                # 3x Dow Jones
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLC", "XLB", "XLRE",
    "SMH", "SOXX",                  # Semiconductors
    "SOXL", "SOXS",                # 3x Semiconductors
    "XBI", "IBB", "LABU", "LABD",  # Biotech
    "GDX", "GDXJ", "NUGT", "DUST", # Gold miners
    "XOP", "OIH",                   # Oil & gas
    "KRE", "DPST",                  # Regional banks
    "ARKK", "ARKW", "ARKG",        # ARK Innovation
    # Commodities / rates
    "GLD", "SLV", "IAU",           # Gold, Silver
    "USO", "UCO", "SCO",           # Oil
    "TLT", "TMF", "TMV",           # Treasuries
    "UNG",                          # Natural Gas
    # Volatility
    "VXX", "UVXY", "SVXY",         # VIX
    # International
    "EEM", "FXI", "KWEB", "EWZ",   # Emerging markets, China, Brazil
}

# ── Leveraged ETF → leverage factor ──────────────────────────────────
# Positive = bull, negative = inverse (bear).
# The absolute value is used for position sizing adjustment.
LEVERAGED_ETFS: dict[str, int] = {
    # Nasdaq-100
    "TQQQ": 3, "SQQQ": -3, "QLD": 2, "QID": -2,
    # S&P 500
    "SPXL": 3, "SPXS": -3, "UPRO": 3, "SPXU": -3,
    "SSO": 2, "SDS": -2,
    # Dow Jones
    "UDOW": 3, "SDOW": -3,
    # Russell 2000
    "TNA": 3, "TZA": -3, "UWM": 2, "TWM": -2,
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
    "UVXY": 2, "SVXY": -1,  # SVXY is -0.5x technically, we just use -1
}


# ── ETF Families (same underlying index/sector) ─────────────────────
# Key = family name, Value = set of tickers that track the same thing.
# Within a family, the bot should only alert ONE representative.
ETF_FAMILIES: dict[str, set[str]] = {
    "sp500":        {"SPY", "VOO", "IVV", "SPXL", "SPXS", "UPRO", "SPXU", "SSO", "SDS"},
    "nasdaq100":    {"QQQ", "QQQM", "TQQQ", "SQQQ", "QLD", "QID"},
    "dow":          {"DIA", "UDOW", "SDOW"},
    "russell2000":  {"IWM", "IWO", "IWN", "TNA", "TZA", "UWM", "TWM"},
    "semis":        {"SMH", "SOXX", "SOXL", "SOXS"},
    "biotech":      {"XBI", "IBB", "LABU", "LABD"},
    "gold_miners":  {"GDX", "GDXJ", "NUGT", "DUST"},
    "oil":          {"USO", "UCO", "SCO", "XOP", "OIH"},
    "treasuries":   {"TLT", "TMF", "TMV"},
    "gold":         {"GLD", "IAU"},
    "silver":       {"SLV"},
    "vix":          {"VXX", "UVXY", "SVXY"},
    "nat_gas":      {"UNG"},
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
