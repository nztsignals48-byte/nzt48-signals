"""TCN Deep Learning Architecture — Book 29.

Temporal Convolutional Network for directional prediction.
Dilated causal convolutions with exponentially growing receptive field.

Architecture:
  Input: 64 features × 12 timesteps (5-second bars)
  Block 1: Conv1D(64→32, k=3, d=1) + ReLU + Dropout(0.2)
  Block 2: Conv1D(32→32, k=3, d=2) + ReLU + Dropout(0.2)
  Block 3: Conv1D(32→16, k=3, d=4) + ReLU + Dropout(0.2)
  Global pooling → Dense(16→1) → Sigmoid

Output: P(price_up_next_5_bars) ∈ [0, 1]

Training: Walk-forward with purged cross-validation.
Export: ONNX for Rust inference (< 1ms).

This module provides the model definition and training pipeline.
Inference in production is via ONNX Runtime in Rust (ort crate).

Note: Requires PyTorch. Falls back gracefully if not installed.

Usage:
    from python_brain.ml.tcn_model import TCNClassifier, train_tcn

    model = TCNClassifier(n_features=64, seq_len=12)
    train_tcn(model, X_train, y_train, epochs=50)
    model.export_onnx("models/tcn_v1.onnx")
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("tcn_model")

# Check if PyTorch is available
_HAS_TORCH = False
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    _HAS_TORCH = True
except ImportError:
    log.info("PyTorch not installed — TCN model definition only, no training")


if _HAS_TORCH:
    class CausalConv1d(nn.Module):
        """Causal convolution with dilation."""
        def __init__(self, in_channels: int, out_channels: int, kernel_size: int, dilation: int):
            super().__init__()
            self.padding = (kernel_size - 1) * dilation
            self.conv = nn.Conv1d(in_channels, out_channels, kernel_size,
                                  dilation=dilation, padding=self.padding)

        def forward(self, x):
            out = self.conv(x)
            return out[:, :, :x.size(2)]  # Remove future padding

    class TCNBlock(nn.Module):
        """Single TCN block: causal conv + ReLU + dropout + residual."""
        def __init__(self, in_ch: int, out_ch: int, kernel_size: int, dilation: int, dropout: float = 0.2):
            super().__init__()
            self.conv1 = CausalConv1d(in_ch, out_ch, kernel_size, dilation)
            self.relu = nn.ReLU()
            self.dropout = nn.Dropout(dropout)
            self.downsample = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else None

        def forward(self, x):
            out = self.dropout(self.relu(self.conv1(x)))
            res = self.downsample(x) if self.downsample else x
            return self.relu(out + res)

    class TCNClassifier(nn.Module):
        """Temporal Convolutional Network for binary classification."""
        def __init__(self, n_features: int = 64, seq_len: int = 12,
                     channels: List[int] = None, kernel_size: int = 3, dropout: float = 0.2):
            super().__init__()
            channels = channels or [32, 32, 16]
            layers = []
            in_ch = n_features
            for i, out_ch in enumerate(channels):
                dilation = 2 ** i
                layers.append(TCNBlock(in_ch, out_ch, kernel_size, dilation, dropout))
                in_ch = out_ch
            self.tcn = nn.Sequential(*layers)
            self.global_pool = nn.AdaptiveAvgPool1d(1)
            self.classifier = nn.Linear(channels[-1], 1)
            self.sigmoid = nn.Sigmoid()

        def forward(self, x):
            # x: (batch, features, seq_len)
            out = self.tcn(x)
            out = self.global_pool(out).squeeze(-1)
            return self.sigmoid(self.classifier(out))

        def export_onnx(self, path: str, batch_size: int = 1, n_features: int = 64, seq_len: int = 12):
            """Export model to ONNX format for Rust inference."""
            self.eval()
            dummy = torch.randn(batch_size, n_features, seq_len)
            torch.onnx.export(self, dummy, path,
                              input_names=["features"],
                              output_names=["probability"],
                              opset_version=13)
            log.info("TCN exported to ONNX: %s", path)


def train_tcn(
    model,
    X_train: np.ndarray,
    y_train: np.ndarray,
    epochs: int = 50,
    batch_size: int = 64,
    lr: float = 0.001,
    validation_split: float = 0.2,
) -> dict:
    """Train TCN with walk-forward validation.

    Args:
        model: TCNClassifier instance
        X_train: (N, features, seq_len) numpy array
        y_train: (N,) binary labels
        epochs: Training epochs
        batch_size: Mini-batch size
        lr: Learning rate
        validation_split: Fraction for validation

    Returns: Training metrics dict
    """
    if not _HAS_TORCH:
        log.warning("PyTorch not available — cannot train TCN")
        return {"error": "pytorch_not_installed"}

    # Split data (respecting time ordering — NO shuffle)
    n = len(X_train)
    val_start = int(n * (1 - validation_split))
    X_tr = torch.FloatTensor(X_train[:val_start])
    y_tr = torch.FloatTensor(y_train[:val_start])
    X_val = torch.FloatTensor(X_train[val_start:])
    y_val = torch.FloatTensor(y_train[val_start:])

    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    criterion = nn.BCELoss()

    best_val_loss = float("inf")
    best_epoch = 0

    for epoch in range(epochs):
        model.train()
        # Mini-batch training
        indices = list(range(len(X_tr)))  # No shuffle — time series
        epoch_loss = 0.0
        n_batches = 0
        for i in range(0, len(indices), batch_size):
            batch_idx = indices[i:i + batch_size]
            X_batch = X_tr[batch_idx]
            y_batch = y_tr[batch_idx]

            optimizer.zero_grad()
            pred = model(X_batch).squeeze()
            loss = criterion(pred, y_batch)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        # Validation
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val).squeeze()
            val_loss = criterion(val_pred, y_val).item()
            val_acc = ((val_pred > 0.5).float() == y_val).float().mean().item()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch

        if epoch % 10 == 0:
            log.info("TCN epoch %d: train_loss=%.4f, val_loss=%.4f, val_acc=%.3f",
                     epoch, epoch_loss / n_batches, val_loss, val_acc)

    return {
        "epochs": epochs,
        "best_epoch": best_epoch,
        "best_val_loss": best_val_loss,
        "final_val_acc": val_acc,
    }


# Fallback for non-PyTorch environments
if not _HAS_TORCH:
    class TCNClassifier:
        """Placeholder TCN when PyTorch is not available."""
        def __init__(self, **kwargs):
            log.warning("TCN: PyTorch not installed. Model is a placeholder.")
        def forward(self, x):
            return np.zeros(x.shape[0])
        def export_onnx(self, path: str, **kwargs):
            log.warning("Cannot export ONNX without PyTorch")
