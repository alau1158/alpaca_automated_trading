# Strategy Refinement Recommendations
*Date: 2026-04-13*

The following improvements were suggested to enhance the robustness and profitability of the automated trading bot:

### 1. Sector Diversification
**Issue:** The bot may pick multiple stocks from the same industry (e.g., all Semiconductors), leading to high correlation risk.
**Solution:** Implement a check in the `MarketScanReader` to ensure that the top N stocks belong to different sectors.

### 2. ATR-Based Dynamic Stops
**Issue:** A fixed 2.5% stop loss might be too tight for volatile stocks (getting shaken out early) or too loose for stable ones.
**Solution:** Use the **Average True Range (ATR)** indicator to set stops (e.g., 2.0x ATR). This adapts the risk to each stock's specific personality.

### 3. Conditional Position Rotation
**Issue:** Hard daily rotation forces sales of "runners" that might still have technical momentum, increasing slippage and taxes.
**Solution:** Only rotate positions if the technical signal has weakened or the News Filter flags an upcoming event, allowing winners to ride longer.

### 4. Backtesting R:R Ratios
**Issue:** 5% Take Profit / 2.5% Stop Loss is a standard "starting point" but may not be optimal for the current market regime.
**Solution:** Run historical simulations to find the "sweet spot" ratio for the S&P 500 tickers the bot currently scans.

### 5. Market Regime Filter
**Issue:** The bot is currently long-only. In a severe bear market, technical "buys" often fail quickly.
**Solution:** Add a global market filter (e.g., "Is SPY above its 200-day EMA?") to pause all new buying during downtrends.
