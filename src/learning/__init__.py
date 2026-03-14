# Learning/Adaptation (Phases 22-25)
# Nightly processes, universe scanning, threshold tuning, edge durability

class UniverseScanner:
    """Phase 23: Nightly Universe Scan"""
    def __init__(self, assets_to_scan=1770):
        self.assets_to_scan = assets_to_scan
        self.watchlist = {"HIGH_CONVICTION": [], "STANDARD": [], "WATCHLIST": []}

    def scan_and_rank(self, asset_scores):
        """Scan universe and rank candidates"""
        sorted_assets = sorted(asset_scores.items(), key=lambda x: x[1], reverse=True)
        self.watchlist["HIGH_CONVICTION"] = [a[0] for a in sorted_assets[:50]]
        self.watchlist["STANDARD"] = [a[0] for a in sorted_assets[50:200]]
        self.watchlist["WATCHLIST"] = [a[0] for a in sorted_assets[200:500]]
        return self.watchlist

class ThresholdRecalibrator:
    """Phase 24: Nightly Threshold Tuning"""
    def __init__(self):
        self.thresholds = {}

    def recalibrate(self, regime, recent_win_rate, recent_sharpe):
        """Adjust thresholds based on recent performance"""
        # If recent Sharpe dropping, raise threshold (be more selective)
        if recent_sharpe < 0.5:
            adjustment = 0.5  # Raise threshold by 0.5
        elif recent_sharpe > 1.0:
            adjustment = -0.3  # Lower threshold slightly (edge strong)
        else:
            adjustment = 0

        self.thresholds[regime] = {
            "confidence_threshold": 6.5 + adjustment,
            "stop_loss_pct": 1.0 * (1 + adjustment * 0.1),
            "position_multiplier": 1.0 / (1 + adjustment * 0.1)
        }
        return self.thresholds[regime]

class EdgeDurabilityReview:
    """Phase 25: Edge Durability Review"""
    def __init__(self):
        self.edge_history = []
        self.disabled_signals = {}

    def review_edge(self, signal_name, recent_dsr, recent_sharpe):
        """Check if edge is decaying or lucky"""
        status = {
            "signal": signal_name,
            "dsr": recent_dsr,
            "sharpe": recent_sharpe,
            "is_decaying": recent_sharpe < 0.4,
            "is_disabled": recent_dsr < 0.5
        }
        self.edge_history.append(status)

        if recent_dsr < 0.5:
            self.disabled_signals[signal_name] = "DISABLED (DSR < 0.5)"

        return status

if __name__ == "__main__":
    scanner = UniverseScanner()
    recalibrator = ThresholdRecalibrator()
    edge_review = EdgeDurabilityReview()

    # Test
    asset_scores = {f"ASSET_{i}": 100 - i for i in range(100)}
    watchlist = scanner.scan_and_rank(asset_scores)
    print(f"✓ Universe scanned: {len(watchlist['HIGH_CONVICTION'])} high conviction candidates")

    thresholds = recalibrator.recalibrate("TRENDING_UP", 0.45, 0.8)
    print(f"✓ Thresholds recalibrated: {thresholds}")

    edge_status = edge_review.review_edge("8INDICATOR", 0.9, 0.7)
    print(f"✓ Edge review: {edge_status}")

    print("\n✅ Phases 22-25 (Learning/Adaptation) core modules ready")
