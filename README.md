# Auto Trading Bot

Automated stock trading bot for Alpaca paper/live trading with S&P 400 screening.

## Features

- **S&P 400 Screening**: Analyzes S&P 400 stocks using backtest signals
- **Scoring**: Ranks by avg_trade (2pts) + win% (1pt) + prof_factor (1pt)
- **Daily Rotation**: Opens positions in top 3 stocks, rotates daily
- **Risk Management**: 2:1 risk/reward (5% profit target, 2.5% stop loss)
- **ATR Stops**: Uses ATR-based stop loss (1 ATR) and take profit (2x ATR)
- **Trailing Stop**: 2% trailing stop when 5% profitable
- **Max Hold**: 14 days max per position
- **Earnings Protection**: Avoids positions 14 days before earnings
- **Re-entry**: Can re-enter after 1% recovery buffer
- **News Filter**: Filters stocks with recent negative news
- **Email Alerts**: Notifications on all buy/sell orders

## Quick Start

```bash
# Setup
source venv/bin/activate
cp env_template .env
# Edit .env with your API keys

# Run in test mode (default)
python3 alpaca_trading_bot.py
```

## Configuration

Edit `config.py` or use environment variables in `.env`:

```python
DRY_RUN_MODE = False    # Set to False for real trades
RUN_TEST_MODE = False  # Set to False to enforce market hours
```

| Setting | Default | Description |
|---------|---------|------------|
| DRY_RUN_MODE | False | Logs orders without executing |
| RUN_TEST_MODE | False | Allows trading outside market hours |
| TOP_N_STOCKS | 3 | Number of stocks to trade |
| TAKE_PROFIT_PCT | 5% | Profit target |
| STOP_LOSS_PCT | 2.5% | Stop loss threshold |
| TRAIL_STOP_PCT | 2% | Trailing stop distance |
| TRAIL_ACTIVATION_PCT | 5% | Activate trailing at 5% profit |
| MAX_HOLDING_DAYS | 14 | Max days to hold position |
| REENTRY_ENABLED | True | Enable re-entry after stop out |

## Files

| File | Purpose |
|------|---------|
| `alpaca_trading_bot.py` | Main trading bot |
| `config.py` | Configuration |
| `screener.py` | S&P 400 stock screener |
| `swing_trading_analyzer.py` | Technical analysis |
| `gemini_analyzer.py` | AI news analysis (optional) |
| `market_scan_runner.py` | Standalone market scanner |
| `alpaca-trading.service` | systemd service file |
| `alpaca-trading.logrotate` | logrotate config |

## Requirements

- Python 3.9+
- yfinance, pandas, numpy, matplotlib
- python-dotenv
- Alpaca API key (paper or live trading)
- Gmail SMTP for email alerts

## License

MIT