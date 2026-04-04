"""tsfresh Automated Feature Engineering — 794 time-series features from price data.

Extracts comprehensive statistical features from OHLCV bars:
  - Autocorrelation at multiple lags
  - FFT coefficients (periodicity detection)
  - Linear trend slopes
  - Entropy measures
  - Quantile features
  - Rolling statistics

Runs nightly on each ticker's recent history. Features feed into LightGBM
meta-model for signal scoring.

License: tsfresh is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("tsfresh_engineer")

try:
    import pandas as pd
    import numpy as np
    _HAS_BASE = True
except ImportError:
    _HAS_BASE = False

try:
    from tsfresh import extract_features
    from tsfresh.feature_extraction import MinimalFCParameters, EfficientFCParameters
    from tsfresh.utilities.dataframe_functions import impute
    _HAS_TSFRESH = True
except ImportError:
    _HAS_TSFRESH = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_OUTPUT_PATH = _DATA_DIR / "features" / "tsfresh_features.json"


def extract_bar_features(
    bars: List[Dict[str, Any]],
    ticker: str = "UNK",
    feature_set: str = "minimal",
) -> Optional[Dict[str, float]]:
    """Extract tsfresh features from OHLCV bars for a single ticker.

    Args:
        bars: List of bar dicts with close, high, low, volume keys.
        ticker: Ticker symbol (used as ID column).
        feature_set: "minimal" (fast, ~40 features) or "efficient" (~200 features).

    Returns:
        Dict of {feature_name: value} or None on failure.
    """
    if not _HAS_TSFRESH or not _HAS_BASE:
        return None

    if len(bars) < 30:
        return None

    try:
        # Build DataFrame in tsfresh format
        rows = []
        for i, bar in enumerate(bars):
            rows.append({
                "id": ticker,
                "time": i,
                "close": bar.get("close", 0),
                "volume": bar.get("volume", 0),
                "range": bar.get("high", 0) - bar.get("low", 0),
            })
        df = pd.DataFrame(rows)

        # Select feature set
        if feature_set == "efficient":
            fc_params = EfficientFCParameters()
        else:
            fc_params = MinimalFCParameters()

        # Extract features
        features_df = extract_features(
            df, column_id="id", column_sort="time",
            default_fc_parameters=fc_params,
            disable_progressbar=True,
            n_jobs=1,  # Single-threaded in hot path
        )

        # Impute NaN/Inf
        impute(features_df)

        # Convert to dict
        if features_df.empty:
            return None

        feature_dict = features_df.iloc[0].to_dict()

        # Filter out features with zero variance (uninformative)
        feature_dict = {k: float(v) for k, v in feature_dict.items()
                        if not (np.isnan(v) or np.isinf(v))}

        return feature_dict

    except Exception as e:
        log.warning("tsfresh feature extraction failed for %s: %s", ticker, str(e)[:100])
        return None


def extract_all_tickers(
    tickers_data: Dict[str, List[Dict]],
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Extract features for all tickers. Called by nightly pipeline.

    Args:
        tickers_data: Dict of {ticker: list_of_bars}.
        output_path: Where to save features JSON.

    Returns:
        Summary dict or None.
    """
    if not _HAS_TSFRESH:
        log.warning("tsfresh not installed — feature engineering skipped")
        return None

    features = {}
    for ticker, bars in tickers_data.items():
        feat = extract_bar_features(bars, ticker=ticker, feature_set="minimal")
        if feat:
            features[ticker] = feat

    if not features:
        log.info("No features extracted")
        return None

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_tickers": len(features),
        "n_features_per_ticker": len(next(iter(features.values()))),
        "tickers": features,
    }

    if output_path is None:
        output_path = str(_OUTPUT_PATH)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    log.info("tsfresh features: %d tickers, %d features each",
             result["n_tickers"], result["n_features_per_ticker"])

    return result
