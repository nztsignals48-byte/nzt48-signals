"""
NZT-48 Trading System -- Universe Governance
==============================================
Governs additions, removals, and promotions within the ticker universe.

No ticker enters the active universe without passing verification.
No ticker is silently removed -- all delistings are logged with reasons.

Lifecycle:
    PROPOSED  -> Ticker submitted for evaluation.
    VERIFIED  -> Passed all checks (data quality, volume, price, spread).
    ACTIVE    -> In the live scanning universe.
    SUSPENDED -> Temporarily removed (data issues, corporate action).
    DELISTED  -> Permanently removed (3+ consecutive days of empty data).

Auto-governance rules:
    auto_delist_check  -- If a ticker returns empty data for N consecutive
                          days, it is flagged for delisting.
    auto_promote_check -- If a ticker in PROPOSED status passes N consecutive
                          days of clean data, it is promoted to VERIFIED.

Usage:
    from core.universe_governance import UniverseGovernance
    gov = UniverseGovernance()
    result = gov.propose_addition(["NEW3.L"])
    verification = gov.verify_ticker("NEW3.L")
    should_delist = gov.auto_delist_check("DEAD3.L", days_empty=3)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.core.universe_governance")

# Governance thresholds
_DEFAULT_DAYS_EMPTY_FOR_DELIST = 3
_DEFAULT_DAYS_CLEAN_FOR_PROMOTE = 5
_MIN_DAILY_VOLUME = 1000           # Minimum daily volume to be considered tradeable
_MIN_PRICE_GBP = 0.01             # Minimum price in GBP
_MAX_SPREAD_BPS = 500             # Maximum bid-ask spread in basis points
_GOVERNANCE_LOG_PATH = "data/universe_governance.json"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Result of verifying a single ticker for universe inclusion.

    Fields:
        ticker:       The ticker symbol being verified.
        status:       VERIFIED | PROPOSED | DELISTED | SUSPENDED.
        data_ok:      Whether data quality checks passed.
        volume_ok:    Whether volume is sufficient.
        price_ok:     Whether price is within acceptable range.
        spread_ok:    Whether bid-ask spread is acceptable.
        last_checked: ISO timestamp of when the check was performed.
        reasons:      List of reasons for the status determination.
    """
    ticker: str = ""
    status: str = "PROPOSED"        # VERIFIED | PROPOSED | DELISTED | SUSPENDED
    data_ok: bool = False
    volume_ok: bool = False
    price_ok: bool = False
    spread_ok: bool = False
    last_checked: str = ""
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        valid_statuses = ("VERIFIED", "PROPOSED", "DELISTED", "SUSPENDED", "ACTIVE")
        if self.status not in valid_statuses:
            raise ValueError(
                f"VerificationResult.status must be one of {valid_statuses}, "
                f"got '{self.status}'"
            )
        if not self.ticker:
            raise ValueError("VerificationResult.ticker is required")
        if not self.last_checked:
            self.last_checked = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

    @property
    def all_checks_pass(self) -> bool:
        """True if all individual checks passed."""
        return self.data_ok and self.volume_ok and self.price_ok and self.spread_ok

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> VerificationResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ProposalResult:
    """Result of proposing one or more tickers for universe addition.

    Fields:
        proposed:   List of tickers that were accepted for evaluation.
        rejected:   List of tickers that failed immediate checks.
        results:    Per-ticker VerificationResult for each proposed ticker.
        timestamp:  When the proposal was submitted.
    """
    proposed: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ProposalResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# UniverseGovernance
# ---------------------------------------------------------------------------

class UniverseGovernance:
    """Governs the ticker universe lifecycle.

    Manages proposals, verifications, auto-delisting, and auto-promotion.
    All governance decisions are logged to a persistent JSON audit trail.
    """

    def __init__(self, governance_log_path: str = _GOVERNANCE_LOG_PATH) -> None:
        self._log_path = Path(governance_log_path)
        self._ticker_state: dict[str, dict] = {}   # ticker -> {status, last_checked, ...}
        self._empty_day_counts: dict[str, int] = {}  # ticker -> consecutive empty days
        self._clean_day_counts: dict[str, int] = {}  # ticker -> consecutive clean days
        self._audit_log: list[dict] = []
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose_addition(self, tickers: list[str]) -> ProposalResult:
        """Propose one or more tickers for addition to the universe.

        Each ticker undergoes immediate verification. Tickers that pass
        all checks are marked VERIFIED. Those that fail are marked
        PROPOSED (pending manual review) or REJECTED (fundamental issues).

        Args:
            tickers: List of ticker symbols to propose.

        Returns:
            ProposalResult with per-ticker verification status.
        """
        proposed = []
        rejected = []
        results = []

        for ticker in tickers:
            ticker = ticker.strip().upper()
            if not ticker:
                continue

            # Basic format validation
            if not self._is_valid_ticker_format(ticker):
                rejected.append(ticker)
                results.append(VerificationResult(
                    ticker=ticker,
                    status="PROPOSED",
                    reasons=[f"Invalid ticker format: '{ticker}'"],
                ).to_dict())
                continue

            # Check if already in universe
            if ticker in self._ticker_state:
                existing = self._ticker_state[ticker]
                if existing.get("status") == "ACTIVE":
                    rejected.append(ticker)
                    results.append(VerificationResult(
                        ticker=ticker,
                        status="ACTIVE",
                        data_ok=True,
                        volume_ok=True,
                        price_ok=True,
                        spread_ok=True,
                        reasons=["Already active in universe"],
                    ).to_dict())
                    continue

            # Run verification
            verification = self.verify_ticker(ticker)
            results.append(verification.to_dict())

            if verification.all_checks_pass:
                proposed.append(ticker)
                self._ticker_state[ticker] = {
                    "status": "VERIFIED",
                    "last_checked": verification.last_checked,
                    "verified_at": verification.last_checked,
                }
            else:
                proposed.append(ticker)  # Still proposed, but needs attention
                self._ticker_state[ticker] = {
                    "status": "PROPOSED",
                    "last_checked": verification.last_checked,
                    "reasons": verification.reasons,
                }

            self._log_event("PROPOSE", ticker, verification.to_dict())

        self._save_state()

        return ProposalResult(
            proposed=proposed,
            rejected=rejected,
            results=results,
        )

    def verify_ticker(self, ticker: str) -> VerificationResult:
        """Verify a single ticker's data quality, volume, price, and spread.

        Fetches recent data via yfinance (if available) and runs the
        DataHealthGate checks plus additional governance checks.

        Args:
            ticker: Ticker symbol to verify.

        Returns:
            VerificationResult with detailed check results.
        """
        result = VerificationResult(ticker=ticker, status="PROPOSED")
        reasons = []

        # Attempt data fetch and validation
        df = self._fetch_ticker_data(ticker)

        if df is None:
            result.data_ok = False
            result.volume_ok = False
            result.price_ok = False
            result.spread_ok = False
            result.status = "PROPOSED"
            result.reasons = ["Failed to fetch data -- cannot verify"]
            return result

        # Data quality check
        data_ok, data_reasons = self._check_data_quality(ticker, df)
        result.data_ok = data_ok
        reasons.extend(data_reasons)

        # Volume check
        volume_ok, vol_reasons = self._check_volume(ticker, df)
        result.volume_ok = volume_ok
        reasons.extend(vol_reasons)

        # Price check
        price_ok, price_reasons = self._check_price(ticker, df)
        result.price_ok = price_ok
        reasons.extend(price_reasons)

        # Spread check (estimated from high-low range if no L2 data)
        spread_ok, spread_reasons = self._check_spread(ticker, df)
        result.spread_ok = spread_ok
        reasons.extend(spread_reasons)

        # Determine final status
        if result.all_checks_pass:
            result.status = "VERIFIED"
        else:
            result.status = "PROPOSED"

        result.reasons = reasons
        return result

    def auto_delist_check(self, ticker: str, days_empty: int = _DEFAULT_DAYS_EMPTY_FOR_DELIST) -> bool:
        """Check if a ticker should be auto-delisted due to consecutive empty data.

        Call this daily for each ticker. If the ticker has had empty/failed
        data for `days_empty` consecutive days, returns True and updates
        the ticker state to SUSPENDED.

        Args:
            ticker:     Ticker symbol to check.
            days_empty: Number of consecutive empty days before delisting.

        Returns:
            True if the ticker should be delisted, False otherwise.
        """
        current_count = self._empty_day_counts.get(ticker, 0)

        # Check if today's data is empty
        df = self._fetch_ticker_data(ticker, period="1d")
        if df is None or (hasattr(df, "empty") and df.empty):
            current_count += 1
            self._empty_day_counts[ticker] = current_count
            logger.info(
                "Ticker %s: empty data day %d/%d",
                ticker, current_count, days_empty,
            )
        else:
            # Data returned -- reset counter
            self._empty_day_counts[ticker] = 0
            return False

        if current_count >= days_empty:
            logger.warning(
                "AUTO-DELIST: %s has had empty data for %d consecutive days",
                ticker, current_count,
            )
            self._ticker_state[ticker] = {
                "status": "SUSPENDED",
                "last_checked": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "reason": f"Empty data for {current_count} consecutive days",
            }
            self._log_event("AUTO_DELIST", ticker, {
                "days_empty": current_count,
                "threshold": days_empty,
            })
            self._save_state()
            return True

        return False

    def auto_promote_check(self, ticker: str, days_clean: int = _DEFAULT_DAYS_CLEAN_FOR_PROMOTE) -> bool:
        """Check if a PROPOSED ticker should be auto-promoted to VERIFIED.

        Call this daily for each proposed ticker. If the ticker has had
        clean data for `days_clean` consecutive days, promotes it to VERIFIED.

        Args:
            ticker:     Ticker symbol to check.
            days_clean: Number of consecutive clean days before promotion.

        Returns:
            True if the ticker was promoted, False otherwise.
        """
        state = self._ticker_state.get(ticker, {})
        if state.get("status") not in ("PROPOSED", None):
            return False  # Only promote PROPOSED tickers

        current_count = self._clean_day_counts.get(ticker, 0)

        # Check if today's data is clean
        verification = self.verify_ticker(ticker)
        if verification.all_checks_pass:
            current_count += 1
            self._clean_day_counts[ticker] = current_count
            logger.info(
                "Ticker %s: clean data day %d/%d for promotion",
                ticker, current_count, days_clean,
            )
        else:
            # Failed -- reset counter
            self._clean_day_counts[ticker] = 0
            return False

        if current_count >= days_clean:
            logger.info(
                "AUTO-PROMOTE: %s has had clean data for %d consecutive days -> VERIFIED",
                ticker, current_count,
            )
            self._ticker_state[ticker] = {
                "status": "VERIFIED",
                "last_checked": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "promoted_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "reason": f"Clean data for {current_count} consecutive days",
            }
            self._log_event("AUTO_PROMOTE", ticker, {
                "days_clean": current_count,
                "threshold": days_clean,
            })
            self._save_state()
            return True

        return False

    def get_ticker_status(self, ticker: str) -> Optional[dict]:
        """Return the current governance state for a ticker, or None."""
        return self._ticker_state.get(ticker)

    def get_all_statuses(self) -> dict[str, dict]:
        """Return governance state for all tracked tickers."""
        return dict(self._ticker_state)

    def get_audit_log(self, limit: int = 100) -> list[dict]:
        """Return the most recent audit log entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of audit log dicts (most recent first).
        """
        return list(reversed(self._audit_log[-limit:]))

    # ------------------------------------------------------------------
    # Internal: verification checks
    # ------------------------------------------------------------------

    def _check_data_quality(self, ticker: str, df) -> tuple[bool, list[str]]:
        """Run data quality checks via DataHealthGate if available."""
        reasons = []
        try:
            from uk_isa.data_health import DataHealthGate
            gate = DataHealthGate()
            result = gate.validate(ticker, df)
            if result.status == "FAIL":
                reasons.extend([f"DATA_FAIL: {e}" for e in result.exceptions])
                return False, reasons
            elif result.status == "WARN":
                reasons.extend([f"DATA_WARN: {w}" for w in result.warnings])
                return True, reasons
            return True, reasons
        except ImportError:
            # Fallback: basic checks
            try:
                if df is None or (hasattr(df, "empty") and df.empty):
                    return False, ["No data available"]
                if len(df) < 2:
                    return False, [f"Insufficient rows: {len(df)}"]
                return True, []
            except Exception as e:
                return False, [f"Data check error: {e}"]

    def _check_volume(self, ticker: str, df) -> tuple[bool, list[str]]:
        """Check if recent volume is sufficient for trading."""
        reasons = []
        try:
            import pandas as pd
            if not isinstance(df, pd.DataFrame):
                return False, ["Not a DataFrame"]

            # Normalise columns
            cols = {c.lower() if isinstance(c, str) else str(c).lower(): c for c in df.columns}
            vol_col = cols.get("volume")
            if vol_col is None:
                return False, ["No volume column"]

            recent_vol = df[vol_col].tail(5)
            avg_vol = float(recent_vol.mean())
            if avg_vol < _MIN_DAILY_VOLUME:
                reasons.append(
                    f"Average 5-day volume ({avg_vol:.0f}) below minimum ({_MIN_DAILY_VOLUME})"
                )
                return False, reasons

            # Check for suspicious zero-volume days
            zero_days = int((recent_vol == 0).sum())
            if zero_days >= 3:
                reasons.append(f"Volume is zero for {zero_days}/5 recent days")
                return False, reasons

            return True, reasons
        except Exception as e:
            return False, [f"Volume check error: {e}"]

    def _check_price(self, ticker: str, df) -> tuple[bool, list[str]]:
        """Check if the price is within acceptable range."""
        reasons = []
        try:
            import pandas as pd
            if not isinstance(df, pd.DataFrame):
                return False, ["Not a DataFrame"]

            cols = {c.lower() if isinstance(c, str) else str(c).lower(): c for c in df.columns}
            close_col = cols.get("close")
            if close_col is None:
                return False, ["No close column"]

            last_close = float(df[close_col].iloc[-1])
            if last_close <= _MIN_PRICE_GBP:
                reasons.append(
                    f"Price ({last_close:.4f}) at or below minimum ({_MIN_PRICE_GBP})"
                )
                return False, reasons

            # For .L tickers, check if price is suspiciously high (pence vs pounds)
            if ticker.endswith(".L") and last_close > 10000:
                reasons.append(
                    f"Price ({last_close:.2f}) suspiciously high -- may be in pence"
                )
                return False, reasons

            return True, reasons
        except Exception as e:
            return False, [f"Price check error: {e}"]

    def _check_spread(self, ticker: str, df) -> tuple[bool, list[str]]:
        """Estimate bid-ask spread from high-low range vs close.

        This is an approximation -- real spread data requires L2 market data.
        We use the average (high-low)/close as a proxy.
        """
        reasons = []
        try:
            import pandas as pd
            if not isinstance(df, pd.DataFrame):
                return False, ["Not a DataFrame"]

            cols = {c.lower() if isinstance(c, str) else str(c).lower(): c for c in df.columns}
            high_col = cols.get("high")
            low_col = cols.get("low")
            close_col = cols.get("close")

            if not all([high_col, low_col, close_col]):
                return True, ["Cannot estimate spread -- missing OHLC columns"]

            recent = df.tail(5)
            highs = recent[high_col].values.astype(float)
            lows = recent[low_col].values.astype(float)
            closes = recent[close_col].values.astype(float)

            # Estimate spread as fraction of (high-low)/close
            # This overestimates the spread but catches extreme cases
            spreads_bps = []
            for h, l, c in zip(highs, lows, closes):
                if c > 0:
                    spread_est = (h - l) / c * 10000  # Convert to bps
                    spreads_bps.append(spread_est)

            if not spreads_bps:
                return True, ["Cannot compute spread estimate"]

            avg_spread_bps = sum(spreads_bps) / len(spreads_bps)
            if avg_spread_bps > _MAX_SPREAD_BPS:
                reasons.append(
                    f"Estimated spread ({avg_spread_bps:.0f} bps) exceeds "
                    f"maximum ({_MAX_SPREAD_BPS} bps)"
                )
                return False, reasons

            return True, reasons
        except Exception as e:
            return True, [f"Spread check warning: {e}"]

    # ------------------------------------------------------------------
    # Internal: data fetching
    # ------------------------------------------------------------------

    def _fetch_ticker_data(self, ticker: str, period: str = "5d"):
        """Fetch recent OHLCV data for a ticker via yfinance.

        Returns a pandas DataFrame or None on failure.
        """
        try:
            import yfinance as yf
            df = yf.download(
                ticker, period=period, interval="1d",
                auto_adjust=True, progress=False,
            )
            if df is None or df.empty:
                return None
            # Normalise MultiIndex columns
            import pandas as pd
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() if isinstance(c, str) else str(c).lower()
                              for c in df.columns]
            return df
        except ImportError:
            logger.warning("yfinance not available -- cannot fetch data for %s", ticker)
            return None
        except Exception as e:
            logger.warning("Failed to fetch data for %s: %s", ticker, e)
            return None

    # ------------------------------------------------------------------
    # Internal: utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_ticker_format(ticker: str) -> bool:
        """Basic ticker format validation."""
        if not ticker:
            return False
        if len(ticker) > 20:
            return False
        # Must contain at least one letter
        if not any(c.isalpha() for c in ticker):
            return False
        # Must not contain spaces
        if " " in ticker:
            return False
        return True

    def _log_event(self, action: str, ticker: str, details: dict) -> None:
        """Append an event to the audit log."""
        event = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            "ticker": ticker,
            "details": details,
        }
        self._audit_log.append(event)
        # Bound the audit log to prevent unbounded growth
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-5000:]

    def _save_state(self) -> None:
        """Persist governance state to disk."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "ticker_state": self._ticker_state,
            "empty_day_counts": self._empty_day_counts,
            "clean_day_counts": self._clean_day_counts,
            "audit_log": self._audit_log[-1000:],  # Only persist last 1000 entries
            "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            with open(self._log_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
            logger.debug("Universe governance state saved to %s", self._log_path)
        except Exception as e:
            logger.error("Failed to save governance state: %s", e)

    def _load_state(self) -> None:
        """Load governance state from disk (cold-start recovery)."""
        if not self._log_path.exists():
            logger.info("No governance state file at %s -- starting fresh", self._log_path)
            return

        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._ticker_state = state.get("ticker_state", {})
            self._empty_day_counts = state.get("empty_day_counts", {})
            self._clean_day_counts = state.get("clean_day_counts", {})
            self._audit_log = state.get("audit_log", [])
            logger.info(
                "Universe governance state loaded: %d tickers tracked",
                len(self._ticker_state),
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to load governance state from %s: %s", self._log_path, e)


# ---------------------------------------------------------------------------
# Universe Validation Report (W10)
# ---------------------------------------------------------------------------

@dataclass
class UniverseValidationReport:
    """Result of validating the full ticker universe from settings.yaml.

    Provides a snapshot of the configured universe: total size, duplicates,
    liquidity flags, and per-list breakdowns.
    """
    total_unique_tickers: int = 0
    core_count: int = 0
    extended_count: int = 0
    expansion_v2_count: int = 0
    expansion_v3_count: int = 0
    duplicates: list[dict] = field(default_factory=list)       # [{ticker, found_in: [list names]}]
    low_liquidity: list[dict] = field(default_factory=list)    # [{ticker, note, avg_vol}]
    invalid_format: list[str] = field(default_factory=list)    # tickers that fail format check
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def is_clean(self) -> bool:
        """True if no duplicates, no invalid formats, and no very_low_liquidity tickers."""
        has_very_low = any(
            "very_low" in item.get("note", "") for item in self.low_liquidity
        )
        return not self.duplicates and not self.invalid_format and not has_very_low

    def summary(self) -> str:
        """Human-readable summary of the validation report."""
        lines = [
            f"=== Universe Validation Report ({self.timestamp}) ===",
            f"Total unique tickers: {self.total_unique_tickers}",
            f"  Core:          {self.core_count}",
            f"  Extended:      {self.extended_count}",
            f"  Expansion v2:  {self.expansion_v2_count}",
            f"  Expansion v3:  {self.expansion_v3_count}",
        ]
        if self.duplicates:
            lines.append(f"\nDUPLICATES ({len(self.duplicates)}):")
            for d in self.duplicates:
                lines.append(f"  {d['ticker']} -> found in: {', '.join(d['found_in'])}")
        else:
            lines.append("\nDuplicates: none")

        if self.low_liquidity:
            lines.append(f"\nLOW LIQUIDITY FLAGS ({len(self.low_liquidity)}):")
            for item in self.low_liquidity:
                lines.append(
                    f"  {item['ticker']}  vol={item.get('avg_vol', '?')}  "
                    f"note={item.get('note', 'low_liquidity')}"
                )
        else:
            lines.append("\nLow liquidity flags: none")

        if self.invalid_format:
            lines.append(f"\nINVALID FORMAT ({len(self.invalid_format)}):")
            for t in self.invalid_format:
                lines.append(f"  {t}")
        else:
            lines.append("\nInvalid format: none")

        status = "CLEAN" if self.is_clean else "WARNINGS"
        lines.append(f"\nStatus: {status}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


def validate_universe(config: Optional[dict] = None) -> UniverseValidationReport:
    """Validate the full ticker universe from settings.yaml.

    Reads core, extended, core_expansion_v2, and core_expansion_v3 lists.
    Checks for:
        - Ticker format validity
        - Duplicates across all four lists
        - Low-liquidity flags (from notes in v2/v3 entries)
        - Total universe size

    Args:
        config: Optional pre-loaded config dict. If None, loads from
                config/settings.yaml via the config module.

    Returns:
        UniverseValidationReport with all findings.
    """
    if config is None:
        try:
            from config import load_config
            config = load_config()
        except Exception as e:
            logger.error("Cannot load config for universe validation: %s", e)
            return UniverseValidationReport()

    # isa_tickers_v2 is nested under v2_engine in settings.yaml
    isa = config.get("v2_engine", {}).get("isa_tickers_v2", {})

    # --- Extract ticker lists ---
    core_list: list[str] = isa.get("core", [])
    extended_list: list[str] = isa.get("extended", [])

    # v2 and v3 are lists of dicts with a 'ticker' key
    v2_raw: list = isa.get("core_expansion_v2", [])
    v3_raw: list = isa.get("core_expansion_v3", [])

    v2_tickers: list[str] = []
    v2_meta: dict[str, dict] = {}  # ticker -> {note, avg_vol, ...}
    for entry in v2_raw:
        if isinstance(entry, dict):
            t = entry.get("ticker", "")
            v2_tickers.append(t)
            v2_meta[t] = entry
        elif isinstance(entry, str):
            v2_tickers.append(entry)

    v3_tickers: list[str] = []
    v3_meta: dict[str, dict] = {}
    for entry in v3_raw:
        if isinstance(entry, dict):
            t = entry.get("ticker", "")
            v3_tickers.append(t)
            v3_meta[t] = entry
        elif isinstance(entry, str):
            v3_tickers.append(entry)

    # --- Duplicate detection across all lists ---
    list_map: dict[str, list[str]] = {}  # ticker -> [list names where found]
    for ticker in core_list:
        list_map.setdefault(ticker, []).append("core")
    for ticker in extended_list:
        list_map.setdefault(ticker, []).append("extended")
    for ticker in v2_tickers:
        list_map.setdefault(ticker, []).append("expansion_v2")
    for ticker in v3_tickers:
        list_map.setdefault(ticker, []).append("expansion_v3")

    duplicates = [
        {"ticker": ticker, "found_in": lists}
        for ticker, lists in sorted(list_map.items())
        if len(lists) > 1
    ]

    # --- Format validation ---
    invalid_format = []
    for ticker in list_map:
        if not UniverseGovernance._is_valid_ticker_format(ticker):
            invalid_format.append(ticker)

    # --- Liquidity flags ---
    low_liquidity = []
    all_meta = {**v2_meta, **v3_meta}
    for ticker, meta in sorted(all_meta.items()):
        note = meta.get("note", "")
        if "low_liquidity" in str(note):
            low_liquidity.append({
                "ticker": ticker,
                "note": note,
                "avg_vol": meta.get("avg_vol", 0),
            })

    # --- Build report ---
    all_unique = set(list_map.keys())
    report = UniverseValidationReport(
        total_unique_tickers=len(all_unique),
        core_count=len(core_list),
        extended_count=len(extended_list),
        expansion_v2_count=len(v2_tickers),
        expansion_v3_count=len(v3_tickers),
        duplicates=duplicates,
        low_liquidity=low_liquidity,
        invalid_format=invalid_format,
    )

    logger.info(
        "Universe validation: %d unique tickers, %d duplicates, %d low-liquidity flags",
        report.total_unique_tickers,
        len(report.duplicates),
        len(report.low_liquidity),
    )

    return report
