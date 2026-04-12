#!/usr/bin/env python3
"""
Swing Trading Stock Analyzer
Analyzes popular stocks using Yahoo Finance data and technical indicators
to provide trading strategies and signals.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for saving charts
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Try to import Gemini analyzer (optional)
try:
    from gemini_analyzer import GeminiAnalyzer, is_gemini_available
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    GeminiAnalyzer = None
    is_gemini_available = lambda: False

# Popular stocks for swing trading
SP500_TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'UNH', 'JNJ',
    'XOM', 'V', 'JPM', 'PG', 'MA', 'HD', 'CVX', 'LLY', 'MRK', 'ABBV',
    'PEP', 'KO', 'COST', 'AVGO', 'WMT', 'CSCO', 'MCD', 'TMO', 'ACN', 'ADBE',
    'DHR', 'CMCSA', 'NKE', 'NFLX', 'AMD', 'VZ', 'NEE', 'TXN', 'PM', 'WFC',
    'BMY', 'RTX', 'HON', 'QCOM', 'T', 'LIN', 'ORCL', 'UPS', 'LOW', 'SBUX',
    'AMGN', 'CAT', 'IBM', 'MS', 'DE', 'INTC', 'GS', 'AMAT', 'BLK', 'AXP',
    'BA', 'NOW', 'INTU', 'GE', 'PLD', 'COP', 'C', 'MDT', 'ISRG', 'SYK',
    'ELV', 'BKNG', 'ADI', 'LMT', 'TJX', 'SPGI', 'MDLZ', 'GILD', 'MMC', 'VRTX',
    'ZTS', 'REGN', 'SCHW', 'CVS', 'CI', 'CME', 'MO', 'SLB', 'PANW', 'F',
    'SO', 'EOG', 'ETN', 'APD', 'DUK', 'USB', 'PNC', 'CARR', 'CB', 'WM',
    'ICE', 'MMC', 'TGT', 'CL', 'NSC', 'AON', 'ITW', 'ECL', 'SHW', 'KLAC',
    'PSA', 'MCO', 'PFE', 'EMR', 'NOC', 'HCA', 'CSX', 'MAR', 'AIG', 'ALL'
]

POPULAR_STOCKS = SP500_TICKERS[:30]


class TechnicalAnalyzer:
    """Technical analysis class with various indicators"""
    
    @staticmethod
    def calculate_sma(data, window):
        """Simple Moving Average"""
        return data['Close'].rolling(window=window).mean()
    
    @staticmethod
    def calculate_ema(data, window):
        """Exponential Moving Average"""
        return data['Close'].ewm(span=window, adjust=False).mean()
    
    @staticmethod
    def calculate_rsi(data, period=14):
        """Relative Strength Index"""
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    @staticmethod
    def calculate_macd(data, fast=12, slow=26, signal=9):
        """MACD (Moving Average Convergence Divergence)"""
        exp1 = data['Close'].ewm(span=fast, adjust=False).mean()
        exp2 = data['Close'].ewm(span=slow, adjust=False).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        histogram = macd - signal_line
        return macd, signal_line, histogram
    
    @staticmethod
    def calculate_bollinger_bands(data, window=20, num_std=2):
        """Bollinger Bands"""
        sma = data['Close'].rolling(window=window).mean()
        std = data['Close'].rolling(window=window).std()
        upper_band = sma + (std * num_std)
        lower_band = sma - (std * num_std)
        return upper_band, sma, lower_band
    
    @staticmethod
    def calculate_atr(data, period=14):
        """Average True Range for volatility"""
        high_low = data['High'] - data['Low']
        high_close = np.abs(data['High'] - data['Close'].shift())
        low_close = np.abs(data['Low'] - data['Close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(period).mean()
        return atr
    
    @staticmethod
    def calculate_stochastic(data, k_period=14, d_period=3):
        """Stochastic Oscillator"""
        lowest_low = data['Low'].rolling(window=k_period).min()
        highest_high = data['High'].rolling(window=k_period).max()
        k_percent = 100 * ((data['Close'] - lowest_low) / (highest_high - lowest_low))
        d_percent = k_percent.rolling(window=d_period).mean()
        return k_percent, d_percent

    @staticmethod
    def calculate_avwap(data, anchor_days=20, anchor_date=None):
        """Anchored Volume Weighted Average Price (AVWAP)
        
        Calculates VWAP starting from a specific anchor point.
        Args:
            data: DataFrame with OHLCV data
            anchor_days: Number of days to look back (if anchor_date not provided)
            anchor_date: Specific datetime to anchor to (e.g., from earnings date)
        Useful for swing trading to identify institutional fair value since that anchor.
        """
        if len(data) < 10:
            return pd.Series(np.nan, index=data.index)
        
        typical_price = (data['High'] + data['Low'] + data['Close']) / 3
        pv = typical_price * data['Volume']
        
        if anchor_date is not None:
            start_idx = data.index.get_indexer([pd.Timestamp(anchor_date)], method='ffill')[0]
            if start_idx < 0:
                start_idx = 0
        else:
            start_idx = len(data) - anchor_days
            if start_idx < 0:
                start_idx = 0
        
        if start_idx >= len(data):
            return pd.Series(np.nan, index=data.index)
        
        cumulative_pv = pv.iloc[start_idx:].cumsum()
        cumulative_vol = data['Volume'].iloc[start_idx:].cumsum()
        
        avwap = cumulative_pv / cumulative_vol
        
        result = pd.Series(np.nan, index=data.index)
        result.iloc[start_idx:] = avwap
        return result


class MarketScanner:
    """Dynamic market scanner to find trading opportunities"""
    
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.ta = TechnicalAnalyzer()
    
    def get_sp500_tickers(self, limit=100):
        """Get S&P 500 tickers (subset)"""
        return SP500_TICKERS[:limit]
    
    def get_top_movers(self, tickers=None, top_n=20):
        """Find stocks with highest price movement and volume"""
        if tickers is None:
            tickers = SP500_TICKERS[:100]
        
        movers = []
        print(f"\nScanning {len(tickers)} stocks for top movers...")
        
        for i, symbol in enumerate(tickers):
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{len(tickers)}")
            
            try:
                data = self.analyzer.fetch_data(symbol)
                if data is None or len(data) < 20:
                    continue
                
                latest = data.iloc[-1]
                prev = data.iloc[-2]
                prev_5 = data.iloc[-6] if len(data) >= 6 else data.iloc[0]
                vol_sma = data['Volume'].rolling(20).mean().iloc[-1]
                
                price_change_1d = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
                price_change_5d = ((latest['Close'] - prev_5['Close']) / prev_5['Close']) * 100
                volume_ratio = latest['Volume'] / vol_sma if vol_sma > 0 else 1
                
                atr = self.ta.calculate_atr(data).iloc[-1]
                volatility = (atr / latest['Close']) * 100 if latest['Close'] > 0 else 0
                
                movers.append({
                    'symbol': symbol,
                    'price': latest['Close'],
                    'change_1d': price_change_1d,
                    'change_5d': price_change_5d,
                    'volume_ratio': volume_ratio,
                    'volatility': volatility
                })
            except Exception:
                continue
        
        movers.sort(key=lambda x: abs(x['change_1d']), reverse=True)
        return movers[:top_n]
    
    def screen_technicals(self, tickers=None, top_n=20,
                          rsi_oversold=True, rsi_overbought=False,
                          min_volume_ratio=1.5, price_above_sma50=None):
        """Screen stocks based on technical criteria"""
        if tickers is None:
            tickers = SP500_TICKERS[:100]
        
        candidates = []
        print(f"\nScreening {len(tickers)} stocks for technical setups...")
        
        for i, symbol in enumerate(tickers):
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{len(tickers)}")
            
            try:
                data = self.analyzer.fetch_data(symbol)
                if data is None or len(data) < 50:
                    continue
                
                latest = data.iloc[-1]
                
                rsi = self.ta.calculate_rsi(data).iloc[-1]
                sma_20 = self.ta.calculate_sma(data, 20).iloc[-1]
                sma_50 = self.ta.calculate_sma(data, 50).iloc[-1]
                vol_sma = data['Volume'].rolling(20).mean().iloc[-1]
                volume_ratio = latest['Volume'] / vol_sma if vol_sma > 0 else 1
                
                avwap = self.ta.calculate_avwap(data, anchor_days=20).iloc[-1]
                
                macd, signal, _ = self.ta.calculate_macd(data)
                macd_latest = macd.iloc[-1]
                signal_latest = signal.iloc[-1]
                
                reasons = []
                score = 0
                
                if rsi_oversold and pd.notna(rsi) and rsi < 30:
                    reasons.append('RSI Oversold')
                    score += 2
                elif rsi_overbought and pd.notna(rsi) and rsi > 70:
                    reasons.append('RSI Overbought')
                    score -= 2
                
                if volume_ratio >= min_volume_ratio:
                    reasons.append(f'High Volume ({volume_ratio:.1f}x)')
                    score += 1
                
                if pd.notna(sma_20) and pd.notna(sma_50):
                    if price_above_sma50 is True and latest['Close'] > sma_50:
                        reasons.append('Price > SMA50')
                        score += 1
                    elif price_above_sma50 is False and latest['Close'] < sma_50:
                        reasons.append('Price < SMA50')
                        score -= 1
                
                if pd.notna(macd_latest) and pd.notna(signal_latest):
                    if macd_latest > signal_latest and macd.iloc[-2] <= signal.iloc[-2]:
                        reasons.append('MACD Bullish Cross')
                        score += 2
                
                if pd.notna(avwap):
                    if latest['Close'] > avwap:
                        reasons.append('Above AVWAP')
                        score += 1
                    elif latest['Close'] < avwap:
                        reasons.append('Below AVWAP')
                        score -= 1
                
                if reasons:
                    candidates.append({
                        'symbol': symbol,
                        'price': latest['Close'],
                        'rsi': rsi if pd.notna(rsi) else 50,
                        'volume_ratio': volume_ratio,
                        'sma_20': sma_20,
                        'sma_50': sma_50,
                        'avwap': avwap if pd.notna(avwap) else None,
                        'reasons': reasons,
                        'score': score
                    })
            except Exception:
                continue
        
        candidates.sort(key=lambda x: abs(x['score']), reverse=True)
        return candidates[:top_n]
    
    def full_market_scan(self, top_n=15):
        """Comprehensive market scan combining all methods"""
        print("\n" + "=" * 60)
        print("FULL MARKET SCAN")
        print("=" * 60)
        
        movers = self.get_top_movers(top_n=30)
        technicals = self.screen_technicals(top_n=30)
        
        mover_symbols = {m['symbol'] for m in movers}
        tech_symbols = {t['symbol'] for t in technicals}
        all_symbols = list(mover_symbols | tech_symbols)
        
        print(f"\nFound {len(movers)} top movers and {len(technicals)} technical setups")
        print(f"Total unique candidates: {len(all_symbols)}")
        
        return {
            'top_movers': movers[:top_n],
            'technical_setups': technicals[:top_n],
            'all_symbols': all_symbols[:top_n * 2]
        }
    
    def display_scan_results(self, results):
        """Display formatted scan results"""
        print("\n" + "=" * 80)
        print("SCAN RESULTS")
        print("=" * 80)
        
        print("\nTOP MOVERS (by price change):")
        print(f"{'Symbol':<8} {'Price':<10} {'1D Chg%':<10} {'5D Chg%':<10} {'Vol Ratio':<10}")
        print("-" * 60)
        for m in results['top_movers'][:10]:
            chg_1d = f"{m['change_1d']:+.2f}%" if m['change_1d'] else "N/A"
            chg_5d = f"{m['change_5d']:+.2f}%" if m['change_5d'] else "N/A"
            print(f"{m['symbol']:<8} ${m['price']:<9.2f} {chg_1d:<10} {chg_5d:<10} {m['volume_ratio']:.1f}x")
        
        print("\n" + "-" * 80)
        print("TECHNICAL SETUPS:")
        print(f"{'Symbol':<8} {'Price':<10} {'RSI':<8} {'Reasons'}")
        print("-" * 60)
        for t in results['technical_setups'][:10]:
            reasons_str = ', '.join(t['reasons'][:3])
            print(f"{t['symbol']:<8} ${t['price']:<9.2f} {t['rsi']:<8.1f} {reasons_str}")
        
        print("=" * 80)


class SwingTradingStrategy:
    """Swing trading strategy implementation"""
    
    def __init__(self, data, earnings_date=None):
        self.data = data.copy()
        self.ta = TechnicalAnalyzer()
        self.earnings_date = earnings_date
        self._calculate_all_indicators()
    
    def _calculate_all_indicators(self):
        """Calculate all technical indicators"""
        # Moving Averages
        self.data['EMA_20'] = self.ta.calculate_ema(self.data, 20)
        self.data['EMA_50'] = self.ta.calculate_ema(self.data, 50)
        self.data['EMA_12'] = self.ta.calculate_ema(self.data, 12)
        self.data['EMA_26'] = self.ta.calculate_ema(self.data, 26)
        
        # RSI
        self.data['RSI'] = self.ta.calculate_rsi(self.data)
        
        # MACD
        self.data['MACD'], self.data['MACD_Signal'], self.data['MACD_Histogram'] = \
            self.ta.calculate_macd(self.data)
        
        # Bollinger Bands
        self.data['BB_Upper'], self.data['BB_Middle'], self.data['BB_Lower'] = \
            self.ta.calculate_bollinger_bands(self.data)
        
        # ATR
        self.data['ATR'] = self.ta.calculate_atr(self.data)
        
        # Stochastic
        self.data['Stoch_K'], self.data['Stoch_D'] = self.ta.calculate_stochastic(self.data)
        
        # AVWAP (Anchored VWAP)
        self.data['AVWAP_20'] = self.ta.calculate_avwap(self.data, anchor_days=20)
        self.data['AVWAP_50'] = self.ta.calculate_avwap(self.data, anchor_days=50)
        
        if self.earnings_date is not None:
            self.data['AVWAP_Earnings'] = self.ta.calculate_avwap(
                self.data, anchor_date=self.earnings_date
            )
        
        # Volume analysis
        self.data['Volume_SMA'] = self.data['Volume'].rolling(window=20).mean()
    
    def generate_signals(self):
        """Generate buy/sell signals based on multiple strategies"""
        signals = []
        
        for i in range(1, len(self.data)):
            current = self.data.iloc[i]
            previous = self.data.iloc[i-1]
            
            signal = {
                'date': self.data.index[i],
                'price': current['Close'],
                'signals': [],
                'strength': 0
            }
            
            # Strategy 1: Moving Average Crossover
            if previous['EMA_20'] <= previous['EMA_50'] and current['EMA_20'] > current['EMA_50']:
                signal['signals'].append('Golden Cross (EMA Bullish)')
                signal['strength'] += 2
            elif previous['EMA_20'] >= previous['EMA_50'] and current['EMA_20'] < current['EMA_50']:
                signal['signals'].append('Death Cross (EMA Bearish)')
                signal['strength'] -= 2
            
            # Strategy 2: RSI Overbought/Oversold
            if current['RSI'] < 30 and previous['RSI'] >= 30:
                signal['signals'].append('RSI Oversold (Bullish)')
                signal['strength'] += 1
            elif current['RSI'] > 70 and previous['RSI'] <= 70:
                signal['signals'].append('RSI Overbought (Bearish)')
                signal['strength'] -= 1
            
            # Strategy 3: MACD Crossover
            if previous['MACD'] <= previous['MACD_Signal'] and current['MACD'] > current['MACD_Signal']:
                signal['signals'].append('MACD Bullish Crossover')
                signal['strength'] += 2
            elif previous['MACD'] >= previous['MACD_Signal'] and current['MACD'] < current['MACD_Signal']:
                signal['signals'].append('MACD Bearish Crossover')
                signal['strength'] -= 2
            
            # Strategy 4: Bollinger Band Reversal
            if current['Close'] < current['BB_Lower']:
                signal['signals'].append('Below Lower BB (Potential Buy)')
                signal['strength'] += 1
            elif current['Close'] > current['BB_Upper']:
                signal['signals'].append('Above Upper BB (Potential Sell)')
                signal['strength'] -= 1
            
            # Strategy 5: Stochastic
            if current['Stoch_K'] < 20 and current['Stoch_D'] < 20:
                signal['signals'].append('Stochastic Oversold')
                signal['strength'] += 1
            elif current['Stoch_K'] > 80 and current['Stoch_D'] > 80:
                signal['signals'].append('Stochastic Overbought')
                signal['strength'] -= 1
            
            # Volume confirmation
            if current['Volume'] > current['Volume_SMA'] * 1.5:
                signal['signals'].append('High Volume Confirmation')
                if signal['strength'] > 0:
                    signal['strength'] += 1
                elif signal['strength'] < 0:
                    signal['strength'] -= 1
            
            # AVWAP confirmation (20-day anchor)
            if pd.notna(current['AVWAP_20']):
                if current['Close'] > current['AVWAP_20']:
                    signal['signals'].append('Above AVWAP')
                    signal['strength'] += 1
                elif current['Close'] < current['AVWAP_20']:
                    signal['signals'].append('Below AVWAP')
                    signal['strength'] -= 1
            
            if signal['signals']:
                signals.append(signal)
        
        return signals
    
    def get_recommendation(self):
        """Get current trading recommendation"""
        latest = self.data.iloc[-1]
        signals = self.generate_signals()
        
        # Get recent signals (last 5 days)
        recent_signals = [s for s in signals if s['date'] >= self.data.index[-5]]
        
        total_strength = sum(s['strength'] for s in recent_signals)
        
        if total_strength >= 3:
            recommendation = "STRONG BUY"
        elif total_strength >= 1:
            recommendation = "BUY"
        elif total_strength <= -3:
            recommendation = "STRONG SELL"
        elif total_strength <= -1:
            recommendation = "SELL"
        else:
            recommendation = "HOLD/NEUTRAL"
        
        price_levels = self._calculate_price_levels(latest, recommendation)
        
        return {
            'recommendation': recommendation,
            'strength': total_strength,
            'recent_signals': recent_signals[-5:],
            'current_price': latest['Close'],
            'rsi': latest['RSI'],
            'macd': latest['MACD'],
            'ema_20': latest['EMA_20'],
            'ema_50': latest['EMA_50'],
            'bb_position': 'Upper' if latest['Close'] > latest['BB_Upper'] else 
                          'Lower' if latest['Close'] < latest['BB_Lower'] else 'Middle',
            'entry_price': price_levels['entry'],
            'stop_loss': price_levels['stop_loss'],
            'take_profit': price_levels['take_profit'],
            'risk_reward_ratio': price_levels['risk_reward_ratio']
        }
    
    def _calculate_price_levels(self, latest, recommendation):
        """Calculate entry, stop loss, and take profit prices"""
        current_price = latest['Close']
        atr = latest['ATR'] if pd.notna(latest['ATR']) else current_price * 0.025
        
        if recommendation in ["STRONG BUY", "BUY"]:
            entry = current_price
            
            atr_stop = entry - (atr * 2)
            support_stop = min(latest['BB_Lower'], latest['EMA_50']) * 0.98
            stop_loss = max(atr_stop, support_stop)
            
            if stop_loss >= entry:
                stop_loss = entry - (atr * 2)
            
            risk = entry - stop_loss
            take_profit = entry + (risk * 2)
            
        elif recommendation in ["STRONG SELL", "SELL"]:
            entry = current_price
            
            atr_stop = entry + (atr * 2)
            resistance_stop = max(latest['BB_Upper'], latest['EMA_20']) * 1.02
            stop_loss = min(atr_stop, resistance_stop)
            
            if stop_loss <= entry:
                stop_loss = entry + (atr * 2)
            
            risk = stop_loss - entry
            take_profit = entry - (risk * 2)
            
        else:
            entry = current_price
            stop_loss = entry - (atr * 1.5)
            risk = entry - stop_loss
            take_profit = entry + (risk * 1.5)
        
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        risk_reward_ratio = reward / risk if risk > 0 else 0
        
        return {
            'entry': round(entry, 2),
            'stop_loss': round(stop_loss, 2),
            'take_profit': round(take_profit, 2),
            'risk_reward_ratio': round(risk_reward_ratio, 2)
        }


class Backtester:
    """Backtesting engine for swing trading strategies"""
    
    def __init__(self, symbol, period="2y", initial_capital=10000, 
                 position_size_pct=0.25, stop_loss_pct=0.02, take_profit_pct=0.04):
        self.symbol = symbol
        self.period = period
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.data = None
        self.trades = []
        self.equity_curve = []
        self._fetch_data()
    
    def _fetch_data(self):
        """Fetch historical data"""
        try:
            ticker = yf.Ticker(self.symbol)
            self.data = ticker.history(period=self.period, interval="1d")
            if len(self.data) < 100:
                print(f"Insufficient data for {self.symbol}")
                self.data = None
                return
            self._calculate_indicators()
        except Exception as e:
            print(f"Error fetching data: {e}")
            self.data = None
    
    def _calculate_indicators(self):
        """Calculate technical indicators for backtesting"""
        ta = TechnicalAnalyzer()
        self.data['EMA_20'] = ta.calculate_ema(self.data, 20)
        self.data['EMA_50'] = ta.calculate_ema(self.data, 50)
        self.data['RSI'] = ta.calculate_rsi(self.data)
        self.data['MACD'], self.data['MACD_Signal'], _ = ta.calculate_macd(self.data)
        self.data['BB_Upper'], self.data['BB_Middle'], self.data['BB_Lower'] = \
            ta.calculate_bollinger_bands(self.data)
        self.data['ATR'] = ta.calculate_atr(self.data)
    
    def run_backtest(self):
        """Run the backtest simulation"""
        if self.data is None:
            return None
        
        self.trades = []
        self.equity_curve = [self.initial_capital]
        
        capital = self.initial_capital
        position = None
        
        for i in range(50, len(self.data) - 1):
            current = self.data.iloc[i]
            next_data = self.data.iloc[i + 1]
            date = self.data.index[i]
            
            signal = self._generate_signal(i)
            
            if position is None and signal == 'BUY':
                position_value = capital * self.position_size_pct
                shares = position_value / current['Close']  # Allow fractional shares
                if shares >= 0.1:
                    position = {
                        'entry_date': date,
                        'entry_price': current['Close'],
                        'shares': shares,
                        'stop_loss': current['Close'] * (1 - self.stop_loss_pct),
                        'take_profit': current['Close'] * (1 + self.take_profit_pct),
                        'reason': signal
                    }
            
            elif position is not None:
                current_price = current['Close']
                stop_hit = current_price <= position['stop_loss']
                profit_hit = current_price >= position['take_profit']
                sell_signal = signal == 'SELL'
                
                if stop_hit or profit_hit or sell_signal or i == len(self.data) - 2:
                    exit_price = current_price
                    pnl = (exit_price - position['entry_price']) * position['shares']
                    pnl_pct = (exit_price - position['entry_price']) / position['entry_price'] * 100
                    
                    self.trades.append({
                        'entry_date': position['entry_date'],
                        'exit_date': date,
                        'entry_price': position['entry_price'],
                        'exit_price': exit_price,
                        'shares': position['shares'],
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'reason': 'Stop Loss' if stop_hit else ('Take Profit' if profit_hit else signal)
                    })
                    
                    capital += pnl
                    position = None
                
                else:
                    position['stop_loss'] = max(position['stop_loss'], 
                                                current_price * (1 - self.stop_loss_pct))
                    position['take_profit'] = max(position['take_profit'],
                                                  current_price * (1 + self.take_profit_pct))
            
            self.equity_curve.append(capital)
        
        return self._calculate_metrics()
    
    def _generate_signal(self, i):
        """Generate buy/sell signal at index i"""
        if i < 1:
            return 'HOLD'
        
        current = self.data.iloc[i]
        prev = self.data.iloc[i - 1]
        
        buy_score = 0
        sell_score = 0
        
        if pd.notna(current['EMA_20']) and pd.notna(current['EMA_50']):
            if prev['EMA_20'] <= prev['EMA_50'] and current['EMA_20'] > current['EMA_50']:
                buy_score += 2
            elif prev['EMA_20'] >= prev['EMA_50'] and current['EMA_20'] < current['EMA_50']:
                sell_score += 2
        
        if pd.notna(current['RSI']):
            if current['RSI'] < 30:
                buy_score += 1
            elif current['RSI'] > 70:
                sell_score += 1
        
        if pd.notna(current['MACD']) and pd.notna(current['MACD_Signal']):
            if prev['MACD'] <= prev['MACD_Signal'] and current['MACD'] > current['MACD_Signal']:
                buy_score += 2
            elif prev['MACD'] >= prev['MACD_Signal'] and current['MACD'] < current['MACD_Signal']:
                sell_score += 2
        
        if pd.notna(current['BB_Lower']) and pd.notna(current['BB_Upper']):
            if current['Close'] < current['BB_Lower']:
                buy_score += 1
            elif current['Close'] > current['BB_Upper']:
                sell_score += 1
        
        if buy_score >= 2:
            return 'BUY'
        elif sell_score >= 2:
            return 'SELL'
        return 'HOLD'
    
    def _calculate_metrics(self):
        """Calculate performance metrics"""
        if not self.trades:
            return {
                'symbol': self.symbol,
                'period': self.period,
                'initial_capital': self.initial_capital,
                'final_capital': self.equity_curve[-1] if self.equity_curve else self.initial_capital,
                'total_trades': 0,
                'total_return': 0,
                'total_return_pct': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'max_drawdown': 0,
                'sharpe_ratio': 0,
                'trades': [],
                'equity_curve': self.equity_curve
            }
        
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in self.trades)
        win_rate = len(winning_trades) / len(self.trades) * 100
        
        avg_win = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        gross_profit = sum(t['pnl'] for t in winning_trades)
        gross_loss = abs(sum(t['pnl'] for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        equity = np.array(self.equity_curve)
        running_max = np.maximum.accumulate(equity)
        drawdowns = (running_max - equity) / running_max
        max_drawdown = np.max(drawdowns) * 100
        
        returns = np.diff(equity) / equity[:-1]
        sharpe_ratio = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        
        return {
            'symbol': self.symbol,
            'period': self.period,
            'initial_capital': self.initial_capital,
            'final_capital': self.equity_curve[-1],
            'total_trades': len(self.trades),
            'total_return': total_pnl,
            'total_return_pct': (total_pnl / self.initial_capital) * 100,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'trades': self.trades,
            'equity_curve': self.equity_curve
        }
    
    def display_results(self):
        """Display backtest results"""
        results = self.run_backtest()
        if results is None:
            return
        
        print("\n" + "=" * 70)
        print(f"BACKTEST RESULTS: {self.symbol}")
        print("=" * 70)
        print(f"Period: {results['period']}")
        print(f"Initial Capital: ${results['initial_capital']:,.2f}")
        print(f"Final Capital:   ${results['final_capital']:,.2f}")
        print("-" * 70)
        print(f"Total Trades:    {results['total_trades']}")
        print(f"Winning Trades:  {results['winning_trades']}")
        print(f"Losing Trades:   {results['losing_trades']}")
        print(f"Win Rate:        {results['win_rate']:.1f}%")
        print("-" * 70)
        print(f"Total Return:    ${results['total_return']:,.2f} ({results['total_return_pct']:.2f}%)")
        print(f"Average Win:    ${results['avg_win']:,.2f}")
        print(f"Average Loss:   ${results['avg_loss']:,.2f}")
        print(f"Profit Factor:  {results['profit_factor']:.2f}")
        print(f"Max Drawdown:   {results['max_drawdown']:.2f}%")
        print(f"Sharpe Ratio:   {results['sharpe_ratio']:.2f}")
        print("=" * 70)
    
    def plot_results(self, save_path=None):
        """Plot backtest equity curve"""
        results = self.run_backtest()
        if results is None or not results['equity_curve']:
            return
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
        
        dates = self.data.index[50:50+len(results['equity_curve'])]
        
        ax1.plot(dates, results['equity_curve'], linewidth=2, color='blue')
        ax1.axhline(y=self.initial_capital, color='gray', linestyle='--', alpha=0.5)
        ax1.fill_between(dates, self.initial_capital, results['equity_curve'],
                        where=[x >= self.initial_capital for x in results['equity_curve']],
                        color='green', alpha=0.3)
        ax1.fill_between(dates, self.initial_capital, results['equity_curve'],
                        where=[x < self.initial_capital for x in results['equity_curve']],
                        color='red', alpha=0.3)
        ax1.set_title(f'{self.symbol} - Backtest Equity Curve')
        ax1.set_ylabel('Portfolio Value ($)')
        ax1.grid(True, alpha=0.3)
        
        trade_pnls = [t['pnl_pct'] for t in results['trades']]
        colors = ['green' if pnl > 0 else 'red' for pnl in trade_pnls]
        ax2.bar(range(len(trade_pnls)), trade_pnls, color=colors, alpha=0.7)
        ax2.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        ax2.set_title('Trade Returns (%)')
        ax2.set_xlabel('Trade Number')
        ax2.set_ylabel('Return (%)')
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"Chart saved to {save_path}")
        
        plt.show()


class StockAnalyzer:
    """Main stock analyzer class"""
    
    def __init__(self, symbols=None, period="6mo", interval="1d"):
        self.symbols = symbols or POPULAR_STOCKS
        self.period = period
        self.interval = interval
        self.results = {}
    
    def fetch_data(self, symbol):
        """Fetch stock data from Yahoo Finance"""
        import io
        import sys
        try:
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=self.period, interval=self.interval)
            sys.stderr = old_stderr
            if len(data) < 50:
                return None
            return data
        except Exception as e:
            sys.stderr = old_stderr
            print(f"Error fetching {symbol}: {e}")
            return None
    
    def get_earnings_dates(self, symbol, limit=4):
        """Fetch earnings dates from Yahoo Finance"""
        import io
        import sys
        try:
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            ticker = yf.Ticker(symbol)
            earnings = ticker.earnings_dates
            sys.stderr = old_stderr
            if earnings is None or len(earnings) == 0:
                return None
            return earnings.head(limit)
        except Exception:
            sys.stderr = old_stderr
            return None
    
    def analyze_stock(self, symbol, use_earnings_avwap=True):
        """Analyze a single stock"""
        data = self.fetch_data(symbol)
        if data is None:
            return None
        
        earnings_date = None
        if use_earnings_avwap:
            earnings = self.get_earnings_dates(symbol, limit=4)
            if earnings is not None and len(earnings) > 0:
                last_data_date = data.index[-1]
                for dt in earnings.index:
                    if dt <= last_data_date:
                        earnings_date = dt
                        break
        
        strategy = SwingTradingStrategy(data, earnings_date=earnings_date)
        recommendation = strategy.get_recommendation()
        
        return {
            'symbol': symbol,
            'data': data,
            'strategy': strategy,
            'recommendation': recommendation
        }
    
    def analyze_all(self):
        """Analyze all stocks in the list"""
        print(f"\nAnalyzing {len(self.symbols)} stocks...")
        print("=" * 60)
        
        for symbol in self.symbols:
            result = self.analyze_stock(symbol)
            if result:
                self.results[symbol] = result
                print(f"✓ {symbol}: {result['recommendation']['recommendation']}")
        
        print("=" * 60)
        return self.results
    
    def get_best_opportunities(self, top_n=10):
        """Get top trading opportunities"""
        if not self.results:
            self.analyze_all()
        
        # Sort by strength
        sorted_results = sorted(
            self.results.items(),
            key=lambda x: abs(x[1]['recommendation']['strength']),
            reverse=True
        )
        
        return sorted_results[:top_n]
    
    def display_summary(self):
        """Display summary of all analyses"""
        if not self.results:
            self.analyze_all()
        
        sorted_results = sorted(
            self.results.items(),
            key=lambda x: x[1]['recommendation']['strength'],
            reverse=True
        )
        
        print("\n" + "=" * 80)
        print("SWING TRADING ANALYSIS SUMMARY")
        print("=" * 80)
        print(f"{'Symbol':<10} {'Price':<10} {'Rec.':<15} {'Strength':<10} {'RSI':<8} {'Trend':<10}")
        print("-" * 80)
        
        for symbol, result in sorted_results:
            rec = result['recommendation']
            trend = "Bullish" if rec['ema_20'] > rec['ema_50'] else "Bearish"
            
            print(f"{symbol:<10} ${rec['current_price']:<9.2f} {rec['recommendation']:<15} "
                  f"{rec['strength']:<10} {rec['rsi']:<8.1f} {trend:<10}")
        
        print("-" * 80)
    
    def plot_stock(self, symbol, save_path=None):
        """Plot comprehensive chart for a stock"""
        if symbol not in self.results:
            result = self.analyze_stock(symbol)
            if not result:
                print(f"Could not analyze {symbol}")
                return
            self.results[symbol] = result
        
        result = self.results[symbol]
        data = result['strategy'].data
        rec = result['recommendation']
        
        fig = plt.figure(figsize=(16, 12))
        fig.suptitle(f'{symbol} - Swing Trading Analysis\nRecommendation: {rec["recommendation"]}', 
                     fontsize=16, fontweight='bold')
        
        # Create grid
        gs = fig.add_gridspec(4, 1, height_ratios=[3, 1, 1, 1], hspace=0.3)
        
        # Plot 1: Price with Moving Averages and Bollinger Bands
        ax1 = fig.add_subplot(gs[0])
        ax1.plot(data.index, data['Close'], label='Close Price', linewidth=2, color='black')
        ax1.plot(data.index, data['EMA_20'], label='EMA 20', color='blue', alpha=0.7)
        ax1.plot(data.index, data['EMA_50'], label='EMA 50', color='orange', alpha=0.7)
        ax1.fill_between(data.index, data['BB_Upper'], data['BB_Lower'], 
                         alpha=0.2, color='gray', label='Bollinger Bands')
        ax1.set_ylabel('Price ($)')
        ax1.set_title(f'{symbol} - Price Chart')
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        
        # Plot 2: Volume - Green for up days, Red for down days
        ax2 = fig.add_subplot(gs[1], sharex=ax1)
        colors = ['green' if data['Close'].iloc[i] >= data['Close'].iloc[i-1] else 'red' for i in range(1, len(data))]
        ax2.bar(data.index[1:], data['Volume'].iloc[1:], color=colors, alpha=0.6)
        ax2.plot(data.index, data['Volume_SMA'], color='blue', linewidth=1.5, label='Volume SMA 20')
        ax2.set_ylabel('Volume')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # Plot 3: RSI
        ax3 = fig.add_subplot(gs[2], sharex=ax1)
        ax3.plot(data.index, data['RSI'], color='purple', linewidth=2)
        ax3.axhline(y=70, color='r', linestyle='--', alpha=0.5, label='Overbought (70)')
        ax3.axhline(y=30, color='g', linestyle='--', alpha=0.5, label='Oversold (30)')
        ax3.fill_between(data.index, 70, 100, alpha=0.1, color='red')
        ax3.fill_between(data.index, 0, 30, alpha=0.1, color='green')
        ax3.set_ylabel('RSI')
        ax3.set_ylim(0, 100)
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        
        # Plot 4: MACD - Green for positive histogram, Red for negative
        ax4 = fig.add_subplot(gs[3], sharex=ax1)
        ax4.plot(data.index, data['MACD'], label='MACD', color='blue')
        ax4.plot(data.index, data['MACD_Signal'], label='Signal', color='red')
        hist_colors = ['green' if val >= 0 else 'red' for val in data['MACD_Histogram']]
        ax4.bar(data.index, data['MACD_Histogram'], label='Histogram', color=hist_colors, alpha=0.7)
        ax4.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        ax4.set_ylabel('MACD')
        ax4.set_xlabel('Date')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        plt.setp(ax1.get_xticklabels(), visible=False)
        plt.setp(ax2.get_xticklabels(), visible=False)
        plt.setp(ax3.get_xticklabels(), visible=False)
        
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"Chart saved to {save_path}")
        
        plt.show()
    
    def print_detailed_report(self, symbol):
        """Print detailed analysis report for a stock"""
        if symbol not in self.results:
            result = self.analyze_stock(symbol)
            if not result:
                print(f"Could not analyze {symbol}")
                return
            self.results[symbol] = result
        
        result = self.results[symbol]
        rec = result['recommendation']
        
        print("\n" + "=" * 80)
        print(f"DETAILED ANALYSIS REPORT: {symbol}")
        print("=" * 80)
        print(f"Current Price: ${rec['current_price']:.2f}")
        print(f"Recommendation: {rec['recommendation']}")
        print(f"Signal Strength: {rec['strength']}")
        print(f"\nPRICE TARGETS:")
        print(f"  Entry Price:   ${rec['entry_price']:.2f}")
        print(f"  Stop Loss:     ${rec['stop_loss']:.2f}")
        print(f"  Take Profit:   ${rec['take_profit']:.2f}")
        print(f"  Risk/Reward:   1:{rec['risk_reward_ratio']:.1f}")
        print(f"\nTechnical Indicators:")
        print(f"  RSI (14): {rec['rsi']:.2f}")
        print(f"  MACD: {rec['macd']:.4f}")
        print(f"  EMA 20: ${rec['ema_20']:.2f}")
        print(f"  EMA 50: ${rec['ema_50']:.2f}")
        print(f"  Bollinger Position: {rec['bb_position']}")
        
        print(f"\nRecent Trading Signals:")
        if rec['recent_signals']:
            for signal in rec['recent_signals']:
                print(f"  - {signal['date'].strftime('%Y-%m-%d')}: {', '.join(signal['signals'])}")
        else:
            print("  No recent signals")
        
        print(f"\nTrading Strategy Recommendations:")
        self._print_strategy_advice(rec)
        print("=" * 80)
    
    def get_ai_analysis(self, symbol, risk_tolerance='moderate'):
        """Get AI-powered analysis using Gemini"""
        if not GEMINI_AVAILABLE:
            print("\n⚠ Gemini AI integration not available.")
            print("   Install with: pip install google-generativeai")
            return None
        
        if not is_gemini_available():
            print("\n⚠ Gemini API key not configured.")
            print("   Set GEMINI_API_KEY environment variable.")
            print("   See GEMINI_SETUP.md for instructions.")
            return None
        
        if symbol not in self.results:
            print(f"\nAnalyzing {symbol}...")
            result = self.analyze_stock(symbol)
            if not result:
                print(f"Could not analyze {symbol}")
                return None
            self.results[symbol] = result
        
        result = self.results[symbol]
        
        try:
            gemini = GeminiAnalyzer()
            print(f"\n🤖 Getting AI analysis for {symbol}...")
            
            # Get main analysis
            ai_result = gemini.analyze_stock(
                symbol,
                result['strategy'].data,
                result['recommendation']
            )
            
            if ai_result['success']:
                print("\n" + "=" * 80)
                print(f"AI ANALYSIS FOR {symbol}")
                print("=" * 80)
                print(ai_result['ai_analysis'])
                
                # Get strategy advice
                strategy_result = gemini.get_strategy_advice(
                    symbol,
                    result['strategy'].data,
                    result['recommendation'],
                    risk_tolerance=risk_tolerance
                )
                
                if strategy_result['success']:
                    print("\n" + "-" * 80)
                    print(f"STRATEGY RECOMMENDATION ({risk_tolerance.upper()} RISK)")
                    print("-" * 80)
                    print(strategy_result['strategy'])
                
                print("=" * 80)
                return ai_result
            else:
                print(f"❌ AI analysis failed: {ai_result['error']}")
                return None
                
        except Exception as e:
            print(f"❌ Error getting AI analysis: {e}")
            return None
    
    def get_ai_comparison(self, top_n=5):
        """Get AI comparison of top opportunities"""
        if not GEMINI_AVAILABLE:
            print("\n⚠ Gemini AI integration not available.")
            return None
        
        if not is_gemini_available():
            print("\n⚠ Gemini API key not configured.")
            print("   Set GEMINI_API_KEY environment variable.")
            return None
        
        if not self.results:
            self.analyze_all()
        
        opportunities = self.get_best_opportunities(top_n)
        
        analyses = []
        for symbol, result in opportunities:
            analyses.append({
                'symbol': symbol,
                'data': result['strategy'].data,
                'recommendation': result['recommendation']
            })
        
        try:
            gemini = GeminiAnalyzer()
            print(f"\n🤖 Getting AI comparison of top {top_n} opportunities...")
            
            comparison = gemini.analyze_multiple(analyses)
            
            if comparison['success']:
                print("\n" + "=" * 80)
                print("AI COMPARISON & RANKING")
                print("=" * 80)
                print(comparison['ai_comparison'])
                print("=" * 80)
                return comparison
            else:
                print(f"❌ AI comparison failed: {comparison['error']}")
                return None
                
        except Exception as e:
            print(f"❌ Error getting AI comparison: {e}")
            return None
    
    def _print_strategy_advice(self, rec):
        """Print specific trading advice based on recommendation"""
        if rec['recommendation'] in ["STRONG BUY", "BUY"]:
            print("  ✓ Consider entering a LONG position")
            print(f"  ✓ Entry Price: ${rec['entry_price']:.2f}")
            print(f"  ✓ Stop Loss: ${rec['stop_loss']:.2f}")
            print(f"  ✓ Take Profit: ${rec['take_profit']:.2f}")
            print(f"  ✓ Risk/Reward Ratio: 1:{rec['risk_reward_ratio']:.1f}")
            
            if rec['bb_position'] == 'Lower':
                print("  ✓ Stock is at lower Bollinger Band - good entry point")
            if rec['rsi'] < 40:
                print("  ✓ RSI suggests oversold conditions - bullish reversal possible")
                
        elif rec['recommendation'] in ["STRONG SELL", "SELL"]:
            print("  ⚠ Consider entering a SHORT position or exiting longs")
            print(f"  ⚠ Entry Price: ${rec['entry_price']:.2f}")
            print(f"  ⚠ Stop Loss: ${rec['stop_loss']:.2f}")
            print(f"  ⚠ Take Profit: ${rec['take_profit']:.2f}")
            print(f"  ⚠ Risk/Reward Ratio: 1:{rec['risk_reward_ratio']:.1f}")
            
            if rec['bb_position'] == 'Upper':
                print("  ⚠ Stock is at upper Bollinger Band - potential reversal")
            if rec['rsi'] > 60:
                print("  ⚠ RSI suggests overbought conditions - bearish reversal possible")
                
        else:
            print("  ➜ No clear directional signal")
            print(f"  ➜ Entry Price: ${rec['entry_price']:.2f}")
            print(f"  ➜ Stop Loss: ${rec['stop_loss']:.2f}")
            print(f"  ➜ Take Profit: ${rec['take_profit']:.2f}")
            print(f"  ➜ Risk/Reward Ratio: 1:{rec['risk_reward_ratio']:.1f}")
            print("  ➜ Wait for clearer setup or range trade between support/resistance")


def main():
    """Main function to run the analyzer"""
    print("\n" + "=" * 80)
    print("SWING TRADING STOCK ANALYZER")
    print("=" * 80)
    print("\nThis program analyzes popular stocks using technical indicators")
    print("to provide swing trading recommendations.")
    print("\nIndicators used:")
    print("  - Exponential Moving Averages (EMA 20, 50)")
    print("  - RSI (Relative Strength Index)")
    print("  - MACD (Moving Average Convergence Divergence)")
    print("  - Bollinger Bands")
    print("  - Stochastic Oscillator")
    print("  - Volume Analysis")
    print("\n" + "=" * 80)
    
    # Initialize analyzer
    analyzer = StockAnalyzer(period="1mo", interval="30m")
    
    # Analyze all stocks
    analyzer.analyze_all()
    
    # Display summary
    analyzer.display_summary()
    
    # Get best opportunities
    print("\n" + "=" * 80)
    print("TOP 10 TRADING OPPORTUNITIES")
    print("=" * 80)
    print(f"{'#':<3} {'Symbol':<8} {'Rec.':<14} {'Entry':<10} {'Stop Loss':<10} {'Take Profit':<12} {'R:R':<6}")
    print("-" * 80)
    opportunities = analyzer.get_best_opportunities(10)
    
    for i, (symbol, result) in enumerate(opportunities, 1):
        rec = result['recommendation']
        print(f"{i:<3} {symbol:<8} {rec['recommendation']:<14} ${rec['entry_price']:<9.2f} "
              f"${rec['stop_loss']:<9.2f} ${rec['take_profit']:<11.2f} 1:{rec['risk_reward_ratio']:.1f}")
    
    # Interactive menu
    scanner = MarketScanner(analyzer)
    
    while True:
        print("\n" + "=" * 80)
        print("OPTIONS:")
        print("  1. View detailed report for a stock")
        print("  2. Plot chart for a stock")
        print("  3. View all recommendations")
        print("  4. Refresh analysis")
        print("  5. Scan market for new opportunities")
        print("  6. Backtest a stock strategy")
        if GEMINI_AVAILABLE and is_gemini_available():
            print("  7. Get AI analysis for a stock")
            print("  8. Get AI comparison of top opportunities")
            print("  9. Exit")
            max_choice = '9'
        else:
            print("  7. Exit")
            max_choice = '7'
            if not GEMINI_AVAILABLE:
                print("\n  💡 Install 'google-generativeai' for AI features")
            elif not is_gemini_available():
                print("\n  💡 Set GEMINI_API_KEY env var for AI features")
        print("=" * 80)
        
        choice = input(f"\nEnter your choice (1-{max_choice}): ").strip()
        
        if choice == '1':
            symbol = input("Enter stock symbol (e.g., AAPL): ").strip().upper()
            if symbol in analyzer.results:
                analyzer.print_detailed_report(symbol)
            else:
                print(f"Symbol {symbol} not in analyzed list. Analyzing now...")
                analyzer.print_detailed_report(symbol)
                
        elif choice == '2':
            symbol = input("Enter stock symbol (e.g., AAPL): ").strip().upper()
            save = input("Save chart to file? (y/n): ").strip().lower() == 'y'
            save_path = None
            if save:
                save_path = input("Enter filename (e.g., chart.png): ").strip()
            analyzer.plot_stock(symbol, save_path)
            
        elif choice == '3':
            analyzer.display_summary()
            
        elif choice == '4':
            print("Refreshing analysis...")
            analyzer.results = {}
            analyzer.analyze_all()
            analyzer.display_summary()
            
        elif choice == '5':
            print("\nMarket Scanner Options:")
            print("  1. Full market scan (recommended)")
            print("  2. Top movers only")
            print("  3. Technical setups only")
            scan_choice = input("Select scan type (1-3, default=1): ").strip() or '1'
            
            if scan_choice == '1':
                results = scanner.full_market_scan(top_n=15)
            elif scan_choice == '2':
                results = {'top_movers': scanner.get_top_movers(top_n=20), 'technical_setups': [], 'all_symbols': []}
            elif scan_choice == '3':
                results = {'top_movers': [], 'technical_setups': scanner.screen_technicals(top_n=20), 'all_symbols': []}
            else:
                results = scanner.full_market_scan(top_n=15)
            
            scanner.display_scan_results(results)
            
            if results['all_symbols']:
                analyze_new = input("\nAnalyze these stocks with full strategy? (y/n): default=y").strip().lower() or 'y'
                if analyze_new == 'y':
                    analyzer.symbols = results['all_symbols']
                    analyzer.analyze_all()
                    analyzer.display_summary()
        
        elif choice == '6':
            print("\n--- Backtest Strategy ---")
            symbol = input("Enter stock symbol (e.g., AAPL): ").strip().upper()
            period = input("Backtest period (default 2y): ").strip() or "2y"
            capital = int(input("Initial capital (default 10000): ").strip() or "10000")
            
            backtester = Backtester(symbol, period=period, initial_capital=capital)
            backtester.display_results()
            
            plot = input("\nPlot equity curve? (y/n): ").strip().lower() == 'y'
            if plot:
                save = input("Save to file? (y/n): ").strip().lower() == 'y'
                save_path = f"{symbol}_backtest.png" if save else None
                backtester.plot_results(save_path)
            
            if GEMINI_AVAILABLE and is_gemini_available():
                choice = '8'
            else:
                print("\nHappy trading! Remember: Always do your own research.")
                break
                
        elif choice == '7' and GEMINI_AVAILABLE and is_gemini_available():
            symbol = input("Enter stock symbol (e.g., AAPL): ").strip().upper()
            print("\nRisk tolerance levels:")
            print("  1. Conservative")
            print("  2. Moderate")
            print("  3. Aggressive")
            risk_choice = input("Select risk level (1-3, default=2): ").strip() or '2'
            risk_map = {'1': 'conservative', '2': 'moderate', '3': 'aggressive'}
            risk_tolerance = risk_map.get(risk_choice, 'moderate')
            analyzer.get_ai_analysis(symbol, risk_tolerance)
            
        elif choice == '8' and GEMINI_AVAILABLE and is_gemini_available():
            top_n = input("Number of top opportunities to compare (default=5): ").strip() or '5'
            try:
                top_n = int(top_n)
                analyzer.get_ai_comparison(top_n)
            except ValueError:
                print("Invalid number. Using default of 5.")
                analyzer.get_ai_comparison(5)
                
        elif choice == '9' and GEMINI_AVAILABLE and is_gemini_available():
            print("\nHappy trading! Remember: Always do your own research.")
            break
            
        elif choice == '8' and (not GEMINI_AVAILABLE or not is_gemini_available()):
            print("\nHappy trading! Remember: Always do your own research.")
            break
            
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()
