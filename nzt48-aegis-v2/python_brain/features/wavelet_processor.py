"""Wavelet Decomposition + Spectral Features — Book 115.

Numpy-only implementation of wavelet-based signal processing for
extracting multi-resolution features from price and volume data.

Components:
  - DWT: Discrete Wavelet Transform using Haar wavelet
  - WaveletDenoiser: SURE thresholding for noise removal
  - SpectralFeatureExtractor: FFT-based spectral features
  - WaveletFeaturePipeline: full pipeline combining all components

Key insight: financial time series contain information at multiple
frequencies. Wavelets decompose the signal into frequency bands,
letting us separately analyze:
  - High-frequency: noise, microstructure, order flow
  - Mid-frequency: intraday trends, momentum
  - Low-frequency: macro trends, regime shifts

State: /app/data/features/wavelet_cache/

Bridge.py integration:
    try:
        from python_brain.features.wavelet_processor import (
            WaveletFeaturePipeline, DWT, SpectralFeatureExtractor,
        )
    except ImportError:
        pass
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger(__name__)

__all__ = [
    "DWT",
    "WaveletDenoiser",
    "SpectralFeatureExtractor",
    "WaveletFeaturePipeline",
]

# ── Paths ──────────────────────────────────────────────────────────────
CACHE_DIR = Path("/app/data/features/wavelet_cache")

# ── Constants ──────────────────────────────────────────────────────────
EPSILON = 1e-12
SQRT2 = math.sqrt(2.0)

# Haar wavelet coefficients
HAAR_LO = np.array([1.0 / SQRT2, 1.0 / SQRT2])    # Low-pass (approximation)
HAAR_HI = np.array([1.0 / SQRT2, -1.0 / SQRT2])    # High-pass (detail)


# ── Discrete Wavelet Transform ─────────────────────────────────────────

class DWT:
    """Discrete Wavelet Transform using Haar wavelet.

    The Haar wavelet is the simplest wavelet, equivalent to computing
    running averages (approximation) and running differences (detail)
    at progressively coarser scales.

    Level 1: splits signal into cA1 (approximation) + cD1 (detail)
    Level 2: splits cA1 into cA2 + cD2
    Level 3: splits cA2 into cA3 + cD3

    Output order: [cA_final, cD_final, ..., cD2, cD1]

    The Haar wavelet is adequate for financial signals because:
    1. It preserves sharp transitions (regime changes, gaps)
    2. It's orthogonal (energy-preserving decomposition)
    3. It's computationally cheap (O(n) per level)
    """

    def __init__(self) -> None:
        self.lo_d = HAAR_LO  # Decomposition low-pass
        self.hi_d = HAAR_HI  # Decomposition high-pass
        self.lo_r = HAAR_LO  # Reconstruction low-pass
        self.hi_r = np.array([-1.0 / SQRT2, 1.0 / SQRT2])  # Reconstruction high-pass

    def _haar_step(self, signal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Single level Haar decomposition.

        Splits signal into approximation (low-frequency) and detail
        (high-frequency) coefficients.

        Args:
            signal: input signal of even length

        Returns:
            (approximation_coeffs, detail_coeffs), each half the length
        """
        n = len(signal)

        # Pad to even length if needed
        if n % 2 != 0:
            signal = np.append(signal, signal[-1])
            n += 1

        half = n // 2
        approx = np.zeros(half)
        detail = np.zeros(half)

        for i in range(half):
            # Haar: average (low-pass) and difference (high-pass)
            approx[i] = (signal[2 * i] + signal[2 * i + 1]) / SQRT2
            detail[i] = (signal[2 * i] - signal[2 * i + 1]) / SQRT2

        return approx, detail

    def _haar_step_inverse(
        self,
        approx: np.ndarray,
        detail: np.ndarray,
    ) -> np.ndarray:
        """Single level Haar reconstruction.

        Reconstructs signal from approximation and detail coefficients.

        Args:
            approx: approximation coefficients (n/2,)
            detail: detail coefficients (n/2,)

        Returns:
            Reconstructed signal (n,)
        """
        half = len(approx)
        n = half * 2
        signal = np.zeros(n)

        for i in range(half):
            signal[2 * i] = (approx[i] + detail[i]) / SQRT2
            signal[2 * i + 1] = (approx[i] - detail[i]) / SQRT2

        return signal

    def decompose(
        self,
        signal: np.ndarray,
        levels: int = 3,
    ) -> List[np.ndarray]:
        """Multi-level wavelet decomposition.

        Args:
            signal: input signal (n,)
            levels: number of decomposition levels

        Returns:
            List of coefficient arrays: [cA_final, cD_final, ..., cD2, cD1]
            cA = approximation (low-freq), cD = detail (high-freq)
        """
        if len(signal) < 2:
            log.warning("DWT.decompose: signal too short (n=%d)", len(signal))
            return [signal.copy()]

        # Maximum possible levels based on signal length
        max_levels = int(math.log2(max(len(signal), 2)))
        levels = min(levels, max_levels)

        details: List[np.ndarray] = []
        current = signal.copy()

        for level in range(levels):
            if len(current) < 2:
                break
            approx, detail = self._haar_step(current)
            details.append(detail)
            current = approx

        # Return: [final_approx, final_detail, ..., detail_1]
        result = [current] + details[::-1]

        log.debug("DWT decompose: %d levels, lengths=%s",
                  levels, [len(c) for c in result])
        return result

    def reconstruct(self, coeffs: List[np.ndarray]) -> np.ndarray:
        """Reconstruct signal from wavelet coefficients.

        Inverse of decompose(). Applies inverse Haar transform
        from coarsest to finest level.

        Args:
            coeffs: [cA_final, cD_final, ..., cD2, cD1] as returned by decompose()

        Returns:
            Reconstructed signal
        """
        if len(coeffs) == 0:
            return np.array([])
        if len(coeffs) == 1:
            return coeffs[0].copy()

        # Start from coarsest approximation
        current = coeffs[0].copy()

        # Iterate from coarsest detail to finest
        # coeffs[1] = cD_final (coarsest detail)
        # coeffs[-1] = cD1 (finest detail)
        for i in range(1, len(coeffs)):
            detail = coeffs[i]

            # Ensure matching lengths
            min_len = min(len(current), len(detail))
            current = current[:min_len]
            detail = detail[:min_len]

            current = self._haar_step_inverse(current, detail)

        return current

    def energy_per_level(self, coeffs: List[np.ndarray]) -> Dict[str, float]:
        """Compute energy (L2 norm squared) at each decomposition level.

        Useful for determining which frequency bands carry the most
        signal energy. In financial data:
        - High energy in detail levels → noisy/choppy
        - High energy in approximation → strong trend

        Args:
            coeffs: decomposition coefficients

        Returns:
            Dict mapping level name to energy fraction
        """
        energies: Dict[str, float] = {}
        total_energy = 0.0

        for i, c in enumerate(coeffs):
            e = float(np.sum(c ** 2))
            if i == 0:
                name = "approx"
            else:
                name = f"detail_{len(coeffs) - i}"
            energies[name] = e
            total_energy += e

        # Normalize to fractions
        if total_energy > 0:
            energies = {k: round(v / total_energy, 6) for k, v in energies.items()}

        return energies


# ── Wavelet Denoiser ───────────────────────────────────────────────────

class WaveletDenoiser:
    """Wavelet-based signal denoising using SURE thresholding.

    Denoises by shrinking detail (high-frequency) coefficients:
    1. Decompose signal via DWT
    2. Estimate noise level from finest detail coefficients
    3. Compute threshold via Stein's Unbiased Risk Estimate (SURE)
    4. Apply soft or hard thresholding to detail coefficients
    5. Reconstruct denoised signal

    Soft thresholding: sign(x) * max(|x| - t, 0) — smoother, biased
    Hard thresholding: x * I(|x| > t) — preserves amplitude, jumpy

    For financial data, soft thresholding is preferred as it produces
    smoother signals for trend detection.
    """

    def __init__(self) -> None:
        self.dwt = DWT()

    def denoise(
        self,
        signal: np.ndarray,
        threshold: str = "soft",
        levels: int = 3,
    ) -> np.ndarray:
        """Denoise signal using wavelet shrinkage.

        Args:
            signal: noisy input signal (n,)
            threshold: "soft" or "hard" thresholding
            levels: DWT decomposition levels

        Returns:
            Denoised signal (n,)
        """
        original_len = len(signal)
        if original_len < 4:
            return signal.copy()

        # Decompose
        coeffs = self.dwt.decompose(signal, levels=levels)

        if len(coeffs) <= 1:
            return signal.copy()

        # Estimate noise from finest detail level (MAD estimator)
        finest_detail = coeffs[-1]
        noise_sigma = self._estimate_noise(finest_detail)

        # Apply thresholding to detail coefficients (not approximation)
        denoised_coeffs = [coeffs[0]]  # Keep approximation unchanged
        for i in range(1, len(coeffs)):
            detail = coeffs[i]

            # SURE threshold for this level
            t = self._compute_sure_threshold(detail, noise_sigma)

            if threshold == "soft":
                denoised_coeffs.append(self._soft_threshold(detail, t))
            else:
                denoised_coeffs.append(self._hard_threshold(detail, t))

        # Reconstruct
        result = self.dwt.reconstruct(denoised_coeffs)

        # Trim to original length
        result = result[:original_len]

        return result

    @staticmethod
    def _soft_threshold(coeffs: np.ndarray, threshold: float) -> np.ndarray:
        """Soft thresholding: sign(x) * max(|x| - t, 0).

        Shrinks coefficients toward zero. Coefficients below threshold
        are set to zero; those above are reduced by threshold amount.

        Args:
            coeffs: wavelet coefficients
            threshold: threshold value

        Returns:
            Thresholded coefficients
        """
        return np.sign(coeffs) * np.maximum(np.abs(coeffs) - threshold, 0.0)

    @staticmethod
    def _hard_threshold(coeffs: np.ndarray, threshold: float) -> np.ndarray:
        """Hard thresholding: x * I(|x| > t).

        Keeps coefficients above threshold unchanged, zeros out the rest.

        Args:
            coeffs: wavelet coefficients
            threshold: threshold value

        Returns:
            Thresholded coefficients
        """
        return coeffs * (np.abs(coeffs) > threshold).astype(np.float64)

    @staticmethod
    def _estimate_noise(detail_coeffs: np.ndarray) -> float:
        """Estimate noise level from finest detail coefficients.

        Uses Median Absolute Deviation (MAD) estimator which is robust
        to outliers (unlike standard deviation).

        sigma = MAD(detail) / 0.6745

        Args:
            detail_coeffs: finest level detail coefficients

        Returns:
            Estimated noise standard deviation
        """
        if len(detail_coeffs) == 0:
            return 1.0
        mad = float(np.median(np.abs(detail_coeffs - np.median(detail_coeffs))))
        return mad / 0.6745 if mad > 0 else float(np.std(detail_coeffs)) + EPSILON

    def _compute_sure_threshold(
        self,
        coeffs: np.ndarray,
        noise_sigma: float,
    ) -> float:
        """Compute optimal threshold via SURE (Stein's Unbiased Risk Estimate).

        SURE provides a data-driven threshold that minimizes the
        estimated MSE of the denoised signal without knowing the true signal.

        For each candidate threshold t:
            SURE(t) = n * sigma^2 - 2 * sigma^2 * #{|c_i| <= t} + sum(min(c_i^2, t^2))

        Args:
            coeffs: wavelet coefficients at this level
            noise_sigma: estimated noise std

        Returns:
            Optimal threshold value
        """
        n = len(coeffs)
        if n == 0 or noise_sigma < EPSILON:
            return 0.0

        sigma_sq = noise_sigma ** 2

        # Sort absolute values for efficient threshold search
        abs_coeffs = np.sort(np.abs(coeffs))

        best_threshold = abs_coeffs[-1] if n > 0 else 0.0
        best_risk = float("inf")

        # Evaluate SURE at each coefficient magnitude as candidate threshold
        for k in range(n):
            t = abs_coeffs[k]

            # Number of coefficients <= t
            n_below = k + 1

            # SURE risk
            clipped = np.minimum(coeffs ** 2, t ** 2)
            risk = n * sigma_sq - 2.0 * sigma_sq * n_below + float(np.sum(clipped))

            if risk < best_risk:
                best_risk = risk
                best_threshold = t

        # Universal threshold as upper bound: sigma * sqrt(2 * log(n))
        universal = noise_sigma * math.sqrt(2.0 * math.log(max(n, 2)))
        best_threshold = min(best_threshold, universal)

        return best_threshold


# ── Spectral Feature Extractor ─────────────────────────────────────────

class SpectralFeatureExtractor:
    """Extract spectral (frequency-domain) features from time series.

    Uses FFT to decompose signal into frequency components and extract:
    - Spectral entropy: randomness of the spectrum (high → noise, low → periodic)
    - Dominant frequency: strongest periodic component
    - Spectral slope: how energy decays with frequency (steeper → smoother)
    - Power bands: energy in predefined frequency bands

    For financial data:
    - Low spectral entropy → predictable periodic pattern (e.g., daily cycle)
    - Steep spectral slope → trend-dominated (low-freq energy)
    - Flat spectral slope → noise-dominated (HF energy)
    """

    def __init__(self) -> None:
        pass

    def extract(
        self,
        signal: np.ndarray,
        sampling_rate: float = 1.0,
    ) -> Dict[str, float]:
        """Extract all spectral features from a signal.

        Args:
            signal: input time series (n,)
            sampling_rate: samples per unit time (e.g., 1.0 for 1 sample/bar)

        Returns:
            Dict with spectral_entropy, dominant_freq, spectral_slope,
            power_bands, total_power
        """
        if len(signal) < 8:
            return {
                "spectral_entropy": 0.0,
                "dominant_freq": 0.0,
                "spectral_slope": 0.0,
                "total_power": 0.0,
                "power_low": 0.0,
                "power_mid": 0.0,
                "power_high": 0.0,
                "power_ratio_low_high": 0.0,
            }

        freq, power = self._fft_power(signal, sampling_rate)

        # Filter out DC component (freq = 0)
        if len(freq) > 1:
            freq = freq[1:]
            power = power[1:]

        if len(power) == 0 or np.sum(power) < EPSILON:
            return {
                "spectral_entropy": 0.0,
                "dominant_freq": 0.0,
                "spectral_slope": 0.0,
                "total_power": 0.0,
                "power_low": 0.0,
                "power_mid": 0.0,
                "power_high": 0.0,
                "power_ratio_low_high": 0.0,
            }

        features: Dict[str, float] = {}

        # Spectral entropy
        features["spectral_entropy"] = round(self._spectral_entropy(power), 6)

        # Dominant frequency
        dom_idx = int(np.argmax(power))
        features["dominant_freq"] = round(float(freq[dom_idx]), 6)

        # Spectral slope
        features["spectral_slope"] = round(self._spectral_slope(freq, power), 6)

        # Total power
        total_power = float(np.sum(power))
        features["total_power"] = round(total_power, 6)

        # Power in frequency bands
        nyquist = sampling_rate / 2.0
        bands = self._compute_power_bands(freq, power, nyquist)
        features.update(bands)

        return features

    @staticmethod
    def _fft_power(
        signal: np.ndarray,
        sampling_rate: float = 1.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Compute FFT power spectrum.

        Args:
            signal: input signal
            sampling_rate: samples per unit time

        Returns:
            (frequencies, power) arrays
        """
        n = len(signal)

        # Remove mean (DC component)
        centered = signal - np.mean(signal)

        # Apply Hann window to reduce spectral leakage
        window = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / (n - 1 + EPSILON)))
        windowed = centered * window

        # FFT
        fft_vals = np.fft.rfft(windowed)

        # Power spectrum (magnitude squared)
        power = np.abs(fft_vals) ** 2 / n

        # Frequency axis
        freq = np.fft.rfftfreq(n, d=1.0 / sampling_rate)

        return freq, power

    @staticmethod
    def _spectral_entropy(power: np.ndarray) -> float:
        """Compute spectral entropy (normalized).

        Entropy of the normalized power spectrum. High entropy means
        the signal energy is spread uniformly across frequencies (noise).
        Low entropy means energy is concentrated at few frequencies (periodic).

        Returns value in [0, 1] where 0 = perfectly periodic, 1 = white noise.

        Args:
            power: power spectrum array

        Returns:
            Normalized spectral entropy in [0, 1]
        """
        # Normalize to probability distribution
        total = np.sum(power)
        if total < EPSILON:
            return 0.0

        p = power / total
        p = p[p > EPSILON]  # Remove zeros

        # Shannon entropy
        entropy = -float(np.sum(p * np.log(p)))

        # Normalize by maximum possible entropy (uniform distribution)
        max_entropy = math.log(len(power)) if len(power) > 1 else 1.0
        if max_entropy > EPSILON:
            entropy /= max_entropy

        return min(entropy, 1.0)

    @staticmethod
    def _spectral_slope(freq: np.ndarray, power: np.ndarray) -> float:
        """Compute spectral slope via log-log linear regression.

        Slope of log(power) vs log(frequency). In financial data:
        - Slope ~ -2: Brownian motion (random walk)
        - Slope < -2: smoother, more persistent (trending)
        - Slope > -2: rougher, more anti-persistent (mean-reverting)

        Args:
            freq: frequency array
            power: power spectrum array

        Returns:
            Spectral slope (typically negative)
        """
        # Filter out zero freq/power
        mask = (freq > EPSILON) & (power > EPSILON)
        if mask.sum() < 3:
            return 0.0

        log_freq = np.log(freq[mask])
        log_power = np.log(power[mask])

        # Linear regression: log_power = slope * log_freq + intercept
        n = len(log_freq)
        mean_x = float(np.mean(log_freq))
        mean_y = float(np.mean(log_power))
        cov = float(np.sum((log_freq - mean_x) * (log_power - mean_y)))
        var = float(np.sum((log_freq - mean_x) ** 2))

        if var < EPSILON:
            return 0.0

        slope = cov / var
        return slope

    @staticmethod
    def _compute_power_bands(
        freq: np.ndarray,
        power: np.ndarray,
        nyquist: float,
    ) -> Dict[str, float]:
        """Compute power in predefined frequency bands.

        Bands (as fraction of Nyquist):
        - Low: 0-0.1 (long-term trends)
        - Mid: 0.1-0.4 (intraday patterns)
        - High: 0.4-1.0 (noise, microstructure)

        Args:
            freq: frequency array
            power: power array
            nyquist: Nyquist frequency

        Returns:
            Dict with power per band and ratios
        """
        total = float(np.sum(power))
        if total < EPSILON or nyquist < EPSILON:
            return {
                "power_low": 0.0,
                "power_mid": 0.0,
                "power_high": 0.0,
                "power_ratio_low_high": 0.0,
            }

        low_mask = freq <= 0.1 * nyquist
        mid_mask = (freq > 0.1 * nyquist) & (freq <= 0.4 * nyquist)
        high_mask = freq > 0.4 * nyquist

        p_low = float(np.sum(power[low_mask])) / total if low_mask.any() else 0.0
        p_mid = float(np.sum(power[mid_mask])) / total if mid_mask.any() else 0.0
        p_high = float(np.sum(power[high_mask])) / total if high_mask.any() else 0.0

        ratio = p_low / max(p_high, EPSILON)

        return {
            "power_low": round(p_low, 6),
            "power_mid": round(p_mid, 6),
            "power_high": round(p_high, 6),
            "power_ratio_low_high": round(ratio, 4),
        }


# ── Wavelet Feature Pipeline ──────────────────────────────────────────

class WaveletFeaturePipeline:
    """Full pipeline: denoise → decompose → spectral features per level.

    Combines all wavelet/spectral components into a feature extraction
    pipeline suitable for feeding into ML models.

    Features produced per signal:
    - Denoised signal statistics (mean, std, trend)
    - Per-level energy distribution
    - Per-level spectral features
    - Cross-level features (energy ratios, etc.)

    Usage:
        pipeline = WaveletFeaturePipeline()
        features = pipeline.process(price_series, volume_series)
    """

    def __init__(self, levels: int = 3) -> None:
        self.levels = levels
        self.dwt = DWT()
        self.denoiser = WaveletDenoiser()
        self.spectral = SpectralFeatureExtractor()

    def process(
        self,
        price_series: np.ndarray,
        volume_series: Optional[np.ndarray] = None,
    ) -> Dict[str, float]:
        """Full wavelet feature extraction pipeline.

        Args:
            price_series: price time series (n,)
            volume_series: optional volume time series (n,)

        Returns:
            Dict of extracted features
        """
        features: Dict[str, float] = {}

        if len(price_series) < 8:
            log.warning("WaveletFeaturePipeline: price series too short (n=%d)",
                        len(price_series))
            return features

        # Step 1: Denoise price series
        denoised_price = self.denoiser.denoise(
            price_series, threshold="soft", levels=self.levels
        )

        # Denoised signal statistics
        features["denoised_mean"] = round(float(np.mean(denoised_price)), 6)
        features["denoised_std"] = round(float(np.std(denoised_price)), 6)

        # Noise level estimate (difference between original and denoised)
        noise = price_series[:len(denoised_price)] - denoised_price
        features["noise_level"] = round(float(np.std(noise)), 6)
        snr = float(np.std(denoised_price)) / max(float(np.std(noise)), EPSILON)
        features["signal_to_noise"] = round(snr, 4)

        # Denoised trend (linear slope)
        if len(denoised_price) > 1:
            x = np.arange(len(denoised_price), dtype=np.float64)
            x_centered = x - x.mean()
            cov = float(np.sum(x_centered * (denoised_price - denoised_price.mean())))
            var_x = float(np.sum(x_centered ** 2))
            trend_slope = cov / max(var_x, EPSILON)
            features["trend_slope"] = round(trend_slope, 8)
        else:
            features["trend_slope"] = 0.0

        # Step 2: Wavelet decomposition (on returns for stationarity)
        returns = np.diff(price_series) / (price_series[:-1] + EPSILON)
        returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)

        if len(returns) >= 4:
            coeffs = self.dwt.decompose(returns, levels=self.levels)

            # Energy per level
            energy = self.dwt.energy_per_level(coeffs)
            for level_name, energy_frac in energy.items():
                features[f"energy_{level_name}"] = energy_frac

            # Step 3: Spectral features per decomposition level
            for i, c in enumerate(coeffs):
                if len(c) < 4:
                    continue

                if i == 0:
                    prefix = "approx"
                else:
                    prefix = f"detail_{len(coeffs) - i}"

                spectral_feats = self.spectral.extract(c, sampling_rate=1.0)
                for feat_name, feat_val in spectral_feats.items():
                    features[f"{prefix}_{feat_name}"] = feat_val

                # Additional per-level statistics
                features[f"{prefix}_kurtosis"] = round(self._kurtosis(c), 4)
                features[f"{prefix}_skewness"] = round(self._skewness(c), 4)
                features[f"{prefix}_zero_crossings"] = round(self._zero_crossing_rate(c), 4)

        # Step 4: Full-signal spectral features (on returns)
        if len(returns) >= 8:
            full_spectral = self.spectral.extract(returns, sampling_rate=1.0)
            for feat_name, feat_val in full_spectral.items():
                features[f"full_{feat_name}"] = feat_val

        # Step 5: Volume features (if provided)
        if volume_series is not None and len(volume_series) >= 8:
            vol_features = self._process_volume(volume_series)
            features.update(vol_features)

        # Step 6: Cross-level features
        cross_features = self._cross_level_features(features)
        features.update(cross_features)

        log.debug("WaveletFeaturePipeline: extracted %d features", len(features))
        return features

    def _process_volume(self, volume_series: np.ndarray) -> Dict[str, float]:
        """Extract wavelet features from volume series.

        Args:
            volume_series: volume time series

        Returns:
            Dict of volume-specific wavelet features
        """
        features: Dict[str, float] = {}

        # Normalize volume (log transform for heavy tails)
        log_vol = np.log(volume_series + 1.0)

        # Denoise
        denoised_vol = self.denoiser.denoise(log_vol, threshold="soft", levels=self.levels)
        features["vol_denoised_std"] = round(float(np.std(denoised_vol)), 6)

        # Volume spectral features
        vol_returns = np.diff(log_vol)
        if len(vol_returns) >= 8:
            vol_spectral = self.spectral.extract(vol_returns, sampling_rate=1.0)
            for name, val in vol_spectral.items():
                features[f"vol_{name}"] = val

        # Volume decomposition energy
        if len(vol_returns) >= 4:
            coeffs = self.dwt.decompose(vol_returns, levels=self.levels)
            energy = self.dwt.energy_per_level(coeffs)
            for level_name, energy_frac in energy.items():
                features[f"vol_energy_{level_name}"] = energy_frac

        return features

    @staticmethod
    def _cross_level_features(features: Dict[str, float]) -> Dict[str, float]:
        """Compute cross-level interaction features.

        Args:
            features: already computed per-level features

        Returns:
            Dict of cross-level features
        """
        cross: Dict[str, float] = {}

        # Energy ratio: approx vs total detail
        approx_energy = features.get("energy_approx", 0.0)
        detail_energies = [
            v for k, v in features.items()
            if k.startswith("energy_detail_")
        ]
        total_detail = sum(detail_energies) if detail_energies else 0.0

        cross["energy_approx_to_detail_ratio"] = round(
            approx_energy / max(total_detail, EPSILON), 4
        )

        # Spectral entropy gradient (does entropy increase with finer resolution?)
        entropies = []
        for i in range(1, 5):
            key = f"detail_{i}_spectral_entropy"
            if key in features:
                entropies.append(features[key])

        if len(entropies) >= 2:
            entropy_gradient = entropies[-1] - entropies[0]
            cross["entropy_gradient"] = round(entropy_gradient, 6)

        return cross

    @staticmethod
    def _kurtosis(x: np.ndarray) -> float:
        """Compute excess kurtosis."""
        n = len(x)
        if n < 4:
            return 0.0
        mean = float(np.mean(x))
        std = float(np.std(x))
        if std < EPSILON:
            return 0.0
        m4 = float(np.mean((x - mean) ** 4))
        return m4 / (std ** 4) - 3.0

    @staticmethod
    def _skewness(x: np.ndarray) -> float:
        """Compute skewness."""
        n = len(x)
        if n < 3:
            return 0.0
        mean = float(np.mean(x))
        std = float(np.std(x))
        if std < EPSILON:
            return 0.0
        m3 = float(np.mean((x - mean) ** 3))
        return m3 / (std ** 3)

    @staticmethod
    def _zero_crossing_rate(x: np.ndarray) -> float:
        """Compute zero-crossing rate."""
        if len(x) < 2:
            return 0.0
        signs = np.sign(x)
        crossings = np.sum(np.abs(np.diff(signs)) > 0)
        return float(crossings) / (len(x) - 1)
