#!/usr/bin/env python3
"""
Alpaca Auto-Trading Bot
Automated trading script that:
- Gets top 3 stocks from market scan
- Allocates equal capital to each
- Uses 2:1 R:R strategy (5% target, 2.5% stop)
- Implements 2% trailing stop when profitable
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
    CHECK_INTERVAL_SECONDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    DRY_RUN_MODE, LOG_FILE, MARKET_TZ,
    ENABLE_NEWS_FILTER, NEWS_DAYS_LOOKBACK, NEWS_LIMIT_PER_STOCK
)
from swing_trading_analyzer import MarketScanner, StockAnalyzer, TechnicalAnalyzer
from news_fetcher import NewsFetcher
from gemini_analyzer import GeminiAnalyzer


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


class MarketScanReader:
    def __init__(self):
        self.ta = TechnicalAnalyzer()
        self.news_fetcher = NewsFetcher() if ENABLE_NEWS_FILTER else None
        try:
            self.gemini = GeminiAnalyzer() if ENABLE_NEWS_FILTER else None
        except Exception as e:
            logger.warning(f"Gemini AI not available for news filtering: {e}")
            self.gemini = None
    
    def run_scan(self, top_n=15):
        logger.info(f"Running market scan for top {top_n} stocks...")
        
        from swing_trading_analyzer import MarketScanner, StockAnalyzer, SP500_TICKERS
        
        analyzer = StockAnalyzer(symbols=SP500_TICKERS[:50], period="1mo", interval="30m")
        scanner = MarketScanner(analyzer)
        
        results = scanner.full_market_scan(top_n=top_n)
        
        logger.info(f"Scan complete: found {len(results['all_symbols'])} candidates")
        
        return results
    
    def check_news_safety(self, symbol):
        """Check if news is safe for trading this symbol"""
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
            logger.info(f"News Analysis Summary: {analysis.get('analysis').splitlines()[0]}...") # Log first line
            return False
            
        logger.info(f"News Filter: {symbol} passed.")
        return True

    def get_top_buy_stocks(self, top_n=3):
        results = self.run_scan(top_n=TOP_N_STOCKS * 5)
        
        candidates = []
        from swing_trading_analyzer import StockAnalyzer
        analyzer = StockAnalyzer(symbols=[], period="1mo", interval="30m")
        
        for symbol in results.get('all_symbols', []):
            try:
                # Perform technical analysis to confirm BUY signal
                analysis = analyzer.analyze_stock(symbol)
                if not analysis:
                    continue
                
                rec = analysis['recommendation']
                # Only consider stocks with BUY or STRONG BUY signals
                if rec['recommendation'] not in ["BUY", "STRONG BUY"]:
                    continue

                ticker = yf.Ticker(symbol)
                info = ticker.info
                
                if info.get('regularMarketPrice') and info.get('regularMarketPrice') > 1:
                    current_price = info.get('regularMarketPrice')
                    
                    # News Filter
                    if not self.check_news_safety(symbol):
                        continue
                    
                    candidates.append({
                        'symbol': symbol,
                        'price': current_price,
                        'volume': info.get('volume', 0),
                        'strength': rec['strength'],
                        'recommendation': rec['recommendation']
                    })
            except Exception as e:
                logger.debug(f"Could not fetch/analyze {symbol}: {e}")
                continue
        
        # Sort by signal strength first, then volume
        candidates.sort(key=lambda x: (x['strength'], x['volume']), reverse=True)
        
        top_stocks = candidates[:top_n]
        
        logger.info(f"Selected top {len(top_stocks)} stocks: {[s['symbol'] for s in top_stocks]}")
        
        return top_stocks


class TradingStrategy:
    def __init__(self):
        self.take_profit_pct = TAKE_PROFIT_PCT
        self.stop_loss_pct = STOP_LOSS_PCT
        self.trail_stop_pct = TRAIL_STOP_PCT
        self.trail_activation_pct = TRAIL_ACTIVATION_PCT
    
    def calculate_position_sizes(self, cash, stock_prices):
        total_capital = cash * len(stock_prices)
        sizes = []
        
        for price in stock_prices:
            qty = int((total_capital / len(stock_prices)) / price)
            qty = (qty // 1) * 1
            sizes.append(max(1, qty))
        
        return sizes
    
    def get_exit_prices(self, entry_price):
        take_profit = entry_price * (1 + self.take_profit_pct)
        stop_loss = entry_price * (1 - self.stop_loss_pct)
        
        return {
            'entry': entry_price,
            'take_profit': take_profit,
            'stop_loss': stop_loss
        }
    
    def should_exit(self, current_price, entry_price, peak_price, current_position):
        entry = entry_price
        pnl_pct = (current_price - entry) / entry
        
        # Stop Loss (Hard exit)
        if pnl_pct <= -self.stop_loss_pct:
            return True, "Stop Loss"
        
        # Trailing Stop logic
        if current_position.get('trailing_active', False):
            trail_price = peak_price * (1 - self.trail_stop_pct)
            if current_price <= trail_price:
                return True, f"Trailing Stop (Profit: {pnl_pct*100:.2f}%)"
        
        # If we hit take profit but trailing isn't active yet, 
        # normally we'd exit, but user wants trailing past 5%.
        # The activate_trailing method handles setting trailing_active.
        
        return False, None
    
    def activate_trailing(self, current_price, entry_price, position_data):
        pnl_pct = (current_price - entry_price) / entry_price
        
        if pnl_pct >= self.trail_activation_pct:
            position_data['trailing_active'] = True
            position_data['peak_price'] = max(
                position_data.get('peak_price', current_price),
                current_price
            )
        
        return position_data


class TradingBot:
    def __init__(self):
        self.alpaca = AlpacaClient()
        self.scanner = MarketScanReader()
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
        
        val_str = f"${portfolio_value:.2f}" if portfolio_value is not None else "Unknown"
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
    
    def is_market_open(self):
        from datetime import time as dt_time
        from config import RUN_TEST_MODE
        
        if RUN_TEST_MODE:
            return True  # Bypass market hours for testing
        
        now = datetime.now()
        current_time = now.time()
        
        open_time = dt_time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)
        close_time = dt_time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
        
        if now.weekday() >= 5:
            return False
        
        return open_time <= current_time <= close_time
    
    def should_rotate(self):
        from config import RUN_TEST_MODE
        
        if RUN_TEST_MODE:
            return True  # Allow rotation anytime in test mode
        
        today = datetime.now().date()
        
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
                'peak_price': float(pos.get('current_price', 0))
            }
    
    def close_all_positions(self):
        logger.info("Closing all positions...")
        
        pdt_safe, pdt_msg = self.check_pdt_protection()
        
        if not pdt_safe:
            logger.warning(f"PDT Protection: {pdt_msg} - skipping close to avoid day trade")
            return
        
        for symbol in list(self.positions.keys()):
            pos = self.positions[symbol]
            
            result = self.alpaca.close_position(symbol)
            
            entry_price = pos['avg_entry_price']
            exit_price = pos['current_price']
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            
            self.email.send_sell_notification(
                symbol, pos['qty'], entry_price, exit_price, pnl_pct, "Daily Rotation"
            )
            self.journal.log_sell(symbol, exit_price)
            
            logger.info(f"Closed {symbol}: P&L = {pnl_pct:.2f}%")
        
        self.positions = {}
        self.alpaca.cancel_all_orders()
    
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
                
                if info and info.get('regularMarketPrice'):
                    stock_data.append({
                        'symbol': symbol,
                        'price': info['regularMarketPrice']
                    })
            except Exception as e:
                logger.error(f"Failed to get price for {symbol}: {e}")
                continue
        
        for stock in stock_data:
            symbol = stock['symbol']
            price = stock['price']
            
            # Check if we have enough cash for this specific allocation
            allocation = min(target_per_stock, cash / len(stock_data))
            
            qty = int(allocation / price)
            
            if qty <= 0:
                logger.warning(f"Cannot buy {symbol}: allocation too small (Target: ${allocation:.2f}, Price: ${price:.2f})")
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
                'peak_price': price
            }
            
            self.email.send_buy_notification(symbol, qty, price, qty * price)
            self.journal.log_buy(symbol, qty, price)
            
            logger.info(f"Opened {symbol}: {qty} shares at ${price}")
    
    def rotate_positions(self):
        logger.info("Running daily position rotation...")
        
        self.close_all_positions()
        
        # Reload positions to see if they actually closed
        self.load_positions()
        if self.positions:
            logger.warning(f"Could not close all positions ({list(self.positions.keys())}). Aborting rotation to avoid over-leverage.")
            return
        
        top_stocks = self.scanner.get_top_buy_stocks(top_n=TOP_N_STOCKS)
        
        symbols = [s['symbol'] for s in top_stocks]
        
        self.open_positions(symbols)
        
        self.last_rotation_date = datetime.now().date()
        
        logger.info(f"Rotation complete. New positions: {symbols}")
    
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
                
                self.strategy.activate_trailing(
                    current_price, pos['avg_entry_price'], pos
                )
                
                should_exit, reason = self.strategy.should_exit(
                    current_price, pos['avg_entry_price'], pos['peak_price'], pos
                )
                
                if should_exit:
                    pdt_safe, pdt_msg = self.check_pdt_protection()
                    
                    if not pdt_safe:
                        logger.warning(f"PDT Protection: {pdt_msg} - skipping exit for {symbol}")
                        should_exit = False
                    else:
                        logger.info(f"Exiting {symbol}: {reason}")
                        
                        result = self.alpaca.close_position(symbol)
                        
                        self.record_day_trade(symbol)
                        
                        entry_price = pos['avg_entry_price']
                        exit_price = current_price
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                        
                        self.email.send_sell_notification(
                            symbol, pos['qty'], entry_price, exit_price, pnl_pct, reason
                        )
                        self.journal.log_sell(symbol, exit_price)
                        
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
        logger.info(f"Rebalancing: need {needed} more stock(s) to reach {TOP_N_STOCKS}")
        
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
                
                if not info or not info.get('regularMarketPrice'):
                    continue
                
                current_price = info['regularMarketPrice']
                
                # Check news safety before rebalancing buy
                if not self.scanner.check_news_safety(symbol):
                    continue

                qty = int(cash / current_price)
                
                if qty <= 0:
                    logger.warning(f"Cannot buy {symbol}: insufficient funds (${cash:.2f}, price: ${current_price:.2f})")
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
                
                self.email.send_buy_notification(symbol, qty, current_price, qty * current_price)
                self.journal.log_buy(symbol, qty, current_price)
                
                logger.info(f"Rebalanced: Opened {symbol} - {qty} shares at ${current_price}")
                
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