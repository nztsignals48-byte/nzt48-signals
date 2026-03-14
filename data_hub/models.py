"""
data_hub/models.py
==================
Core data models: Bar, Quote, CorporateAction, InstrumentMeta, DataReliabilityScore.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional


@dataclass
class Bar:
    ticker:    str
    timestamp: datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float
    source:    str = "yfinance"  # ibkr | polygon | yfinance
    adjusted:  bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class Quote:
    ticker:    str
    timestamp: datetime
    bid:       float
    ask:       float
    last:      float
    spread_bps: float = 0.0
    source:    str = "proxy"  # ibkr | proxy

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.ask > 0 else self.last

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class CorporateAction:
    ticker:      str
    action_date: date
    action_type: str       # SPLIT | DIVIDEND | SPINOFF
    ratio:       float = 1.0   # split ratio, e.g. 2.0 = 2-for-1
    amount:      float = 0.0   # dividend amount
    notes:       str   = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action_date"] = str(self.action_date)
        return d


@dataclass
class InstrumentMeta:
    ticker:      str
    exchange:    str  = "LSE"
    currency:    str  = "GBP"
    isin:        str  = ""
    figi:        str  = ""
    name:        str  = ""
    leverage:    int  = 1
    asset_class: str  = "ETP"
    active:      bool = True
    updated_at:  str  = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DataReliabilityScore:
    ticker:          str
    score:           float = 1.0    # 0-1
    source:          str   = "yfinance"
    validated:       bool  = False
    validator_agree: bool  = True
    disagreement_pct: float = 0.0
    n_bars:          int   = 0
    issues:          list  = field(default_factory=list)
    computed_at:     str   = ""

    def to_dict(self) -> dict:
        return asdict(self)
