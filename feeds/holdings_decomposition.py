"""
NZT-48 Trading System — Holdings Decomposition
Maps every ETP/ETF to its underlying constituents.
Used by the Pre-Market Intelligence Engine to see inside funds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("nzt48.feeds.holdings")

_CONFIG_DIR = Path(__file__).parent.parent / "config"


@dataclass
class Holding:
    """A single constituent holding within an ETP/ETF."""
    ticker: str
    weight: float
    name: str = ""


@dataclass
class ETPProfile:
    """Complete profile of an ETP/ETF including its constituents."""
    etp_ticker: str
    index: str = ""
    leverage: int = 1
    direction: str = "LONG"
    inverse: str = ""
    type: str = "index"  # "index", "single_stock", "sector"
    top_holdings: list[Holding] = field(default_factory=list)
    sector_weights: dict[str, float] = field(default_factory=dict)


class HoldingsDecomposer:
    """Loads and queries ETP constituent data from holdings.yaml.

    Usage:
        h = HoldingsDecomposer()
        profile = h.get("QQQ3.L")
        print(profile.top_holdings)  # Top 10-15 stocks

        # Reverse lookup: which ETPs does NVDA affect?
        etps = h.get_etps_for_stock("NVDA")
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._profiles: dict[str, ETPProfile] = {}
        self._reverse_map: dict[str, list[tuple[str, float]]] = {}  # stock -> [(etp, weight)]

        path = Path(config_path) if config_path else _CONFIG_DIR / "holdings.yaml"
        self._load(path)

    def _load(self, path: Path) -> None:
        """Parse holdings.yaml into ETPProfile objects."""
        if not path.exists():
            logger.warning("holdings.yaml not found at %s", path)
            return

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        # Support both flat format and wrapped under "holdings:" key
        if "holdings" in raw and isinstance(raw["holdings"], dict):
            raw = raw["holdings"]

        # Two-pass load: primary profiles first, then resolve aliases
        primaries: dict[str, ETPProfile] = {}
        aliases: dict[str, dict] = {}

        for etp_ticker, data in raw.items():
            if not isinstance(data, dict):
                continue
            if "alias_of" in data:
                aliases[etp_ticker] = data
            else:
                primaries[etp_ticker] = self._parse_entry(etp_ticker, data)

        # Resolve aliases: inherit top_holdings + sector_weights from parent
        for etp_ticker, data in aliases.items():
            parent_key = data["alias_of"]
            parent = primaries.get(parent_key)
            if not parent:
                logger.warning("Alias %s references unknown parent %s", etp_ticker, parent_key)
                continue

            profile = ETPProfile(
                etp_ticker=etp_ticker,
                index=data.get("index", parent.index),
                leverage=int(data.get("leverage", parent.leverage)),
                direction=data.get("direction", parent.direction),
                inverse=data.get("inverse", ""),
                type=data.get("type", parent.type),
                top_holdings=list(parent.top_holdings),
                sector_weights=dict(parent.sector_weights),
            )
            primaries[etp_ticker] = profile

        self._profiles = primaries

        # Build reverse map: stock -> list of (etp_ticker, weight)
        for etp_ticker, profile in self._profiles.items():
            for h in profile.top_holdings:
                if h.ticker not in self._reverse_map:
                    self._reverse_map[h.ticker] = []
                self._reverse_map[h.ticker].append((etp_ticker, h.weight))

        logger.info(
            "Holdings loaded: %d ETPs, %d unique constituents",
            len(self._profiles), len(self._reverse_map),
        )

    @staticmethod
    def _parse_entry(etp_ticker: str, data: dict) -> ETPProfile:
        """Parse a single primary YAML entry into an ETPProfile."""
        holdings = []
        for h in data.get("top_holdings", []):
            holdings.append(Holding(
                ticker=h.get("ticker", ""),
                weight=float(h.get("weight", 0)),
                name=h.get("name", ""),
            ))
        return ETPProfile(
            etp_ticker=etp_ticker,
            index=data.get("index", ""),
            leverage=int(data.get("leverage", 1)),
            direction=data.get("direction", "LONG"),
            inverse=data.get("inverse", ""),
            type=data.get("type", "index"),
            top_holdings=holdings,
            sector_weights=data.get("sector_weights", {}),
        )

    def get(self, etp_ticker: str) -> Optional[ETPProfile]:
        """Get the full profile for an ETP/ETF ticker."""
        return self._profiles.get(etp_ticker)

    def get_top_holdings(self, etp_ticker: str, n: int = 10) -> list[Holding]:
        """Get the top N holdings for an ETP, sorted by weight descending."""
        profile = self._profiles.get(etp_ticker)
        if not profile:
            return []
        sorted_h = sorted(profile.top_holdings, key=lambda h: h.weight, reverse=True)
        return sorted_h[:n]

    def get_all_constituents(self) -> set[str]:
        """Get the union of all underlying stock tickers across all ETPs."""
        return set(self._reverse_map.keys())

    def get_etps_for_stock(self, stock: str) -> list[tuple[str, float]]:
        """Reverse lookup: which ETPs contain this stock, and at what weight?

        Returns list of (etp_ticker, weight) tuples, sorted by weight descending.
        """
        entries = self._reverse_map.get(stock, [])
        return sorted(entries, key=lambda x: x[1], reverse=True)

    def get_all_profiles(self) -> dict[str, ETPProfile]:
        """Get all ETP profiles."""
        return dict(self._profiles)

    def get_long_etps(self) -> list[str]:
        """Get all LONG-direction ETP tickers."""
        return [t for t, p in self._profiles.items() if p.direction == "LONG"]

    def get_inverse_etps(self) -> list[str]:
        """Get all SHORT/inverse-direction ETP tickers."""
        return [t for t, p in self._profiles.items() if p.direction == "SHORT"]

    def get_by_type(self, etp_type: str) -> list[ETPProfile]:
        """Get all profiles of a given type (index, single_stock, sector)."""
        return [p for p in self._profiles.values() if p.type == etp_type]
