"""Earnings Sentiment Feed — stub for earnings sentiment signals."""
import json, os, logging
logger = logging.getLogger(__name__)

class EarningsSentimentFeed:
    def __init__(self, data_path="data/earnings_sentiment.json"):
        self._path = data_path

    def get_sentiment(self, ticker: str) -> dict:
        try:
            if os.path.exists(self._path):
                with open(self._path) as f:
                    data = json.load(f)
                return data.get(ticker, {})
        except Exception as e:
            logger.warning("EarningsSentimentFeed.get_sentiment error: %s", e)
        return {}

    def update(self, ticker: str, sentiment: dict):
        try:
            data = {}
            if os.path.exists(self._path):
                with open(self._path) as f:
                    data = json.load(f)
            data[ticker] = sentiment
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("EarningsSentimentFeed.update error: %s", e)
