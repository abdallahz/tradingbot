import os
from pathlib import Path

from tradingbot.app.session_runner import SessionRunner


def test_session_runner_mock_mode():
    """Test session runner works in mock mode."""
    root = Path.cwd()
    runner = SessionRunner(root, use_real_data=False)
    
    morning, midday = runner.run_day()
    
    assert morning.run_type == "morning"
    assert midday.run_type == "midday"
    assert len(morning.cards) >= 0
    assert len(midday.cards) >= 0


def test_session_runner_real_mode_initialization():
    """Test session runner can initialize in real data mode (even with dummy credentials)."""
    root = Path.cwd()
    
    # This will fail to fetch real data but should initialize without crashing
    runner = SessionRunner(root, use_real_data=True)
    
    # Verify real data components are initialized
    assert runner.alpaca_client is not None
    assert runner.catalyst_scorer is not None
    assert runner.use_real_data is True
