"""
Trading Configuration
"""
import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_ENDPOINT = os.environ.get("ALPACA_ENDPOINT", "https://api.alpaca.markets/v2")
ALPACA_KEY = os.environ.get("ALPACA_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_RECIPIENTS = [e.strip() for e in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if e.strip()]

TOP_N_STOCKS = 3
ALLOCATION_PERCENT = 1.0

TAKE_PROFIT_PCT = 0.05
STOP_LOSS_PCT = 0.025
TRAIL_STOP_PCT = 0.02
TRAIL_ACTIVATION_PCT = 0.05

RISK_REWARD_RATIO = TAKE_PROFIT_PCT / STOP_LOSS_PCT

REENTRY_ENABLED = True
REENTRY_BUFFER_PCT = 0.01  # 1% buffer above entry to confirm recovery

CHECK_INTERVAL_SECONDS = 300
MARKET_OPEN_HOUR = 10
MARKET_OPEN_MINUTE = 0
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

DRY_RUN_MODE = False
RUN_TEST_MODE = False

# News Filter Settings
ENABLE_NEWS_FILTER = True
NEWS_DAYS_LOOKBACK = 7
NEWS_LIMIT_PER_STOCK = 10

LOG_FILE = "alpaca_trading.log"

MARKET_TZ = "America/New_York"
