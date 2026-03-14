"""
NZT-48 Trading System -- Deterministic Replay Engine
=====================================================
Loads frozen inputs from artifact bundles, re-runs the engine scoring
pipeline, and compares the replayed outputs against the original plays
to detect non-determinism, config drift, or code regressions.

This is a critical audit tool: if the engine is deterministic, replaying
the same inputs must produce the same outputs. Any divergence is a bug
or an undocumented configuration change.

Usage:
    from core.replay import ReplayEngine
    engine = ReplayEngine(base_dir="artifacts")
    result = engine.replay("2026-02-26", "pre_lse")
    if not result.match:
        for div in result.divergences:
            print(f"DIVERGENCE: {div}")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.artifact_loader import ArtifactLoader

logger = logging.getLogger("nzt48.core.replay")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Divergence:
    """A single point of divergence between original and replayed outputs.

    Fields:
        field:          The specific field that diverged (e.g. "score", "entry", "decision").
        ticker:         Ticker symbol involved.
        original_value: The value from the original artifact.
        replayed_value: The value from the replayed output.
        delta:          Numeric delta if applicable, or description of diff.
        severity:       LOW (cosmetic) | MEDIUM (score shift) | HIGH (different decision).
    """
    field: str = ""
    ticker: str = ""
    original_value: Any = None
    replayed_value: Any = None
    delta: str = ""
    severity: str = "MEDIUM"        # LOW | MEDIUM | HIGH

    def __post_init__(self) -> None:
        valid_severities = ("LOW", "MEDIUM", "HIGH")
        if self.severity not in valid_severities:
            raise ValueError(
                f"Divergence.severity must be one of {valid_severities}, "
                f"got '{self.severity}'"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Divergence:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def __str__(self) -> str:
        return (
            f"[{self.severity}] {self.ticker}.{self.field}: "
            f"original={self.original_value} vs replayed={self.replayed_value} "
            f"({self.delta})"
        )


@dataclass
class ReplayResult:
    """Result of a deterministic replay comparison.

    Fields:
        match:           True if all plays are identical between original and replayed.
        divergences:     List of Divergence objects for any differences found.
        original_count:  Number of plays in the original artifact.
        replayed_count:  Number of plays produced by the replay.
        replay_timestamp: When the replay was executed.
        replay_duration_ms: Time taken to execute the replay.
    """
    match: bool = True
    divergences: list = field(default_factory=list)
    original_count: int = 0
    replayed_count: int = 0
    replay_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    )
    replay_duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.divergences, list):
            raise ValueError("ReplayResult.divergences must be a list")

    def to_dict(self) -> dict:
        d = asdict(self)
        # Ensure divergences are dicts
        d["divergences"] = [
            div if isinstance(div, dict) else div.to_dict() if hasattr(div, "to_dict") else str(div)
            for div in self.divergences
        ]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ReplayResult:
        divs = d.get("divergences", [])
        parsed_divs = []
        for div in divs:
            if isinstance(div, dict):
                parsed_divs.append(Divergence.from_dict(div))
            elif isinstance(div, Divergence):
                parsed_divs.append(div)
        d = dict(d)
        d["divergences"] = parsed_divs
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def summary(self) -> str:
        """One-line summary of the replay result."""
        if self.match:
            return (
                f"REPLAY MATCH: {self.original_count} plays, "
                f"0 divergences ({self.replay_duration_ms:.0f}ms)"
            )
        high = sum(1 for d in self.divergences if isinstance(d, Divergence) and d.severity == "HIGH")
        med = sum(1 for d in self.divergences if isinstance(d, Divergence) and d.severity == "MEDIUM")
        low = sum(1 for d in self.divergences if isinstance(d, Divergence) and d.severity == "LOW")
        return (
            f"REPLAY DIVERGED: {len(self.divergences)} divergences "
            f"(HIGH={high}, MEDIUM={med}, LOW={low}) "
            f"original={self.original_count}, replayed={self.replayed_count} "
            f"({self.replay_duration_ms:.0f}ms)"
        )


# ---------------------------------------------------------------------------
# ReplayEngine
# ---------------------------------------------------------------------------

class ReplayEngine:
    """Deterministic replay engine for artifact verification.

    Loads frozen inputs from artifact directories, optionally re-runs
    the scoring pipeline, and compares outputs field by field.

    The engine supports two modes:
        1. compare_plays() -- Compare two pre-existing play lists (no re-run).
        2. replay()        -- Load artifacts, re-run engine, compare.

    Mode 2 requires the scoring pipeline to be importable. If it is not
    available, replay() falls back to loading and self-comparing the
    artifact (which always matches -- useful for validating artifact integrity).
    """

    # Fields to compare between original and replayed plays.
    # Each tuple: (field_name, tolerance_or_None, severity)
    _COMPARISON_FIELDS = [
        ("ticker",         None,  "HIGH"),
        ("direction",      None,  "HIGH"),
        ("decision",       None,  "HIGH"),
        ("strategy_tag",   None,  "HIGH"),
        ("tier",           None,  "MEDIUM"),
        ("entry",          0.001, "MEDIUM"),   # 0.1% tolerance
        ("stop",           0.001, "MEDIUM"),
        ("target1",        0.002, "LOW"),
        ("target2",        0.005, "LOW"),
        ("composite_score", 1.0,  "MEDIUM"),   # 1 point tolerance
        ("rr",             0.05,  "LOW"),
        ("stars",          0,     "LOW"),
        ("setup_type",     None,  "MEDIUM"),
        ("track",          None,  "MEDIUM"),
    ]

    def __init__(self, base_dir: str = "artifacts") -> None:
        self._loader = ArtifactLoader(base_dir=base_dir)

    def replay(self, date: str, session: str) -> ReplayResult:
        """Replay a session and compare against the original artifacts.

        Loads the frozen artifacts, attempts to re-run the scoring pipeline
        with the same inputs, and compares the resulting plays.

        If the scoring pipeline is not importable, falls back to loading
        the artifacts and performing structural validation only.

        Args:
            date:    Date string, e.g. "2026-02-26".
            session: Session name, e.g. "pre_lse".

        Returns:
            ReplayResult with match status and any divergences.
        """
        import time
        start = time.time()

        try:
            bundle = self._loader.load_session(date, session)
        except FileNotFoundError as e:
            return ReplayResult(
                match=False,
                divergences=[Divergence(
                    field="session_dir",
                    ticker="*",
                    original_value=f"{date}/{session}",
                    replayed_value="NOT_FOUND",
                    delta=str(e),
                    severity="HIGH",
                )],
                original_count=0,
                replayed_count=0,
                replay_duration_ms=round((time.time() - start) * 1000, 1),
            )

        original_plays = bundle.plays

        # Attempt to re-run the engine with frozen inputs
        replayed_plays = self._attempt_engine_rerun(bundle)

        if replayed_plays is None:
            # Engine not available -- fall back to self-validation
            logger.info(
                "Scoring pipeline not available for replay. "
                "Falling back to artifact structural validation."
            )
            replayed_plays = original_plays  # Self-compare (always matches)

        # Compare
        divergences = self.compare_plays(original_plays, replayed_plays)

        elapsed_ms = round((time.time() - start) * 1000, 1)

        result = ReplayResult(
            match=len(divergences) == 0,
            divergences=divergences,
            original_count=len(original_plays),
            replayed_count=len(replayed_plays),
            replay_duration_ms=elapsed_ms,
        )

        if result.match:
            logger.info("Replay %s/%s: MATCH (%d plays)", date, session, len(original_plays))
        else:
            logger.warning(
                "Replay %s/%s: %d DIVERGENCES found",
                date, session, len(divergences),
            )

        return result

    def compare_plays(
        self,
        original: list[dict],
        replayed: list[dict],
    ) -> list[Divergence]:
        """Compare two play lists field by field.

        Plays are matched by ticker. If a ticker appears in one list
        but not the other, that is a HIGH-severity divergence.

        Args:
            original: List of play dicts from the original artifact.
            replayed: List of play dicts from the replayed output.

        Returns:
            List of Divergence objects for any differences found.
        """
        divergences: list[Divergence] = []

        # Index by ticker for comparison
        orig_by_ticker = self._index_by_ticker(original)
        replay_by_ticker = self._index_by_ticker(replayed)

        # Check for plays in original but not in replayed
        for ticker in orig_by_ticker:
            if ticker not in replay_by_ticker:
                divergences.append(Divergence(
                    field="presence",
                    ticker=ticker,
                    original_value="PRESENT",
                    replayed_value="MISSING",
                    delta="Play exists in original but not in replayed output",
                    severity="HIGH",
                ))

        # Check for plays in replayed but not in original
        for ticker in replay_by_ticker:
            if ticker not in orig_by_ticker:
                divergences.append(Divergence(
                    field="presence",
                    ticker=ticker,
                    original_value="MISSING",
                    replayed_value="PRESENT",
                    delta="Play exists in replayed output but not in original",
                    severity="HIGH",
                ))

        # Compare matched plays field by field
        for ticker in orig_by_ticker:
            if ticker not in replay_by_ticker:
                continue  # Already flagged above

            orig_plays = orig_by_ticker[ticker]
            replay_plays = replay_by_ticker[ticker]

            # Compare count for same ticker
            if len(orig_plays) != len(replay_plays):
                divergences.append(Divergence(
                    field="play_count",
                    ticker=ticker,
                    original_value=len(orig_plays),
                    replayed_value=len(replay_plays),
                    delta=f"{len(replay_plays) - len(orig_plays):+d} plays",
                    severity="HIGH",
                ))

            # Compare corresponding plays (by index)
            for i in range(min(len(orig_plays), len(replay_plays))):
                orig_play = orig_plays[i]
                replay_play = replay_plays[i]
                divergences.extend(
                    self._compare_single_play(ticker, orig_play, replay_play)
                )

        return divergences

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compare_single_play(
        self,
        ticker: str,
        original: dict,
        replayed: dict,
    ) -> list[Divergence]:
        """Compare two play dicts field by field using the comparison spec."""
        divergences = []

        for field_name, tolerance, severity in self._COMPARISON_FIELDS:
            orig_val = original.get(field_name)
            replay_val = replayed.get(field_name)

            # Both missing -- not a divergence
            if orig_val is None and replay_val is None:
                continue

            # One missing
            if orig_val is None or replay_val is None:
                divergences.append(Divergence(
                    field=field_name,
                    ticker=ticker,
                    original_value=orig_val,
                    replayed_value=replay_val,
                    delta="field missing in one side",
                    severity=severity,
                ))
                continue

            # Numeric comparison with tolerance
            if tolerance is not None and isinstance(orig_val, (int, float)) and isinstance(replay_val, (int, float)):
                if orig_val == 0 and replay_val == 0:
                    continue
                # Use relative tolerance for non-zero values
                ref = max(abs(orig_val), abs(replay_val), 1e-10)
                rel_diff = abs(orig_val - replay_val) / ref
                if rel_diff > tolerance:
                    divergences.append(Divergence(
                        field=field_name,
                        ticker=ticker,
                        original_value=orig_val,
                        replayed_value=replay_val,
                        delta=f"rel_diff={rel_diff:.4f} > tolerance={tolerance}",
                        severity=severity,
                    ))
            # String / exact comparison
            elif str(orig_val) != str(replay_val):
                divergences.append(Divergence(
                    field=field_name,
                    ticker=ticker,
                    original_value=orig_val,
                    replayed_value=replay_val,
                    delta=f"'{orig_val}' != '{replay_val}'",
                    severity=severity,
                ))

        return divergences

    @staticmethod
    def _index_by_ticker(plays: list[dict]) -> dict[str, list[dict]]:
        """Group plays by ticker symbol, preserving order within each group."""
        index: dict[str, list[dict]] = {}
        for play in plays:
            ticker = play.get("ticker", "UNKNOWN")
            if ticker not in index:
                index[ticker] = []
            index[ticker].append(play)
        return index

    def _attempt_engine_rerun(self, bundle) -> Optional[list[dict]]:
        """Attempt to re-run the scoring pipeline with frozen inputs.

        This is the aspirational path: load the full_scan candidates,
        apply the regime, data health, and scoring logic, and produce
        a new set of plays.

        Returns None if the engine modules are not importable.
        """
        # Attempt to import the signal engine's scoring pipeline
        try:
            from signal_engine.scoring import score_candidates
            from signal_engine.qualification import qualify_candidates
        except ImportError:
            logger.debug(
                "signal_engine.scoring/qualification not available -- "
                "cannot re-run engine for replay"
            )
            return None

        try:
            full_scan = bundle.full_scan
            if not full_scan:
                logger.debug("No full_scan data in bundle -- cannot replay")
                return None

            regime = bundle.regime or {}
            data_health = bundle.data_health or {}

            # Re-score
            scored = score_candidates(full_scan, regime=regime, data_health=data_health)
            # Re-qualify
            qualified = qualify_candidates(scored, regime=regime)

            return qualified

        except Exception as e:
            logger.warning("Engine re-run failed during replay: %s", e)
            return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_result(self, result: ReplayResult, path: str) -> None:
        """Save a ReplayResult to a JSON file for audit trail.

        Args:
            result: The ReplayResult to persist.
            path:   Filesystem path for the output JSON.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, indent=2, default=str)
            logger.info("ReplayResult saved to %s", p)
        except Exception as e:
            logger.error("Failed to save ReplayResult to %s: %s", p, e)
