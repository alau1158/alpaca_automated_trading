import logging
import sys
import os

# Mock configuration for testing
os.environ['ALPACA_KEY'] = 'test'
os.environ['ALPACA_SECRET'] = 'test'
os.environ['GEMINI_API_KEY'] = 'test'

from news_fetcher import NewsFetcher
from gemini_analyzer import GeminiAnalyzer
from alpaca_trading_bot import MarketScanReader

logging.basicConfig(level=logging.INFO)

def test_imports():
    print("Testing imports and class initialization...")
    fetcher = NewsFetcher()
    print("✓ NewsFetcher initialized")
    
    try:
        analyzer = GeminiAnalyzer()
        print("✓ GeminiAnalyzer initialized")
    except Exception as e:
        print(f" GeminiAnalyzer failed (expected if no valid key): {e}")
        
    reader = MarketScanReader()
    print("✓ MarketScanReader initialized")

if __name__ == "__main__":
    test_imports()
