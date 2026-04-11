"""
Gemini AI Integration for Stock Analysis
Uses Google's Gemini API to provide AI-powered trading insights
"""

import os
import json
from typing import Dict, List, Optional
import pandas as pd
import google.generativeai as genai

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class GeminiAnalyzer:
    """AI-powered stock analysis using Gemini"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Gemini analyzer
        
        Args:
            api_key: Gemini API key (defaults to GEMINI_API_KEY env variable)
        """
        self.api_key = api_key or os.getenv('GEMINI_API_KEY')
        if not self.api_key:
            raise ValueError(
                "Gemini API key required. Set GEMINI_API_KEY environment variable "
                "or pass api_key parameter."
            )
        
        genai.configure(api_key=self.api_key)
        # Model names for google-generativeai SDK
        self.model_names = [
            'gemini-3.1-pro-preview',
            'gemini-3-flash-preview'
      #      'gemini-2.5-flash',
      #      'gemini-2.5-flash-latest',
      #      'gemini-1.5-flash-latest',
      #      'gemini-1.5-pro-latest', 
      #      'gemini-1.0-pro-latest',
      #      'gemini-pro'
        ]
        self.model = None
        self.current_model_name = None
        
        # Try to find a working model
        for model_name in self.model_names:
            try:
                test_model = genai.GenerativeModel(model_name)
                # Quick test
                test_model.generate_content("hi", generation_config={'max_output_tokens': 1})
                self.model = test_model
                self.current_model_name = model_name
                print(f"✓ Using Gemini model: {model_name}")
                break
            except Exception as e:
                error_msg = str(e)
                if "404" in error_msg or "not found" in error_msg.lower():
                    continue
                elif "permission" in error_msg.lower() or "denied" in error_msg.lower():
                    print(f"✗ Permission denied for {model_name}")
                    continue
                else:
                    # Different error, re-raise
                    raise
        
        if not self.model:
            raise ValueError(
                f"Could not find a valid Gemini model with your API key. "
                f"Tried: {', '.join(self.model_names)}. "
                f"Your API key may need Gemini API access enabled. "
                f"Visit https://ai.google.dev/ to enable access."
            )
    
    def format_stock_data(self, symbol: str, data, recommendation: Dict) -> str:
        """Format stock data for Gemini analysis"""
        latest = data.iloc[-1]
        prev = data.iloc[-2] if len(data) > 1 else latest
        
        # Calculate some additional metrics
        price_change_1d = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        price_change_5d = ((latest['Close'] - data.iloc[-5]['Close']) / data.iloc[-5]['Close']) * 100 if len(data) >= 5 else 0
        price_change_20d = ((latest['Close'] - data.iloc[-20]['Close']) / data.iloc[-20]['Close']) * 100 if len(data) >= 20 else 0
        
        volatility = data['Close'].pct_change().std() * 100
        
        # Format recent signals
        recent_signals_text = "\n".join([
            f"  - {s['date'].strftime('%Y-%m-%d')}: {', '.join(s['signals'])} (Strength: {s['strength']:+d})"
            for s in recommendation.get('recent_signals', [])
        ]) or "  No recent signals"
        
        entry = recommendation.get('entry_price', latest['Close'])
        stop_loss = recommendation.get('stop_loss', latest['Close'])
        take_profit = recommendation.get('take_profit', latest['Close'])
        risk_reward = recommendation.get('risk_reward_ratio', 0)
        
        # AVWAP data
        avwap_20 = data['AVWAP_20'].iloc[-1] if 'AVWAP_20' in data.columns and pd.notna(data['AVWAP_20'].iloc[-1]) else None
        avwap_50 = data['AVWAP_50'].iloc[-1] if 'AVWAP_50' in data.columns and pd.notna(data['AVWAP_50'].iloc[-1]) else None
        avwap_earnings = data['AVWAP_Earnings'].iloc[-1] if 'AVWAP_Earnings' in data.columns and pd.notna(data['AVWAP_Earnings'].iloc[-1]) else None
        
        avwap_20_pct = ((latest['Close'] / avwap_20 - 1) * 100) if avwap_20 else 0
        avwap_50_pct = ((latest['Close'] / avwap_50 - 1) * 100) if avwap_50 else 0
        avwap_earnings_pct = ((latest['Close'] / avwap_earnings - 1) * 100) if avwap_earnings else 0
        
        avwap_20_status = 'Above' if avwap_20 and latest['Close'] > avwap_20 else 'Below' if avwap_20 else 'N/A'
        avwap_50_status = 'Above' if avwap_50 and latest['Close'] > avwap_50 else 'Below' if avwap_50 else 'N/A'
        avwap_earnings_status = 'Above' if avwap_earnings and latest['Close'] > avwap_earnings else 'Below' if avwap_earnings else 'N/A'
        
        avwap_20_str = f"${avwap_20:.2f} ({avwap_20_status}, {avwap_20_pct:+.2f}%)" if avwap_20 else "N/A"
        avwap_50_str = f"${avwap_50:.2f} ({avwap_50_status}, {avwap_50_pct:+.2f}%)" if avwap_50 else "N/A"
        avwap_earnings_str = f"${avwap_earnings:.2f} ({avwap_earnings_status}, {avwap_earnings_pct:+.2f}%)" if avwap_earnings else "N/A"
        
        prompt = f"""Analyze this stock data for swing trading opportunities:

STOCK: {symbol}
CURRENT PRICE: ${latest['Close']:.2f}

PRICE ACTION:
- 1-Day Change: {price_change_1d:+.2f}%
- 5-Day Change: {price_change_5d:+.2f}%
- 20-Day Change: {price_change_20d:+.2f}%
- Volatility (20d): {volatility:.2f}%

TECHNICAL INDICATORS:
- RSI (14): {recommendation['rsi']:.2f} (30=oversold, 70=overbought)
- MACD: {recommendation['macd']:.4f}
- EMA 20: ${recommendation['ema_20']:.2f}
- EMA 50: ${recommendation['ema_50']:.2f}
- Bollinger Position: {recommendation['bb_position']}
- Current vs EMA 20: {'Above' if latest['Close'] > recommendation['ema_20'] else 'Below'} ({((latest['Close']/recommendation['ema_20']-1)*100):+.2f}%)
- Current vs EMA 50: {'Above' if latest['Close'] > recommendation['ema_50'] else 'Below'} ({((latest['Close']/recommendation['ema_50']-1)*100):+.2f}%)
- Stochastic K: {data['Stoch_K'].iloc[-1]:.2f}
- Stochastic D: {data['Stoch_D'].iloc[-1]:.2f}

AVWAP (Volume-Weighted Average Price - Institutional Fair Value):
- AVWAP 20-day: {avwap_20_str}
- AVWAP 50-day: {avwap_50_str}
- AVWAP Earnings: {avwap_earnings_str}

VOLUME:
- Current Volume: {latest['Volume']:,.0f}
- Volume vs 20d Avg: {(latest['Volume'] / data['Volume_SMA'].iloc[-1]):.2f}x

SYSTEM CALCULATED PRICE LEVELS:
- Suggested Entry: ${entry:.2f}
- Suggested Stop Loss: ${stop_loss:.2f}
- Suggested Take Profit: ${take_profit:.2f}
- Risk/Reward Ratio: 1:{risk_reward:.1f}

RECENT SIGNALS (Last 5 days):
{recent_signals_text}

SYSTEM RECOMMENDATION: {recommendation['recommendation']} (Strength: {recommendation['strength']:+d})

Based on this data, provide:
1. OVERALL ASSESSMENT: Bullish/Bearish/Neutral with confidence level (1-10)
2. KEY INSIGHTS: What stands out in the technical setup?
3. RISK FACTORS: What could go wrong?
4. PRICE LEVELS - Please provide YOUR specific recommendations:
   - ENTRY PRICE: What price should I enter? (Confirm or adjust the system's ${entry:.2f})
   - STOP LOSS: What price should I exit if wrong? (Confirm or adjust ${stop_loss:.2f})
   - TAKE PROFIT: What price should I exit with profit? (Confirm or adjust ${take_profit:.2f})
   - Explain your reasoning for these levels
5. TIME HORIZON: How long to hold if entering position?
6. POSITION SIZE SUGGESTION: Conservative/Moderate/Aggressive based on setup quality

Be specific with price levels and realistic about risks."""
        
        return prompt
    
    def analyze_stock(self, symbol: str, data, recommendation: Dict) -> Dict:
        """
        Send stock data to Gemini for AI analysis
        
        Returns:
            Dict with AI analysis results
        """
        try:
            prompt = self.format_stock_data(symbol, data, recommendation)
            
            response = self.model.generate_content(prompt)
            
            return {
                'symbol': symbol,
                'ai_analysis': response.text,
                'success': True,
                'error': None
            }
            
        except Exception as e:
            return {
                'symbol': symbol,
                'ai_analysis': None,
                'success': False,
                'error': str(e)
            }
    
    def analyze_multiple(self, analyses: List[Dict]) -> Dict:
        """
        Compare multiple stocks and get Gemini's top picks
        
        Args:
            analyses: List of dicts with 'symbol', 'data', 'recommendation'
        """
        try:
            # Format summary of all stocks
            stocks_summary = []
            for analysis in analyses:
                rec = analysis['recommendation']
                stocks_summary.append(
                    f"{analysis['symbol']}: {rec['recommendation']} "
                    f"(Strength: {rec['strength']:+d}, RSI: {rec['rsi']:.1f}, "
                    f"Price: ${rec['current_price']:.2f})"
                )
            
            prompt = f"""As a swing trading expert, analyze these {len(analyses)} stocks and rank them by trading opportunity quality:

STOCKS:
{chr(10).join(stocks_summary)}

Provide:
1. TOP 3 PICKS: Rank the best opportunities with brief rationale
2. AVOID LIST: Any stocks with concerning technical setups
3. MARKET CONTEXT: Overall market sentiment based on these stocks
4. WATCHLIST: Stocks to monitor for future entry

Focus on setups with:
- Clear trend direction
- Good risk/reward ratios
- Confirming volume
- Manageable volatility"""
            
            response = self.model.generate_content(prompt)
            
            return {
                'ai_comparison': response.text,
                'success': True,
                'error': None
            }
            
        except Exception as e:
            return {
                'ai_comparison': None,
                'success': False,
                'error': str(e)
            }
    
    def get_strategy_advice(self, symbol: str, data, recommendation: Dict, 
                           risk_tolerance: str = 'moderate') -> Dict:
        """
        Get specific strategy advice based on risk tolerance
        
        Args:
            risk_tolerance: 'conservative', 'moderate', or 'aggressive'
        """
        try:
            latest = data.iloc[-1]
            atr = data['ATR'].iloc[-1] if 'ATR' in data.columns else (latest['High'] - latest['Low'])
            
            entry = recommendation.get('entry_price', latest['Close'])
            stop_loss = recommendation.get('stop_loss', latest['Close'])
            take_profit = recommendation.get('take_profit', latest['Close'])
            
            prompt = f"""Provide specific {risk_tolerance.upper()} risk strategy for {symbol}:

CURRENT SETUP:
- Price: ${latest['Close']:.2f}
- ATR (volatility): ${atr:.2f}
- Recommendation: {recommendation['recommendation']}
- RSI: {recommendation['rsi']:.1f}

SYSTEM CALCULATED LEVELS:
- Entry: ${entry:.2f}
- Stop Loss: ${stop_loss:.2f}
- Take Profit: ${take_profit:.2f}

For {risk_tolerance} risk tolerance, provide:

1. VALIDATE OR ADJUST THESE PRICE LEVELS:
   - Do you agree with Entry at ${entry:.2f}? If not, what price and why?
   - Do you agree with Stop Loss at ${stop_loss:.2f}? If not, what price and why?
   - Do you agree with Take Profit at ${take_profit:.2f}? If not, what price and why?

2. POSITION SIZING:
   - Recommended % of portfolio
   - Max risk per trade ($ or %)

3. ENTRY STRATEGY:
   - Immediate entry or wait for pullback?
   - Entry triggers

4. STOP LOSS MANAGEMENT:
   - Technical stop level
   - Time stop (exit if no movement in X days)

5. TAKE PROFIT TARGETS:
   - Conservative target (1:1.5)
   - Moderate target (1:2)
   - Aggressive target (1:3+)

6. TRADE MANAGEMENT:
   - When to move stop to breakeven
   - Scaling out strategy
   - Trailing stop approach

Be specific with dollar amounts and percentages. Give clear YES/NO on whether you agree with the system's price levels."""
            
            response = self.model.generate_content(prompt)
            
            return {
                'symbol': symbol,
                'risk_tolerance': risk_tolerance,
                'strategy': response.text,
                'success': True,
                'error': None
            }
            
        except Exception as e:
            return {
                'symbol': symbol,
                'strategy': None,
                'success': False,
                'error': str(e)
            }


# Helper function to check if Gemini is available
def is_gemini_available():
    """Check if Gemini API key is configured"""
    return os.getenv('GEMINI_API_KEY') is not None
