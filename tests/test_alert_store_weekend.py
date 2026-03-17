from tradingbot.web.alert_store import save_alert, save_session
from datetime import date

def test_save_alert_skips_weekend(monkeypatch):
    calls = []
    def fake_get_supabase():
        class Dummy:
            def table(self, name):
                class Table:
                    def insert(self, row):
                        class Exec:
                            def execute(self):
                                calls.append(row)
                                return None
                        return Exec()
                return Table()
        return Dummy()
    monkeypatch.setattr("tradingbot.web.alert_store._get_supabase", fake_get_supabase)
    # Saturday
    alert = {"trade_date": "2026-03-14", "symbol": "WEEKEND", "side": "long"}
    save_alert(alert)
    # Sunday
    alert = {"trade_date": "2026-03-15", "symbol": "WEEKEND", "side": "short"}
    save_alert(alert)
    # Weekday
    alert = {"trade_date": "2026-03-17", "symbol": "WEEKDAY", "side": "long"}
    save_alert(alert)
    assert all(r["symbol"] != "WEEKEND" for r in calls), "Weekend alerts should not be saved"
    assert any(r["symbol"] == "WEEKDAY" for r in calls), "Weekday alert should be saved"

def test_save_session_skips_weekend(monkeypatch):
    calls = []
    def fake_get_supabase():
        class Dummy:
            def table(self, name):
                class Table:
                    def insert(self, row):
                        class Exec:
                            def execute(self):
                                calls.append(row)
                                return None
                        return Exec()
                return Table()
        return Dummy()
    monkeypatch.setattr("tradingbot.web.alert_store._get_supabase", fake_get_supabase)
    # Saturday
    session = {"trade_date": "2026-03-14", "session": "morning"}
    save_session(session)
    # Sunday
    session = {"trade_date": "2026-03-15", "session": "midday"}
    save_session(session)
    # Weekday
    session = {"trade_date": "2026-03-17", "session": "morning"}
    save_session(session)
    assert all(r["session"] != "morning" or r.get("trade_date") != "2026-03-14" for r in calls), "Weekend sessions should not be saved"
    assert any(r.get("trade_date") == "2026-03-17" for r in calls), "Weekday session should be saved"
