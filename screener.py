import os
import sys
import smtplib
import logging
import sqlite3
import warnings
import argparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Optional

import dotenv
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from tabulate import tabulate

warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CACHE_DB = 'screener_cache.db'
DEFAULT_DAYS_TO_GET = 400   # Extra history needed for backtesting
STAGE1_MONTHS = 3
STAGE2_DAYS = 30
STAGE3_LOOKBACK = 20        # 20-day avg volume window
VOLUME_SURGE_MULT = 1.0     # 100% of avg = at least equal to avg
RANGE_TOLERANCE = 0.30      # 30% max price range over 30 days
BACKTEST_HOLD_DAYS = 10     # 2-week swing (trading days)
BACKTEST_STOP_LOSS = 0.07   # 7% stop loss
BACKTEST_LOOKBACK_DAYS = 252  # ~1 year of backtest signals


# ─────────────────────────────────────────
# ENV / DB
# ─────────────────────────────────────────

def load_env():
    dotenv.load_dotenv()
    return {
        'smtp_server': os.getenv('SMTP_SERVER'),
        'smtp_port': int(os.getenv('SMTP_PORT', 587)),
        'email_user': os.getenv('EMAIL_USER'),
        'email_password': os.getenv('EMAIL_PASSWORD'),
        'recipients': os.getenv('RECIPIENT_EMAILS', '').replace('\n', ',').split(',')
    }

def init_db():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stock_cache (
            ticker TEXT PRIMARY KEY,
            data_json TEXT,
            updated_at TEXT
        )
    ''')
    conn.commit()
    return conn


# ─────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────

def get_sp500_tickers() -> list:
    return _get_wiki_tickers('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', 'S&P 500')

def get_sp400_tickers() -> list:
    return _get_wiki_tickers('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies', 'S&P 400')

def _get_wiki_tickers(url: str, name: str) -> list:
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        tickers = tables[0]['Symbol'].tolist()
        tickers = [t.replace('.', '-') for t in tickers if pd.notna(t)]
        logger.info(f'Fetched {len(tickers)} {name} tickers')
        return tickers
    except Exception as e:
        logger.error(f'Failed to fetch {name} tickers: {e}')
        sys.exit(1)

def get_stock_data(ticker: str, conn: sqlite3.Connection, retry_count: int = 0) -> Optional[dict]:
    max_retries = 2
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=f'{DEFAULT_DAYS_TO_GET}d', auto_adjust=False)
        if hist.empty:
            return None
        info = stock.info
        try:
            calendar = stock.calendar
        except Exception:
            calendar = None
        return {'history': hist, 'info': info, 'calendar': calendar}
    except Exception as e:
        if 'Too Many Requests' in str(e) and retry_count < max_retries:
            import time
            time.sleep(2 ** retry_count)
            return get_stock_data(ticker, conn, retry_count + 1)
        logger.debug(f'Failed to get data for {ticker}: {e}')
        return None

def get_earnings_date(calendar) -> str:
    if not calendar:
        return 'N/A'
    dates = calendar.get('Earnings Date', [])
    return str(dates[0]) if dates else 'N/A'

def get_ex_dividend(calendar) -> str:
    if not calendar:
        return 'N/A'
    ex_div = calendar.get('Ex-Dividend Date', None)
    return str(ex_div) if ex_div else 'N/A'


# ─────────────────────────────────────────
# INDICATOR CALCULATIONS
# ─────────────────────────────────────────

def calculate_sma(prices: pd.Series, period: int) -> float:
    if len(prices) < period:
        return np.nan
    return prices.iloc[-period:].mean()

def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    """
    FIX: Original used simple average for RSI — not the standard Wilder smoothing.
    Standard RSI uses Wilder's smoothed moving average (EMA with alpha=1/period).
    This fix gives results consistent with TradingView, Thinkorswim, etc.
    """
    if len(prices) < period + 1:
        return np.nan
    delta = prices.diff().dropna()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    # Wilder smoothing (equivalent to EMA with alpha = 1/period)
    avg_gain = gains.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    avg_loss = losses.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return np.nan
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return round(tr.iloc[-period:].mean(), 2)

def calculate_bollinger_bands(prices: pd.Series, period: int = 20) -> dict:
    if len(prices) < period:
        return {'upper': np.nan, 'middle': np.nan, 'lower': np.nan}
    sma = prices.iloc[-period:].mean()
    std = prices.iloc[-period:].std()
    return {
        'upper': round(sma + 2 * std, 2),
        'middle': round(sma, 2),
        'lower': round(sma - 2 * std, 2)
    }

def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(prices) < slow + signal:
        return {'macd': np.nan, 'signal': np.nan, 'histogram': np.nan}
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return {
        'macd': round(macd_line.iloc[-1], 4),
        'signal': round(signal_line.iloc[-1], 4),
        'histogram': round((macd_line - signal_line).iloc[-1], 4)
    }

def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                          k_period: int = 14, d_period: int = 3) -> dict:
    if len(close) < k_period:
        return {'k': np.nan, 'd': np.nan}
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    denom = highest_high - lowest_low
    k = 100 * ((close - lowest_low) / denom.replace(0, np.nan))
    d = k.rolling(window=d_period).mean()
    return {'k': round(k.iloc[-1], 2), 'd': round(d.iloc[-1], 2)}


# ─────────────────────────────────────────
# SIGNAL SCORING
# ─────────────────────────────────────────

def calculate_buy_signal(rsi: float, macd: float, macd_signal: float,
                          stoch_k: float, price: float,
                          bb_upper: float, bb_lower: float) -> tuple[str, int]:
    """
    Returns (signal_label, score).

    FIXES vs original:
    1. RSI thresholds were out of order — >85 check must come BEFORE >75 check,
       otherwise >85 always falls into the >75 branch and never gets -2.
    2. Stochastic >60 branch was added in original but is NOT in the stated strategy.
       Removed it — only the three stated conditions apply: >80, <20, <40.
    3. Score thresholds aligned to match stated strategy table:
       STRONG BUY ≥3, BUY ≥1, HOLD =0, SELL ≥-1, STRONG SELL <-1
       (original used ≥4 / ≥2 which made STRONG BUY nearly impossible).
    """
    score = 0
    any_nan = any(pd.isna(v) for v in [rsi, macd, macd_signal, stoch_k, bb_upper, bb_lower])
    if not any_nan:
        # RSI — for breakout stocks, overbought is expected, not penalized
        # Removing oversold check (RSI < 45) since breakout at 30-day high can't be oversold
        if rsi > 85:
            score -= 1  # Reduced from -2: extremely overbought still has some risk
        elif rsi > 70:
            score -= 0.5  # Slightly overbought is fine for momentum
        # No penalty for RSI 45-70 (neutral zone)
        # No reward for RSI < 45 (unreachable for breakouts anyway)

        # MACD
        if macd > macd_signal:
            score += 2
        elif macd < macd_signal - 0.5:
            score -= 1

        # Stochastic — momentum confirmation
        if stoch_k > 80:
            score += 1  # Strong momentum, not overbought in this context
        # Removed: stoch_k < 20 (oversold impossible at breakout)
        # Removed: stoch_k < 40 (too harsh for momentum plays)

        # Bollinger Bands — reward riding the upper band (momentum confirmation)
        if price > bb_upper:
            score += 1  # Strong breakout, riding upper band is bullish
        elif price > bb_upper * 0.95:
            score += 1  # Near upper band = good momentum
        elif price < bb_lower:
            score -= 1  # Below lower band = mean reversion, not momentum

    if score >= 3:
        return "STRONG BUY", score
    elif score >= 1:
        return "BUY", score
    elif score == 0:
        return "HOLD", score
    elif score >= -1:
        return "SELL", score
    else:
        return "STRONG SELL", score


# ─────────────────────────────────────────
# STAGE FILTERS (applied to a given date window)
# ─────────────────────────────────────────

def run_stages(hist_clean: pd.DataFrame, as_of_idx: int) -> Optional[dict]:
    """
    Run all 3 stage filters as of a specific bar index.
    Returns a dict of computed values, or None if any stage fails.
    This is used for both live screening and backtesting.
    """
    if as_of_idx < 200:
        return None

    window = hist_clean.iloc[:as_of_idx + 1]
    closes = window['Close']
    highs = window['High']
    lows = window['Low']
    volumes = window['Volume']

    current_price = closes.iloc[-1]

    # ── Stage 1: Trend Filter ──
    end_date = window.index[-1]
    start_3m = end_date - timedelta(days=int(STAGE1_MONTHS * 30))
    hist_3m = window[window.index >= start_3m]
    if len(hist_3m) < 20:
        return None

    price_3m_ago = hist_3m['Close'].iloc[0]
    return_3m = (current_price / price_3m_ago - 1) * 100
    sma_200 = closes.iloc[-200:].mean()
    above_200 = current_price > sma_200

    if return_3m < 20 or not above_200:
        return None

    # ── Stage 2: Setup Filter ──
    start_30d = end_date - timedelta(days=STAGE2_DAYS + 10)
    hist_30d = window[window.index >= start_30d].iloc[-STAGE2_DAYS:]
    if len(hist_30d) < 15:
        return None

    high_30d = hist_30d['Close'].max()
    low_30d = hist_30d['Close'].min()
    range_pct = (high_30d - low_30d) / high_30d

    if range_pct > RANGE_TOLERANCE:
        return None

    # ── Stage 3: Breakout Trigger ──
    breakout = current_price >= high_30d  # at or above 30-day high

    vol_avg = volumes.iloc[-STAGE3_LOOKBACK:].mean()
    today_vol = volumes.iloc[-1]
    volume_surge = today_vol >= (vol_avg * VOLUME_SURGE_MULT)

    if not breakout or not volume_surge:
        return None

    # ── Indicators (last 60 bars of current window) ──
    ind_close = closes.iloc[-60:]
    ind_high = highs.iloc[-60:]
    ind_low = lows.iloc[-60:]

    rsi = calculate_rsi(ind_close)
    atr = calculate_atr(ind_high, ind_low, ind_close)
    bb = calculate_bollinger_bands(ind_close)
    macd = calculate_macd(ind_close)
    stoch = calculate_stochastic(ind_high, ind_low, ind_close)

    signal, score = calculate_buy_signal(
        rsi, macd['macd'], macd['signal'],
        stoch['k'], current_price, bb['upper'], bb['lower']
    )

    return {
        'price': round(current_price, 2),
        'return_3m': round(return_3m, 2),
        'volume_surge': round(today_vol / vol_avg, 2),
        'atr': atr,
        'rsi': rsi,
        'macd': macd['histogram'],
        'stoch_k': stoch['k'],
        'bb_upper': bb['upper'],
        'signal': signal,
        'score': score,
    }


# ─────────────────────────────────────────
# BACKTEST
# ─────────────────────────────────────────

def backtest_ticker(hist_clean: pd.DataFrame, ticker: str) -> list:
    """
    Walk-forward backtest: for each trading day in the backtest window,
    run the full 3-stage filter. On a BUY/STRONG BUY signal, simulate
    entry at next-day open and exit at:
      - 10 trading days later (time stop), OR
      - 7% stop loss (whichever comes first).
    Resumes scanning the day after each trade exits, allowing multiple
    trades per ticker over the backtest window.
    Returns a list of trade dicts.
    """
    closes = hist_clean['Close']
    opens = hist_clean['Open']
    n = len(hist_clean)
    trades = []
    # Start after enough history for all indicators (200-day SMA + 60-bar indicators)
    start_i = max(220, n - BACKTEST_LOOKBACK_DAYS - BACKTEST_HOLD_DAYS - 5)
    # Don't scan the last HOLD_DAYS bars — not enough room to complete a trade
    end_i = n - BACKTEST_HOLD_DAYS - 1

    i = start_i
    while i < end_i:
        result = run_stages(hist_clean, i)
        if result is None or result['signal'] not in ('BUY', 'STRONG BUY'):
            i += 1
            continue

        # Entry: next-day open (realistic — can't buy at the signal close)
        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_date = hist_clean.index[entry_idx]
        entry_price = opens.iloc[entry_idx]
        stop_price = entry_price * (1 - BACKTEST_STOP_LOSS)

        exit_price = None
        exit_date = None
        exit_reason = None
        exit_idx = entry_idx  # track where trade ends so we resume after it

        # Simulate each day of the hold period
        for j in range(1, BACKTEST_HOLD_DAYS + 1):
            idx = entry_idx + j
            if idx >= n:
                break
            day_low = hist_clean['Low'].iloc[idx]
            day_close = closes.iloc[idx]
            exit_idx = idx

            if day_low <= stop_price:
                exit_price = stop_price
                exit_date = hist_clean.index[idx]
                exit_reason = 'stop_loss'
                break

            if j == BACKTEST_HOLD_DAYS:
                exit_price = day_close
                exit_date = hist_clean.index[idx]
                exit_reason = 'time_exit'

        if exit_price is not None:
            pnl_pct = (exit_price / entry_price - 1) * 100
            trades.append({
                'ticker': ticker,
                'signal': result['signal'],
                'entry_date': str(entry_date.date()),
                'exit_date': str(exit_date.date()),
                'entry_price': round(entry_price, 2),
                'exit_price': round(exit_price, 2),
                'pnl_pct': round(pnl_pct, 2),
                'exit_reason': exit_reason,
                'score': result['score'],
            })

        # Resume scanning the day after this trade exited
        i = exit_idx + 1

    return trades

def compute_backtest_summary(trades: list) -> dict:
    if not trades:
        return {}
    df = pd.DataFrame(trades)
    wins = df[df['pnl_pct'] > 0]
    losses = df[df['pnl_pct'] <= 0]
    win_rate = len(wins) / len(df) * 100
    avg_win = wins['pnl_pct'].mean() if len(wins) else 0
    avg_loss = losses['pnl_pct'].mean() if len(losses) else 0
    gross_profit = wins['pnl_pct'].sum()
    gross_loss = abs(losses['pnl_pct'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Simple drawdown approximation from cumulative P&L
    cumulative = df['pnl_pct'].cumsum()
    rolling_max = cumulative.cummax()
    drawdown = cumulative - rolling_max
    max_drawdown = drawdown.min()

    strong_buy_trades = df[df['signal'] == 'STRONG BUY']
    buy_trades = df[df['signal'] == 'BUY']

    return {
        'total_trades': len(df),
        'win_rate': round(win_rate, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'total_return': round(df['pnl_pct'].sum(), 2),
        'avg_return_per_trade': round(df['pnl_pct'].mean(), 2),
        'max_drawdown': round(max_drawdown, 2),
        'stop_loss_exits': int((df['exit_reason'] == 'stop_loss').sum()),
        'time_exits': int((df['exit_reason'] == 'time_exit').sum()),
        'strong_buy_win_rate': round(
            len(strong_buy_trades[strong_buy_trades['pnl_pct'] > 0]) / len(strong_buy_trades) * 100, 1
        ) if len(strong_buy_trades) else None,
        'buy_win_rate': round(
            len(buy_trades[buy_trades['pnl_pct'] > 0]) / len(buy_trades) * 100, 1
        ) if len(buy_trades) else None,
        'trades': df.sort_values('entry_date').to_dict('records'),
    }


# ─────────────────────────────────────────
# LIVE ANALYSIS
# ─────────────────────────────────────────

def analyze_stock(ticker: str, data: dict) -> Optional[dict]:
    hist = data['history']
    if hist.empty or len(hist) < 200:
        return None

    try:
        hist_clean = hist.copy()
        hist_clean.index = hist_clean.index.tz_convert(None)
    except Exception:
        hist_clean = hist.copy()

    result = run_stages(hist_clean, len(hist_clean) - 1)
    if result is None:
        return None

    # Run backtest on this ticker's history
    bt_trades = backtest_ticker(hist_clean, ticker)
    bt_summary = compute_backtest_summary(bt_trades)

    calendar = data.get('calendar')
    return {
        'ticker': ticker,
        **result,
        'earnings_date': get_earnings_date(calendar),
        'ex_dividend': get_ex_dividend(calendar),
        'backtest': bt_summary,
    }


# ─────────────────────────────────────────
# EMAIL REPORT
# ─────────────────────────────────────────

def build_html_report(results: list) -> str:
    signal_color = {
        'STRONG BUY': '#1a7f37',
        'BUY': '#2563eb',
        'HOLD': '#92400e',
        'SELL': '#b91c1c',
        'STRONG SELL': '#7f1d1d',
    }
    signal_bg = {
        'STRONG BUY': '#dcfce7',
        'BUY': '#dbeafe',
        'HOLD': '#fef3c7',
        'SELL': '#fee2e2',
        'STRONG SELL': '#fecaca',
    }

    rows = ''
    for r in results:
        bt = r.get('backtest', {})
        sig = r['signal']
        pf = bt.get('profit_factor', None)
        pf_str = f"{pf:.2f}" if pf and pf != float('inf') else ('∞' if pf == float('inf') else 'N/A')
        win_str = f"{bt.get('win_rate', 'N/A')}%" if bt else 'N/A'
        trades_str = str(bt.get('total_trades', 'N/A')) if bt else 'N/A'
        avg_ret = bt.get('avg_return_per_trade', None)
        avg_ret_str = f"{avg_ret:+.2f}%" if avg_ret is not None else 'N/A'
        dd_str = f"{bt.get('max_drawdown', 'N/A')}%" if bt else 'N/A'

        rows += f"""
        <tr>
          <td style="font-weight:600;padding:8px 12px;">{r['ticker']}</td>
          <td style="padding:8px 12px;">${r['price']}</td>
          <td style="padding:8px 12px;">{r['return_3m']}%</td>
          <td style="padding:8px 12px;">{r['volume_surge']:.1f}x</td>
          <td style="padding:8px 12px;">${r['atr']}</td>
          <td style="padding:8px 12px;">
            <span style="background:{signal_bg.get(sig,'#f3f4f6')};color:{signal_color.get(sig,'#111')};
              padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">{sig}</span>
          </td>
          <td style="padding:8px 12px;">{int(r['score']):+d}</td>
          <td style="padding:8px 12px;">{trades_str}</td>
          <td style="padding:8px 12px;">{win_str}</td>
          <td style="padding:8px 12px;">{avg_ret_str}</td>
          <td style="padding:8px 12px;">{pf_str}</td>
          <td style="padding:8px 12px;">{dd_str}</td>
          <td style="padding:8px 12px;font-size:12px;color:#6b7280;">{r['earnings_date']}</td>
          <td style="padding:8px 12px;font-size:12px;color:#6b7280;">{r['ex_dividend']}</td>
        </tr>"""

    all_trades = []
    for r in results:
        bt = r.get('backtest', {})
        all_trades.extend(bt.get('trades', []))
    agg = compute_backtest_summary(all_trades) if all_trades else {}

    agg_html = ''
    if agg:
        agg_html = f"""
        <h3 style="margin:24px 0 8px;color:#111827;">Backtest Summary (Past ~12 Months, All Signals)</h3>
        <table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;margin-bottom:24px;">
          <tr>
            <td style="padding:6px 20px 6px 0;color:#6b7280;">Total trades</td>
            <td style="padding:6px 0;font-weight:600;">{agg.get('total_trades','N/A')}</td>
            <td style="padding:6px 20px 6px 30px;color:#6b7280;">Win rate</td>
            <td style="padding:6px 0;font-weight:600;">{agg.get('win_rate','N/A')}%</td>
          </tr>
          <tr>
            <td style="padding:6px 20px 6px 0;color:#6b7280;">Avg win</td>
            <td style="padding:6px 0;font-weight:600;color:#1a7f37;">+{agg.get('avg_win','N/A')}%</td>
            <td style="padding:6px 20px 6px 30px;color:#6b7280;">Avg loss</td>
            <td style="padding:6px 0;font-weight:600;color:#b91c1c;">{agg.get('avg_loss','N/A')}%</td>
          </tr>
          <tr>
            <td style="padding:6px 20px 6px 0;color:#6b7280;">Profit factor</td>
            <td style="padding:6px 0;font-weight:600;">{agg.get('profit_factor','N/A')}</td>
            <td style="padding:6px 20px 6px 30px;color:#6b7280;">Max drawdown</td>
            <td style="padding:6px 0;font-weight:600;color:#b91c1c;">{agg.get('max_drawdown','N/A')}%</td>
          </tr>
          <tr>
            <td style="padding:6px 20px 6px 0;color:#6b7280;">Stop-loss exits</td>
            <td style="padding:6px 0;font-weight:600;">{agg.get('stop_loss_exits','N/A')}</td>
            <td style="padding:6px 20px 6px 30px;color:#6b7280;">Time exits</td>
            <td style="padding:6px 0;font-weight:600;">{agg.get('time_exits','N/A')}</td>
          </tr>
          <tr>
            <td style="padding:6px 20px 6px 0;color:#6b7280;">STRONG BUY win rate</td>
            <td style="padding:6px 0;font-weight:600;">{str(agg.get('strong_buy_win_rate','N/A'))+'%' if agg.get('strong_buy_win_rate') is not None else 'N/A'}</td>
            <td style="padding:6px 20px 6px 30px;color:#6b7280;">BUY win rate</td>
            <td style="padding:6px 0;font-weight:600;">{str(agg.get('buy_win_rate','N/A'))+'%' if agg.get('buy_win_rate') is not None else 'N/A'}</td>
          </tr>
        </table>"""

    return f"""
    <div style="font-family:Arial,sans-serif;color:#111827;">
      <p style="color:#6b7280;font-size:13px;">
        Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp;
        Strategy: 3-month momentum breakout &nbsp;|&nbsp;
        Backtest: ~12 months walk-forward, 10-day hold, 7% stop-loss
      </p>

      {agg_html}

      <h3 style="margin:24px 0 8px;color:#111827;">Today's Signals</h3>
      <div style="overflow-x:auto;">
      <table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;width:100%;">
        <thead>
          <tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Ticker</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Price</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">3M Return</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Vol Surge</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">ATR</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Signal</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Score</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">BT Trades</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">BT Win%</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">BT Avg/Trade</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">BT Prof.Factor</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">BT MaxDD</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Earnings</th>
            <th style="padding:10px 12px;text-align:left;white-space:nowrap;">Ex-Div</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      </div>

      <p style="margin-top:20px;font-size:11px;color:#9ca3af;">
        BT = Backtest (past ~12 months). Past performance does not guarantee future results.
        Stop-loss: {BACKTEST_STOP_LOSS*100:.0f}%. Hold period: {BACKTEST_HOLD_DAYS} trading days.
        This is not financial advice.
      </p>

      <h4 style="margin:24px 0 8px;color:#111827;font-size:13px;">Backtest Metrics Explained</h4>
      <p style="font-size:11px;color:#6b7280;line-height:1.5;">
        <strong>BT Trades</strong> — how many times in the past ~12 months that stock triggered a BUY or STRONG BUY signal and a full trade was simulated. More trades = more confidence in the other numbers. If it says 2, the win rate is basically meaningless.<br><br>
        <strong>BT Win%</strong> — out of those trades, what percentage were profitable when they exited (either after 10 days or at the 7% stop-loss). 50% = broke even on wins vs losses, 60%+ is decent for a swing strategy.<br><br>
        <strong>BT Avg/Trade</strong> — the average profit or loss per trade as a percentage. This matters more than win rate alone — you could win 70% of trades but still lose money if your losses are huge. You want this to be a positive number.<br><br>
        <strong>BT Prof.Factor</strong> — total profits divided by total losses across all trades. A score of 1.0 means you broke even, above 1.5 is generally considered good, above 2.0 is strong. For example, a profit factor of 1.8 means for every $1 lost, the strategy made $1.80.<br><br>
        <strong>BT MaxDD</strong> — the worst peak-to-trough loss in the cumulative P&L during the backtest period. It answers "what's the worst run of bad trades I would have had to sit through?" A MaxDD of -15% means at some point your running total dropped 15% from its high before recovering.
      </p>
      <p style="margin-top:12px;font-size:11px;color:#9ca3af;">
        The most important ones to look at together are Win% + Avg/Trade + Prof.Factor as a trio — a strategy can look good on any one of them individually but fall apart on the others.
      </p>
    </div>"""

def send_email(config: dict, html_body: str, subject: str = 'Stock Screener Report'):
    if not config['email_user'] or not config['email_password']:
        logger.warning('Email not configured, skipping send')
        return False

    msg = MIMEMultipart('alternative')
    msg['From'] = config['email_user']
    msg['To'] = ', '.join(r for r in config['recipients'] if r)
    msg['Subject'] = subject

    full_html = f"""
    <html><head><meta charset="utf-8"></head>
    <body style="margin:0;padding:24px;background:#ffffff;">
      <h2 style="font-family:Arial,sans-serif;color:#111827;margin:0 0 4px;">
        Stock Screener Report
      </h2>
      {html_body}
    </body></html>"""

    msg.attach(MIMEText(full_html, 'html'))

    try:
        server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
        server.starttls()
        server.login(config['email_user'], config['email_password'])
        server.sendmail(config['email_user'], config['recipients'], msg.as_string())
        server.quit()
        logger.info(f'Email sent to {len(config["recipients"])} recipients')
        return True
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
        return False


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def run_screener(index: str = 'sp500'):
    logger.info(f'Starting stock screener for {index.upper()}...')
    config = load_env()
    conn = init_db()

    if index == 'sp400':
        tickers = get_sp400_tickers()
    else:
        tickers = get_sp500_tickers()

    logger.info(f'Screening {len(tickers)} stocks...')

    failed_tickers = []
    results = []

    for i, ticker in enumerate(tickers):
        if (i + 1) % 50 == 0:
            logger.info(f'Progress: {i+1}/{len(tickers)} | signals so far: {len(results)}')

        try:
            data = get_stock_data(ticker, conn)
            if not data:
                failed_tickers.append(ticker)
                continue

            result = analyze_stock(ticker, data)
            if result:
                results.append(result)
                bt = result.get('backtest', {})
                logger.info(
                    f"SIGNAL: {ticker} | {result['signal']} (score {result['score']:+d}) "
                    f"| 3M: {result['return_3m']}% "
                    f"| BT win%: {bt.get('win_rate','?')}% over {bt.get('total_trades','?')} trades"
                )
        except Exception as e:
            logger.debug(f'Error processing {ticker}: {e}')
            failed_tickers.append(ticker)

    logger.info(f'Screening complete. Signals: {len(results)} | Failed: {len(failed_tickers)}')

    if not results:
        send_email(config, '<p>No stocks passed all screening criteria today.</p>',
                   'Stock Screener — No Results')
        print('No stocks found matching all criteria.')
        conn.close()
        return

    # Sort: signal strength first, then 3M return descending
    signal_order = {'STRONG BUY': 0, 'BUY': 1, 'HOLD': 2, 'SELL': 3, 'STRONG SELL': 4}
    results.sort(key=lambda r: (signal_order.get(r['signal'], 9), -r['return_3m']))

    # Console summary
    table_data = [[
        r['ticker'], f"${r['price']}", f"{r['return_3m']}%",
        f"{r['volume_surge']:.1f}x", f"${r['atr']}", r['signal'],
        f"{int(r['score']):+d}",
        r['backtest'].get('win_rate', 'N/A'),
        r['backtest'].get('total_trades', 'N/A'),
        r['earnings_date'], r['ex_dividend']
    ] for r in results]

    headers = ['Ticker', 'Price', '3M Ret%', 'Vol', 'ATR', 'Signal', 'Score',
               'BT Win%', 'BT Trades', 'Earnings', 'Ex-Div']
    print(tabulate(table_data, headers=headers, tablefmt='grid'))

    html_report = build_html_report(results)
    send_email(config, html_report,
               f'Stock Screener — {len(results)} Signal{"s" if len(results) != 1 else ""}')

    conn.close()
    logger.info('Done.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Stock Screener')
    parser.add_argument('-sp500', action='store_true', help='Screen S&P 500 stocks (default)')
    parser.add_argument('-sp400', action='store_true', help='Screen S&P 400 stocks')
    args = parser.parse_args()

    if args.sp400:
        index = 'sp400'
    else:
        index = 'sp500'

    run_screener(index)
