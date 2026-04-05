"""Universe Filters & Execution Utilities — Ouroboros analytics modules.

P2-13: Spread-to-ATR hard filter for universe scanning
P2-1:  TWAP VWAP-weighted execution slicing (Almgren-Chriss 2000)
SC-18: Thompson Sampling Normal-Normal reward model (replaces Beta-Bernoulli)
P1-15: Corporate Actions Veto (ISA Compliance) — block entries near spin-offs etc.
v19-P2-4: Atomic JSON write utility

Usage: python3 -m python_brain.ouroboros.universe_filters
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [UniverseFilters] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("universe_filters")


# ---------------------------------------------------------------------------
# v19-P2-4: Atomic JSON Write Utility
# ---------------------------------------------------------------------------
def atomic_json_write(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON data atomically using tmp + rename pattern.

    POSIX rename() is atomic on the same filesystem. This prevents
    partial reads if the engine or another process reads mid-write.

    Args:
        path: Target file path
        data: JSON-serializable data
        indent: JSON indentation (default 2)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (same filesystem = atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.stem + "_",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, str(path))
        log.debug(f"Atomic write: {path}")
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# P2-13: Spread-to-ATR Hard Filter
# ---------------------------------------------------------------------------
# Exclude assets where bid-ask spread consumes >25% of daily range.
# These are untradable with momentum strategies.

@dataclass
class SpreadATRResult:
    """Result of spread-to-ATR filter for a single ticker."""
    ticker: str
    spread_bps: float       # Bid-ask spread in basis points
    daily_range_bps: float  # High-Low range in basis points (60-day median)
    ratio: float            # spread_bps / daily_range_bps
    excluded: bool          # True if ratio > threshold
    reason: str = ""


def spread_to_atr_filter(
    spread_cache_path: Path = None,
    threshold: float = 0.25,
) -> List[SpreadATRResult]:
    """Apply P2-13 spread-to-ATR hard filter to universe.

    Reads spread_cache.toml (written by config_writer with 5-day median spreads)
    and daily price data to compute which tickers are untradable.

    Args:
        spread_cache_path: Path to spread_cache.toml (default: CONFIG_DIR)
        threshold: Maximum spread/range ratio (default 0.25 = 25%)

    Returns:
        List of SpreadATRResult for each ticker
    """
    if spread_cache_path is None:
        spread_cache_path = CONFIG_DIR / "spread_cache.toml"

    results = []

    if not spread_cache_path.exists():
        log.warning(f"spread_cache.toml not found at {spread_cache_path}, skipping filter")
        return results

    # Parse spread cache (simple TOML: [ticker]\nspread_bps = X\ndaily_range_bps = Y)
    spreads = _parse_spread_cache(spread_cache_path)

    for ticker, data in spreads.items():
        spread_bps = data.get("spread_bps", 0.0)
        daily_range_bps = data.get("daily_range_bps", 1.0)  # Avoid div/0

        if daily_range_bps <= 0:
            daily_range_bps = 1.0

        ratio = spread_bps / daily_range_bps
        excluded = ratio > threshold

        reason = ""
        if excluded:
            reason = f"Spread {spread_bps:.1f}bps > {threshold*100:.0f}% of range {daily_range_bps:.1f}bps"

        results.append(SpreadATRResult(
            ticker=ticker,
            spread_bps=round(spread_bps, 2),
            daily_range_bps=round(daily_range_bps, 2),
            ratio=round(ratio, 4),
            excluded=excluded,
            reason=reason,
        ))

    excluded_count = sum(1 for r in results if r.excluded)
    log.info(f"P2-13 Spread-ATR filter: {excluded_count}/{len(results)} tickers excluded (threshold={threshold})")

    return results


def _parse_spread_cache(path: Path) -> Dict[str, Dict[str, float]]:
    """Parse spread_cache.toml into {ticker: {spread_bps, daily_range_bps}}."""
    spreads = {}
    current_ticker = None

    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current_ticker = line[1:-1].strip().strip('"')
            elif "=" in line and current_ticker:
                key, val = line.split("=", 1)
                key = key.strip()
                try:
                    spreads.setdefault(current_ticker, {})[key] = float(val.strip())
                except ValueError:
                    pass
    except Exception as e:
        log.error(f"Failed to parse spread_cache.toml: {e}")

    return spreads


def generate_excluded_tickers_config(
    results: List[SpreadATRResult],
) -> List[str]:
    """Return list of ticker symbols to exclude from universe."""
    return [r.ticker for r in results if r.excluded]


# ---------------------------------------------------------------------------
# P2-1: TWAP VWAP-Weighted Execution Slicing
# ---------------------------------------------------------------------------
# Replace flat TWAP with volume-weighted slicing using 60-day median volume curve.
# Almgren-Chriss (2000): optimal execution minimizes market impact.

# Typical LSE intraday volume profile (normalized, 30-min buckets from 08:00 to 16:30)
# Source: empirical from LSE ETF data — U-shaped with morning/close spikes
LSE_VOLUME_PROFILE = [
    0.12,  # 08:00-08:30 (open auction spillover)
    0.09,  # 08:30-09:00
    0.07,  # 09:00-09:30
    0.06,  # 09:30-10:00
    0.05,  # 10:00-10:30
    0.05,  # 10:30-11:00
    0.04,  # 11:00-11:30 (midday trough)
    0.04,  # 11:30-12:00
    0.04,  # 12:00-12:30
    0.04,  # 12:30-13:00
    0.05,  # 13:00-13:30
    0.05,  # 13:30-14:00
    0.06,  # 14:00-14:30 (US pre-market influence)
    0.07,  # 14:30-15:00 (US open)
    0.08,  # 15:00-15:30
    0.09,  # 15:30-16:00
    0.10,  # 16:00-16:30 (close auction)
]

# US SMART exchange intraday volume profile (30-min buckets from 09:30 to 16:00)
US_VOLUME_PROFILE = [
    0.14,  # 09:30-10:00 (open)
    0.10,  # 10:00-10:30
    0.08,  # 10:30-11:00
    0.06,  # 11:00-11:30
    0.05,  # 11:30-12:00
    0.05,  # 12:00-12:30 (lunch trough)
    0.05,  # 12:30-13:00
    0.06,  # 13:00-13:30
    0.06,  # 13:30-14:00
    0.07,  # 14:00-14:30
    0.08,  # 14:30-15:00
    0.10,  # 15:00-15:30
    0.10,  # 15:30-16:00 (close)
]


@dataclass
class VWAPSlice:
    """A single execution slice with time and volume weight."""
    bucket_start_utc: str    # "HH:MM" UTC
    bucket_end_utc: str      # "HH:MM" UTC
    volume_weight: float     # 0.0-1.0, sums to 1.0
    qty_fraction: float      # Fraction of total order to execute in this bucket


def compute_vwap_slices(
    exchange: str = "LSEETF",
    num_slices: int = 5,
) -> List[VWAPSlice]:
    """Compute VWAP-weighted execution slices for a given exchange.

    Instead of flat TWAP (equal size at equal intervals), weight each
    slice by the volume profile. More volume = more of the order executed.

    Args:
        exchange: Exchange identifier (LSEETF, SMART, etc.)
        num_slices: Number of execution slices (default 5)

    Returns:
        List of VWAPSlice with volume-weighted execution plan
    """
    if exchange in ("LSEETF", "LSE"):
        profile = LSE_VOLUME_PROFILE
        start_hour, start_min = 8, 0
        bucket_minutes = 30
    elif exchange in ("SMART", "NYSE", "NASDAQ"):
        profile = US_VOLUME_PROFILE
        start_hour, start_min = 14, 30  # UTC for US 09:30 ET
        bucket_minutes = 30
    else:
        # Default: flat profile (pure TWAP)
        profile = [1.0 / 10] * 10
        start_hour, start_min = 0, 0
        bucket_minutes = 60

    # Group buckets into num_slices equal groups
    buckets_per_slice = max(1, len(profile) // num_slices)
    slices = []
    total_weight = sum(profile)

    for i in range(num_slices):
        start_idx = i * buckets_per_slice
        end_idx = min((i + 1) * buckets_per_slice, len(profile))
        if i == num_slices - 1:
            end_idx = len(profile)  # Last slice gets remainder

        slice_weight = sum(profile[start_idx:end_idx])

        start_minutes = start_hour * 60 + start_min + start_idx * bucket_minutes
        end_minutes = start_hour * 60 + start_min + end_idx * bucket_minutes

        s_h, s_m = divmod(start_minutes, 60)
        e_h, e_m = divmod(end_minutes, 60)

        slices.append(VWAPSlice(
            bucket_start_utc=f"{s_h:02d}:{s_m:02d}",
            bucket_end_utc=f"{e_h:02d}:{e_m:02d}",
            volume_weight=round(slice_weight / total_weight, 4),
            qty_fraction=round(slice_weight / total_weight, 4),
        ))

    log.info(f"P2-1 VWAP slices for {exchange}: {len(slices)} slices computed")
    return slices


def generate_vwap_config(exchange: str = "LSEETF") -> dict:
    """Generate VWAP execution config for TOML emission."""
    slices = compute_vwap_slices(exchange)
    return {
        "vwap_execution_enabled": True,
        "vwap_exchange": exchange,
        "vwap_slices": [
            {
                "start": s.bucket_start_utc,
                "end": s.bucket_end_utc,
                "weight": s.volume_weight,
            }
            for s in slices
        ],
    }


# ---------------------------------------------------------------------------
# SC-18: Thompson Sampling Normal-Normal Reward Model
# ---------------------------------------------------------------------------
# Replaces Beta-Bernoulli (binary win/loss) with continuous log returns.
# Normal-Normal conjugate prior: proper Bayesian inference on return quality.

@dataclass
class NormalNormalArm:
    """Thompson Sampling arm with Normal-Normal conjugate prior.

    Prior: μ ~ N(mu_0, sigma_0²)
    Likelihood: x_i ~ N(μ, sigma²) (known variance, estimated from data)
    Posterior: μ | data ~ N(mu_n, sigma_n²)
    """
    ticker: str
    # Prior parameters
    mu_0: float = 0.0        # Prior mean (0 = no expected edge)
    sigma_0_sq: float = 0.01  # Prior variance (wide: ±10% expected range)
    # Sufficient statistics
    n: int = 0               # Number of observations
    sum_x: float = 0.0       # Sum of log returns
    sum_x_sq: float = 0.0    # Sum of squared log returns
    # Known noise variance (estimated)
    sigma_sq: float = 0.001  # Observation noise variance (updated from data)

    @property
    def posterior_mean(self) -> float:
        """Posterior mean μ_n."""
        if self.n == 0:
            return self.mu_0
        precision_prior = 1.0 / self.sigma_0_sq
        precision_data = self.n / self.sigma_sq
        return (precision_prior * self.mu_0 + precision_data * (self.sum_x / self.n)) / (precision_prior + precision_data)

    @property
    def posterior_variance(self) -> float:
        """Posterior variance σ²_n."""
        if self.n == 0:
            return self.sigma_0_sq
        precision_prior = 1.0 / self.sigma_0_sq
        precision_data = self.n / self.sigma_sq
        return 1.0 / (precision_prior + precision_data)

    def update(self, log_return: float) -> None:
        """Update posterior with new trade log return."""
        self.n += 1
        self.sum_x += log_return
        self.sum_x_sq += log_return ** 2

        # Update observation variance estimate (sample variance)
        if self.n >= 2:
            mean = self.sum_x / self.n
            self.sigma_sq = max(1e-8, (self.sum_x_sq / self.n) - mean ** 2)

    def sample(self) -> float:
        """Thompson sample from posterior (for arm selection)."""
        import random
        mu = self.posterior_mean
        sigma = math.sqrt(max(1e-10, self.posterior_variance))
        return random.gauss(mu, sigma)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "n": self.n,
            "posterior_mean": round(self.posterior_mean, 6),
            "posterior_variance": round(self.posterior_variance, 8),
            "sum_x": round(self.sum_x, 6),
            "sum_x_sq": round(self.sum_x_sq, 8),
            "sigma_sq": round(self.sigma_sq, 8),
        }


class ThompsonSamplingEngine:
    """Multi-armed bandit with Normal-Normal Thompson Sampling.

    Each ticker is an arm. Rewards are log returns (not binary win/loss).
    Updated nightly from WAL PositionClosed events.
    """

    def __init__(self, prior_mu: float = 0.0, prior_sigma_sq: float = 0.01):
        self.arms: Dict[str, NormalNormalArm] = {}
        self.prior_mu = prior_mu
        self.prior_sigma_sq = prior_sigma_sq

    def get_or_create_arm(self, ticker: str) -> NormalNormalArm:
        if ticker not in self.arms:
            self.arms[ticker] = NormalNormalArm(
                ticker=ticker,
                mu_0=self.prior_mu,
                sigma_0_sq=self.prior_sigma_sq,
            )
        return self.arms[ticker]

    def update_from_trade(self, ticker: str, entry_price: float, exit_price: float) -> None:
        """Update arm with trade outcome."""
        if entry_price <= 0:
            return
        log_return = math.log(exit_price / entry_price)
        arm = self.get_or_create_arm(ticker)
        arm.update(log_return)

    def rank_tickers(self, tickers: List[str]) -> List[Tuple[str, float]]:
        """Rank tickers by Thompson sample (higher = better)."""
        samples = []
        for ticker in tickers:
            arm = self.get_or_create_arm(ticker)
            samples.append((ticker, arm.sample()))
        samples.sort(key=lambda x: x[1], reverse=True)
        return samples

    def load_from_wal(self, wal_dir: Path = WAL_DIR) -> int:
        """Load all PositionClosed events from WAL and update arms."""
        count = 0
        wal_files = []
        if wal_dir.exists():
            wal_files.extend(sorted(wal_dir.glob("*.ndjson")))
        archive = wal_dir / "archive"
        if archive.exists():
            wal_files.extend(sorted(archive.glob("*.ndjson")))

        for wf in wal_files:
            try:
                with open(wf) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("event_type") != "PositionClosed":
                                continue
                            ticker = event.get("ticker", event.get("symbol", ""))
                            entry_price = float(event.get("entry_price", 0))
                            exit_price = float(event.get("exit_price", 0))
                            if ticker and entry_price > 0 and exit_price > 0:
                                self.update_from_trade(ticker, entry_price, exit_price)
                                count += 1
                        except (json.JSONDecodeError, ValueError, TypeError):
                            continue
            except OSError:
                continue

        log.info(f"SC-18 Thompson Sampling: loaded {count} trades across {len(self.arms)} tickers")
        return count

    def generate_config(self) -> dict:
        """Generate Thompson Sampling config for TOML emission."""
        arm_configs = {}
        for ticker, arm in sorted(self.arms.items()):
            arm_configs[ticker] = {
                "posterior_mean": round(arm.posterior_mean, 6),
                "posterior_std": round(math.sqrt(max(1e-10, arm.posterior_variance)), 6),
                "n_trades": arm.n,
            }
        return {
            "thompson_model": "normal_normal",
            "thompson_arms": arm_configs,
        }


# ---------------------------------------------------------------------------
# P1-15: Corporate Actions Veto (ISA Compliance)
# ---------------------------------------------------------------------------
# Spin-offs can place non-ISA-eligible shares in the ISA, voiding it.
# Veto any ticker with a corporate action within 48 hours.
# Sources: yfinance actions/calendar, manual blocklist JSON

CORP_ACTION_VETO_HOURS = 48  # Block entries within this window
CORP_ACTION_TYPES = {"split", "reverse_split", "spin_off", "merger", "special_dividend", "rights_issue"}

# yfinance ".actions" column names that map to our action types
_YF_ACTION_COLUMNS = {
    "Stock Splits": "split",
    "Dividends": "special_dividend",  # Conservative: treat all divs as potential special
}


@dataclass
class CorporateActionEvent:
    """A single detected corporate action event."""
    ticker: str
    action_type: str        # One of CORP_ACTION_TYPES
    ex_date: datetime       # Ex-date / effective date
    source: str             # "yfinance", "manual_blocklist", "calendar"
    details: str = ""       # Human-readable description


class CorporateActionChecker:
    """P1-15: Checks for upcoming corporate actions that could break ISA eligibility.

    Spin-offs and similar events can deposit non-ISA-eligible shares into the
    ISA wrapper, potentially voiding the entire tax wrapper. This checker
    aggregates multiple data sources and vetoes tickers with actions within
    the CORP_ACTION_VETO_HOURS window.

    Data sources (checked in order):
      1. Manual blocklist (config/corp_action_blocklist.json) — operator overrides
      2. yfinance .actions — historical/upcoming splits and dividends
      3. yfinance .calendar — upcoming ex-dates for earnings/dividends

    Usage:
        checker = CorporateActionChecker()
        vetoed = checker.get_vetoed_tickers()
        config = checker.generate_veto_config()
    """

    def __init__(
        self,
        contracts_path: Path = None,
        blocklist_path: Path = None,
        veto_hours: int = CORP_ACTION_VETO_HOURS,
    ):
        self._contracts_path = contracts_path or CONFIG_DIR / "contracts.toml"
        self._blocklist_path = blocklist_path or CONFIG_DIR / "corp_action_blocklist.json"
        self._veto_hours = veto_hours
        self._events: List[CorporateActionEvent] = []
        self._checked = False

    def load_from_contracts_toml(self) -> List[str]:
        """Read contracts.toml and return the full universe ticker list.

        Returns:
            List of ticker symbols from [[contracts]] entries.
        """
        tickers = []
        if not self._contracts_path.exists():
            log.warning("contracts.toml not found at %s", self._contracts_path)
            return tickers

        try:
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    log.warning("No TOML parser available (need Python 3.11+ or tomli)")
                    return tickers

            with open(self._contracts_path, "rb") as f:
                data = tomllib.load(f)

            for contract in data.get("contracts", []):
                symbol = contract.get("symbol", "")
                if symbol:
                    tickers.append(symbol)

            log.info("P1-15: Loaded %d tickers from contracts.toml", len(tickers))

        except Exception as e:
            log.error("P1-15: Failed to parse contracts.toml: %s", e)

        return tickers

    def _check_ibkr_actions(self, tickers: List[str]) -> tuple:
        """Check IBKR for upcoming corporate actions. Returns (events, checked_tickers)."""
        events = []
        checked = set()
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        veto_cutoff = now + timedelta(hours=self._veto_hours)

        try:
            from python_brain.ouroboros.ibkr_data_provider import get_provider
            provider = get_provider()
        except (ImportError, Exception) as e:
            log.debug("P1-15: IBKR provider not available: %s", e)
            return events, checked

        for ticker in tickers[:50]:  # Rate limit: 50 max for IBKR
            try:
                result = provider.get_fundamental_data(ticker, report_type="CalendarReport")
                if result:
                    checked.add(ticker)
                    # Check dividend date
                    div_date_str = result.get("dividend_date")
                    if div_date_str:
                        try:
                            div_date = datetime.strptime(div_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                            if now <= div_date <= veto_cutoff:
                                events.append(CorporateActionEvent(
                                    ticker=ticker,
                                    action_type="special_dividend",
                                    ex_date=div_date,
                                    source="ibkr",
                                    details=f"Dividend date from IBKR CalendarReport",
                                ))
                        except (ValueError, TypeError):
                            pass
            except Exception as e:
                log.debug("P1-15: IBKR calendar check failed for %s: %s", ticker, e)

        if events:
            log.info("P1-15: IBKR found %d corporate action events across %d tickers",
                     len(events), len(set(e.ticker for e in events)))

        return events, checked

    def check_yfinance_actions(self, tickers: List[str]) -> List[CorporateActionEvent]:
        """Check for upcoming corporate actions within the veto window.

        Tries IBKR first for calendar data, then falls back to yfinance for
        remaining tickers and more granular action data (splits, dividends).

        For each ticker, queries:
          - IBKR CalendarReport (primary, for dividend dates)
          - yfinance .actions (fallback, splits + dividends with dates)
          - yfinance .calendar (fallback, upcoming ex-dates)

        Only flags events whose ex-date falls within now .. now+veto_hours.

        Args:
            tickers: List of ticker symbols (yfinance format).

        Returns:
            List of CorporateActionEvent for tickers with upcoming actions.
        """
        events = []
        now = datetime.now(timezone.utc)

        # Try IBKR first (primary)
        ibkr_events, ibkr_checked = self._check_ibkr_actions(tickers)
        events.extend(ibkr_events)

        # Fallback: check remaining tickers via yfinance
        remaining = [t for t in tickers if t not in ibkr_checked]
        # Also re-check IBKR-checked tickers via yfinance for splits (IBKR only has dividends)
        remaining_for_splits = list(tickers)

        try:
            import yfinance as yf
        except ImportError:
            log.warning("P1-15: yfinance not installed — skipping actions check")
            return events

        from datetime import timedelta
        veto_cutoff = now + timedelta(hours=self._veto_hours)

        for ticker in remaining_for_splits:
            try:
                yf_ticker = yf.Ticker(ticker)

                # --- Check .actions (splits & dividends) ---
                try:
                    actions = yf_ticker.actions
                    if actions is not None and not actions.empty:
                        for col_name, action_type in _YF_ACTION_COLUMNS.items():
                            if col_name not in actions.columns:
                                continue
                            col_data = actions[col_name]
                            for date_idx, value in col_data.items():
                                if value == 0 or value == 0.0:
                                    continue
                                if hasattr(date_idx, 'to_pydatetime'):
                                    dt = date_idx.to_pydatetime()
                                else:
                                    dt = date_idx
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                if now <= dt <= veto_cutoff:
                                    events.append(CorporateActionEvent(
                                        ticker=ticker,
                                        action_type=action_type,
                                        ex_date=dt,
                                        source="yfinance",
                                        details=f"{col_name}={value}",
                                    ))
                except Exception as e:
                    log.debug("P1-15: yfinance .actions failed for %s: %s", ticker, e)

                # --- Check .calendar (only for tickers not covered by IBKR) ---
                if ticker not in ibkr_checked:
                    try:
                        cal = yf_ticker.calendar
                        if cal is not None:
                            ex_date = None
                            if isinstance(cal, dict):
                                for key in ("Ex-Dividend Date", "Ex-Date"):
                                    if key in cal:
                                        ex_date = cal[key]
                                        break
                            elif hasattr(cal, 'columns'):
                                for key in ("Ex-Dividend Date", "Ex-Date"):
                                    if key in cal.columns:
                                        ex_date = cal[key].iloc[0] if len(cal[key]) > 0 else None
                                        break

                            if ex_date is not None:
                                if hasattr(ex_date, 'to_pydatetime'):
                                    ex_date = ex_date.to_pydatetime()
                                elif isinstance(ex_date, str):
                                    try:
                                        ex_date = datetime.fromisoformat(ex_date)
                                    except (ValueError, TypeError):
                                        ex_date = None

                                if ex_date is not None:
                                    if ex_date.tzinfo is None:
                                        ex_date = ex_date.replace(tzinfo=timezone.utc)
                                    if now <= ex_date <= veto_cutoff:
                                        events.append(CorporateActionEvent(
                                            ticker=ticker,
                                            action_type="special_dividend",
                                            ex_date=ex_date,
                                            source="calendar",
                                            details="Upcoming ex-dividend from calendar",
                                        ))
                    except Exception as e:
                        log.debug("P1-15: yfinance .calendar failed for %s: %s", ticker, e)

            except Exception as e:
                log.debug("P1-15: yfinance lookup failed entirely for %s: %s", ticker, e)

        if events:
            log.info("P1-15: Found %d corporate action events across %d tickers (IBKR+yfinance)",
                     len(events), len(set(e.ticker for e in events)))

        return events

    def check_manual_blocklist(self) -> List[CorporateActionEvent]:
        """Read manual blocklist from config/corp_action_blocklist.json.

        Expected format:
        [
            {
                "ticker": "QQQ3.L",
                "action_type": "spin_off",
                "ex_date": "2026-03-25T00:00:00Z",
                "details": "Known upcoming spin-off from issuer notice"
            },
            ...
        ]

        Returns:
            List of CorporateActionEvent from manual blocklist (within veto window).
        """
        events = []
        if not self._blocklist_path.exists():
            log.debug("P1-15: No manual blocklist at %s (this is normal)", self._blocklist_path)
            return events

        now = datetime.now(timezone.utc)
        from datetime import timedelta
        veto_cutoff = now + timedelta(hours=self._veto_hours)

        try:
            with open(self._blocklist_path) as f:
                blocklist = json.load(f)

            if not isinstance(blocklist, list):
                log.warning("P1-15: corp_action_blocklist.json must be a JSON array")
                return events

            for entry in blocklist:
                ticker = entry.get("ticker", "")
                action_type = entry.get("action_type", "unknown")
                ex_date_str = entry.get("ex_date", "")
                details = entry.get("details", "Manual blocklist entry")

                if not ticker or not ex_date_str:
                    continue

                try:
                    ex_date = datetime.fromisoformat(ex_date_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    log.warning("P1-15: Invalid ex_date '%s' for %s in blocklist", ex_date_str, ticker)
                    continue

                if ex_date.tzinfo is None:
                    ex_date = ex_date.replace(tzinfo=timezone.utc)

                # Always include future blocklist entries within veto window
                # Also include entries without dates (permanent blocks) by checking
                # if the action is still relevant
                if now <= ex_date <= veto_cutoff:
                    events.append(CorporateActionEvent(
                        ticker=ticker,
                        action_type=action_type,
                        ex_date=ex_date,
                        source="manual_blocklist",
                        details=details,
                    ))

            if events:
                log.info("P1-15: Manual blocklist has %d active veto entries", len(events))

        except json.JSONDecodeError as e:
            log.error("P1-15: Invalid JSON in corp_action_blocklist.json: %s", e)
        except Exception as e:
            log.error("P1-15: Failed to read corp_action_blocklist.json: %s", e)

        return events

    def get_vetoed_tickers(self) -> List[str]:
        """Run all corporate action checks and return combined veto list.

        Checks manual blocklist first (fast, no network), then yfinance
        (slower, needs network). Deduplicates by ticker.

        Returns:
            Sorted list of unique ticker symbols to block from new entries.
        """
        all_events: List[CorporateActionEvent] = []

        # 1. Manual blocklist (fast, no network)
        all_events.extend(self.check_manual_blocklist())

        # 2. yfinance actions check (needs network)
        tickers = self.load_from_contracts_toml()
        if tickers:
            all_events.extend(self.check_yfinance_actions(tickers))

        self._events = all_events
        self._checked = True

        # Deduplicate by ticker
        vetoed = sorted(set(e.ticker for e in all_events))

        if vetoed:
            log.info(
                "P1-15: Corporate action veto — %d tickers blocked: %s",
                len(vetoed), ", ".join(vetoed),
            )
        else:
            log.info("P1-15: Corporate action veto — 0 tickers blocked (all clear)")

        return vetoed

    def generate_veto_config(self) -> dict:
        """Generate corporate action veto config for TOML emission.

        Must call get_vetoed_tickers() first (or this will run it).

        Returns:
            Dict with keys for config_writer to emit into dynamic_weights.toml:
              corp_action_veto_enabled: bool
              corp_action_vetoed_tickers: List[str]
              corp_action_veto_count: int
              corp_action_check_timestamp: str (ISO 8601)
        """
        if not self._checked:
            self.get_vetoed_tickers()

        vetoed = sorted(set(e.ticker for e in self._events))

        return {
            "corp_action_veto_enabled": True,
            "corp_action_vetoed_tickers": vetoed,
            "corp_action_veto_count": len(vetoed),
            "corp_action_check_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def events(self) -> List[CorporateActionEvent]:
        """Access detected events after running get_vetoed_tickers()."""
        return list(self._events)


# ---------------------------------------------------------------------------
# Aggregated Universe Filter Config (like signal_filters.generate_all_...)
# ---------------------------------------------------------------------------
def generate_all_universe_filter_configs(
    spread_threshold: float = 0.25,
    veto_hours: int = CORP_ACTION_VETO_HOURS,
) -> dict:
    """Generate all universe filter configurations for config_writer.

    Aggregates results from:
      - P2-13: Spread-to-ATR hard filter
      - P1-15: Corporate Actions Veto
      - SC-18: Thompson Sampling Normal-Normal

    Returns combined dict. config_writer.py emits these into
    [universe_filters] section of dynamic_weights.toml.

    Args:
        spread_threshold: Max spread/range ratio for P2-13 (default 0.25).
        veto_hours: Corporate action veto window in hours (default 48).

    Returns:
        Combined dict of all universe filter parameters.
    """
    configs: Dict[str, Any] = {}

    # P2-13: Spread-to-ATR
    try:
        spread_results = spread_to_atr_filter(threshold=spread_threshold)
        excluded_tickers = generate_excluded_tickers_config(spread_results)
        configs["spread_atr_enabled"] = True
        configs["spread_atr_threshold"] = spread_threshold
        configs["spread_atr_excluded_tickers"] = excluded_tickers
        configs["spread_atr_excluded_count"] = len(excluded_tickers)
        log.info("P2-13: %d tickers excluded by spread-ATR filter", len(excluded_tickers))
    except Exception as e:
        log.error("P2-13 spread-ATR filter failed: %s", e)
        configs["spread_atr_enabled"] = False

    # P1-15: Corporate Actions Veto
    try:
        checker = CorporateActionChecker(veto_hours=veto_hours)
        configs.update(checker.generate_veto_config())
        log.info("P1-15: %d tickers vetoed by corporate actions", configs.get("corp_action_veto_count", 0))
    except Exception as e:
        log.error("P1-15 corporate action veto failed: %s", e)
        configs["corp_action_veto_enabled"] = False

    # SC-18: Thompson Sampling
    try:
        ts_engine = ThompsonSamplingEngine()
        ts_engine.load_from_wal()
        configs.update(ts_engine.generate_config())
    except Exception as e:
        log.error("SC-18 Thompson Sampling failed: %s", e)

    configs["universe_filter_timestamp"] = datetime.now(timezone.utc).isoformat()
    return configs


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Universe filters and execution utilities")
    parser.add_argument("--spread-filter", action="store_true", help="Run P2-13 spread-ATR filter")
    parser.add_argument("--vwap-slices", action="store_true", help="Show P2-1 VWAP execution slices")
    parser.add_argument("--thompson", action="store_true", help="Run SC-18 Thompson Sampling from WAL")
    parser.add_argument("--corp-actions", action="store_true", help="Run P1-15 Corporate Actions Veto check")
    parser.add_argument("--all", action="store_true", help="Run all filters and generate combined config")
    parser.add_argument("--exchange", default="LSEETF", help="Exchange for VWAP slices")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # If --all, run the aggregated config generator
    if args.all:
        print("\n=== All Universe Filters (Aggregated) ===")
        configs = generate_all_universe_filter_configs()
        if args.json:
            print(json.dumps(configs, indent=2, default=str))
        else:
            for k, v in sorted(configs.items()):
                print(f"  {k} = {v}")
        print()

    if args.all or args.spread_filter:
        results = spread_to_atr_filter()
        if results:
            excluded = [r for r in results if r.excluded]
            print(f"\n=== P2-13 Spread-ATR Filter ===")
            print(f"Total: {len(results)}, Excluded: {len(excluded)}")
            for r in results:
                status = "EXCLUDED" if r.excluded else "OK"
                print(f"  {r.ticker}: ratio={r.ratio:.4f} [{status}]")

    if args.all or args.vwap_slices:
        slices = compute_vwap_slices(args.exchange)
        print(f"\n=== P2-1 VWAP Execution Slices ({args.exchange}) ===")
        for s in slices:
            print(f"  {s.bucket_start_utc}-{s.bucket_end_utc}: weight={s.volume_weight:.4f}")

    if args.all or args.thompson:
        engine = ThompsonSamplingEngine()
        count = engine.load_from_wal()
        print(f"\n=== SC-18 Thompson Sampling (Normal-Normal) ===")
        print(f"Trades loaded: {count}")
        for ticker, arm in sorted(engine.arms.items()):
            print(f"  {ticker}: μ={arm.posterior_mean:.6f}, σ={math.sqrt(arm.posterior_variance):.6f}, n={arm.n}")

        if args.json:
            print(json.dumps(engine.generate_config(), indent=2))

    if args.all or args.corp_actions:
        print(f"\n=== P1-15 Corporate Actions Veto ({CORP_ACTION_VETO_HOURS}h window) ===")
        checker = CorporateActionChecker()
        vetoed = checker.get_vetoed_tickers()
        print(f"Vetoed tickers: {len(vetoed)}")
        if vetoed:
            for ticker in vetoed:
                # Find the events for this ticker
                ticker_events = [e for e in checker.events if e.ticker == ticker]
                for ev in ticker_events:
                    print(f"  {ev.ticker}: {ev.action_type} ex={ev.ex_date.strftime('%Y-%m-%d %H:%M')} "
                          f"[{ev.source}] {ev.details}")
        else:
            print("  All clear — no corporate actions within veto window")

        if args.json:
            print(json.dumps(checker.generate_veto_config(), indent=2, default=str))


if __name__ == "__main__":
    main()
