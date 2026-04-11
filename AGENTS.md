# Autotrading Repository

## Key Scripts

| Script | Purpose |
|--------|---------|
| `alpaca_trading_bot.py` | Main automated trading bot (Alpaca paper trading) |
| `market_scan_runner.py` | Daily market scan report generator |
| `swing_trading_analyzer.py` | Stock analysis with technical indicators |
| `config.py` | Trading configuration (API keys, strategy params) |

## Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Run trading bot
python3 alpaca_trading_bot.py

# Run market scan report
python3 market_scan_runner.py [--email]

# Run stock analyzer interactively
python3 swing_trading_analyzer.py
```

## Critical Configuration

Edit `config.py` to enable live trading:

```python
DRY_RUN_MODE = False    # Enable real trades (default: True)
RUN_TEST_MODE = False  # Disable test mode (default: True)
```

- `DRY_RUN_MODE = True`: Logs orders but doesn't execute (safe for testing)
- `RUN_TEST_MODE = True`: Ignores market hours (for testing outside market hours)

## Dependencies

In `venv/`, installed from `requirements.txt`:
- yfinance, pandas, numpy, matplotlib
- python-dotenv (for .env loading)

## API Keys

 Loaded from `.env` file:
- Alpaca: `ALPACA_KEY`, `ALPACA_SECRET`, `ALPACA_ENDPOINT`
- Email: `SMTP_USER`, `SMTP_PASSWORD`

## Strategy (alpaca_trading_bot.py)

- Top 3 stocks from market scan daily rotation
- 33.3% allocation per stock
- 5% take profit, 2.5% stop loss (2:1 R:R)
- 2% trailing stop when profitable
- Email notifications on all trades