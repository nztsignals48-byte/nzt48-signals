"""
NZT-48 V8.0 — Peer Instrument Finder
======================================
Selects PEER instruments from the extended ISA universe based on
similarity to the 12 CORE instruments.  Three similarity methods
are combined with configurable weights:

  Method A — Correlation:  Rolling return correlation vs CORE (50%)
  Method B — Factor/Theme: Shared ISA_FACTOR_GROUPS membership   (30%)
  Method C — Momentum/Vol: ATR%, RSI regime bucket, ADX profile  (20%)

Selection enforces diversity (max 2 per factor_group) and excludes
tickers that failed to download.

Output: list[PeerMatch] written to peers_intel.json via
        write_peer_artifact().

Imports the canonical universe from isa_universe to avoid duplication.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import yfinance as yf

from uk_isa.isa_universe import (
    CORE_UNIVERSE,
    EXTENDED_UNIVERSE,
    ISA_FACTOR_GROUPS,
    get_factor_group,
)

logger = logging.getLogger("nzt48.peer_finder")

ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PeerMatch:
    """A single peer instrument selected for similarity to CORE."""

    ticker: str
    similarity_score: float          # 0.0-1.0
    similarity_method: str           # "correlation" | "factor" | "momentum" | "combined"
    core_parent: str                 # which CORE ticker is it most similar to
    factor_group: str
    tier: str = "PEER"
    tradable: bool = True            # passes basic tradeability check
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "similarity_score": round(self.similarity_score, 4),
            "similarity_method": self.similarity_method,
            "core_parent": self.core_parent,
            "factor_group": self.factor_group,
            "tier": self.tier,
            "tradable": self.tradable,
            "reasons": self.reasons,
        }


# ─────────────────────────────────────────────────────────────────────────────
# PeerFinder
# ─────────────────────────────────────────────────────────────────────────────


class PeerFinder:
    """
    Selects PEER instruments based on similarity to CORE instruments.

    Three scoring axes are combined:
      A. Correlation of daily returns vs CORE  (weight: 50%)
      B. Factor/theme group overlap            (weight: 30%)
      C. ATR% / RSI / ADX profile match        (weight: 20%)

    Top ``target_count`` candidates (post-diversity filter) are returned.
    """

    # Combination weights
    W_CORR: float = 0.50
    W_FACTOR: float = 0.30
    W_MOMENTUM: float = 0.20

    # Diversity cap — max peers from a single factor_group
    MAX_PER_GROUP: int = 2

    def __init__(
        self,
        core_list: list[str],
        candidates: list[str],
        target_count: int,
    ) -> None:
        """
        Parameters
        ----------
        core_list :
            The 12 CORE tickers (from isa_universe.CORE_UNIVERSE).
        candidates :
            Pool of potential peers (EXTENDED_UNIVERSE minus CORE, or
            any custom list).
        target_count :
            Exact number of peers to select.  Typically
            ``math.ceil(0.50 * len(core_list))`` = 6.
        """
        self.core_list = list(core_list)
        self.candidates = [t for t in candidates if t not in set(core_list)]
        self.target_count = target_count

        # Caches populated during find_peers()
        self._core_returns: dict[str, np.ndarray] = {}
        self._cand_returns: dict[str, np.ndarray] = {}
        self._cand_ohlcv: dict[str, dict] = {}  # ticker → {close, high, low}
        self._core_ohlcv: dict[str, dict] = {}

    # ── Public entry point ─────────────────────────────────────────────────

    def find_peers(
        self,
        period: str = "60d",
        interval: str = "1d",
    ) -> list[PeerMatch]:
        """Run full peer selection.  Returns ``target_count`` PeerMatch objects."""
        logger.info(
            "PeerFinder: selecting %d peers from %d candidates vs %d CORE",
            self.target_count,
            len(self.candidates),
            len(self.core_list),
        )

        # Fetch data for both pools
        self._fetch_all(period, interval)

        # Score each candidate on three axes
        corr_scores = self._correlation_similarity(period, interval)
        factor_scores = self._factor_theme_similarity()
        momentum_scores = self._momentum_vol_similarity(period, interval)

        # Combine
        combined = self._combine_scores(corr_scores, factor_scores, momentum_scores)

        # Find best core parent per candidate (by correlation)
        core_parents = self._best_core_parent()

        # Build ranked list
        ranked: list[tuple[str, float]] = sorted(
            combined.items(), key=lambda kv: kv[1], reverse=True,
        )

        # Apply diversity filter and select
        selected: list[PeerMatch] = []
        group_counts: dict[str, int] = {}

        for ticker, score in ranked:
            if len(selected) >= self.target_count:
                break

            # Skip zero-data tickers
            if ticker not in self._cand_returns and ticker not in self._cand_ohlcv:
                logger.debug("Skipping %s — no data", ticker)
                continue

            fg = get_factor_group(ticker)
            if group_counts.get(fg, 0) >= self.MAX_PER_GROUP:
                logger.debug(
                    "Skipping %s — diversity cap reached for %s", ticker, fg,
                )
                continue

            group_counts[fg] = group_counts.get(fg, 0) + 1

            # Determine dominant method
            method = self._dominant_method(
                ticker, corr_scores, factor_scores, momentum_scores,
            )

            parent = core_parents.get(ticker, self.core_list[0] if self.core_list else "")

            reasons = self._build_reasons(
                ticker, corr_scores, factor_scores, momentum_scores, parent,
            )

            selected.append(PeerMatch(
                ticker=ticker,
                similarity_score=score,
                similarity_method=method,
                core_parent=parent,
                factor_group=fg,
                tier="PEER",
                tradable=True,
                reasons=reasons,
            ))

        logger.info(
            "PeerFinder: selected %d peers: %s",
            len(selected),
            [p.ticker for p in selected],
        )
        return selected

    # ── Method A: Correlation Similarity ───────────────────────────────────

    def _correlation_similarity(
        self,
        period: str,
        interval: str,
    ) -> dict[str, float]:
        """
        For each candidate, compute max rolling correlation of daily
        returns vs any CORE ticker.  Score = max_correlation clamped [0, 1].
        """
        scores: dict[str, float] = {}

        for cand in self.candidates:
            cand_ret = self._cand_returns.get(cand)
            if cand_ret is None or len(cand_ret) < 10:
                scores[cand] = 0.0
                continue

            best_corr = 0.0
            for core in self.core_list:
                core_ret = self._core_returns.get(core)
                if core_ret is None or len(core_ret) < 10:
                    continue

                # Align lengths
                min_len = min(len(cand_ret), len(core_ret))
                if min_len < 10:
                    continue

                try:
                    corr = float(np.corrcoef(
                        cand_ret[-min_len:], core_ret[-min_len:],
                    )[0, 1])
                    if np.isnan(corr):
                        corr = 0.0
                    # For inverse pairs, high negative correlation is also
                    # similarity (they track the same underlying, just inverted)
                    best_corr = max(best_corr, abs(corr))
                except Exception:
                    pass

            scores[cand] = float(np.clip(best_corr, 0.0, 1.0))

        return scores

    # ── Method B: Factor / Theme Similarity ────────────────────────────────

    def _factor_theme_similarity(self) -> dict[str, float]:
        """
        For each candidate, check how many ISA_FACTOR_GROUPS it shares
        with any CORE ticker.  Score = shared_groups / max_groups clamped [0, 1].

        Inverse pairs (e.g. NVD3.L long + NVDS.L short) count as sharing
        the same theme if their factor groups overlap (ai_gpt, single_stock_short
        both relate to the NVIDIA theme).
        """
        # Build CORE group set
        core_groups: set[str] = set()
        for core in self.core_list:
            for group, members in ISA_FACTOR_GROUPS.items():
                if core in members:
                    core_groups.add(group)

        if not core_groups:
            return {c: 0.0 for c in self.candidates}

        # Build inverse-theme mapping:
        # long↔short pairs share underlying exposure
        _INVERSE_THEME_MAP: dict[str, str] = {
            "single_stock_short": "single_stock_long",
            "single_stock_long": "single_stock_short",
            "nasdaq_beta_short": "nasdaq_beta_long",
            "nasdaq_beta_long": "nasdaq_beta_short",
        }

        max_groups = len(core_groups)
        scores: dict[str, float] = {}

        for cand in self.candidates:
            cand_groups: set[str] = set()
            for group, members in ISA_FACTOR_GROUPS.items():
                if cand in members:
                    cand_groups.add(group)
                    # Add the inverse-theme equivalent
                    inv = _INVERSE_THEME_MAP.get(group)
                    if inv:
                        cand_groups.add(inv)

            shared = len(cand_groups & core_groups)
            score = shared / max_groups if max_groups > 0 else 0.0
            scores[cand] = float(np.clip(score, 0.0, 1.0))

        return scores

    # ── Method C: Momentum / Volatility Profile Similarity ─────────────────

    def _momentum_vol_similarity(
        self,
        period: str,
        interval: str,
    ) -> dict[str, float]:
        """
        For each candidate, compute an ATR%, RSI regime bucket, and ADX
        level bucket.  Compare the resulting 3-D profile vector to the
        average CORE profile.  Score = 1 - normalised_distance clamped [0, 1].
        """
        # Build average CORE profile
        core_profiles: list[np.ndarray] = []
        for core in self.core_list:
            p = self._compute_profile(core, is_core=True)
            if p is not None:
                core_profiles.append(p)

        if not core_profiles:
            return {c: 0.0 for c in self.candidates}

        avg_core = np.mean(core_profiles, axis=0)  # shape (3,)

        # Max possible distance for normalisation (Euclidean across 3 dims,
        # each dimension ranges 0-1 after internal normalisation)
        max_distance = np.sqrt(3.0)

        scores: dict[str, float] = {}
        for cand in self.candidates:
            p = self._compute_profile(cand, is_core=False)
            if p is None:
                scores[cand] = 0.0
                continue

            dist = float(np.linalg.norm(p - avg_core))
            norm_dist = dist / max_distance if max_distance > 0 else 0.0
            scores[cand] = float(np.clip(1.0 - norm_dist, 0.0, 1.0))

        return scores

    # ── Score Combination ──────────────────────────────────────────────────

    def _combine_scores(
        self,
        corr: dict[str, float],
        factor: dict[str, float],
        momentum: dict[str, float],
    ) -> dict[str, float]:
        """Weighted combination: 50% correlation + 30% factor + 20% momentum."""
        combined: dict[str, float] = {}
        for cand in self.candidates:
            c = corr.get(cand, 0.0)
            f = factor.get(cand, 0.0)
            m = momentum.get(cand, 0.0)
            combined[cand] = (
                self.W_CORR * c + self.W_FACTOR * f + self.W_MOMENTUM * m
            )
        return combined

    # ── Internal helpers ───────────────────────────────────────────────────

    def _fetch_all(self, period: str, interval: str) -> None:
        """Batch-fetch daily OHLCV for all CORE + candidate tickers."""
        all_tickers = list(set(self.core_list + self.candidates))
        if not all_tickers:
            return

        logger.debug("Fetching %d tickers for peer analysis", len(all_tickers))
        for ticker in all_tickers:
            try:
                df = yf.download(
                    ticker,
                    period=period,
                    interval=interval,
                    auto_adjust=True,
                    progress=False,
                )
                if df is None or df.empty or len(df) < 10:
                    logger.debug("No data for %s", ticker)
                    continue

                # Normalise column names (handle MultiIndex from yfinance)
                if hasattr(df.columns, "get_level_values"):
                    try:
                        df.columns = [
                            c[0].lower() if isinstance(c, tuple) else c.lower()
                            for c in df.columns
                        ]
                    except Exception:
                        df.columns = [str(c).lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]

                close = df["close"].values.astype(float)
                returns = np.diff(close) / close[:-1]
                returns = returns[np.isfinite(returns)]

                ohlcv = {
                    "close": close,
                    "high": df["high"].values.astype(float) if "high" in df.columns else close,
                    "low": df["low"].values.astype(float) if "low" in df.columns else close,
                }

                if ticker in set(self.core_list):
                    self._core_returns[ticker] = returns
                    self._core_ohlcv[ticker] = ohlcv
                if ticker in set(self.candidates):
                    self._cand_returns[ticker] = returns
                    self._cand_ohlcv[ticker] = ohlcv

            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", ticker, exc)

    def _compute_profile(
        self, ticker: str, is_core: bool,
    ) -> Optional[np.ndarray]:
        """
        Build a normalised 3-D profile vector: [atr_pct_norm, rsi_bucket, adx_bucket].
        Each dimension is scaled to [0, 1].

        Returns None if data is insufficient.
        """
        ohlcv = (self._core_ohlcv if is_core else self._cand_ohlcv).get(ticker)
        if ohlcv is None:
            return None

        close = ohlcv["close"]
        high = ohlcv["high"]
        low = ohlcv["low"]
        n = len(close)

        if n < 20:
            return None

        # ── ATR% (14-period) ──
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )
        atr_14 = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))
        atr_pct = (atr_14 / close[-1] * 100) if close[-1] > 0 else 0.0
        # Normalise: leveraged ETPs typically have ATR% 2-15.
        # Map to 0-1 via clamp + linear scale.
        atr_norm = float(np.clip((atr_pct - 1.0) / 14.0, 0.0, 1.0))

        # ── RSI regime bucket (14-period) ──
        diff = np.diff(close)
        if len(diff) < 14:
            return None
        gains = np.where(diff > 0, diff, 0.0)[-14:]
        losses = np.where(diff < 0, -diff, 0.0)[-14:]
        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # Normalise RSI 0-100 → 0-1
        rsi_norm = float(np.clip(rsi / 100.0, 0.0, 1.0))

        # ── ADX level bucket ──
        if n < 28:
            adx_norm = 0.3  # default mid-range
        else:
            h = high[-28:]
            l = low[-28:]
            c = close[-28:]
            up = h[1:] - h[:-1]
            dn = l[:-1] - l[1:]
            pdm = np.where((up > dn) & (up > 0), up, 0.0)
            ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
            tr2 = np.maximum(
                h[1:] - l[1:],
                np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])),
            )

            def _smooth14(arr: np.ndarray) -> np.ndarray:
                out = np.zeros(len(arr))
                out[0] = arr[0]
                for i in range(1, len(arr)):
                    out[i] = arr[i] / 14.0 + out[i - 1] * 13.0 / 14.0
                return out

            s_tr = _smooth14(tr2[-14:])
            s_pdm = _smooth14(pdm[-14:])
            s_ndm = _smooth14(ndm[-14:])
            safe_tr = np.where(s_tr > 0, s_tr, 1e-9)
            pdi = 100.0 * s_pdm / safe_tr
            ndi = 100.0 * s_ndm / safe_tr
            denom = np.where(pdi + ndi > 0, pdi + ndi, 1e-9)
            dx = 100.0 * np.abs(pdi - ndi) / denom
            adx = float(np.mean(dx))
            # Normalise: ADX typically 10-60 → 0-1
            adx_norm = float(np.clip((adx - 10.0) / 50.0, 0.0, 1.0))

        return np.array([atr_norm, rsi_norm, adx_norm])

    def _best_core_parent(self) -> dict[str, str]:
        """
        For each candidate, find the CORE ticker with highest absolute
        return correlation → that is the 'core_parent'.
        """
        parents: dict[str, str] = {}

        for cand in self.candidates:
            cand_ret = self._cand_returns.get(cand)
            if cand_ret is None or len(cand_ret) < 10:
                parents[cand] = self.core_list[0] if self.core_list else ""
                continue

            best_corr = -1.0
            best_core = self.core_list[0] if self.core_list else ""

            for core in self.core_list:
                core_ret = self._core_returns.get(core)
                if core_ret is None or len(core_ret) < 10:
                    continue
                min_len = min(len(cand_ret), len(core_ret))
                if min_len < 10:
                    continue
                try:
                    corr = abs(float(np.corrcoef(
                        cand_ret[-min_len:], core_ret[-min_len:],
                    )[0, 1]))
                    if np.isnan(corr):
                        corr = 0.0
                    if corr > best_corr:
                        best_corr = corr
                        best_core = core
                except Exception:
                    pass

            parents[cand] = best_core

        return parents

    @staticmethod
    def _dominant_method(
        ticker: str,
        corr: dict[str, float],
        factor: dict[str, float],
        momentum: dict[str, float],
    ) -> str:
        """Return the label of whichever individual method scored highest."""
        mapping = {
            "correlation": corr.get(ticker, 0.0),
            "factor": factor.get(ticker, 0.0),
            "momentum": momentum.get(ticker, 0.0),
        }
        best = max(mapping, key=lambda k: mapping[k])
        return best

    @staticmethod
    def _build_reasons(
        ticker: str,
        corr: dict[str, float],
        factor: dict[str, float],
        momentum: dict[str, float],
        parent: str,
    ) -> list[str]:
        """Build human-readable list of reasons for this peer selection."""
        reasons: list[str] = []
        c = corr.get(ticker, 0.0)
        f = factor.get(ticker, 0.0)
        m = momentum.get(ticker, 0.0)

        if c >= 0.7:
            reasons.append(f"High correlation ({c:.2f}) with CORE parent {parent}")
        elif c >= 0.4:
            reasons.append(f"Moderate correlation ({c:.2f}) with CORE parent {parent}")

        if f >= 0.5:
            reasons.append(f"Strong factor/theme overlap (score {f:.2f})")
        elif f > 0.0:
            reasons.append(f"Partial factor/theme overlap (score {f:.2f})")

        if m >= 0.7:
            reasons.append(f"Very similar momentum/vol profile (score {m:.2f})")
        elif m >= 0.4:
            reasons.append(f"Similar ATR%/RSI/ADX profile (score {m:.2f})")

        fg = get_factor_group(ticker)
        if fg != "unknown":
            reasons.append(f"Factor group: {fg}")

        if not reasons:
            reasons.append("Selected by combined scoring")

        return reasons


# ─────────────────────────────────────────────────────────────────────────────
# Artifact writer
# ─────────────────────────────────────────────────────────────────────────────


def write_peer_artifact(
    peers: list[PeerMatch],
    date_str: str,
    session: str,
    run_date: Optional[date] = None,
) -> Path:
    """
    Write peers_intel.json to artifacts/YYYY-MM-DD/{session}/peers_intel.json.

    Uses atomic write (tmp → fsync → rename) consistent with the rest of
    the NZT-48 artifact pipeline.
    """
    today = run_date or date.today()
    session_key = session.lower().replace(" ", "_")
    out_dir = ARTIFACTS_ROOT / str(today) / session_key
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "session": session,
        "peer_count": len(peers),
        "peers": [p.to_dict() for p in peers],
    }

    out_path = out_dir / "peers_intel.json"
    tmp_fd, tmp_name = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(json.dumps(payload, indent=2, default=str))
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(out_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise

    logger.info("Wrote peers_intel.json → %s (%d peers)", out_path, len(peers))
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: default instantiation with canonical universe
# ─────────────────────────────────────────────────────────────────────────────


def default_candidates() -> list[str]:
    """Return EXTENDED_UNIVERSE minus CORE_UNIVERSE (the candidate pool)."""
    core_set = set(CORE_UNIVERSE)
    return [t for t in EXTENDED_UNIVERSE if t not in core_set]


def run_peer_selection(
    target_count: int = 6,
    period: str = "60d",
    interval: str = "1d",
    session: str = "LSE_OPEN",
) -> list[PeerMatch]:
    """
    Convenience function: instantiate PeerFinder with canonical universe,
    run selection, write artifact, and return results.
    """
    import math

    candidates = default_candidates()
    count = target_count or math.ceil(0.50 * len(CORE_UNIVERSE))

    finder = PeerFinder(
        core_list=CORE_UNIVERSE,
        candidates=candidates,
        target_count=count,
    )
    peers = finder.find_peers(period=period, interval=interval)

    today_str = date.today().isoformat()
    try:
        write_peer_artifact(peers, date_str=today_str, session=session)
    except Exception as exc:
        logger.warning("Failed to write peer artifact: %s", exc)

    return peers
