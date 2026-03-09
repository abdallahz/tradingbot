"""Quick test — sends a real message to your Telegram bot."""
import sys
sys.path.insert(0, "src")

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(".env"))

from tradingbot.models import TradeCard
from tradingbot.notifications.telegram_notifier import TelegramNotifier

notifier = TelegramNotifier.from_env()

# Build a fake card
card = TradeCard(
    symbol="NVDA",
    side="long",
    score=82.0,
    entry_price=875.50,
    stop_price=858.25,
    tp1_price=892.75,
    tp2_price=910.00,
    invalidation_price=850.00,
    session_tag="morning",
    reason=["volume_spike", "ema9_20_hold", "vwap_reclaim"],
    patterns=["bull_flag", "above_vwap", "breakout"],
    chart_path="outputs/charts/DEMO_20260309_0947.png",
)

print("Sending test alert to Telegram...")
ok = notifier.send_trade_alert(card)
print("✅ Message sent!" if ok else "❌ Failed — check token/chat_id in .env")
