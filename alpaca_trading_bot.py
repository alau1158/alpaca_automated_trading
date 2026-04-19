#!/usr/bin/env python3
"""
Alpaca Auto-Trading Bot
Automated trading script that:
- Gets top 3 stocks from S&P 400 screener
- Selects stocks by backtest scoring (avg/trade 2pts, win% 1pt, prof_factor 1pt)
- Uses ATR-based exit (1 ATR stop loss, 2x ATR take profit)
- Implements 5% trailing stop after hitting 2x ATR
- Exits sideways stocks after 14 days within ±ATR range
- Rotates positions daily during market hours
- Sends email notifications on all trades
"""

import os
import sys
import time
import csv
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ALPACA_ENDPOINT, ALPACA_KEY, ALPACA_SECRET,
    SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_RECIPIENTS,
    TOP_N_STOCKS, ALLOCATION_PERCENT,
    TAKE_PROFIT_PCT, STOP_LOSS_PCT, TRAIL_STOP_PCT, TRAIL_ACTIVATION_PCT,
    REENTRY_ENABLED, REENTRY_BUFFER_PCT,
    MAX_HOLDING_DAYS, EARNINGS_LOOKAHEAD_DAYS,
    CHECK_INTERVAL_SECONDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    DRY_RUN_MODE, LOG_FILE, MARKET_TZ,
    ENABLE_NEWS_FILTER, NEWS_DAYS_LOOKBACK, NEWS_LIMIT_PER_STOCK
)
from swing_trading_analyzer import MarketScanner, StockAnalyzer, TechnicalAnalyzer
from news_fetcher import NewsFetcher
from gemini_analyzer import GeminiAnalyzer
import screener


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.smtp_user = SMTP_USER
        self.smtp_password = SMTP_PASSWORD
        self.recipients = EMAIL_RECIPIENTS
    
    def send_trade_email(self, subject, body):
        if not self.smtp_user or not self.smtp_password:
            logger.warning("Email not configured - SMTP credentials not set")
            return False
        
        try:
            msg = MIMEMultipart()
            msg["From"] = self.smtp_user
            msg["To"] = ", ".join(self.recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_user, self.recipients, msg.as_string())
            
            logger.info(f"Email sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    def send_buy_notification(self, symbol, qty, price, position_value):
        subject = f"[BUY] {symbol} - Position Opened"
        body = f"""Trade Alert - BUY Order Executed

Stock: {symbol}
Quantity: {qty} shares
Entry Price: ${price:.2f}
Position Value: ${position_value:.2f}
Allocation: {ALLOCATION_PERCENT*100:.1f}% of portfolio

Strategy: Top {TOP_N_STOCKS} from market scan
Take Profit: {TAKE_PROFIT_PCT*100:.1f}%
Stop Loss: {STOP_LOSS_PCT*100:.1f}%
Trailing Stop: {TRAIL_STOP_PCT*100:.1f}% (when profitable)

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}
"""
        return self.send_trade_email(subject, body)
    
    def send_sell_notification(self, symbol, qty, entry_price, exit_price, pnl_pct, reason):
        subject = f"[SELL] {symbol} - {reason}"
        pnl_direction = "+" if pnl_pct > 0 else ""
        body = f"""Trade Alert - SELL Order Executed

Stock: {symbol}
Quantity: {qty} shares
Entry Price: ${entry_price:.2f}
Exit Price: ${exit_price:.2f}
P&L: {pnl_direction}{pnl_pct:.2f}%
Reason: {reason}

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}
"""
        return self.send_trade_email(subject, body)
    
    def send_reentry_notification(self, symbol, qty, price, position_value, original_entry_price):
        subject = f"[RE-ENTRY] {symbol} - Position Re-Opened"
        body = f"""Trade Alert - RE-ENTRY BUY Order Executed

Stock: {symbol}
Quantity: {qty} shares
Re-Entry Price: ${price:.2f}
Position Value: ${position_value:.2f}
Allocation: {ALLOCATION_PERCENT*100:.1f}% of portfolio

Original Entry Price: ${original_entry_price:.2f}
Recovery: {((price - original_entry_price) / original_entry_price * 100):.2f}%

Strategy: Re-entry on recovery (1% buffer above original entry)
Take Profit: {TAKE_PROFIT_PCT*100:.1f}%
Stop Loss: {STOP_LOSS_PCT*100:.1f}%

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}
"""
        return self.send_trade_email(subject, body)
    
    def send_daily_summary(self, positions, total_value, cash, next_rotation):
        position_lines = []
        total_pnl = 0
        
        for pos in positions:
            pnl = pos.get('market_value', 0) - pos.get('cost_basis', 0)
            pnl_pct = (pnl / pos.get('cost_basis', 1)) * 100 if pos.get('cost_basis', 0) > 0 else 0
            total_pnl += pnl
            position_lines.append(
                f"  {pos['symbol']}: {pos['qty']} shares @ ${pos['current_price']:.2f} "
                f"(P&L: {pnl_direction(pnl_pct)}{pnl_pct:.2f}%)"
            )
        
        positions_text = "\n".join(position_lines) if position_lines else "  No open positions"
        
        subject = f"[SUMMARY] Daily Trading Summary - {datetime.now().strftime('%Y-%m-%d')}"
        body = f"""Daily Trading Summary

Open Positions:
{positions_text}

Total Portfolio Value: ${total_value:.2f}
Available Cash: ${cash:.2f}
Unrealized P&L: {pnl_direction(total_pnl)}${abs(total_pnl):.2f}

Next Position Rotation: {next_rotation}

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}
"""
        return self.send_trade_email(subject, body)


class JournalLogger:
    def __init__(self, journal_path="/home/alau/autotrading/journal.csv"):
        self.journal_path = journal_path
    
    def _format_date(self, dt):
        return dt.strftime("%Y%m%d")
    
    def log_buy(self, symbol, quantity, price):
        try:
            with open(self.journal_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    symbol.upper(),
                    quantity,
                    self._format_date(datetime.now()),
                    f"{price:.2f}",
                    "",
                    ""
                ])
            logger.info(f"Journal: Logged BUY {symbol}")
        except Exception as e:
            logger.error(f"Failed to log buy to journal: {e}")
    
    def log_sell(self, symbol, sold_price):
        try:
            with open(self.journal_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            for row in rows:
                row = {k.strip(): v for k, v in row.items()}
                if row.get("ticket", "").upper() == symbol.upper() and not row.get("sold_date", "").strip():
                    row["sold_date"] = self._format_date(datetime.now())
                    row["sold_price"] = f"{sold_price:.2f}"
                    
                    with open(self.journal_path, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=["ticket", "quanity", "bought_date", "bought_price", "sold_date", "sold_price"])
                        writer.writeheader()
                        for r in rows:
                            writer.writerow(r)
                    logger.info(f"Journal: Logged SELL {symbol}")
                    return
            
            logger.warning(f"No open position found for {symbol} in journal")
        except Exception as e:
            logger.error(f"Failed to log sell to journal: {e}")
    
    def log_reentry(self, symbol, quantity, price):
        self.log_buy(symbol, quantity, price)
    
    def get_bought_date(self, symbol):
        try:
            with open(self.journal_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            for row in rows:
                row = {k.strip(): v for k, v in row.items()}
                if row.get("ticket", "").upper() == symbol.upper() and not row.get("sold_date", "").strip():
                    bought_date_str = row.get("bought_date", "").strip()
                    if bought_date_str:
                        return datetime.strptime(bought_date_str, "%Y%m%d")
            return None
        except Exception as e:
            logger.error(f"Failed to get bought date from journal: {e}")
            return None


def pnl_direction(pnl):
    return "+" if pnl > 0 else ""


class AlpacaClient:
    def __init__(self):
        self.base_url = ALPACA_ENDPOINT
        self.key = ALPACA_KEY
        self.secret = ALPACA_SECRET
        self.headers = {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret
        }
    
    def _request(self, method, endpoint, data=None):
        import requests
        url = f"{self.base_url}{endpoint}"
        try:
            if method == "GET":
                resp = requests.get(url, headers=self.headers, timeout=30)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=self.headers, timeout=30)
            elif method == "DELETE":
                resp = requests.delete(url, headers=self.headers, timeout=30)
            else:
                return None
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except Exception as e:
            logger.error(f"Alpaca API error: {e}")
            return None
    
    def get_account(self):
        return self._request("GET", "/account")
    
    def get_clock(self):
        return self._request("GET", "/clock")
    
    def get_positions(self):
        return self._request("GET", "/positions") or []
    
    def get_position(self, symbol):
        positions = self.get_positions()
        for pos in positions:
            if pos.get('symbol') == symbol:
                return pos
        return None
    
    def get_open_orders(self):
        return self._request("GET", "/orders") or []
    
    def cancel_all_orders(self):
        orders = self.get_open_orders()
        for order in orders:
            self._request("DELETE", f"/orders/{order['id']}")
    
    def place_market_order(self, symbol, qty, side):
        data = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day"
        }
        if DRY_RUN_MODE:
            logger.info(f"[DRY RUN] Would place {side} order: {symbol} x{qty}")
            return {"id": "dry_run", "status": "ok"}
        return self._request("POST", "/orders", data)
    
    def place_limit_order(self, symbol, qty, side, limit_price):
        data = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": "day"
        }
        if DRY_RUN_MODE:
            logger.info(f"[DRY RUN] Would place {side} limit order: {symbol} x{qty} @ ${limit_price}")
            return {"id": "dry_run", "status": "ok"}
        return self._request("POST", "/orders", data)
    
    def close_position(self, symbol):
        if DRY_RUN_MODE:
            logger.info(f"[DRY RUN] Would close position: {symbol}")
            return {"id": "dry_run", "status": "ok"}
        return self._request("DELETE", f"/positions/{symbol}")


class ScreenerReader:
    def __init__(self):
        self.news_fetcher = NewsFetcher() if ENABLE_NEWS_FILTER else None
        try:
            self.gemini = GeminiAnalyzer() if ENABLE_NEWS_FILTER else None
        except Exception as e:
            logger.warning(f"Gemini AI not available for news filtering: {e}")
            self.gemini = None

    def run_screener(self, index='sp400'):
        logger.info(f"Running screener for {index.upper()}...")
        results = screener.run_screener(index)
        return results

    def check_news_safety(self, symbol):
        if not ENABLE_NEWS_FILTER or not self.news_fetcher or not self.gemini:
            return True
        logger.info(f"Checking news for {symbol}...")
        news = self.news_fetcher.get_stock_news(
            symbol, days=NEWS_DAYS_LOOKBACK, limit=NEWS_LIMIT_PER_STOCK
        )
        if not news:
            logger.info(f"News Filter: No recent news for {symbol}, proceeding.")
            return True
        formatted_news = self.news_fetcher.format_news_for_ai(news)
        analysis = self.gemini.analyze_news(symbol, formatted_news)
        if not analysis.get('proceed', True):
            logger.info(f"News Filter: Skipping {symbol} due to negative news or upcoming events.")
            return False
        logger.info(f"News Filter: {symbol} passed.")
        return True

    def check_earnings_safe(self, symbol):
        import io
        import sys
        from datetime import datetime, timezone
        try:
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            ticker = yf.Ticker(symbol)
            earnings = ticker.earnings_dates
            sys.stderr = old_stderr
            if earnings is None or len(earnings) == 0:
                return True
            now = datetime.now(timezone.utc)
            for date in earnings.index:
                days_until = (date.replace(tzinfo=timezone.utc) - now).days
                if 0 <= days_until <= EARNINGS_LOOKAHEAD_DAYS:
                    logger.info(f"Earnings Filter: Skipping {symbol} - earnings in {days_until} days")
                    return False
            return True
        except Exception as e:
            sys.stderr = old_stderr
            return True

    def score_stocks(self, results):
        def calc_score(stock):
            bt = stock.get('backtest', {})
            win_rate = bt.get('win_rate', 0) or 0
            avg_trade = bt.get('avg_return_per_trade', 0) or 0
            prof_factor = bt.get('profit_factor', 0) or 0
            return {
                'symbol': stock['ticker'],
                'price': stock['price'],
                'atr': stock.get('atr'),
                'win_rate': win_rate,
                'avg_trade': avg_trade,
                'prof_factor': prof_factor,
                'total_trades': bt.get('total_trades', 0),
                'signal': stock.get('signal')
            }

        scored = [calc_score(r) for r in results if r.get('backtest')]
        if not scored:
            return []

        scored.sort(key=lambda x: x['avg_trade'], reverse=True)
        for i, s in enumerate(scored):
            if s['avg_trade'] is not None:
                s['avg_trade_score'] = 2 if i == 0 else (1 if i == 1 else (0.5 if i == 2 else 0))
            else:
                s['avg_trade_score'] = 0

        scored.sort(key=lambda x: x['win_rate'], reverse=True)
        for i, s in enumerate(scored):
            if s['win_rate'] is not None and s['win_rate'] > 50:
                s['win_rate_score'] = 1 if i == 0 else (0.5 if i == 1 else (0.25 if i == 2 else 0))
            else:
                s['win_rate_score'] = 0

        scored.sort(key=lambda x: x['prof_factor'], reverse=True)
        for i, s in enumerate(scored):
            if s['prof_factor'] is not None:
                s['prof_factor_score'] = 1 if i == 0 else (0.5 if i == 1 else (0.25 if i == 2 else 0))
            else:
                s['prof_factor_score'] = 0

        for s in scored:
            s['total_score'] = s.get('avg_trade_score', 0) + s.get('win_rate_score', 0) + s.get('prof_factor_score', 0)

        scored.sort(key=lambda x: x['total_score'], reverse=True)
        return scored

    def get_top_buy_stocks(self, top_n=3):
        logger.info("Running S&P 400 screener...")
        from screener import get_sp400_tickers, get_stock_data, init_db, analyze_stock
        try:
            conn = init_db()
            tickers = get_sp400_tickers()
            logger.info(f"Screening {len(tickers)} S&P 400 stocks...")
        except Exception as e:
            logger.error(f"Failed to get S&P 400 tickers: {e}")
            return []

        candidates = []
        for i, ticker in enumerate(tickers):
            if (i + 1) % 50 == 0:
                logger.info(f"Screener progress: {i+1}/{len(tickers)} | candidates so far: {len(candidates)}")
            try:
                data = get_stock_data(ticker, conn)
                if not data:
                    continue
                result = analyze_stock(ticker, data)
                if not result:
                    continue
                sig = result.get('signal', '')
                if sig not in ('BUY', 'STRONG BUY'):
                    continue
                if result.get('win_rate', 0) <= 50:
                    logger.debug(f"Skipping {ticker}: win rate {result.get('win_rate')}% <= 50%")
                    continue
                candidates.append(result)
            except Exception as e:
                logger.debug(f"Error processing {ticker}: {e}")
                continue

        conn.close()
        logger.info(f"Screener found {len(candidates)} passing stocks")

        if not candidates:
            logger.warning("No stocks passed screener criteria")
            return []

        scored = self.score_stocks(candidates)
        top_stocks = scored[:top_n]

        for s in top_stocks:
            logger.info(
                f"Selected: {s['symbol']} | Score: {s['total_score']:.2f} | "
                f"Win%: {s['win_rate']}% | Avg/Trade: {s['avg_trade']:.2f}% | PF: {s['prof_factor']:.2f}"
            )

        return top_stocks


class ATRTradingStrategy:
    TAKE_PROFIT_MULT = 2.0
    TRAIL_STOP_PCT = 0.05
    MAX_HOLDING_DAYS = 14

    def __init__(self):
        pass

    def calculate_position_sizes(self, cash, stock_prices):
        total_capital = cash * len(stock_prices)
        sizes = []
        for price in stock_prices:
            qty = int((total_capital / len(stock_prices)) / price)
            qty = (qty // 1) * 1
            sizes.append(max(1, qty))
        return sizes

    def get_atr(self, symbol):
        try:
            from screener import calculate_atr
            stock = yf.Ticker(symbol)
            hist = stock.history(period='3mo', auto_adjust=False)
            if hist.empty or len(hist) < 20:
                return None
            atr = calculate_atr(hist['High'], hist['Low'], hist['Close'])
            return atr
        except Exception as e:
            logger.debug(f"Failed to get ATR for {symbol}: {e}")
            return None

    def get_exit_prices(self, entry_price, atr):
        if atr is None:
            return {'entry': entry_price, 'stop_loss': entry_price * 0.95, 'take_profit': entry_price * 1.05}
        stop_loss = entry_price - atr
        take_profit = entry_price + (atr * self.TAKE_PROFIT_MULT)
        return {'entry': entry_price, 'stop_loss': stop_loss, 'take_profit': take_profit}

    def should_exit(self, symbol, current_price, entry_price, peak_price, current_position, journal, atr=None):
        if atr is None:
            return False, None
        pnl_pct = (current_price - entry_price) / entry_price

        stop_loss = entry_price - atr
        if current_price <= stop_loss:
            return True, f"Stop Loss (ATR: ${atr:.2f})"

        if current_position.get('trailing_active', False):
            trail_price = peak_price * (1 - self.TRAIL_STOP_PCT)
            if current_price <= trail_price:
                return True, f"Trailing Stop (Profit: {pnl_pct*100:.2f}%)"

        take_profit_target = entry_price + (atr * self.TAKE_PROFIT_MULT)
        if current_price >= take_profit_target and not current_position.get('trailing_active'):
            current_position['trailing_active'] = True
            current_position['peak_price'] = current_price
            logger.info(f"Take profit target hit for {symbol} - activating 5% trailing stop")

        bought_dt = journal.get_bought_date(symbol)
        if bought_dt:
            now = datetime.now()
            if bought_dt.tzinfo is not None:
                now = datetime.now(timezone.utc)
            holding_days = (now - bought_dt.replace(tzinfo=None)).days

            if holding_days >= self.MAX_HOLDING_DAYS:
                in_range = entry_price - atr <= current_price <= entry_price + atr
                if in_range:
                    return True, f"Sideways Exit ({holding_days} days, within ±ATR)"
                else:
                    return True, f"Max Hold Time ({holding_days} days)"

        return False, None

    def activate_trailing(self, current_price, entry_price, position_data, atr=None):
        if atr is None:
            return position_data
        take_profit_target = entry_price + (atr * self.TAKE_PROFIT_MULT)
        if current_price >= take_profit_target:
            position_data['trailing_active'] = True
            position_data['peak_price'] = max(
                position_data.get('peak_price', current_price),
                current_price
            )
        return position_data


class TradingStrategy(ATRTradingStrategy):
    def __init__(self):
        super().__init__()


class TradingBot:
    def __init__(self):
        self.alpaca = AlpacaClient()
        self.scanner = ScreenerReader()
        self.strategy = TradingStrategy()
        self.email = EmailNotifier()
        self.journal = JournalLogger()
        self.positions = {}
        self.stopped_positions = {}  # Track exited positions for re-entry
        self.last_rotation_date = None
        self.daily_trades = {}  # Track trades per symbol per day for PDT protection
    
    PDT_THRESHOLD = 25000
    
    def check_pdt_protection(self):
        cash, portfolio_value = self.get_account_info()
        
        if portfolio_value is not None and portfolio_value >= self.PDT_THRESHOLD:
            return True, "Account value sufficient for day trading"
        
        val_str = f"${portfolio_value:.2f}" if portfolio_value is not None else "Unknown (check API)"
        return False, f"Account value {val_str} below $25,000 PDT threshold"
    
    def record_day_trade(self, symbol):
        today = datetime.now().date()
        if symbol not in self.daily_trades:
            self.daily_trades[symbol] = {}
        if today not in self.daily_trades[symbol]:
            self.daily_trades[symbol][today] = 0
        self.daily_trades[symbol][today] += 1
    
    def get_day_trade_count(self, symbol):
        today = datetime.now().date()
        return self.daily_trades.get(symbol, {}).get(today, 0)
    
    def get_clock(self):
        return self._request("GET", "/clock")
    
    def is_market_open(self):
        from datetime import time as dt_time
        from config import RUN_TEST_MODE
        import pytz
        
        if RUN_TEST_MODE:
            return True
        
        try:
            clock = self.alpaca.get_clock()
            if clock and clock.get("is_open") is not None:
                return clock["is_open"]
        except:
            pass
        
        ny_tz = pytz.timezone(MARKET_TZ)
        now = datetime.now(ny_tz)
        current_time = now.time()
        
        open_time = dt_time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)
        close_time = dt_time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
        
        if now.weekday() >= 5:
            return False
        
        return open_time <= current_time <= close_time
    
    def should_rotate(self):
        from config import RUN_TEST_MODE
        import pytz
        
        if RUN_TEST_MODE:
            return True
        
        ny_tz = pytz.timezone(MARKET_TZ)
        today = datetime.now(ny_tz).date()
        
        if self.last_rotation_date != today:
            if self.is_market_open():
                return True
        
        return False
    
    def get_account_info(self):
        account = self.alpaca.get_account()
        if not account:
            return None, None
        
        cash = float(account.get('cash', 0))
        portfolio_value = float(account.get('portfolio_value', cash))
        
        return cash, portfolio_value
    
    def load_positions(self):
        from config import DRY_RUN_MODE
        
        alpaca_positions = self.alpaca.get_positions()
        
        if DRY_RUN_MODE:
            return
        
        if not alpaca_positions:
            self.positions = {}
            return
        
        self.positions = {}
        for pos in alpaca_positions:
            symbol = pos.get('symbol')
            self.positions[symbol] = {
                'symbol': symbol,
                'qty': int(float(pos.get('qty', 0))),
                'avg_entry_price': float(pos.get('avg_entry_price', 0)),
                'current_price': float(pos.get('current_price', 0)),
                'market_value': float(pos.get('market_value', 0)),
                'cost_basis': float(pos.get('cost_basis', 0)),
                'unrealized_pl': float(pos.get('unrealized_pl', 0)),
                'trailing_active': False,
                'peak_price': float(pos.get('current_price', 0)),
                'entry_time': pos.get('opened_at'),
                'atr': pos.get('atr')
            }
    
    def close_all_positions(self):
        logger.info("Closing all positions...")
        
        closed_symbols = []
        failed_symbols = []
        
        for symbol in list(self.positions.keys()):
            pos = self.positions.get(symbol)
            if not pos:
                continue
            
            entry_time = pos.get('entry_time')
            entry_date = None
            is_opened_today = False
            
            if entry_time:
                try:
                    if isinstance(entry_time, str):
                        if 'T' in entry_time:
                            entry_date = datetime.fromisoformat(entry_time.replace('Z', '+00:00')).date()
                        else:
                            entry_date = datetime.strptime(entry_time, "%Y-%m-%d").date()
                    else:
                        entry_date = entry_time.date()
                    
                    today = datetime.now(timezone.utc).date()
                    is_opened_today = (entry_date == today)
                    logger.info(f"Position {symbol}: entry_date={entry_date}, today={today}, held_overnight={not is_opened_today}")
                    
                    if is_opened_today:
                        logger.warning(f"Position {symbol} opened today ({entry_date}) - skipping close to avoid day trade")
                        failed_symbols.append(symbol)
                        continue
                except Exception as e:
                    logger.debug(f"Could not parse entry_time for {symbol}: {e}")
            else:
                # No entry_time - assume it's an old position (should have been opened before today)
                logger.info(f"Position {symbol}: no entry_time, assuming held overnight - allowing close")
            
            logger.info(f"Closing position {symbol} (held overnight: {entry_date is None or (entry_date is not None and entry_date < datetime.now(timezone.utc).date())})")
            result = self.alpaca.close_position(symbol)
            
            entry_price = pos['avg_entry_price']
            exit_price = pos['current_price']
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            
            self.email.send_sell_notification(
                symbol, pos['qty'], entry_price, exit_price, pnl_pct, "Daily Rotation"
            )
            self.journal.log_sell(symbol, exit_price)
            
            logger.info(f"Closed {symbol}: P&L = {pnl_pct:.2f}%")
            closed_symbols.append(symbol)
        
        self.positions = {}
        self.alpaca.cancel_all_orders()
        
        return closed_symbols, failed_symbols
    
    def open_positions(self, symbols):
        logger.info(f"Opening positions in: {symbols}")
        
        self.load_positions()
        
        existing_symbols = set(self.positions.keys())
        symbols_to_buy = [s for s in symbols if s not in existing_symbols]
        
        if len(symbols_to_buy) < len(symbols):
            skipped = set(symbols) - set(symbols_to_buy)
            logger.info(f"Skipping already-owned stocks: {skipped}")
        
        if not symbols_to_buy:
            logger.info("All target stocks already owned - no new positions to open")
            return
        
        cash, portfolio_value = self.get_account_info()
        
        if portfolio_value is None or cash is None or cash <= 0:
            logger.warning(f"Insufficient cash (Cash: ${cash:.2f})")
            return
        
        # Each stock should get ALLOCATION_PERCENT of the portfolio
        target_per_stock = portfolio_value * ALLOCATION_PERCENT

        stock_data = []

        for symbol in symbols_to_buy:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                atr = self.strategy.get_atr(symbol)

                if info and info.get('regularMarketPrice') and atr:
                    stock_data.append({
                        'symbol': symbol,
                        'price': info['regularMarketPrice'],
                        'atr': atr
                    })
            except Exception as e:
                logger.error(f"Failed to get price/ATR for {symbol}: {e}")
                continue

        if not stock_data:
            logger.warning("No valid stock data for positions")
            return

        for stock in stock_data:
            symbol = stock['symbol']
            price = stock['price']
            atr = stock['atr']

            allocation = min(target_per_stock, cash / len(stock_data))
            qty = int(allocation / price)

            if qty <= 0:
                continue

            result = self.alpaca.place_market_order(symbol, qty, "buy")

            self.positions[symbol] = {
                'symbol': symbol,
                'qty': qty,
                'avg_entry_price': price,
                'current_price': price,
                'market_value': qty * price,
                'cost_basis': qty * price,
                'unrealized_pl': 0,
                'trailing_active': False,
                'peak_price': price,
                'entry_time': datetime.now(timezone.utc).isoformat(),
                'atr': atr
            }

            self.email.send_buy_notification(symbol, qty, price, qty * price)
            self.journal.log_buy(symbol, qty, price)

            logger.info(f"Opened {symbol}: {qty} shares at ${price}, ATR ${atr:.2f}")
    
    def rotate_positions(self):
        logger.info("Running daily position rebalancing...")

        self.load_positions()

        current_count = len(self.positions)
        if current_count >= TOP_N_STOCKS:
            logger.info(f"Already have {current_count} positions - no rotation needed")
            self.last_rotation_date = datetime.now().date()
            return

        if current_count == 0:
            logger.info("No positions - opening new set")
            top_stocks = self.scanner.get_top_buy_stocks(top_n=TOP_N_STOCKS)
            symbols = [s['symbol'] for s in top_stocks]
            self.open_positions(symbols)
        else:
            needed = TOP_N_STOCKS - current_count
            logger.info(f"Have {current_count} position(s), filling {needed} more")
            self.rebalance_positions()

        self.last_rotation_date = datetime.now().date()
        logger.info(f"Rotation complete. Total positions: {len(self.positions)}")
    
    def _was_opened_today(self, pos):
        """Check if position was opened today"""
        if not pos:
            return False
        entry_time = pos.get('entry_time')
        if not entry_time:
            return False
        
        try:
            if isinstance(entry_time, str):
                if 'T' in entry_time:
                    entry_date = datetime.fromisoformat(entry_time.replace('Z', '+00:00')).date()
                else:
                    entry_date = datetime.strptime(entry_time, "%Y-%m-%d").date()
            else:
                entry_date = entry_time.date()
            
            today = datetime.now(timezone.utc).date()
            return entry_date == today
        except:
            return False
    
    def _was_held_more_than_one_day(self, pos):
        """Check if position was opened more than 1 trading day ago"""
        if not pos:
            return False
        entry_time = pos.get('entry_time')
        if not entry_time:
            return True  # Assume old if no timestamp
        
        try:
            if isinstance(entry_time, str):
                if 'T' in entry_time:
                    entry_date = datetime.fromisoformat(entry_time.replace('Z', '+00:00')).date()
                else:
                    entry_date = datetime.strptime(entry_time, "%Y-%m-%d").date()
            else:
                entry_date = entry_time.date()
            
            today = datetime.now(timezone.utc).date()
            return entry_date < today
        except:
            return True  # Assume old if can't parse
    
    def monitor_positions(self):
        logger.info("Monitoring positions...")

        self.load_positions()

        if not self.positions:
            logger.info("No open positions")
            return

        for symbol, pos in list(self.positions.items()):
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info or not info.get('regularMarketPrice'):
                    continue

                current_price = info['regularMarketPrice']

                pos['current_price'] = current_price
                pos['market_value'] = pos['qty'] * current_price

                peak = pos.get('peak_price', current_price)
                if current_price > peak:
                    pos['peak_price'] = current_price

                atr = pos.get('atr')
                if atr is None:
                    atr = self.strategy.get_atr(symbol)
                    pos['atr'] = atr

                self.strategy.activate_trailing(
                    current_price, pos['avg_entry_price'], pos, atr
                )

                should_exit, reason = self.strategy.should_exit(
                    symbol, current_price, pos['avg_entry_price'], pos['peak_price'], pos, self.journal, atr
                )
                
                if should_exit:
                    # Allow exit if position held >1 day (not a day trade)
                    # Only block same-day exits for accounts under PDT threshold
                    pdt_safe, pdt_msg = self.check_pdt_protection()
                    is_opened_today = self._was_opened_today(pos)

                    # Allow exit if: position held overnight OR account has sufficient funds
                    if not is_opened_today or pdt_safe:
                        logger.info(f"Exiting {symbol}: {reason}")

                        exit_price = current_price
                        entry_price = pos['avg_entry_price']
                        qty = pos['qty']
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100

                        try:
                            result = self.alpaca.close_position(symbol)
                            self.record_day_trade(symbol)
                            self.email.send_sell_notification(
                                symbol, qty, entry_price, exit_price, pnl_pct, reason
                            )

                            if REENTRY_ENABLED:
                                self.stopped_positions[symbol] = {
                                    'entry_price': entry_price,
                                    'exit_price': exit_price,
                                    'exit_time': datetime.now(),
                                    'stop_reason': reason
                                }
                                logger.info(f"Tracking {symbol} for re-entry at ${entry_price}")

                            del self.positions[symbol]
                            logger.info(f"Closed {symbol}: {reason} at ${exit_price}, P&L = {pnl_pct:.2f}%")
                        finally:
                            self.journal.log_sell(symbol, exit_price)

            except Exception as e:
                logger.error(f"Error monitoring {symbol}: {e}")
                continue
    
    def check_reentry(self):
        if not REENTRY_ENABLED:
            return
        
        if not self.stopped_positions:
            return
        
        if not self.is_market_open():
            return
        
        cash, portfolio_value = self.get_account_info()
        
        if not cash or cash <= 0:
            return
        
        reentry_symbols = list(self.stopped_positions.keys())
        
        for symbol in reentry_symbols:
            if symbol in self.positions:
                del self.stopped_positions[symbol]
                continue
            
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                
                if not info or not info.get('regularMarketPrice'):
                    continue
                
                current_price = info['regularMarketPrice']
                entry_price = self.stopped_positions[symbol]['entry_price']
                
                reentry_threshold = entry_price * (1 + REENTRY_BUFFER_PCT)
                
                if current_price >= reentry_threshold:
                    # Check news safety before re-entry
                    if not self.scanner.check_news_safety(symbol):
                        logger.info(f"Re-entry blocked for {symbol} due to news safety filter.")
                        # Optionally remove from stopped_positions so we don't keep checking?
                        # Or keep it and wait for better news? 
                        # Let's keep it for now, but maybe the user wants it removed if news is bad.
                        continue

                    allocation = cash * ALLOCATION_PERCENT
                    qty = int(allocation / current_price)
                    
                    if qty <= 0:
                        logger.warning(f"Cannot re-enter {symbol}: allocation too small")
                        continue
                    
                    result = self.alpaca.place_market_order(symbol, qty, "buy")
                    
                    self.positions[symbol] = {
                        'symbol': symbol,
                        'qty': qty,
                        'avg_entry_price': current_price,
                        'current_price': current_price,
                        'market_value': qty * current_price,
                        'cost_basis': qty * current_price,
                        'unrealized_pl': 0,
                        'trailing_active': False,
                        'peak_price': current_price
                    }
                    
                    self.email.send_reentry_notification(symbol, qty, current_price, qty * current_price, entry_price)
                    self.journal.log_reentry(symbol, qty, current_price)
                    
                    del self.stopped_positions[symbol]
                    
                    logger.info(f"Re-entered {symbol}: {qty} shares at ${current_price} (was exited at ${entry_price})")
            
            except Exception as e:
                logger.error(f"Error checking re-entry for {symbol}: {e}")
                continue
    
    def rebalance_positions(self):
        if not self.is_market_open():
            return

        if len(self.positions) >= TOP_N_STOCKS:
            return

        needed = TOP_N_STOCKS - len(self.positions)
        logger.info(f"Rebalancing: have {len(self.positions)}, need {needed} more to reach {TOP_N_STOCKS}")

        cash, portfolio_value = self.get_account_info()
        if not cash or cash <= 0:
            logger.warning("No cash for rebalancing")
            return

        top_stocks = self.scanner.get_top_buy_stocks(top_n=needed + len(self.positions))

        available_symbols = [s['symbol'] for s in top_stocks if s['symbol'] not in self.positions]
        symbols_to_buy = available_symbols[:needed]

        if not symbols_to_buy:
            logger.warning("No new stocks available for rebalancing")
            return

        for symbol in symbols_to_buy:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                atr = self.strategy.get_atr(symbol)

                if not info or not info.get('regularMarketPrice') or not atr:
                    logger.debug(f"Skipping {symbol}: no price/ATR")
                    continue

                current_price = info['regularMarketPrice']

                if not self.scanner.check_news_safety(symbol):
                    continue

                allocation = min(portfolio_value * ALLOCATION_PERCENT, cash)
                qty = int(allocation / current_price)

                if qty <= 0:
                    logger.warning(f"Cannot buy {symbol}: insufficient funds")
                    continue

                result = self.alpaca.place_market_order(symbol, qty, "buy")

                if result is None:
                    logger.error(f"Failed to place order for {symbol}")
                    continue

                self.positions[symbol] = {
                    'symbol': symbol,
                    'qty': qty,
                    'avg_entry_price': current_price,
                    'current_price': current_price,
                    'market_value': qty * current_price,
                    'cost_basis': qty * current_price,
                    'unrealized_pl': 0,
                    'trailing_active': False,
                    'peak_price': current_price,
                    'entry_time': datetime.now(timezone.utc).isoformat(),
                    'atr': atr
                }

                self.email.send_buy_notification(symbol, qty, current_price, qty * current_price)
                self.journal.log_buy(symbol, qty, current_price)

                logger.info(f"Rebalanced: Opened {symbol} - {qty} shares at ${current_price}, ATR ${atr:.2f}")

                cash -= qty * current_price

                if len(self.positions) >= TOP_N_STOCKS:
                    break

            except Exception as e:
                logger.error(f"Error rebalancing {symbol}: {e}")
                continue
    
    def run(self):
        logger.info("Starting trading bot...")
        logger.info(f"Dry run mode: {DRY_RUN_MODE}")
        
        while True:
            try:
                if self.is_market_open():
                    if REENTRY_ENABLED:
                        self.check_reentry()
                
                if self.should_rotate():
                    logger.info("Market open - rotating positions")
                    self.rotate_positions()
                
                if self.is_market_open():
                    self.monitor_positions()
                    self.rebalance_positions()
                else:
                    logger.info("Market closed - waiting")
                
                time.sleep(CHECK_INTERVAL_SECONDS)
            
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(60)


def main():
    print("\n" + "=" * 60)
    print("ALPACA AUTO-TRADING BOT")
    print("=" * 60)
    
    print(f"\nConfiguration:")
    print(f"  Top N Stocks: {TOP_N_STOCKS}")
    print(f"  Allocation per stock: {ALLOCATION_PERCENT*100:.1f}%")
    print(f"  Take Profit: {TAKE_PROFIT_PCT*100:.1f}%")
    print(f"  Stop Loss: {STOP_LOSS_PCT*100:.1f}%")
    print(f"  Trailing Stop: {TRAIL_STOP_PCT*100:.1f}%")
    print(f"  Re-Entry on Recovery: {REENTRY_ENABLED}")
    if REENTRY_ENABLED:
        print(f"  Re-Entry Buffer: {REENTRY_BUFFER_PCT*100:.1f}%")
    print(f"  News Filter: {ENABLE_NEWS_FILTER} ({NEWS_DAYS_LOOKBACK} days lookback)")
    print(f"  Check Interval: {CHECK_INTERVAL_SECONDS/60:.0f} minutes")
    print(f"  Market Hours: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MINUTE:02d} - {MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MINUTE:02d} ET")
    print(f"  Dry Run Mode: {DRY_RUN_MODE}")
    print(f"  PDT Protection: $25,000 threshold")
    
    if DRY_RUN_MODE:
        print("\n*** DRY RUN MODE ENABLED - NO REAL TRADES ***")
        print("Set DRY_RUN_MODE = False in config.py to enable live trading")
    
    print("\n" + "=" * 60)
    
    bot = TradingBot()
    bot.run()


if __name__ == "__main__":
    main()