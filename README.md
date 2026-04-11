# Auto Trading Bot

Automated stock trading bot for Alpaca paper trading with daily market scanning.

## Features

- **Daily Market Scan**: Analyzes S&P 500 stocks using technical indicators
- **Auto-Trading**: Opens positions in top 3 stocks, rotates daily
- **Risk Management**: 2:1 risk/reward (5% profit target, 2.5% stop loss)
- **Trailing Stop**: 2% trailing stop when profitable
- **Email Alerts**: Notifications on all buy/sell orders

## Quick Start

```bash
# Setup
source venv/bin/activate

# Run in test mode (default)
python3 alpaca_trading_bot.py
```

## Configuration

Edit `config.py` to enable live trading:

```python
DRY_RUN_MODE = False    # Set to False for real trades
RUN_TEST_MODE = False  # Set to False to enforce market hours
```

| Setting | Default | Description |
|---------|---------|------------|
| DRY_RUN_MODE | True | Logs orders without executing |
| RUN_TEST_MODE | True | Allows trading outside market hours |
| TOP_N_STOCKS | 3 | Number of stocks to trade |
| TAKE_PROFIT_PCT | 5% | Profit target |
| STOP_LOSS_PCT | 2.5% | Stop loss threshold |
| TRAIL_STOP_PCT | 2% | Trailing stop distance |

## Files

| File | Purpose |
|------|---------|
| `alpaca_trading_bot.py` | Main trading bot |
| `config.py` | Configuration |
| `market_scan_runner.py` | Market scanner |
| `swing_trading_analyzer.py` | Technical analysis |

## Requirements

- Python 3.9+
- yfinance, pandas, numpy
- Alpaca API key (paper trading)

## License

MIT