from __future__ import annotations
import json, os, logging, datetime
from core.clock import now_utc

logger = logging.getLogger(__name__)
_DATA_PATH = 'data/short_interest.json'
S16_UNIVERSE = ['NVDA','TSLA','MU','AMD','AVGO','MRVL','ARM','TSM','SMCI','VRT','CRDO','ANET','QCOM','LRCX','KLAC','ON']

class ShortInterestFeed:
    def __init__(self):
        self._data = {}
        self._load()
    def _load(self):
        try:
            if os.path.exists(_DATA_PATH):
                with open(_DATA_PATH) as f: self._data = json.load(f)
        except Exception: self._data = {}
    def _save(self):
        os.makedirs('data', exist_ok=True)
        with open(_DATA_PATH, 'w') as f: json.dump(self._data, f, indent=2)
    def fetch_daily(self):
        results = {}
        try:
            import yfinance as yf
            for t in S16_UNIVERSE:
                try:
                    info = yf.Ticker(t).info or {}
                    results[t] = {'short_pct_float': round(float(info.get('shortPercentOfFloat',0) or 0)*100,2), 'short_ratio_days': round(float(info.get('shortRatio',0) or 0),2), 'updated_at': now_utc().isoformat()}
                except Exception: results[t] = {'short_pct_float': 0, 'short_ratio_days': 0}
        except Exception as e: logger.warning('fetch failed: %s', e)
        if results:
            self._data = results
            self._save()
        return results
    def get_short_pct(self, ticker): return self._data.get(ticker, {}).get('short_pct_float', 0.0)
    def is_squeeze_candidate(self, ticker): return self.get_short_pct(ticker) > 15.0
    def get_confidence_boost(self, ticker, momentum_positive): return 8 if self.is_squeeze_candidate(ticker) and momentum_positive else 0
