from tradingbot.config import ConfigLoader
from tradingbot.app.session_runner import SessionRunner
from pathlib import Path

def test_o2_relaxed_configurable():
    root = Path.cwd()
    config = ConfigLoader(root)
    scanner_config = config.scanner()
    o2_cfg = scanner_config.get("o2_relaxed", {})
    runner = SessionRunner(root, use_real_data=False)
    # Check that the relaxed_scanner uses config values
    relaxed = runner.relaxed_scanner
    assert relaxed.price_min == o2_cfg.get("price_min", scanner_config["scanner"]["price_min"])
    assert relaxed.price_max == o2_cfg.get("price_max", scanner_config["scanner"]["price_max"])
    assert relaxed.min_gap_pct == o2_cfg.get("min_gap_pct", 0.0)
    assert relaxed.min_premarket_volume == o2_cfg.get("min_premarket_volume", 0)
    assert relaxed.min_dollar_volume == o2_cfg.get("min_dollar_volume", 0)
    assert relaxed.max_spread_pct == o2_cfg.get("max_spread_pct", 5.0)
