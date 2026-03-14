"""
NZT-48 V8.0 -- Universe Manager
================================
Thread-safe singleton that loads config/universe.yaml and provides
structured access to the three-tier instrument universe:

  CORE       — 12 primary tradable ISA leveraged ETPs (TRADE eligible)
  PEER       — Top-k similar instruments for context (WATCH/INTEL only)
  FULL_SCAN  — Broad market benchmarks and underlyings (INTEL only)

Usage::

    from uk_isa.universe_manager import get_universe_manager

    um = get_universe_manager()
    print(um.core_list)          # ['QQQ3.L', '3LUS.L', ...]
    print(um.all_tradable)       # same as core_list (only CORE can trade)
    print(um.get_tier("NVDA"))   # "FULL_SCAN"

The module also writes daily universe artifacts to
``artifacts/YYYY-MM-DD/universe/`` for audit and downstream consumption.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # nzt48-signals/
_UNIVERSE_YAML = _PROJECT_ROOT / "config" / "universe.yaml"

# ---------------------------------------------------------------------------
# Defaults — used when universe.yaml is missing or incomplete
# ---------------------------------------------------------------------------

_DEFAULT_CORE: list[str] = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
]

_DEFAULT_PEER_EXPANSION_RATIO: float = 0.50

_DEFAULT_PEER_CANDIDATES: list[str] = [
    "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "AVGO3.L", "PLTR3.L",
    "AMZL.L", "MSFL.L", "3LDE.L", "3LEU.L", "3GOL.L", "3SIL.L",
    "3OIL.L", "COIN3.L", "MSTRL.L", "BAC3.L", "GS3.L", "LLY3.L",
    "MFAS.L",
]

_DEFAULT_FULL_SCAN: list[str] = [
    "QQQ", "SPY", "SMH", "SOXX", "IWM", "DIA", "XLK", "XLF", "XLE",
    "XLV", "^VIX", "TLT", "GLD", "USO", "DX-Y.NYB", "BTC-USD",
    "NVDA", "TSLA", "TSM", "MU", "AMD", "AVGO", "ARM",
    "AMZN", "MSFT", "META", "PLTR", "GOOG", "AAPL",
]

_DEFAULT_COMPUTE_BUDGET: dict[str, int] = {
    "core_pct": 70,
    "peer_pct": 20,
    "full_scan_pct": 10,
}

_DEFAULT_SCAN_CADENCE: dict[str, int] = {
    "core_interval_seconds": 60,
    "peer_interval_seconds": 180,
    "full_scan_interval_seconds": 600,
}

_DEFAULT_FACTOR_THEMES: dict[str, list[str]] = {
    "nasdaq_beta": ["QQQ3.L", "QQQS.L", "QQQ5.L"],
    "sp500_beta": ["3LUS.L", "3USS.L", "SP5L.L"],
    "semiconductors": ["3SEM.L", "NVD3.L", "AMD3.L", "ARM3.L", "AVGO3.L"],
    "ai_tech": ["GPT3.L", "PLTR3.L", "MFAS.L"],
    "mega_cap_tech": ["AMZL.L", "MSFL.L"],
    "tesla_plays": ["TSL3.L", "TSLS.L"],
    "tsmc_plays": ["TSM3.L"],
    "micron_plays": ["MU2.L"],
    "commodities": ["3GOL.L", "3SIL.L", "3OIL.L"],
    "europe_index": ["3LDE.L", "3LEU.L"],
    "crypto_adjacent": ["COIN3.L", "MSTRL.L"],
    "financials": ["BAC3.L", "GS3.L"],
    "pharma": ["LLY3.L"],
}

_DEFAULT_PEER_SELECTION: dict[str, Any] = {
    "methods": [
        "correlation_similarity",
        "factor_theme_similarity",
        "momentum_vol_similarity",
    ],
    "correlation_lookback_days": 60,
    "min_similarity_score": 0.40,
}


# ---------------------------------------------------------------------------
# UniverseManager
# ---------------------------------------------------------------------------

@dataclass
class UniverseManager:
    """Centralised, read-mostly manager for the NZT-48 instrument universe.

    Loaded once from ``config/universe.yaml`` and cached for the process
    lifetime.  All public properties and methods are thread-safe (the
    underlying data is immutable after construction).

    Parameters
    ----------
    config_path : Path
        Absolute path to ``universe.yaml``.  Falls back to hardcoded
        defaults if the file is missing or unreadable.
    """

    # --- internal state (set during __post_init__) ---
    _core_list: list[str] = field(default_factory=list, repr=False)
    _peer_candidates: list[str] = field(default_factory=list, repr=False)
    _peer_list: list[str] = field(default_factory=list, repr=False)
    _full_scan_list: list[str] = field(default_factory=list, repr=False)
    _peer_expansion_ratio: float = field(default=_DEFAULT_PEER_EXPANSION_RATIO, repr=False)
    _peer_size_target: int = field(default=0, repr=False)
    _full_scan_max_size: int = field(default=500, repr=False)
    _compute_budget: dict[str, int] = field(default_factory=dict, repr=False)
    _scan_cadence: dict[str, int] = field(default_factory=dict, repr=False)
    _factor_themes: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _peer_selection: dict[str, Any] = field(default_factory=dict, repr=False)
    _allow_trade_from_peers: bool = field(default=False, repr=False)
    _allow_trade_from_full_scan: bool = field(default=False, repr=False)
    _raw_config: dict[str, Any] = field(default_factory=dict, repr=False)

    # Config path (public, settable before __post_init__)
    config_path: Path = field(default=_UNIVERSE_YAML)

    def __post_init__(self) -> None:
        """Load universe.yaml and populate all internal state."""
        raw = self._load_yaml(self.config_path)
        self._raw_config = raw
        uni = raw.get("universe", {}) if raw else {}

        # --- Core list ---
        self._core_list = list(uni.get("core_list", _DEFAULT_CORE))

        # --- Peer expansion ---
        self._peer_expansion_ratio = float(
            uni.get("peer_expansion_ratio", _DEFAULT_PEER_EXPANSION_RATIO)
        )
        self._peer_size_target = math.ceil(
            self._peer_expansion_ratio * len(self._core_list)
        )
        self._peer_candidates = list(
            uni.get("peer_candidates", _DEFAULT_PEER_CANDIDATES)
        )

        # --- Peer list (initially empty until select_peers is called) ---
        self._peer_list = []

        # --- Full scan ---
        raw_full = list(uni.get("full_scan_list", _DEFAULT_FULL_SCAN))
        self._full_scan_max_size = int(uni.get("full_scan_max_size", 500))
        self._full_scan_list = raw_full[: self._full_scan_max_size]

        # --- Compute budget ---
        self._compute_budget = {
            "core_pct": int(uni.get("compute_budget", {}).get("core_pct", _DEFAULT_COMPUTE_BUDGET["core_pct"])),
            "peer_pct": int(uni.get("compute_budget", {}).get("peer_pct", _DEFAULT_COMPUTE_BUDGET["peer_pct"])),
            "full_scan_pct": int(uni.get("compute_budget", {}).get("full_scan_pct", _DEFAULT_COMPUTE_BUDGET["full_scan_pct"])),
        }

        # --- Scan cadence ---
        cadence_raw = uni.get("scan_cadence", {})
        self._scan_cadence = {
            "core_interval_seconds": int(cadence_raw.get("core_interval_seconds", _DEFAULT_SCAN_CADENCE["core_interval_seconds"])),
            "peer_interval_seconds": int(cadence_raw.get("peer_interval_seconds", _DEFAULT_SCAN_CADENCE["peer_interval_seconds"])),
            "full_scan_interval_seconds": int(cadence_raw.get("full_scan_interval_seconds", _DEFAULT_SCAN_CADENCE["full_scan_interval_seconds"])),
        }

        # --- Factor themes ---
        self._factor_themes = dict(uni.get("factor_themes", _DEFAULT_FACTOR_THEMES))

        # --- Peer selection config ---
        self._peer_selection = dict(uni.get("peer_selection", _DEFAULT_PEER_SELECTION))

        # --- Trading permissions ---
        self._allow_trade_from_peers = bool(uni.get("allow_trade_from_peers", False))
        self._allow_trade_from_full_scan = bool(uni.get("allow_trade_from_full_scan", False))

        logger.info(
            "UniverseManager initialised: %d core, %d peer candidates, "
            "%d full_scan, peer_size_target=%d",
            len(self._core_list),
            len(self._peer_candidates),
            len(self._full_scan_list),
            self._peer_size_target,
        )

    # ------------------------------------------------------------------
    # YAML loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        """Load a YAML file, returning an empty dict on any failure."""
        if yaml is None:
            logger.error(
                "PyYAML is not installed — using hardcoded defaults. "
                "Install with: pip install pyyaml"
            )
            return {}

        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                logger.warning(
                    "universe.yaml did not parse as a dict (got %s) — using defaults",
                    type(data).__name__,
                )
                return {}
            logger.info("Loaded universe config from %s", path)
            return data
        except FileNotFoundError:
            logger.warning("universe.yaml not found at %s — using defaults", path)
            return {}
        except Exception:
            logger.exception("Failed to load universe.yaml at %s — using defaults", path)
            return {}

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def core_list(self) -> list[str]:
        """The 12 primary tradable ISA leveraged ETPs."""
        return list(self._core_list)

    @property
    def peer_candidates(self) -> list[str]:
        """Full pool of hand-curated peer candidates."""
        return list(self._peer_candidates)

    @property
    def peer_list(self) -> list[str]:
        """Currently selected peers (populated after ``select_peers()``)."""
        return list(self._peer_list)

    @property
    def full_scan_list(self) -> list[str]:
        """Broad market context instruments (INTEL only)."""
        return list(self._full_scan_list)

    @property
    def peer_size_target(self) -> int:
        """Target number of peers: ceil(peer_expansion_ratio * len(core))."""
        return self._peer_size_target

    @property
    def all_tradable(self) -> list[str]:
        """Instruments eligible for TRADE signals (CORE only unless overridden)."""
        tradable = list(self._core_list)
        if self._allow_trade_from_peers:
            tradable.extend(t for t in self._peer_list if t not in tradable)
        return tradable

    @property
    def all_watchable(self) -> list[str]:
        """Instruments eligible for WATCH/INTEL signals (CORE + PEERS)."""
        seen: set[str] = set()
        result: list[str] = []
        for t in self._core_list + self._peer_list:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    @property
    def all_scannable(self) -> list[str]:
        """Every instrument across all three tiers (de-duplicated, order-preserving)."""
        seen: set[str] = set()
        result: list[str] = []
        for t in self._core_list + self._peer_list + self._full_scan_list:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    # ------------------------------------------------------------------
    # Peer selection
    # ------------------------------------------------------------------

    def select_peers(self, similarity_scores: dict[str, float]) -> list[str]:
        """Select top-k peers from peer_candidates by similarity score.

        Parameters
        ----------
        similarity_scores : dict[str, float]
            Mapping of ``ticker -> similarity_score`` for each peer candidate.
            Only candidates present in ``peer_candidates`` are considered.
            Candidates below ``min_similarity_score`` are excluded.

        Returns
        -------
        list[str]
            The selected peer tickers, sorted by descending score.
            Length is clamped to ``peer_size_target``.

        Notes
        -----
        After selection, access the result via the ``peer_list`` property.
        A warning is logged if the selected count deviates from
        ``peer_size_target`` by more than 2.
        """
        min_score = float(self._peer_selection.get("min_similarity_score", 0.40))
        candidate_set = set(self._peer_candidates)

        # Filter: must be a known candidate AND meet minimum score
        qualified: list[tuple[str, float]] = [
            (ticker, score)
            for ticker, score in similarity_scores.items()
            if ticker in candidate_set and score >= min_score
        ]

        # Sort descending by score, break ties alphabetically
        qualified.sort(key=lambda x: (-x[1], x[0]))

        # Take top-k
        selected = [ticker for ticker, _ in qualified[: self._peer_size_target]]
        self._peer_list = selected

        # Validate count
        self._validate_peer_count(len(selected))

        logger.info(
            "Selected %d peers (target=%d, min_score=%.2f): %s",
            len(selected),
            self._peer_size_target,
            min_score,
            selected,
        )
        return list(selected)

    def _validate_peer_count(self, actual: int) -> None:
        """Log a warning if peer count deviates from target by more than 2."""
        tolerance = 2
        if abs(actual - self._peer_size_target) > tolerance:
            logger.warning(
                "Peer count %d deviates from target %d by more than %d "
                "(peer_expansion_ratio=%.2f, core_size=%d)",
                actual,
                self._peer_size_target,
                tolerance,
                self._peer_expansion_ratio,
                len(self._core_list),
            )

    # ------------------------------------------------------------------
    # Tier / theme lookups
    # ------------------------------------------------------------------

    def get_tier(self, ticker: str) -> str:
        """Return the tier for a given ticker.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.

        Returns
        -------
        str
            One of ``"CORE"``, ``"PEER"``, ``"FULL_SCAN"``, or ``"UNKNOWN"``.
        """
        if ticker in self._core_list:
            return "CORE"
        if ticker in self._peer_list:
            return "PEER"
        if ticker in self._full_scan_list:
            return "FULL_SCAN"
        return "UNKNOWN"

    def get_factor_theme(self, ticker: str) -> str:
        """Return the primary factor/theme group for a ticker.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.

        Returns
        -------
        str
            The first matching theme name from ``factor_themes``, or
            ``"unclassified"`` if the ticker does not belong to any group.
        """
        for theme, members in self._factor_themes.items():
            if ticker in members:
                return theme
        return "unclassified"

    # ------------------------------------------------------------------
    # Budget / cadence
    # ------------------------------------------------------------------

    def get_compute_budget(self) -> dict[str, int]:
        """Return compute budget allocation percentages.

        Returns
        -------
        dict[str, int]
            Keys: ``core_pct``, ``peer_pct``, ``full_scan_pct``.
            Values sum to 100.
        """
        return dict(self._compute_budget)

    def get_scan_cadence(self) -> dict[str, int]:
        """Return scan interval per tier in seconds.

        Returns
        -------
        dict[str, int]
            Keys: ``core_interval_seconds``, ``peer_interval_seconds``,
            ``full_scan_interval_seconds``.
        """
        return dict(self._scan_cadence)

    # ------------------------------------------------------------------
    # Artifact writing
    # ------------------------------------------------------------------

    def write_universe_artifacts(self, date_str: str) -> Path:
        """Write daily universe snapshot to JSON artifacts.

        Creates three files under ``artifacts/<date_str>/universe/``:
          - ``core.json``      — core tickers + metadata
          - ``peers.json``     — selected peers + metadata
          - ``full_scan.json`` — full scan tickers

        Parameters
        ----------
        date_str : str
            Date string in ``YYYY-MM-DD`` format, used as the directory name.

        Returns
        -------
        Path
            The directory where artifacts were written.

        Raises
        ------
        OSError
            If the artifacts directory cannot be created.
        """
        artifact_dir = _PROJECT_ROOT / "artifacts" / date_str / "universe"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # Core artifact
        core_artifact = {
            "date": date_str,
            "tier": "CORE",
            "count": len(self._core_list),
            "tickers": self._core_list,
            "tradable": True,
            "factor_themes": {
                t: self.get_factor_theme(t) for t in self._core_list
            },
        }
        core_path = artifact_dir / "core.json"
        core_path.write_text(
            json.dumps(core_artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Peers artifact
        peers_artifact = {
            "date": date_str,
            "tier": "PEER",
            "count": len(self._peer_list),
            "peer_size_target": self._peer_size_target,
            "peer_expansion_ratio": self._peer_expansion_ratio,
            "tickers": self._peer_list,
            "tradable": self._allow_trade_from_peers,
            "factor_themes": {
                t: self.get_factor_theme(t) for t in self._peer_list
            },
        }
        peers_path = artifact_dir / "peers.json"
        peers_path.write_text(
            json.dumps(peers_artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Full scan artifact
        full_scan_artifact = {
            "date": date_str,
            "tier": "FULL_SCAN",
            "count": len(self._full_scan_list),
            "max_size": self._full_scan_max_size,
            "tickers": self._full_scan_list,
            "tradable": False,
        }
        full_scan_path = artifact_dir / "full_scan.json"
        full_scan_path.write_text(
            json.dumps(full_scan_artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        logger.info(
            "Wrote universe artifacts to %s (%d core, %d peers, %d full_scan)",
            artifact_dir,
            len(self._core_list),
            len(self._peer_list),
            len(self._full_scan_list),
        )
        return artifact_dir

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of the universe configuration."""
        lines = [
            "NZT-48 Universe Summary",
            "=" * 40,
            f"  CORE:      {len(self._core_list)} tickers (tradable)",
            f"  PEERS:     {len(self._peer_list)}/{self._peer_size_target} selected "
            f"(from {len(self._peer_candidates)} candidates)",
            f"  FULL_SCAN: {len(self._full_scan_list)} tickers (intel only)",
            f"  TOTAL:     {len(self.all_scannable)} unique tickers",
            f"  Budget:    CORE {self._compute_budget['core_pct']}% | "
            f"PEER {self._compute_budget['peer_pct']}% | "
            f"SCAN {self._compute_budget['full_scan_pct']}%",
            f"  Cadence:   CORE {self._scan_cadence['core_interval_seconds']}s | "
            f"PEER {self._scan_cadence['peer_interval_seconds']}s | "
            f"SCAN {self._scan_cadence['full_scan_interval_seconds']}s",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"UniverseManager(core={len(self._core_list)}, "
            f"peers={len(self._peer_list)}/{self._peer_size_target}, "
            f"full_scan={len(self._full_scan_list)})"
        )


# ---------------------------------------------------------------------------
# Thread-safe singleton
# ---------------------------------------------------------------------------

_INSTANCE: UniverseManager | None = None
_INSTANCE_LOCK = threading.Lock()


def get_universe_manager(
    config_path: Path | None = None,
    force_reload: bool = False,
) -> UniverseManager:
    """Return the singleton UniverseManager instance.

    Thread-safe.  The manager is constructed on first call and cached for
    the process lifetime unless ``force_reload=True``.

    Parameters
    ----------
    config_path : Path, optional
        Override path to ``universe.yaml``.  Only used on first call
        (or after a forced reload).
    force_reload : bool
        If ``True``, discard the cached instance and reload from disk.

    Returns
    -------
    UniverseManager
        The singleton instance.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None and not force_reload:
            return _INSTANCE

        path = config_path if config_path is not None else _UNIVERSE_YAML
        _INSTANCE = UniverseManager(config_path=path)
        return _INSTANCE
