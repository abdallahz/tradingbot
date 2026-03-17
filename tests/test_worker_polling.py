import time
from unittest.mock import patch
from tradingbot.app import worker

def test_worker_polling_interval(monkeypatch):
    """Test that worker main loop uses the new 10s polling interval."""
    sleep_calls = []
    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # Stop after first call to avoid infinite loop
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", fake_sleep)
    # Patch _now_et and _load_schedule to avoid real time dependency
    monkeypatch.setattr(worker, "_now_et", lambda: worker.ET.localize(worker.datetime(2023, 3, 17, 8, 0)))
    monkeypatch.setattr(worker, "_load_schedule", lambda: {"premarket_scan": "08:00"})
    # Patch _HANDLERS to avoid running real jobs
    monkeypatch.setattr(worker, "_HANDLERS", {"premarket_scan": lambda: None})
    try:
        worker.main()
    except KeyboardInterrupt:
        pass
    assert 10 in sleep_calls, f"Expected polling interval of 10s, got: {sleep_calls}"
