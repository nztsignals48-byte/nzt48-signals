"""Denoising Diffusion for Synthetic Market Data Generation — Book 156B.

Numpy-only denoising diffusion probabilistic model (DDPM) for generating
synthetic market data. Implements the forward noising process and reverse
denoising process using a simple MLP denoiser.

The forward process gradually adds Gaussian noise to real market data
over T timesteps. The reverse process (learned by the denoiser) removes
noise step by step to generate new samples from pure noise.

Why diffusion for market data:
  - Captures complex multimodal distributions (unlike GAN mode collapse)
  - Preserves temporal structure when conditioned on sequence context
  - Controllable generation quality via noise schedule
  - More stable training than GANs (no adversarial dynamics)

Validation: Generated data is tested for stylized facts (fat tails,
volatility clustering, leverage effect).

State persisted to /app/data/diffusion/.

Usage:
    from python_brain.synthetic.diffusion_models import (
        MarketDataGenerator, DiffusionConfig, NoiseScheduler,
        SimpleDenoisingNetwork,
    )
    config = DiffusionConfig(n_steps=100)
    generator = MarketDataGenerator(config)
    generator.train(real_data, epochs=50)
    synthetic = generator.generate(n_samples=1000, seq_len=60)
    validation = generator.validate_stylized_facts(real_data, synthetic)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("diffusion_models")

__all__ = [
    "DiffusionConfig",
    "NoiseScheduler",
    "SimpleDenoisingNetwork",
    "MarketDataGenerator",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/diffusion")


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class DiffusionConfig:
    """Configuration for the diffusion model.

    Attributes:
        n_steps: Number of diffusion timesteps (T).
        beta_start: Noise schedule start value.
        beta_end: Noise schedule end value.
        hidden_dim: Denoiser MLP hidden layer size.
        lr: Learning rate for training.
        seed: Random seed.
    """
    n_steps: int = 100
    beta_start: float = 1e-4
    beta_end: float = 0.02
    hidden_dim: int = 64
    lr: float = 1e-3
    seed: int = 42


# ── Noise Scheduler ───────────────────────────────────────────────────

class NoiseScheduler:
    """Manages the noise schedule for the forward diffusion process.

    Implements a linear beta schedule and precomputes alpha/alpha_bar
    for efficient sampling at arbitrary timesteps.
    """

    def __init__(self, n_steps: int = 100,
                 beta_start: float = 1e-4,
                 beta_end: float = 0.02):
        """Initialize noise scheduler.

        Args:
            n_steps: Total number of diffusion steps.
            beta_start: Starting noise level.
            beta_end: Ending noise level.
        """
        self.n_steps = n_steps
        self.betas = np.linspace(beta_start, beta_end, n_steps)
        self.alphas = 1.0 - self.betas
        self.alpha_bars = np.cumprod(self.alphas)

        # Precompute sqrt values for efficiency
        self._sqrt_alpha_bars = np.sqrt(self.alpha_bars)
        self._sqrt_one_minus_alpha_bars = np.sqrt(1.0 - self.alpha_bars)
        self._sqrt_alphas = np.sqrt(self.alphas)

        log.info("NoiseScheduler: n_steps=%d, beta=[%.4f, %.4f], "
                 "final_alpha_bar=%.4f",
                 n_steps, beta_start, beta_end, self.alpha_bars[-1])

    def _get_alpha_bar(self, t: int) -> float:
        """Get cumulative product of alphas up to step t.

        Args:
            t: Diffusion timestep [0, n_steps-1].

        Returns:
            alpha_bar_t = prod(alpha_i for i in 0..t).
        """
        t = max(0, min(t, self.n_steps - 1))
        return float(self.alpha_bars[t])

    def add_noise(self, x0: np.ndarray,
                  t: int) -> Tuple[np.ndarray, np.ndarray]:
        """Forward process: add noise to data at timestep t.

        q(x_t | x_0) = N(x_t; sqrt(alpha_bar_t) * x_0,
                         (1 - alpha_bar_t) * I)

        Args:
            x0: Clean data, shape (batch, dim) or (dim,).
            t: Diffusion timestep.

        Returns:
            Tuple (x_noisy, noise) where noise is the sampled noise.
        """
        t = max(0, min(t, self.n_steps - 1))
        sqrt_ab = self._sqrt_alpha_bars[t]
        sqrt_one_minus_ab = self._sqrt_one_minus_alpha_bars[t]

        noise = np.random.default_rng().standard_normal(x0.shape)
        x_noisy = sqrt_ab * x0 + sqrt_one_minus_ab * noise

        return x_noisy, noise

    def add_noise_batch(self, x0: np.ndarray, t_batch: np.ndarray,
                        rng: np.random.Generator) -> Tuple[np.ndarray, np.ndarray]:
        """Add noise to a batch with different timesteps per sample.

        Args:
            x0: Clean data, shape (batch, dim).
            t_batch: Timesteps, shape (batch,).
            rng: Random number generator.

        Returns:
            Tuple (x_noisy, noise), each shape (batch, dim).
        """
        t_batch = np.clip(t_batch, 0, self.n_steps - 1).astype(int)
        noise = rng.standard_normal(x0.shape)

        sqrt_ab = self._sqrt_alpha_bars[t_batch][:, None]
        sqrt_one_minus_ab = self._sqrt_one_minus_alpha_bars[t_batch][:, None]

        x_noisy = sqrt_ab * x0 + sqrt_one_minus_ab * noise
        return x_noisy, noise

    def remove_noise_step(self, x_t: np.ndarray, predicted_noise: np.ndarray,
                          t: int, rng: np.random.Generator) -> np.ndarray:
        """Single reverse diffusion step: denoise from t to t-1.

        p(x_{t-1} | x_t) using the predicted noise.

        Args:
            x_t: Noisy data at step t.
            predicted_noise: Noise predicted by the denoiser.
            t: Current timestep.
            rng: Random number generator.

        Returns:
            Denoised data at step t-1.
        """
        t = max(0, min(t, self.n_steps - 1))
        beta_t = self.betas[t]
        sqrt_alpha_t = self._sqrt_alphas[t]
        sqrt_one_minus_ab = self._sqrt_one_minus_alpha_bars[t]

        # Predicted x_0 contribution
        coeff = beta_t / (sqrt_one_minus_ab + 1e-10)
        mean = (x_t - coeff * predicted_noise) / (sqrt_alpha_t + 1e-10)

        if t > 0:
            # Add noise for stochastic sampling (not at t=0)
            sigma = math.sqrt(beta_t)
            noise = rng.standard_normal(x_t.shape)
            return mean + sigma * noise
        return mean


# ── Simple Denoising Network ──────────────────────────────────────────

class SimpleDenoisingNetwork:
    """MLP denoiser that predicts noise from noisy input and timestep.

    Input: concatenation of [x_noisy, time_embedding]
    Output: predicted noise (same dimension as x_noisy)

    Time embedding uses sinusoidal positional encoding.
    """

    def __init__(self, data_dim: int, hidden_dim: int = 64,
                 n_steps: int = 100, seed: int = 42):
        """Initialize denoiser.

        Args:
            data_dim: Dimension of the data to denoise.
            hidden_dim: MLP hidden layer size.
            n_steps: Number of diffusion timesteps (for time embedding).
            seed: Random seed.
        """
        self.data_dim = data_dim
        self.hidden_dim = hidden_dim
        self.n_steps = n_steps
        self._rng = np.random.default_rng(seed)

        # Time embedding dimension
        self._time_dim = min(data_dim, 16)

        # MLP layers: input -> hidden -> hidden -> output
        input_dim = data_dim + self._time_dim
        std1 = math.sqrt(2.0 / input_dim)
        std2 = math.sqrt(2.0 / hidden_dim)
        std3 = math.sqrt(2.0 / hidden_dim)

        self.W1 = self._rng.normal(0, std1, (input_dim, hidden_dim))
        self.b1 = np.zeros(hidden_dim)
        self.W2 = self._rng.normal(0, std2, (hidden_dim, hidden_dim))
        self.b2 = np.zeros(hidden_dim)
        self.W3 = self._rng.normal(0, std3, (hidden_dim, data_dim))
        self.b3 = np.zeros(data_dim)

        # Adam optimizer state
        self._m = {"W1": 0, "b1": 0, "W2": 0, "b2": 0, "W3": 0, "b3": 0}
        self._v = {"W1": 0, "b1": 0, "W2": 0, "b2": 0, "W3": 0, "b3": 0}
        self._adam_t = 0

        self._n_train_steps = 0
        log.info("SimpleDenoisingNetwork: data_dim=%d, hidden=%d, time_dim=%d",
                 data_dim, hidden_dim, self._time_dim)

    def _time_embedding(self, t: np.ndarray) -> np.ndarray:
        """Sinusoidal time embedding.

        Args:
            t: Timestep(s), shape (batch,) or scalar.

        Returns:
            Embedding, shape (batch, time_dim).
        """
        t = np.atleast_1d(np.asarray(t, dtype=np.float64))
        half_dim = self._time_dim // 2
        freqs = np.exp(-math.log(10000.0) * np.arange(half_dim) / max(half_dim, 1))
        args = t[:, None] * freqs[None, :]
        embedding = np.concatenate([np.sin(args), np.cos(args)], axis=-1)
        # Pad if time_dim is odd
        if self._time_dim % 2 == 1:
            embedding = np.concatenate([embedding, np.zeros((len(t), 1))], axis=-1)
        return embedding

    def forward(self, x_noisy: np.ndarray, t: np.ndarray) -> np.ndarray:
        """Predict noise from noisy input and timestep.

        Args:
            x_noisy: Noisy data, shape (batch, data_dim) or (data_dim,).
            t: Timesteps, shape (batch,) or scalar.

        Returns:
            Predicted noise, same shape as x_noisy.
        """
        single = x_noisy.ndim == 1
        if single:
            x_noisy = x_noisy.reshape(1, -1)

        t_emb = self._time_embedding(t)
        x_in = np.concatenate([x_noisy, t_emb], axis=-1)

        # Forward through MLP
        self._h1_pre = x_in @ self.W1 + self.b1
        self._h1 = np.maximum(0.0, self._h1_pre)  # ReLU
        self._h2_pre = self._h1 @ self.W2 + self.b2
        self._h2 = np.maximum(0.0, self._h2_pre)  # ReLU
        output = self._h2 @ self.W3 + self.b3

        # Cache for backprop
        self._x_in = x_in

        if single:
            return output.squeeze(0)
        return output

    def _backward(self, grad_output: np.ndarray, lr: float) -> None:
        """Backward pass with Adam optimizer."""
        batch_size = max(grad_output.shape[0], 1)

        # Layer 3
        grad_W3 = self._h2.T @ grad_output / batch_size
        grad_b3 = np.mean(grad_output, axis=0)
        grad_h2 = grad_output @ self.W3.T

        # ReLU
        grad_h2 = grad_h2 * (self._h2_pre > 0)

        # Layer 2
        grad_W2 = self._h1.T @ grad_h2 / batch_size
        grad_b2 = np.mean(grad_h2, axis=0)
        grad_h1 = grad_h2 @ self.W2.T

        # ReLU
        grad_h1 = grad_h1 * (self._h1_pre > 0)

        # Layer 1
        grad_W1 = self._x_in.T @ grad_h1 / batch_size
        grad_b1 = np.mean(grad_h1, axis=0)

        # Gradient clipping
        for g in [grad_W1, grad_b1, grad_W2, grad_b2, grad_W3, grad_b3]:
            np.clip(g, -1.0, 1.0, out=g)

        # Adam update
        self._adam_t += 1
        beta1, beta2, eps = 0.9, 0.999, 1e-8

        for name, param, grad in [
            ("W1", "W1", grad_W1), ("b1", "b1", grad_b1),
            ("W2", "W2", grad_W2), ("b2", "b2", grad_b2),
            ("W3", "W3", grad_W3), ("b3", "b3", grad_b3),
        ]:
            self._m[name] = beta1 * self._m[name] + (1 - beta1) * grad
            self._v[name] = beta2 * self._v[name] + (1 - beta2) * grad ** 2
            m_hat = self._m[name] / (1 - beta1 ** self._adam_t)
            v_hat = self._v[name] / (1 - beta2 ** self._adam_t)
            update = lr * m_hat / (np.sqrt(v_hat) + eps)
            setattr(self, param, getattr(self, param) - update)

    def train(self, data: np.ndarray, epochs: int = 50,
              lr: float = 1e-3, batch_size: int = 32,
              scheduler: Optional[NoiseScheduler] = None) -> Dict[str, Any]:
        """Train the denoiser on clean data.

        Args:
            data: Clean data, shape (n_samples, data_dim).
            epochs: Number of training epochs.
            lr: Learning rate.
            batch_size: Mini-batch size.
            scheduler: NoiseScheduler for adding noise. Creates default if None.

        Returns:
            Training history dict.
        """
        if data.ndim == 1:
            data = data.reshape(-1, 1)

        n_samples = data.shape[0]
        if scheduler is None:
            scheduler = NoiseScheduler(self.n_steps)

        losses: List[float] = []
        log.info("Training denoiser: %d samples, %d epochs, lr=%.4f",
                 n_samples, epochs, lr)

        for epoch in range(epochs):
            epoch_loss = 0.0
            n_batches = 0

            indices = self._rng.permutation(n_samples)

            for start in range(0, n_samples - batch_size + 1, batch_size):
                batch_idx = indices[start:start + batch_size]
                x0 = data[batch_idx]

                # Random timesteps for each sample
                t_batch = self._rng.integers(0, self.n_steps, size=batch_size)

                # Add noise
                x_noisy, noise = scheduler.add_noise_batch(x0, t_batch, self._rng)

                # Predict noise
                pred_noise = self.forward(x_noisy, t_batch.astype(np.float64))

                # MSE loss
                error = pred_noise - noise
                loss = float(np.mean(error ** 2))
                epoch_loss += loss
                n_batches += 1

                # Backprop
                grad = 2.0 * error / error.size * error.shape[0]
                self._backward(grad, lr)

                self._n_train_steps += 1

            avg_loss = epoch_loss / max(n_batches, 1)
            losses.append(avg_loss)

            if (epoch + 1) % max(epochs // 10, 1) == 0 or epoch == 0:
                log.info("Epoch %d/%d: loss=%.6f", epoch + 1, epochs, avg_loss)

        return {
            "epochs": epochs,
            "final_loss": round(losses[-1], 6) if losses else 0.0,
            "min_loss": round(min(losses), 6) if losses else 0.0,
            "train_steps": self._n_train_steps,
            "losses": [round(l, 6) for l in losses],
        }


# ── Market Data Generator ─────────────────────────────────────────────

class MarketDataGenerator:
    """Generates synthetic market data using denoising diffusion.

    Trains on real return sequences and generates new sequences
    that preserve the statistical properties (stylized facts) of
    real market data.
    """

    def __init__(self, config: Optional[DiffusionConfig] = None):
        """Initialize market data generator.

        Args:
            config: DiffusionConfig. Uses defaults if None.
        """
        self._config = config or DiffusionConfig()
        self._scheduler = NoiseScheduler(
            self._config.n_steps,
            self._config.beta_start,
            self._config.beta_end,
        )
        self._denoiser: Optional[SimpleDenoisingNetwork] = None
        self._data_mean: float = 0.0
        self._data_std: float = 1.0
        self._data_dim: int = 0
        self._rng = np.random.default_rng(self._config.seed)
        self._trained: bool = False

        log.info("MarketDataGenerator initialized: T=%d, beta=[%.4f,%.4f], "
                 "hidden=%d",
                 self._config.n_steps, self._config.beta_start,
                 self._config.beta_end, self._config.hidden_dim)

    def train(self, real_data: np.ndarray, epochs: int = 50) -> Dict[str, Any]:
        """Train the diffusion model on real market data.

        Args:
            real_data: Real return data, shape (n_samples,) or (n_samples, dim).
            epochs: Number of training epochs.

        Returns:
            Training metrics dict.
        """
        if real_data.ndim == 1:
            real_data = real_data.reshape(-1, 1)

        self._data_dim = real_data.shape[1]
        self._data_mean = float(np.mean(real_data))
        self._data_std = float(np.std(real_data))
        if self._data_std < 1e-8:
            self._data_std = 1.0

        # Normalize
        normalized = (real_data - self._data_mean) / self._data_std

        # Initialize denoiser
        self._denoiser = SimpleDenoisingNetwork(
            data_dim=self._data_dim,
            hidden_dim=self._config.hidden_dim,
            n_steps=self._config.n_steps,
            seed=self._config.seed,
        )

        # Train
        history = self._denoiser.train(
            normalized, epochs=epochs, lr=self._config.lr,
            scheduler=self._scheduler,
        )

        self._trained = True
        log.info("Training complete: final_loss=%.6f", history["final_loss"])
        return history

    def generate(self, n_samples: int = 100,
                 seq_len: int = 60) -> np.ndarray:
        """Generate synthetic market data via reverse diffusion.

        Starts from pure noise and iteratively denoises to produce
        realistic market return sequences.

        Args:
            n_samples: Number of sequences to generate.
            seq_len: Length of each sequence.

        Returns:
            Synthetic data, shape (n_samples, seq_len).
        """
        if not self._trained or self._denoiser is None:
            log.warning("Generator not trained — returning random noise")
            return self._rng.normal(0, 1, (n_samples, seq_len))

        dim = self._data_dim if self._data_dim > 0 else 1
        # Generate dim-at-a-time, then tile to seq_len
        # For simplicity, generate flat samples and reshape
        total = n_samples * seq_len
        batch_dim = min(dim, 1)

        # Start from pure noise
        x = self._rng.standard_normal((total, batch_dim))

        # Reverse diffusion
        for t in range(self._config.n_steps - 1, -1, -1):
            t_batch = np.full(total, float(t))
            predicted_noise = self._denoiser.forward(x, t_batch)
            x = self._scheduler.remove_noise_step(x, predicted_noise, t, self._rng)

        # Denormalize
        x = x * self._data_std + self._data_mean
        synthetic = x.reshape(n_samples, seq_len)

        log.info("Generated %d synthetic sequences of length %d",
                 n_samples, seq_len)
        return synthetic

    def validate_stylized_facts(self, real: np.ndarray,
                                synthetic: np.ndarray) -> Dict[str, Any]:
        """Validate that synthetic data exhibits market stylized facts.

        Tests for:
          1. Fat tails (excess kurtosis > 0)
          2. Volatility clustering (autocorrelation of |returns|)
          3. Leverage effect (negative correlation between returns and vol)

        Args:
            real: Real return data.
            synthetic: Synthetic return data.

        Returns:
            Validation report dict.
        """
        real = np.asarray(real).ravel()
        synthetic = np.asarray(synthetic).ravel()

        def _kurtosis(x: np.ndarray) -> float:
            std = np.std(x) + 1e-10
            return float(np.mean(((x - np.mean(x)) / std) ** 4) - 3.0)

        def _acf_abs(x: np.ndarray, lag: int = 10) -> float:
            abs_x = np.abs(x)
            n = len(abs_x)
            if n <= lag:
                return 0.0
            mean = np.mean(abs_x)
            var = np.var(abs_x)
            if var < 1e-12:
                return 0.0
            return float(np.mean((abs_x[:n - lag] - mean) * (abs_x[lag:] - mean)) / var)

        def _leverage_effect(x: np.ndarray, lag: int = 5) -> float:
            """Correlation between returns and future volatility."""
            if len(x) <= lag:
                return 0.0
            vol = np.abs(x)
            r = x[:len(x) - lag]
            v = vol[lag:]
            if np.std(r) < 1e-10 or np.std(v) < 1e-10:
                return 0.0
            return float(np.corrcoef(r, v)[0, 1])

        results: Dict[str, Any] = {}

        # Fat tails
        real_kurt = _kurtosis(real)
        synth_kurt = _kurtosis(synthetic)
        results["fat_tails"] = {
            "real_excess_kurtosis": round(real_kurt, 3),
            "synthetic_excess_kurtosis": round(synth_kurt, 3),
            "both_leptokurtic": real_kurt > 0 and synth_kurt > 0,
            "kurtosis_ratio": round(synth_kurt / max(real_kurt, 0.01), 2),
        }

        # Volatility clustering
        real_acf = _acf_abs(real)
        synth_acf = _acf_abs(synthetic)
        results["vol_clustering"] = {
            "real_acf_abs_lag10": round(real_acf, 4),
            "synthetic_acf_abs_lag10": round(synth_acf, 4),
            "both_positive": real_acf > 0 and synth_acf > 0,
        }

        # Leverage effect
        real_lev = _leverage_effect(real)
        synth_lev = _leverage_effect(synthetic)
        results["leverage_effect"] = {
            "real_correlation": round(real_lev, 4),
            "synthetic_correlation": round(synth_lev, 4),
            "both_negative": real_lev < 0 and synth_lev < 0,
        }

        # Moment matching
        results["moments"] = {
            "mean_error": round(abs(np.mean(real) - np.mean(synthetic)), 6),
            "std_ratio": round(np.std(synthetic) / max(np.std(real), 1e-10), 3),
            "skew_real": round(float(np.mean(((real - np.mean(real)) / (np.std(real) + 1e-10)) ** 3)), 3),
            "skew_synth": round(float(np.mean(((synthetic - np.mean(synthetic)) / (np.std(synthetic) + 1e-10)) ** 3)), 3),
        }

        # Overall score
        checks = [
            results["fat_tails"]["both_leptokurtic"],
            results["vol_clustering"]["both_positive"],
            0.5 < results["moments"]["std_ratio"] < 2.0,
            results["moments"]["mean_error"] < 0.01,
        ]
        n_pass = sum(checks)
        results["score"] = f"{n_pass}/{len(checks)}"
        results["verdict"] = "PASS" if n_pass >= len(checks) - 1 else "NEEDS_IMPROVEMENT"

        log.info("Stylized facts validation: %s (%s)",
                 results["score"], results["verdict"])
        return results

    def save(self, path: str = "/app/data/diffusion/model.npz") -> None:
        """Save model state."""
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            if self._denoiser is not None:
                np.savez(str(save_path),
                         W1=self._denoiser.W1, b1=self._denoiser.b1,
                         W2=self._denoiser.W2, b2=self._denoiser.b2,
                         W3=self._denoiser.W3, b3=self._denoiser.b3,
                         meta=np.array([self._data_mean, self._data_std,
                                        self._data_dim, self._config.n_steps]))
                log.info("Diffusion model saved to %s", path)
        except Exception as e:
            log.error("Failed to save diffusion model: %s", e)

    def load(self, path: str = "/app/data/diffusion/model.npz") -> None:
        """Load model state."""
        try:
            data = np.load(path, allow_pickle=False)
            meta = data["meta"]
            self._data_mean = float(meta[0])
            self._data_std = float(meta[1])
            self._data_dim = int(meta[2])

            self._denoiser = SimpleDenoisingNetwork(
                data_dim=self._data_dim,
                hidden_dim=self._config.hidden_dim,
                n_steps=int(meta[3]),
                seed=self._config.seed,
            )
            self._denoiser.W1 = data["W1"]
            self._denoiser.b1 = data["b1"]
            self._denoiser.W2 = data["W2"]
            self._denoiser.b2 = data["b2"]
            self._denoiser.W3 = data["W3"]
            self._denoiser.b3 = data["b3"]
            self._trained = True
            log.info("Diffusion model loaded from %s", path)
        except FileNotFoundError:
            log.info("No saved model at %s", path)
        except Exception as e:
            log.error("Failed to load diffusion model: %s", e)
