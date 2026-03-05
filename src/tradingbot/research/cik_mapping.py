"""
Symbol to SEC CIK Code Mapping

Maps stock symbols to their SEC Central Index Key (CIK) codes.
CIK is required to query SEC EDGAR API.

Reference: https://www.sec.gov/cgi-bin/browse-edgar
"""

# Static mapping of major symbols
# Expand this as needed - values are zero-padded CIK codes
SYMBOL_TO_CIK = {
    # Tech mega-cap
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "GOOG": "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
    "TSLA": "0001318605",
    "NVDA": "0001045810",
    "AMD": "0000002488",
    "INTEL": "0000050104",
    "CRM": "0001108772",
    "ADBE": "0000796343",
    "NFLX": "0001065280",
    "PYPL": "0001633917",
    "SQ": "0001512673",
    
    # Finance
    "JPM": "0000047867",
    "BAC": "0000070858",
    "WFC": "0000072971",
    "GS": "0000886982",
    "MS": "0000895421",
    "BLK": "0001110542",
    "AXP": "0000004962",
    "MA": "0001141391",
    "V": "0001403161",
    
    # Healthcare/Pharma
    "JNJ": "0000200406",
    "UNH": "0000731766",
    "PFE": "0000078003",
    "ABBV": "0001551152",
    "MRK": "0000310158",
    "TMO": "0001044396",
    "LLY": "0000059478",
    "AMGN": "0000318154",
    "AZN": "0000895663",
    "GILD": "0000882095",
    
    # Consumer/Retail
    "TSLA": "0001318605",
    "WMT": "0000104169",
    "TGT": "0000027419",
    "COST": "0000909832",
    "NKE": "0000069646",
    "SBUX": "0000829224",
    "MCD": "0000063908",
    
    # Energy
    "XOM": "0000034088",
    "CVX": "0000093410",
    "COP": "0000034687",
    
    # Industrial
    "BA": "0000012927",
    "GE": "0000040545",
    "CAT": "0000018230",
    "HON": "0000354693",
    
    # Communication
    "VZ": "0000732712",
    "T": "0000732733",
    "CMCSA": "0001116132",
    
    # Utilities
    "NEE": "0000753308",
    "DUK": "0000083246",
    
    # Real Estate
    "PSLG": "0001393312",
    
    # Semiconductors
    "QCOM": "0000804842",
    "ASML": "0000914273",
    "TSM": "0001046342",
    "AVGO": "0001591954",
    "MRVL": "0000895898",
    
    # Cloud/SaaS
    "SNOW": "0001640147",
    "SHOP": "0001616707",
    "ZOOM": "0001616707",
    "OKTA": "0001667114",
    "WDAY": "0001616707",
}


def get_cik(symbol: str) -> str | None:
    """
    Get CIK code for a stock symbol.
    
    Args:
        symbol: Stock ticker (e.g., "AAPL")
        
    Returns:
        CIK code (zero-padded) or None if not found
    """
    clean_symbol = symbol.upper().strip()
    return SYMBOL_TO_CIK.get(clean_symbol)


def is_cik_available(symbol: str) -> bool:
    """Check if CIK mapping exists for a symbol."""
    return get_cik(symbol) is not None
