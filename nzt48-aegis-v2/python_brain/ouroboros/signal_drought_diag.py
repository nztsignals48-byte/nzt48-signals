"""ISS-002 — Signal Drought Diagnostic Pipeline Tracer.

Traces the entire tick -> signal pipeline to find exactly where ticks get
filtered out when the system receives thousands of ticks but generates 0 signals.

Analyses:
  1. WAL events — SignalRejected counts by gate, ticker, and reason
  2. Config — dynamic_weights.toml thresholds vs known working defaults
  3. Gate vetoes — /app/data/gate_vetoes.ndjson per-indicator/ticker/time-of-day
  4. Bridge config — hardcoded thresholds in bridge.py (warmup, cooldown, floors)

Quarantine rules (same as all Ouroboros modules):
  - READ-ONLY: never writes to WAL, config, or engine state
  - Safe to run during live trading

Usage:
    python3 -m python_brain.ouroboros.signal_drought_diag
    python3 -m python_brain.ouroboros.signal_drought_diag --json
    python3 -m python_brain.ouroboros.signal_drought_diag --fix-preview
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup — works both locally and in Docker (/app)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SignalDrought] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("signal_drought_diag")

# ---------------------------------------------------------------------------
# Known working defaults — baseline for comparison
# ---------------------------------------------------------------------------
KNOWN_DEFAULTS = {
    "confidence_floor": {
        "working": 45,
        "warn_above": 70,
        "danger_above": 80,
        "description": "Adaptive confidence floor from dynamic_weights.toml",
    },
    "leverage_conf_floor_3x": {
        "working": 65,
        "warn_above": 75,
        "danger_above": 85,
        "description": "Confidence floor for 3x leveraged ETPs (hardcoded in bridge.py)",
    },
    "leverage_conf_floor_5x": {
        "working": 80,
        "warn_above": 90,
        "danger_above": 95,
        "description": "Confidence floor for 5x leveraged ETPs (hardcoded in bridge.py)",
    },
    "min_warmup_bars": {
        "working": 200,
        "warn_above": 300,
        "danger_above": 500,
        "description": "Minimum ticks before signal generation (200 = ~16 min of 5s data)",
    },
    "cooldown_ticks": {
        "working": 300,
        "warn_above": 500,
        "danger_above": 1000,
        "description": "Signal cooldown per ticker (300 x 5s = 25 min)",
    },
    "hurst_block_below": {
        "working": 0.40,
        "warn_below": 0.45,
        "danger_below": 0.50,
        "description": "Hurst < this blocks momentum signals (mean-reverting regime)",
    },
    "vwap_extension_max_pct": {
        "working": 1.5,
        "warn_below": 1.0,
        "danger_below": 0.5,
        "description": "Max % above VWAP for long entry (anti-chase)",
    },
    "structural_score_min": {
        "working": 30,
        "warn_above": 40,
        "danger_above": 50,
        "description": "Minimum Structural Tradability Score (0-100)",
    },
    "spread_limit_leveraged_pct": {
        "working": 2.0,
        "warn_below": 1.5,
        "danger_below": 1.0,
        "description": "Max spread % for leveraged ETPs",
    },
    "spread_limit_unleveraged_pct": {
        "working": 0.5,
        "warn_below": 0.3,
        "danger_below": 0.2,
        "description": "Max spread % for unleveraged instruments",
    },
}


# ---------------------------------------------------------------------------
# Data classes for structured results
# ---------------------------------------------------------------------------
@dataclass
class WalAnalysis:
    """Results from WAL file scanning."""
    total_wal_events: int = 0
    signal_rejected_count: int = 0
    signal_rejected_by_gate: Dict[str, int] = field(default_factory=dict)
    signal_rejected_by_ticker: Dict[str, int] = field(default_factory=dict)
    signal_rejected_by_reason: Dict[str, int] = field(default_factory=dict)
    routed_orders: int = 0
    position_closed: int = 0
    fill_events: int = 0
    system_ready_count: int = 0
    event_type_counts: Dict[str, int] = field(default_factory=dict)
    wal_files_scanned: int = 0


@dataclass
class ConfigAnalysis:
    """Results from config file analysis."""
    confidence_floor: Optional[float] = None
    indicator_gates: List[Dict] = field(default_factory=list)
    ticker_blacklist: List[str] = field(default_factory=list)
    bayesian_win_rate: Optional[float] = None
    bayesian_trade_count: Optional[int] = None
    regime_best: Optional[str] = None
    regime_worst: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    raw_config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GateVetoAnalysis:
    """Results from gate_vetoes.ndjson analysis."""
    total_vetoes: int = 0
    vetoes_by_gate: Dict[str, int] = field(default_factory=dict)
    vetoes_by_ticker: Dict[str, int] = field(default_factory=dict)
    vetoes_by_hour: Dict[int, int] = field(default_factory=dict)
    top_gate: Optional[str] = None
    top_ticker: Optional[str] = None
    sample_vetoes: List[Dict] = field(default_factory=list)
    indicator_distributions: Dict[str, Dict[str, float]] = field(default_factory=dict)
    veto_file_exists: bool = False
    veto_file_path: str = ""


@dataclass
class BridgeConfigCheck:
    """Results from bridge.py hardcoded threshold analysis."""
    confidence_floor_adaptive: Optional[float] = None
    confidence_floor_3x: int = 65
    confidence_floor_5x: int = 80
    min_warmup_bars: int = 200
    cooldown_ticks: int = 300
    hurst_block_threshold: float = 0.40
    vwap_extension_max: float = 1.5
    structural_score_min: int = 30
    spread_limit_leveraged: float = 2.0
    spread_limit_unleveraged: float = 0.5
    mtf_confirmation_required: bool = True
    volume_slope_gate_active: bool = True
    bars_per_5min: int = 60
    warnings: List[str] = field(default_factory=list)


@dataclass
class FunnelStage:
    """One stage of the rejection funnel."""
    stage: str
    description: str
    ticks_entering: int
    ticks_blocked: int
    ticks_passing: int
    block_pct: float


@dataclass
class FixRecommendation:
    """One recommended config change."""
    parameter: str
    current_value: Any
    recommended_value: Any
    reason: str
    impact: str  # "high", "medium", "low"
    config_file: str


@dataclass
class DiagnosticReport:
    """Complete diagnostic report."""
    timestamp: str
    wal: WalAnalysis
    config: ConfigAnalysis
    gate_vetoes: GateVetoAnalysis
    bridge: BridgeConfigCheck
    funnel: List[FunnelStage]
    recommendations: List[FixRecommendation]
    verdict: str


# ---------------------------------------------------------------------------
# 1. WAL Analysis
# ---------------------------------------------------------------------------
def analyze_wal(wal_dir: Optional[Path] = None) -> WalAnalysis:
    """Scan all WAL files for signal-related events."""
    if wal_dir is None:
        wal_dir = WAL_DIR
    result = WalAnalysis()

    wal_files = []
    if wal_dir.exists():
        wal_files.extend(sorted(wal_dir.glob("*.ndjson")))
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

    result.wal_files_scanned = len(wal_files)

    for wf in wal_files:
        try:
            with open(wf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    result.total_wal_events += 1
                    payload = event.get("payload", {})

                    # Determine event type from payload key
                    # WAL payloads are tagged enums: {"payload": {"SignalRejected": {...}}}
                    if isinstance(payload, dict):
                        for event_type, data in payload.items():
                            result.event_type_counts[event_type] = (
                                result.event_type_counts.get(event_type, 0) + 1
                            )

                            if event_type == "SignalRejected":
                                result.signal_rejected_count += 1
                                gate = data.get("gate_name", "unknown")
                                result.signal_rejected_by_gate[gate] = (
                                    result.signal_rejected_by_gate.get(gate, 0) + 1
                                )
                                symbol = data.get("symbol", "unknown")
                                result.signal_rejected_by_ticker[symbol] = (
                                    result.signal_rejected_by_ticker.get(symbol, 0) + 1
                                )
                                reason = data.get("gate_reason", "unknown")
                                # Truncate reason to first 80 chars for grouping
                                reason_key = reason[:80] if len(reason) > 80 else reason
                                result.signal_rejected_by_reason[reason_key] = (
                                    result.signal_rejected_by_reason.get(reason_key, 0) + 1
                                )

                            elif event_type == "RoutedOrder":
                                result.routed_orders += 1
                            elif event_type == "PositionClosed":
                                result.position_closed += 1
                            elif event_type == "FillEvent":
                                result.fill_events += 1
                            elif event_type == "SystemReady":
                                result.system_ready_count += 1
                            break  # Only one key per payload
        except OSError as e:
            log.warning(f"Failed to read WAL file {wf}: {e}")

    return result


# ---------------------------------------------------------------------------
# 2. Config Analysis
# ---------------------------------------------------------------------------
def analyze_config(config_dir: Optional[Path] = None) -> ConfigAnalysis:
    """Read dynamic_weights.toml and flag potential issues."""
    if config_dir is None:
        config_dir = CONFIG_DIR
    result = ConfigAnalysis()

    dw_path = config_dir / "dynamic_weights.toml"
    if not dw_path.exists():
        result.warnings.append(f"dynamic_weights.toml NOT FOUND at {dw_path}")
        return result

    try:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                result.warnings.append(
                    "Neither tomllib nor tomli available — cannot parse TOML"
                )
                return result

        with open(dw_path, "rb") as f:
            data = tomllib.load(f)

        result.raw_config = data

        # Signal section
        signal = data.get("signal", {})
        result.confidence_floor = signal.get("confidence_floor")
        if result.confidence_floor is not None:
            cf = result.confidence_floor
            if cf >= KNOWN_DEFAULTS["confidence_floor"]["danger_above"]:
                result.warnings.append(
                    f"DANGER: confidence_floor={cf} is extremely high "
                    f"(>={KNOWN_DEFAULTS['confidence_floor']['danger_above']}). "
                    f"Very few signals will pass. Recommend {KNOWN_DEFAULTS['confidence_floor']['working']}."
                )
            elif cf >= KNOWN_DEFAULTS["confidence_floor"]["warn_above"]:
                result.warnings.append(
                    f"WARNING: confidence_floor={cf} is high "
                    f"(>={KNOWN_DEFAULTS['confidence_floor']['warn_above']}). "
                    f"May suppress many valid signals."
                )

        # Ticker blacklist
        bl = data.get("ticker_blacklist", {}).get("tickers", [])
        result.ticker_blacklist = bl
        if len(bl) > 5:
            result.warnings.append(
                f"WARNING: {len(bl)} tickers blacklisted — may be overly aggressive. "
                f"Blacklisted: {', '.join(bl[:10])}"
            )

        # Indicator gates
        gates = data.get("indicator_gates", {})
        rules = gates.get("rules", [])
        result.indicator_gates = rules
        for rule in rules:
            ind = rule.get("indicator", "?")
            direction = rule.get("direction", "?")
            threshold = rule.get("threshold", "?")
            # Check ADX gate
            if ind == "adx" and direction == "above" and isinstance(threshold, (int, float)):
                if threshold >= 25:
                    result.warnings.append(
                        f"WARNING: ADX gate requires ADX > {threshold}. "
                        f"ADX > 25 blocks >60% of ticks in typical LSE conditions. "
                        f"Recommend ADX > 15 for paper trading."
                    )
            # Check RVOL gate
            if ind == "rvol" and direction == "above" and isinstance(threshold, (int, float)):
                if threshold >= 2.0:
                    result.warnings.append(
                        f"WARNING: RVOL gate requires RVOL > {threshold}. "
                        f"RVOL > 2.0 only occurs during high-volume events. "
                        f"Recommend RVOL > 1.0 for paper trading."
                    )
            # Check Hurst gate
            if ind == "hurst" and direction == "above" and isinstance(threshold, (int, float)):
                if threshold >= 0.55:
                    result.warnings.append(
                        f"WARNING: Hurst gate requires H > {threshold}. "
                        f"Only strongly trending markets pass this. "
                        f"Recommend H > 0.45 for paper trading."
                    )

        # Bayesian stats
        bayesian = data.get("bayesian", {})
        result.bayesian_win_rate = bayesian.get("win_rate")
        result.bayesian_trade_count = bayesian.get("trade_count")

        # Regime
        regime = data.get("regime", {})
        result.regime_best = regime.get("best")
        result.regime_worst = regime.get("worst")

    except Exception as e:
        result.warnings.append(f"Failed to parse dynamic_weights.toml: {e}")

    return result


# ---------------------------------------------------------------------------
# 3. Gate Veto Analysis
# ---------------------------------------------------------------------------
def analyze_gate_vetoes(data_dir: Optional[Path] = None) -> GateVetoAnalysis:
    """Read gate_vetoes.ndjson and compute per-gate/ticker/time-of-day stats."""
    if data_dir is None:
        data_dir = DATA_DIR
    result = GateVetoAnalysis()

    # Try multiple paths (Docker vs local)
    candidates = [
        data_dir / "gate_vetoes.ndjson",
        Path("/app/data/gate_vetoes.ndjson"),
    ]

    veto_path = None
    for c in candidates:
        if c.exists():
            veto_path = c
            break

    if veto_path is None:
        result.veto_file_exists = False
        result.veto_file_path = str(candidates[0])
        return result

    result.veto_file_exists = True
    result.veto_file_path = str(veto_path)

    # Collect indicator values for distribution analysis
    indicator_values: Dict[str, List[float]] = defaultdict(list)

    try:
        with open(veto_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                result.total_vetoes += 1

                gate = record.get("gate", "unknown")
                result.vetoes_by_gate[gate] = result.vetoes_by_gate.get(gate, 0) + 1

                symbol = record.get("symbol", "unknown")
                result.vetoes_by_ticker[symbol] = result.vetoes_by_ticker.get(symbol, 0) + 1

                # Time-of-day from timestamp
                ts = record.get("ts", 0)
                if ts > 0:
                    try:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                        hour = dt.hour
                        result.vetoes_by_hour[hour] = result.vetoes_by_hour.get(hour, 0) + 1
                    except (ValueError, OSError):
                        pass

                # Collect indicator values for distribution analysis
                indicators = record.get("indicators", {})
                for ind_name in ("hurst", "adx", "rvol", "vol_slope", "spread_pct",
                                 "structural_score", "n_5min_bars", "n_ticks"):
                    val = indicators.get(ind_name)
                    if val is not None and isinstance(val, (int, float)):
                        indicator_values[ind_name].append(float(val))

                # Keep first 5 sample vetoes for human inspection
                if len(result.sample_vetoes) < 5:
                    result.sample_vetoes.append(record)

    except OSError as e:
        log.warning(f"Failed to read gate vetoes file: {e}")
        return result

    # Compute indicator distributions (min, max, mean, p25, p50, p75)
    for ind_name, values in indicator_values.items():
        if not values:
            continue
        values_sorted = sorted(values)
        n = len(values_sorted)
        result.indicator_distributions[ind_name] = {
            "count": n,
            "min": round(values_sorted[0], 4),
            "max": round(values_sorted[-1], 4),
            "mean": round(sum(values_sorted) / n, 4),
            "p25": round(values_sorted[int(n * 0.25)], 4),
            "p50": round(values_sorted[int(n * 0.50)], 4),
            "p75": round(values_sorted[int(n * 0.75)], 4),
        }

    # Top gate and ticker
    if result.vetoes_by_gate:
        result.top_gate = max(result.vetoes_by_gate, key=result.vetoes_by_gate.get)
    if result.vetoes_by_ticker:
        result.top_ticker = max(result.vetoes_by_ticker, key=result.vetoes_by_ticker.get)

    return result


# ---------------------------------------------------------------------------
# 4. Bridge Config Check (extract hardcoded values from bridge.py)
# ---------------------------------------------------------------------------
def check_bridge_config() -> BridgeConfigCheck:
    """Extract and verify hardcoded thresholds from bridge.py."""
    result = BridgeConfigCheck()

    bridge_path = _PROJECT_ROOT / "python_brain" / "bridge.py"
    if not bridge_path.exists():
        result.warnings.append(f"bridge.py not found at {bridge_path}")
        return result

    try:
        source = bridge_path.read_text()
    except OSError as e:
        result.warnings.append(f"Failed to read bridge.py: {e}")
        return result

    # Extract key constants via regex
    _extract_int(source, r"SIGNAL_COOLDOWN_TICKS\s*=\s*(\d+)", result, "cooldown_ticks", 300)
    _extract_int(source, r"MIN_WARMUP_BARS\s*=\s*(\d+)", result, "min_warmup_bars", 200)
    _extract_int(source, r"BARS_PER_5MIN\s*=\s*(\d+)", result, "bars_per_5min", 60)

    # Leverage confidence floors
    _extract_int(source, r"leverage_conf_floor\s*=\s*80", result, "confidence_floor_5x", 80)
    _extract_int(source, r"leverage_conf_floor\s*=\s*65", result, "confidence_floor_3x", 65)

    # Hurst block threshold
    m = re.search(r"if hurst < ([\d.]+):", source)
    if m:
        result.hurst_block_threshold = float(m.group(1))

    # VWAP extension max
    m = re.search(r"vwap_distance_pct > ([\d.]+):", source)
    if m:
        result.vwap_extension_max = float(m.group(1))

    # Structural score minimum
    m = re.search(r"structural_score < (\d+):", source)
    if m:
        result.structural_score_min = int(m.group(1))

    # Spread limits
    m = re.search(r"spread_limit = ([\d.]+)\s+if leverage >= 3", source)
    if m:
        result.spread_limit_leveraged = float(m.group(1))

    # Check for potential issues
    _check_bridge_thresholds(result)

    return result


def _extract_int(source: str, pattern: str, result: BridgeConfigCheck, attr: str, default: int):
    """Extract an integer from source using regex, set on result."""
    m = re.search(pattern, source)
    if m:
        try:
            setattr(result, attr, int(m.group(1)) if m.lastindex else default)
        except (ValueError, IndexError):
            pass


def _check_bridge_thresholds(result: BridgeConfigCheck):
    """Flag any bridge.py thresholds that are too aggressive."""
    # Warmup check
    if result.min_warmup_bars > KNOWN_DEFAULTS["min_warmup_bars"]["warn_above"]:
        result.warnings.append(
            f"WARNING: MIN_WARMUP_BARS={result.min_warmup_bars} is high. "
            f"First {result.min_warmup_bars * 5 / 60:.0f} minutes of each session produce ZERO signals. "
            f"Working default: {KNOWN_DEFAULTS['min_warmup_bars']['working']}."
        )

    # Cooldown check
    if result.cooldown_ticks > KNOWN_DEFAULTS["cooldown_ticks"]["warn_above"]:
        result.warnings.append(
            f"WARNING: SIGNAL_COOLDOWN_TICKS={result.cooldown_ticks} is high. "
            f"After each signal, ticker is silenced for {result.cooldown_ticks * 5 / 60:.0f} minutes. "
            f"Working default: {KNOWN_DEFAULTS['cooldown_ticks']['working']}."
        )

    # 5-min bar requirement analysis
    bars_needed = result.bars_per_5min * 3  # Need at least 3 five-min bars
    if result.min_warmup_bars < bars_needed:
        result.warnings.append(
            f"INFO: Warmup ({result.min_warmup_bars}) < 3x 5-min bars ({bars_needed}). "
            f"First ~{bars_needed * 5 / 60:.0f} min will use raw 5s bars for indicators "
            f"(ADX/Hurst unreliable on 5s data)."
        )
    else:
        wait_mins = result.min_warmup_bars * 5 / 60
        result.warnings.append(
            f"INFO: Warmup period = {result.min_warmup_bars} ticks = {wait_mins:.0f} min. "
            f"No signals possible before {wait_mins:.0f} min into session."
        )


# ---------------------------------------------------------------------------
# 5. Build rejection funnel
# ---------------------------------------------------------------------------
def build_funnel(
    gate_vetoes: GateVetoAnalysis,
    wal: WalAnalysis,
    bridge: BridgeConfigCheck,
    total_ticks: int = 6288,
) -> List[FunnelStage]:
    """Build a rejection funnel showing how many ticks are lost at each stage.

    Uses gate veto data to estimate the funnel. If no veto data, uses
    bridge config to estimate theoretical blocking rates.
    """
    funnel = []
    remaining = total_ticks

    # Stage 1: Warmup period
    # 200 warmup bars per ticker. If 49 tickers, first 200 ticks per ticker are silent.
    # But ticks are spread across tickers, so roughly first 200 ticks are all warmup.
    warmup_blocked = min(remaining, bridge.min_warmup_bars)
    # Actually, warmup is per-ticker. With N tickers each needing M warmup ticks,
    # and ticks arriving round-robin, warmup blocks ~M*N/N = M ticks total per ticker.
    # Estimate: first 200 ticks of each ticker = blocked.
    # With 6288 ticks over ~49 tickers = ~128 ticks per ticker on average.
    # If 128 < 200, then ALL ticks for that ticker are in warmup!
    estimated_tickers = max(len(gate_vetoes.vetoes_by_ticker), 10)
    ticks_per_ticker = total_ticks / estimated_tickers if estimated_tickers > 0 else total_ticks
    if ticks_per_ticker < bridge.min_warmup_bars:
        # ALL ticks blocked by warmup for most tickers!
        warmup_pct = 95.0  # Nearly all
        warmup_blocked = int(remaining * 0.95)
    else:
        # Only first warmup_bars/total fraction blocked
        warmup_pct = min(100, (bridge.min_warmup_bars / ticks_per_ticker) * 100)
        warmup_blocked = int(remaining * warmup_pct / 100)

    funnel.append(FunnelStage(
        stage="1_warmup",
        description=f"Warmup filter ({bridge.min_warmup_bars} bars required per ticker)",
        ticks_entering=remaining,
        ticks_blocked=warmup_blocked,
        ticks_passing=remaining - warmup_blocked,
        block_pct=round(warmup_blocked / max(remaining, 1) * 100, 1),
    ))
    remaining -= warmup_blocked

    # Remaining stages use gate veto counts (real data if available)
    if gate_vetoes.total_vetoes > 0:
        # Order gates by veto count (most blocking first)
        gate_order = sorted(gate_vetoes.vetoes_by_gate.items(), key=lambda x: -x[1])

        stage_num = 2
        for gate_name, count in gate_order:
            if remaining <= 0:
                break
            blocked = min(count, remaining)
            funnel.append(FunnelStage(
                stage=f"{stage_num}_{gate_name}",
                description=f"Gate: {gate_name}",
                ticks_entering=remaining,
                ticks_blocked=blocked,
                ticks_passing=remaining - blocked,
                block_pct=round(blocked / max(remaining, 1) * 100, 1),
            ))
            remaining -= blocked
            stage_num += 1
    else:
        # No gate veto data — estimate from bridge config
        funnel.append(FunnelStage(
            stage="2_no_veto_data",
            description="No gate_vetoes.ndjson data available — cannot trace individual gates",
            ticks_entering=remaining,
            ticks_blocked=0,
            ticks_passing=remaining,
            block_pct=0.0,
        ))

    # Final: strategy evaluation (TypeA-F + Orchestrator)
    signals_generated = wal.routed_orders
    strategy_blocked = max(0, remaining - signals_generated)
    funnel.append(FunnelStage(
        stage=f"{len(funnel) + 1}_strategy_eval",
        description="Strategy evaluation (TypeA-F + Orchestrator — no signal generated)",
        ticks_entering=remaining,
        ticks_blocked=strategy_blocked,
        ticks_passing=signals_generated,
        block_pct=round(strategy_blocked / max(remaining, 1) * 100, 1),
    ))

    return funnel


# ---------------------------------------------------------------------------
# 6. Generate fix recommendations
# ---------------------------------------------------------------------------
def generate_recommendations(
    config: ConfigAnalysis,
    gate_vetoes: GateVetoAnalysis,
    bridge: BridgeConfigCheck,
    wal: WalAnalysis,
    total_ticks: int = 6288,
) -> List[FixRecommendation]:
    """Generate actionable fix recommendations based on the analysis."""
    recs = []

    # --- Warmup analysis ---
    estimated_tickers = max(len(gate_vetoes.vetoes_by_ticker), 10)
    ticks_per_ticker = total_ticks / estimated_tickers if estimated_tickers > 0 else total_ticks
    if ticks_per_ticker < bridge.min_warmup_bars:
        recs.append(FixRecommendation(
            parameter="MIN_WARMUP_BARS",
            current_value=bridge.min_warmup_bars,
            recommended_value=max(60, int(ticks_per_ticker * 0.5)),
            reason=(
                f"With {total_ticks} total ticks across ~{estimated_tickers} tickers, "
                f"each ticker gets only ~{ticks_per_ticker:.0f} ticks. "
                f"Warmup of {bridge.min_warmup_bars} bars means ZERO tickers ever leave warmup. "
                f"This alone explains 0 signals."
            ),
            impact="high",
            config_file="python_brain/bridge.py (line ~690, MIN_WARMUP_BARS)",
        ))

    # --- Confidence floor ---
    if config.confidence_floor is not None and config.confidence_floor > 65:
        recs.append(FixRecommendation(
            parameter="confidence_floor",
            current_value=config.confidence_floor,
            recommended_value=45,
            reason=(
                f"Adaptive confidence floor {config.confidence_floor} combined with "
                f"leverage-aware floor (65/80) creates very high bar. "
                f"For paper trading with <50 trades, recommend 45."
            ),
            impact="medium",
            config_file="config/dynamic_weights.toml [signal] confidence_floor",
        ))

    # --- Gate veto analysis ---
    if gate_vetoes.total_vetoes > 0:
        # Find the dominant blocker
        if gate_vetoes.top_gate:
            top_count = gate_vetoes.vetoes_by_gate.get(gate_vetoes.top_gate, 0)
            top_pct = top_count / gate_vetoes.total_vetoes * 100
            if top_pct > 40:
                recs.append(FixRecommendation(
                    parameter=f"gate:{gate_vetoes.top_gate}",
                    current_value=f"{top_count} vetoes ({top_pct:.0f}% of all)",
                    recommended_value="Loosen or disable this gate for paper trading",
                    reason=(
                        f"Gate '{gate_vetoes.top_gate}' vetoes {top_pct:.0f}% of all candidates. "
                        f"This is the single biggest signal killer."
                    ),
                    impact="high",
                    config_file="python_brain/bridge.py (gate logic)",
                ))

        # MTF misalignment
        mtf_count = gate_vetoes.vetoes_by_gate.get("mtf_misaligned", 0)
        if mtf_count > 0:
            mtf_pct = mtf_count / gate_vetoes.total_vetoes * 100
            if mtf_pct > 20:
                recs.append(FixRecommendation(
                    parameter="mtf_confirmation",
                    current_value=f"Required (blocking {mtf_count} signals, {mtf_pct:.0f}%)",
                    recommended_value="Disable for paper trading (or require 2/3 alignment instead of 3/3)",
                    reason=(
                        f"Multi-timeframe alignment gate blocks {mtf_pct:.0f}% of signals. "
                        f"On 5-second bars, micro-noise causes frequent misalignment."
                    ),
                    impact="high",
                    config_file="python_brain/bridge.py (FIX 10, ~line 880)",
                ))

        # Hurst mean-reverting
        hurst_count = gate_vetoes.vetoes_by_gate.get("hurst_mean_reverting", 0)
        if hurst_count > 0:
            hurst_pct = hurst_count / gate_vetoes.total_vetoes * 100
            if hurst_pct > 15:
                recs.append(FixRecommendation(
                    parameter="hurst_block_threshold",
                    current_value=bridge.hurst_block_threshold,
                    recommended_value=0.35,
                    reason=(
                        f"Hurst regime gate blocks {hurst_count} signals ({hurst_pct:.0f}%). "
                        f"LSE leveraged ETPs often show H < 0.40 due to market-maker activity. "
                        f"Lowering to 0.35 permits borderline trending."
                    ),
                    impact="medium",
                    config_file="python_brain/bridge.py (FIX 6, ~line 841)",
                ))

        # Structural tradability
        sts_count = gate_vetoes.vetoes_by_gate.get("structural_tradability", 0)
        if sts_count > 0:
            sts_pct = sts_count / gate_vetoes.total_vetoes * 100
            if sts_pct > 10:
                recs.append(FixRecommendation(
                    parameter="structural_score_min",
                    current_value=bridge.structural_score_min,
                    recommended_value=20,
                    reason=(
                        f"Structural Tradability Score gate blocks {sts_count} signals ({sts_pct:.0f}%). "
                        f"Score < 30 is common for leveraged ETPs with wide spreads. "
                        f"Lowering to 20 during paper trading lets more signals through for learning."
                    ),
                    impact="medium",
                    config_file="python_brain/bridge.py (N3a, ~line 794)",
                ))

        # Spread too wide
        spread_count = gate_vetoes.vetoes_by_gate.get("spread_too_wide", 0)
        if spread_count > 0:
            spread_pct = spread_count / gate_vetoes.total_vetoes * 100
            if spread_pct > 10:
                recs.append(FixRecommendation(
                    parameter="spread_limit_leveraged_pct",
                    current_value=bridge.spread_limit_leveraged,
                    recommended_value=3.0,
                    reason=(
                        f"Spread gate blocks {spread_count} signals ({spread_pct:.0f}%). "
                        f"Leveraged ETPs on LSE often have >2% spread outside core hours. "
                        f"Widening to 3% during paper trading trades off fill quality for learning."
                    ),
                    impact="medium",
                    config_file="python_brain/bridge.py (G1, ~line 1043)",
                ))

        # VWAP extension
        vwap_count = (
            gate_vetoes.vetoes_by_gate.get("vwap_extension", 0) +
            gate_vetoes.vetoes_by_gate.get("vwap_extension_3pct", 0)
        )
        if vwap_count > 0:
            vwap_pct = vwap_count / gate_vetoes.total_vetoes * 100
            if vwap_pct > 10:
                recs.append(FixRecommendation(
                    parameter="vwap_extension_limits",
                    current_value=f"1.5% (FIX 4) + 3.0% (G2)",
                    recommended_value="2.5% / 5.0%",
                    reason=(
                        f"VWAP extension gates block {vwap_count} signals ({vwap_pct:.0f}%). "
                        f"Leveraged ETPs with 3x/5x leverage move faster relative to VWAP. "
                        f"Widening thresholds accommodates leverage-amplified moves."
                    ),
                    impact="medium",
                    config_file="python_brain/bridge.py (FIX 4 + G2, ~lines 825/1057)",
                ))

        # Cooldown
        cooldown_count = gate_vetoes.vetoes_by_gate.get("cooldown", 0)
        if cooldown_count > 0:
            cd_pct = cooldown_count / gate_vetoes.total_vetoes * 100
            if cd_pct > 5:
                recs.append(FixRecommendation(
                    parameter="SIGNAL_COOLDOWN_TICKS",
                    current_value=bridge.cooldown_ticks,
                    recommended_value=120,
                    reason=(
                        f"Cooldown blocks {cooldown_count} signals ({cd_pct:.0f}%). "
                        f"Current cooldown = {bridge.cooldown_ticks * 5 / 60:.0f} min per ticker. "
                        f"Recommend 120 ticks = 10 min for paper trading."
                    ),
                    impact="low",
                    config_file="python_brain/bridge.py (~line 59, SIGNAL_COOLDOWN_TICKS)",
                ))

    # --- Indicator gates from config ---
    for rule in config.indicator_gates:
        ind = rule.get("indicator", "")
        direction = rule.get("direction", "")
        threshold = rule.get("threshold", 0)
        if ind == "adx" and direction == "above" and threshold >= 25:
            recs.append(FixRecommendation(
                parameter="indicator_gate:adx",
                current_value=f"ADX > {threshold}",
                recommended_value="ADX > 15",
                reason="ADX > 25 only passes during strong trends. Most LSE ETPs show ADX 10-20.",
                impact="high",
                config_file="config/dynamic_weights.toml [indicator_gates]",
            ))
        if ind == "rvol" and direction == "above" and threshold >= 2.0:
            recs.append(FixRecommendation(
                parameter="indicator_gate:rvol",
                current_value=f"RVOL > {threshold}",
                recommended_value="RVOL > 1.0",
                reason="RVOL > 2.0 is rare. Most normal trading has RVOL 0.8-1.5.",
                impact="high",
                config_file="config/dynamic_weights.toml [indicator_gates]",
            ))

    # --- Blacklist check ---
    if len(config.ticker_blacklist) > 5:
        recs.append(FixRecommendation(
            parameter="ticker_blacklist",
            current_value=f"{len(config.ticker_blacklist)} tickers blacklisted",
            recommended_value="Clear blacklist (< 50 total trades, insufficient data)",
            reason=(
                f"With only {config.bayesian_trade_count or 'few'} trades, "
                f"blacklisting {len(config.ticker_blacklist)} tickers removes too much opportunity. "
                f"Need 10+ trades per ticker before blacklisting is meaningful."
            ),
            impact="medium",
            config_file="config/dynamic_weights.toml [ticker_blacklist]",
        ))

    # --- No gate veto data at all ---
    if not gate_vetoes.veto_file_exists:
        recs.append(FixRecommendation(
            parameter="gate_veto_logging",
            current_value="Missing (no gate_vetoes.ndjson file)",
            recommended_value="Verify bridge.py is writing to /app/data/gate_vetoes.ndjson",
            reason=(
                "Gate veto log file not found. Either the bridge has not run, "
                "the data directory is wrong, or the file was cleaned up. "
                "Without this file, we cannot trace where signals are being blocked."
            ),
            impact="high",
            config_file="python_brain/bridge.py (~line 67, _gate_veto_log_path)",
        ))

    return recs


# ---------------------------------------------------------------------------
# 7. Generate verdict
# ---------------------------------------------------------------------------
def generate_verdict(
    wal: WalAnalysis,
    config: ConfigAnalysis,
    gate_vetoes: GateVetoAnalysis,
    bridge: BridgeConfigCheck,
    funnel: List[FunnelStage],
    recs: List[FixRecommendation],
    total_ticks: int = 6288,
) -> str:
    """Generate a plain-English verdict summarizing the root cause."""
    lines = []

    if wal.routed_orders == 0 and wal.signal_rejected_count == 0 and gate_vetoes.total_vetoes == 0:
        # No WAL rejections AND no gate vetoes — ticks never reach gate evaluation
        estimated_tickers = max(len(gate_vetoes.vetoes_by_ticker), 10)
        ticks_per_ticker = total_ticks / estimated_tickers if estimated_tickers > 0 else total_ticks
        if ticks_per_ticker < bridge.min_warmup_bars:
            lines.append(
                "ROOT CAUSE: WARMUP STARVATION. "
                f"With {total_ticks} ticks across ~{estimated_tickers} tickers, each ticker "
                f"gets ~{ticks_per_ticker:.0f} ticks — but warmup requires {bridge.min_warmup_bars}. "
                f"NO ticker ever exits warmup. Ticks are silently discarded before any gate runs."
            )
        else:
            lines.append(
                "ROOT CAUSE: UNKNOWN — no WAL rejections, no gate vetoes, no signals. "
                "Possible causes: bridge.py not running, ticks not reaching Python bridge, "
                "or WAL/veto files not persisted to disk."
            )

    elif gate_vetoes.total_vetoes > 0 and wal.routed_orders == 0:
        # Gate vetoes exist but no orders — gates are too aggressive
        top_gates = sorted(gate_vetoes.vetoes_by_gate.items(), key=lambda x: -x[1])[:3]
        gate_summary = ", ".join(f"{g}={c}" for g, c in top_gates)
        lines.append(
            f"ROOT CAUSE: GATE OVER-FILTERING. "
            f"{gate_vetoes.total_vetoes} gate vetoes logged, 0 signals passed. "
            f"Top blockers: {gate_summary}. "
            f"Ticks reach the gate evaluation stage but every single one is rejected."
        )

    elif wal.signal_rejected_count > 0 and wal.routed_orders == 0:
        # WAL has rejections but no orders — Rust-side risk gates blocking
        top_gates = sorted(wal.signal_rejected_by_gate.items(), key=lambda x: -x[1])[:3]
        gate_summary = ", ".join(f"{g}={c}" for g, c in top_gates)
        lines.append(
            f"ROOT CAUSE: RUST-SIDE RISK GATES. "
            f"{wal.signal_rejected_count} SignalRejected events in WAL, 0 RoutedOrders. "
            f"Python bridge generates signals but Rust risk arbiter rejects them all. "
            f"Top rejection gates: {gate_summary}."
        )

    elif wal.routed_orders > 0:
        lines.append(
            f"NOT A DROUGHT: {wal.routed_orders} orders routed, "
            f"{wal.position_closed} positions closed. System is generating signals."
        )

    # Add high-impact recommendations
    high_recs = [r for r in recs if r.impact == "high"]
    if high_recs:
        lines.append("")
        lines.append(f"HIGH-IMPACT FIXES ({len(high_recs)}):")
        for r in high_recs:
            lines.append(f"  - {r.parameter}: {r.current_value} -> {r.recommended_value}")
            lines.append(f"    Reason: {r.reason[:120]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. Run full diagnostic
# ---------------------------------------------------------------------------
def run_diagnostic(total_ticks: int = 6288) -> DiagnosticReport:
    """Run the complete signal drought diagnostic pipeline."""
    log.info("Starting signal drought diagnostic...")

    wal = analyze_wal()
    log.info(f"WAL: {wal.total_wal_events} events, {wal.signal_rejected_count} rejections, "
             f"{wal.routed_orders} orders, {wal.wal_files_scanned} files scanned")

    config = analyze_config()
    log.info(f"Config: confidence_floor={config.confidence_floor}, "
             f"{len(config.indicator_gates)} indicator gates, "
             f"{len(config.ticker_blacklist)} blacklisted tickers")

    gate_vetoes = analyze_gate_vetoes()
    log.info(f"Gate vetoes: {gate_vetoes.total_vetoes} total, "
             f"top_gate={gate_vetoes.top_gate}, top_ticker={gate_vetoes.top_ticker}")

    bridge = check_bridge_config()
    log.info(f"Bridge: warmup={bridge.min_warmup_bars}, cooldown={bridge.cooldown_ticks}, "
             f"hurst_block={bridge.hurst_block_threshold}")

    funnel = build_funnel(gate_vetoes, wal, bridge, total_ticks)
    recs = generate_recommendations(config, gate_vetoes, bridge, wal, total_ticks)
    verdict = generate_verdict(wal, config, gate_vetoes, bridge, funnel, recs, total_ticks)

    return DiagnosticReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        wal=wal,
        config=config,
        gate_vetoes=gate_vetoes,
        bridge=bridge,
        funnel=funnel,
        recommendations=recs,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# 9. Output formatters
# ---------------------------------------------------------------------------
def format_human(report: DiagnosticReport) -> str:
    """Format the report for human consumption."""
    lines = []
    sep = "=" * 72

    lines.append(sep)
    lines.append("  SIGNAL DROUGHT DIAGNOSTIC REPORT  (ISS-002)")
    lines.append(f"  Generated: {report.timestamp}")
    lines.append(sep)

    # Verdict
    lines.append("")
    lines.append("VERDICT:")
    lines.append(report.verdict)

    # WAL Summary
    lines.append("")
    lines.append("-" * 72)
    lines.append("WAL ANALYSIS:")
    lines.append(f"  Files scanned:        {report.wal.wal_files_scanned}")
    lines.append(f"  Total WAL events:     {report.wal.total_wal_events}")
    lines.append(f"  SignalRejected:       {report.wal.signal_rejected_count}")
    lines.append(f"  RoutedOrder (trades): {report.wal.routed_orders}")
    lines.append(f"  PositionClosed:       {report.wal.position_closed}")
    lines.append(f"  FillEvent:            {report.wal.fill_events}")
    lines.append(f"  SystemReady:          {report.wal.system_ready_count}")

    if report.wal.event_type_counts:
        lines.append("  Event type breakdown:")
        for et, count in sorted(report.wal.event_type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    {et:30s}  {count}")

    if report.wal.signal_rejected_by_gate:
        lines.append("  Signal rejections by gate:")
        for gate, count in sorted(report.wal.signal_rejected_by_gate.items(), key=lambda x: -x[1]):
            lines.append(f"    {gate:30s}  {count}")

    if report.wal.signal_rejected_by_ticker:
        lines.append("  Signal rejections by ticker:")
        for ticker, count in sorted(report.wal.signal_rejected_by_ticker.items(), key=lambda x: -x[1]):
            lines.append(f"    {ticker:20s}  {count}")

    # Config Summary
    lines.append("")
    lines.append("-" * 72)
    lines.append("CONFIG ANALYSIS (dynamic_weights.toml):")
    lines.append(f"  confidence_floor:     {report.config.confidence_floor}")
    lines.append(f"  indicator_gates:      {len(report.config.indicator_gates)} rules")
    lines.append(f"  ticker_blacklist:     {len(report.config.ticker_blacklist)} tickers")
    lines.append(f"  bayesian_win_rate:    {report.config.bayesian_win_rate}")
    lines.append(f"  bayesian_trade_count: {report.config.bayesian_trade_count}")
    for rule in report.config.indicator_gates:
        lines.append(f"    gate: {rule.get('indicator')} {rule.get('direction')} {rule.get('threshold')}")
    for w in report.config.warnings:
        lines.append(f"  {w}")

    # Gate Veto Summary
    lines.append("")
    lines.append("-" * 72)
    lines.append("GATE VETO ANALYSIS:")
    if not report.gate_vetoes.veto_file_exists:
        lines.append(f"  FILE NOT FOUND: {report.gate_vetoes.veto_file_path}")
        lines.append("  (Bridge may not have run, or data dir is incorrect)")
    else:
        lines.append(f"  File: {report.gate_vetoes.veto_file_path}")
        lines.append(f"  Total vetoes:   {report.gate_vetoes.total_vetoes}")

        if report.gate_vetoes.vetoes_by_gate:
            lines.append("  Vetoes by gate:")
            for gate, count in sorted(report.gate_vetoes.vetoes_by_gate.items(), key=lambda x: -x[1]):
                pct = count / max(report.gate_vetoes.total_vetoes, 1) * 100
                lines.append(f"    {gate:30s}  {count:6d}  ({pct:5.1f}%)")

        if report.gate_vetoes.vetoes_by_ticker:
            lines.append("  Vetoes by ticker (top 15):")
            for ticker, count in sorted(report.gate_vetoes.vetoes_by_ticker.items(), key=lambda x: -x[1])[:15]:
                pct = count / max(report.gate_vetoes.total_vetoes, 1) * 100
                lines.append(f"    {ticker:20s}  {count:6d}  ({pct:5.1f}%)")

        if report.gate_vetoes.vetoes_by_hour:
            lines.append("  Vetoes by hour (UTC):")
            for hour in sorted(report.gate_vetoes.vetoes_by_hour.keys()):
                count = report.gate_vetoes.vetoes_by_hour[hour]
                bar = "#" * min(50, int(count / max(report.gate_vetoes.total_vetoes, 1) * 200))
                lines.append(f"    {hour:02d}:00  {count:6d}  {bar}")

        if report.gate_vetoes.indicator_distributions:
            lines.append("  Indicator distributions at veto time:")
            for ind, stats in sorted(report.gate_vetoes.indicator_distributions.items()):
                lines.append(
                    f"    {ind:20s}  n={stats['count']:5d}  "
                    f"min={stats['min']:8.4f}  p25={stats['p25']:8.4f}  "
                    f"p50={stats['p50']:8.4f}  p75={stats['p75']:8.4f}  "
                    f"max={stats['max']:8.4f}  mean={stats['mean']:8.4f}"
                )

    # Bridge Config
    lines.append("")
    lines.append("-" * 72)
    lines.append("BRIDGE CONFIG (hardcoded in bridge.py):")
    lines.append(f"  MIN_WARMUP_BARS:         {report.bridge.min_warmup_bars} ({report.bridge.min_warmup_bars * 5 / 60:.0f} min)")
    lines.append(f"  SIGNAL_COOLDOWN_TICKS:   {report.bridge.cooldown_ticks} ({report.bridge.cooldown_ticks * 5 / 60:.0f} min)")
    lines.append(f"  BARS_PER_5MIN:           {report.bridge.bars_per_5min}")
    lines.append(f"  Confidence floor (3x):   {report.bridge.confidence_floor_3x}")
    lines.append(f"  Confidence floor (5x):   {report.bridge.confidence_floor_5x}")
    lines.append(f"  Hurst block threshold:   {report.bridge.hurst_block_threshold}")
    lines.append(f"  VWAP extension max:      {report.bridge.vwap_extension_max}%")
    lines.append(f"  Structural score min:    {report.bridge.structural_score_min}")
    lines.append(f"  Spread limit (levgd):    {report.bridge.spread_limit_leveraged}%")
    lines.append(f"  Spread limit (unlevgd):  {report.bridge.spread_limit_unleveraged}%")
    lines.append(f"  MTF confirmation:        {report.bridge.mtf_confirmation_required}")
    lines.append(f"  Volume slope gate:       {report.bridge.volume_slope_gate_active}")
    for w in report.bridge.warnings:
        lines.append(f"  {w}")

    # Rejection Funnel
    lines.append("")
    lines.append("-" * 72)
    lines.append("REJECTION FUNNEL:")
    for stage in report.funnel:
        lines.append(
            f"  {stage.stage:30s}  "
            f"entering={stage.ticks_entering:6d}  "
            f"blocked={stage.ticks_blocked:6d} ({stage.block_pct:5.1f}%)  "
            f"passing={stage.ticks_passing:6d}"
        )

    # Recommendations
    lines.append("")
    lines.append("-" * 72)
    lines.append(f"RECOMMENDATIONS ({len(report.recommendations)}):")
    if not report.recommendations:
        lines.append("  No specific recommendations — system appears configured correctly.")
    else:
        for i, rec in enumerate(report.recommendations, 1):
            impact_marker = {"high": "!!!", "medium": "!!", "low": "!"}.get(rec.impact, "")
            lines.append(f"  [{i}] {impact_marker} {rec.parameter}")
            lines.append(f"      Current:     {rec.current_value}")
            lines.append(f"      Recommended: {rec.recommended_value}")
            lines.append(f"      Reason:      {rec.reason}")
            lines.append(f"      Config file: {rec.config_file}")
            lines.append(f"      Impact:      {rec.impact.upper()}")
            lines.append("")

    lines.append(sep)
    return "\n".join(lines)


def format_fix_preview(report: DiagnosticReport) -> str:
    """Format a preview of what config changes would help."""
    lines = []
    sep = "=" * 72

    lines.append(sep)
    lines.append("  FIX PREVIEW — Proposed config changes to resolve signal drought")
    lines.append(sep)
    lines.append("")
    lines.append("NOTE: This is READ-ONLY preview. No changes are applied.")
    lines.append("      Apply changes manually after reviewing each recommendation.")
    lines.append("")

    if not report.recommendations:
        lines.append("No fixes needed — system appears configured correctly.")
        return "\n".join(lines)

    # Group by impact
    for impact_level in ("high", "medium", "low"):
        impact_recs = [r for r in report.recommendations if r.impact == impact_level]
        if not impact_recs:
            continue

        marker = {"high": "CRITICAL", "medium": "IMPORTANT", "low": "OPTIONAL"}[impact_level]
        lines.append(f"--- {marker} ({len(impact_recs)} changes) ---")
        lines.append("")

        for rec in impact_recs:
            lines.append(f"  File: {rec.config_file}")
            lines.append(f"  Change: {rec.parameter}")
            lines.append(f"    FROM: {rec.current_value}")
            lines.append(f"    TO:   {rec.recommended_value}")
            lines.append(f"    WHY:  {rec.reason}")
            lines.append("")

    # Estimated impact summary
    high_count = len([r for r in report.recommendations if r.impact == "high"])
    lines.append(sep)
    if high_count > 0:
        lines.append(f"Applying the {high_count} CRITICAL fix(es) should unblock signal generation.")
    else:
        lines.append("No critical fixes identified. Signal drought may have a different root cause.")
    lines.append(sep)

    return "\n".join(lines)


def to_json(report: DiagnosticReport) -> str:
    """Serialize report to JSON."""
    def _to_dict(obj):
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return str(obj)

    return json.dumps(asdict(report), indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    global WAL_DIR, DATA_DIR, CONFIG_DIR

    parser = argparse.ArgumentParser(
        description="ISS-002: Signal Drought Diagnostic Pipeline Tracer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 -m python_brain.ouroboros.signal_drought_diag\n"
            "  python3 -m python_brain.ouroboros.signal_drought_diag --json\n"
            "  python3 -m python_brain.ouroboros.signal_drought_diag --fix-preview\n"
            "  python3 -m python_brain.ouroboros.signal_drought_diag --ticks 12000\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--fix-preview", action="store_true",
                        help="Show what config changes would help")
    parser.add_argument("--ticks", type=int, default=6288,
                        help="Total tick count to analyze (default: 6288)")
    parser.add_argument("--wal-dir", type=str, default=None,
                        help="WAL directory path")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Data directory path")
    parser.add_argument("--config-dir", type=str, default=None,
                        help="Config directory path")

    args = parser.parse_args()

    # Override globals if custom paths provided
    if args.wal_dir:
        WAL_DIR = Path(args.wal_dir)
    if args.data_dir:
        DATA_DIR = Path(args.data_dir)
    if args.config_dir:
        CONFIG_DIR = Path(args.config_dir)

    report = run_diagnostic(total_ticks=args.ticks)

    if args.json:
        print(to_json(report))
    elif args.fix_preview:
        print(format_fix_preview(report))
    else:
        print(format_human(report))


if __name__ == "__main__":
    main()
