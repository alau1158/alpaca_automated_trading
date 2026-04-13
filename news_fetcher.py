import requests
import logging
from datetime import datetime, timedelta
from config import ALPACA_KEY, ALPACA_SECRET

logger = logging.getLogger(__name__)

class NewsFetcher:
    def __init__(self):
        self.base_url = "https://data.alpaca.markets/v1beta1/news"
        self.headers = {
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET
        }

    def get_stock_news(self, symbol, days=7, limit=10):
        """Fetch news for a specific stock symbol"""
        start_time = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
        
        params = {
            "symbols": symbol,
            "start": start_time,
            "limit": limit,
            "sort": "desc"
        }
        
        try:
            response = requests.get(self.base_url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()
            news_data = response.json()
            return news_data.get("news", [])
        except Exception as e:
            logger.error(f"Error fetching news for {symbol}: {e}")
            return []

    def format_news_for_ai(self, news_items):
        """Format news items for AI analysis"""
        if not news_items:
            return "No recent news found."
        
        formatted_news = []
        for item in news_items:
            headline = item.get("headline", "")
            summary = item.get("summary", "")
            created_at = item.get("created_at", "")
            source = item.get("source", "")
            
            formatted_item = f"Date: {created_at}\nHeadline: {headline}\nSummary: {summary}\nSource: {source}\n---"
            formatted_news.append(formatted_item)
            
        return "\n".join(formatted_news)
