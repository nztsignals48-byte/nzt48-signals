"""Book 23 Training Script — Train LightGBM entry classifier on historical data.

This script:
1. Loads 2+ years of minute-level OHLCV data
2. Engineers all 48 features
3. Labels targets based on 500-bar forward window
4. Trains LightGBM with time-series cross-validation
5. Validates on held-out 6-month test set
6. Exports model to ONNX for Rust inference
7. Generates validation report

Usage:
    python train_entry_classifier.py --data /path/to/ohlcv.csv --output /app/data/entry_classifier/model.txt
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Import our classifier modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from ml.entry_classifier_book23 import (
    FeatureEngineer,
    TargetLabeler,
    EntryClassifierTrainer,
    ONNXExporter,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def load_ohlcv_data(csv_path: str) -> pd.DataFrame:
    """Load OHLCV data from CSV."""
    logger.info(f"Loading OHLCV data from {csv_path}")
    df = pd.read_csv(csv_path)

    # Validate columns
    required_cols = {'timestamp', 'open', 'high', 'low', 'close', 'volume'}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"CSV must have columns: {required_cols}")

    # Parse timestamps
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)

    logger.info(f"Loaded {len(df)} bars")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    return df


def main():
    parser = argparse.ArgumentParser(description='Train entry classifier')
    parser.add_argument('--data', required=True, help='Path to OHLCV CSV file')
    parser.add_argument('--output', default='/app/data/entry_classifier/model.txt',
                       help='Output model path')
    parser.add_argument('--entry-target-pips', type=float, default=15.0,
                       help='Win threshold in pips')
    parser.add_argument('--test-size', type=float, default=0.2,
                       help='Fraction of data for holdout test')
    parser.add_argument('--export-onnx', action='store_true',
                       help='Export model to ONNX format')

    args = parser.parse_args()

    # Load data
    df = load_ohlcv_data(args.data)

    # Engineer features
    logger.info("Engineering 48 features...")
    feature_engineer = FeatureEngineer(df)
    X = feature_engineer.compute_all_features()
    logger.info(f"Feature matrix shape: {X.shape}")

    # Label targets
    logger.info("Labeling targets (500-bar forward window)...")
    labeler = TargetLabeler(df, entry_target_pips=args.entry_target_pips)
    y = labeler.label_targets(lookahead_bars=500)
    logger.info(f"Target distribution: {np.bincount(y)}")

    # Train classifier
    logger.info("Training LightGBM classifier...")
    trainer = EntryClassifierTrainer()
    metrics = trainer.train(X, y, test_size=args.test_size)

    # Save model
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    trainer.save(args.output)

    # Feature importance
    logger.info("Top 10 features:")
    importance = trainer.get_feature_importance(top_n=10)
    for _, row in importance.iterrows():
        logger.info(f"  {row['feature']}: {row['importance']:.4f}")

    # Validation report
    report = {
        'timestamp': datetime.now().isoformat(),
        'data': {
            'csv_path': args.data,
            'n_bars': len(df),
            'date_range': {
                'start': df['timestamp'].min().isoformat(),
                'end': df['timestamp'].max().isoformat(),
            },
        },
        'features': {
            'n_features': X.shape[1],
            'feature_names': X.columns.tolist(),
        },
        'target': {
            'n_samples': len(y),
            'n_positive': int(np.sum(y)),
            'n_negative': int(len(y) - np.sum(y)),
            'positive_rate': float(np.mean(y)),
        },
        'model': {
            'type': 'LightGBM',
            'max_depth': 7,
            'min_samples_leaf': 50,
            'learning_rate': 0.05,
        },
        'metrics': metrics,
    }

    # Save report
    report_path = args.output.replace('.txt', '_report.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved to {report_path}")

    # Export to ONNX if requested
    if args.export_onnx:
        logger.info("Exporting model to ONNX...")
        onnx_path = args.output.replace('.txt', '.onnx')
        ONNXExporter.export_to_onnx(trainer.model, trainer.feature_names, onnx_path)

    logger.info("Training complete!")

    return 0


if __name__ == '__main__':
    sys.exit(main())
