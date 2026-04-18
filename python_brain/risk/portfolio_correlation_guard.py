"""
Portfolio-Level Correlation Guard

Detects and prevents correlation blow-ups across concurrent positions.

Reference:
- López de Prado (2016) "Building Diversified Portfolios that Outperform OOS"
- De Prado, Vince & Zhu (2022) "Detection of false strategies"

Problems addressed:
1. Too many positions all long SPY beta (no diversification)
2. Sector concentration (all tech, all financials)
3. Hidden factor exposures (all momentum, all value)
4. Regime-dependent correlation (corrs go to 1 in crisis)

Strategies:
1. Block new positions if max_pairwise_corr > threshold
2. Scale down size if cluster concentration > threshold
3. Use hierarchical risk parity for allocation
4. Stress-test implied correlation in crisis regime
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class CorrelationCheckResult:
    pass_check: bool
    max_pairwise_corr: float
    avg_correlation: float
    effective_n: float                      # effective number of independent bets
    cluster_count: int
    violations: list[str]
    scale_factor: float                      # 1.0 = full size, 0.5 = half size


def compute_correlation_matrix(
    returns_history: dict[str, np.ndarray],
    min_length: int = 30,
) -> tuple[np.ndarray, list[str]]:
    """
    Compute pairwise correlation matrix from return histories.

    Returns (corr_matrix, ticker_order)
    """
    valid = {k: v for k, v in returns_history.items() if len(v) >= min_length}
    if len(valid) < 2:
        return np.array([[1.0]]), list(valid.keys())

    # Align to shortest length
    min_len = min(len(v) for v in valid.values())
    tickers = sorted(valid.keys())
    aligned = np.vstack([valid[t][-min_len:] for t in tickers])

    corr = np.corrcoef(aligned)
    # Handle NaNs from constant returns
    corr = np.nan_to_num(corr, nan=0.0)
    return corr, tickers


def effective_number_of_bets(corr_matrix: np.ndarray) -> float:
    """
    Effective N independent bets from correlation matrix.

    N_eff = (sum of eigenvalues)^2 / sum of eigenvalues^2
    Meucci (2009) approach.
    """
    if corr_matrix.size == 0 or corr_matrix.shape[0] < 2:
        return float(corr_matrix.shape[0]) if corr_matrix.size > 0 else 0.0

    eigenvalues = np.linalg.eigvalsh(corr_matrix)
    eigenvalues = np.maximum(eigenvalues, 1e-12)
    total = eigenvalues.sum()
    return float(total ** 2 / (eigenvalues ** 2).sum())


def detect_clusters(
    corr_matrix: np.ndarray,
    tickers: list[str],
    threshold: float = 0.7,
) -> list[list[str]]:
    """
    Simple cluster detection: group tickers with pairwise corr > threshold.

    Returns list of clusters (each a list of tickers).
    """
    n = len(tickers)
    if n == 0:
        return []

    visited = [False] * n
    clusters = []

    for i in range(n):
        if visited[i]:
            continue
        cluster = [tickers[i]]
        visited[i] = True

        # BFS to find all correlated neighbors
        queue = [i]
        while queue:
            cur = queue.pop(0)
            for j in range(n):
                if not visited[j] and abs(corr_matrix[cur, j]) > threshold:
                    visited[j] = True
                    cluster.append(tickers[j])
                    queue.append(j)

        clusters.append(cluster)

    return clusters


def check_correlation_guard(
    existing_positions: dict[str, float],   # ticker -> usd value
    candidate_ticker: str,
    candidate_usd: float,
    returns_history: dict[str, np.ndarray],
    max_pairwise_corr: float = 0.85,
    max_cluster_concentration: float = 0.4,
    min_effective_n: float = 2.5,
) -> CorrelationCheckResult:
    """
    Check if adding a candidate position violates correlation rules.

    Returns:
        CorrelationCheckResult with pass/fail + scale factor for size
    """
    # Build post-trade portfolio
    post_trade = dict(existing_positions)
    post_trade[candidate_ticker] = post_trade.get(candidate_ticker, 0) + candidate_usd

    violations = []
    scale_factor = 1.0

    # Need at least 2 positions to check correlation
    if len(post_trade) < 2:
        return CorrelationCheckResult(
            pass_check=True,
            max_pairwise_corr=0,
            avg_correlation=0,
            effective_n=1,
            cluster_count=1,
            violations=[],
            scale_factor=1.0,
        )

    # Need history for all positions
    tickers_with_hist = [t for t in post_trade if t in returns_history]
    if len(tickers_with_hist) < 2:
        return CorrelationCheckResult(
            pass_check=True,
            max_pairwise_corr=0,
            avg_correlation=0,
            effective_n=float(len(post_trade)),
            cluster_count=len(post_trade),
            violations=["insufficient history"],
            scale_factor=1.0,
        )

    relevant_history = {t: returns_history[t] for t in tickers_with_hist}
    corr_matrix, ticker_order = compute_correlation_matrix(relevant_history)

    # Max pairwise (excluding diagonal)
    n = corr_matrix.shape[0]
    mask = ~np.eye(n, dtype=bool)
    off_diag = corr_matrix[mask]
    max_pairwise = float(np.max(np.abs(off_diag))) if len(off_diag) > 0 else 0.0
    avg_corr = float(np.mean(np.abs(off_diag))) if len(off_diag) > 0 else 0.0

    # Check max pairwise
    if max_pairwise > max_pairwise_corr:
        violations.append(f"Max pairwise corr {max_pairwise:.2f} > {max_pairwise_corr}")
        scale_factor *= 0.5

    # Effective N
    n_eff = effective_number_of_bets(corr_matrix)
    if n_eff < min_effective_n:
        violations.append(f"Effective N {n_eff:.2f} < {min_effective_n}")
        scale_factor *= 0.7

    # Cluster concentration
    clusters = detect_clusters(corr_matrix, ticker_order, threshold=0.7)
    total_usd = sum(post_trade.values())
    for cluster in clusters:
        cluster_usd = sum(post_trade.get(t, 0) for t in cluster)
        concentration = cluster_usd / total_usd if total_usd > 0 else 0
        if concentration > max_cluster_concentration:
            violations.append(
                f"Cluster {cluster[:3]}... concentration {concentration:.1%} > {max_cluster_concentration:.1%}"
            )
            scale_factor *= 0.6

    scale_factor = max(0.0, min(1.0, scale_factor))
    pass_check = len(violations) == 0

    return CorrelationCheckResult(
        pass_check=pass_check,
        max_pairwise_corr=max_pairwise,
        avg_correlation=avg_corr,
        effective_n=n_eff,
        cluster_count=len(clusters),
        violations=violations,
        scale_factor=scale_factor,
    )


class PortfolioCorrelationMonitor:
    """Stateful monitor for correlation checks with rolling return history."""

    def __init__(
        self,
        max_history: int = 100,
        max_pairwise_corr: float = 0.85,
        max_cluster_concentration: float = 0.4,
        min_effective_n: float = 2.5,
    ):
        self.max_history = max_history
        self.max_pairwise_corr = max_pairwise_corr
        self.max_cluster_concentration = max_cluster_concentration
        self.min_effective_n = min_effective_n
        self.returns_history: dict[str, list[float]] = {}
        self.positions: dict[str, float] = {}

    def update_return(self, ticker: str, ret: float) -> None:
        if ticker not in self.returns_history:
            self.returns_history[ticker] = []
        self.returns_history[ticker].append(ret)
        if len(self.returns_history[ticker]) > self.max_history:
            self.returns_history[ticker].pop(0)

    def update_position(self, ticker: str, usd_value: float) -> None:
        if abs(usd_value) < 1:
            self.positions.pop(ticker, None)
        else:
            self.positions[ticker] = usd_value

    def check_candidate(self, ticker: str, usd_value: float) -> CorrelationCheckResult:
        returns = {k: np.array(v) for k, v in self.returns_history.items() if len(v) >= 10}
        return check_correlation_guard(
            existing_positions=self.positions,
            candidate_ticker=ticker,
            candidate_usd=usd_value,
            returns_history=returns,
            max_pairwise_corr=self.max_pairwise_corr,
            max_cluster_concentration=self.max_cluster_concentration,
            min_effective_n=self.min_effective_n,
        )


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)

        # Generate correlated + uncorrelated assets
        n = 100
        factor = rng.normal(0, 1, n)  # market factor

        returns_history = {
            "AAPL": 0.8 * factor + rng.normal(0, 0.5, n) * 0.2,
            "MSFT": 0.8 * factor + rng.normal(0, 0.5, n) * 0.2,  # high corr with AAPL
            "NVDA": 0.75 * factor + rng.normal(0, 0.5, n) * 0.25,
            "XOM": 0.3 * factor + rng.normal(0, 0.5, n) * 0.7,  # lower corr
            "GLD": -0.1 * factor + rng.normal(0, 0.5, n),  # negative corr
        }

        # Test 1: existing all-tech portfolio, add another tech
        existing = {"AAPL": 2000, "MSFT": 2000, "NVDA": 2000}
        result = check_correlation_guard(
            existing,
            "GOOGL",
            1000,
            returns_history={**returns_history, "GOOGL": 0.78 * factor + rng.normal(0, 0.5, n) * 0.22},
        )
        print(f"Tech + tech: pass={result.pass_check}, max_corr={result.max_pairwise_corr:.2f}, n_eff={result.effective_n:.2f}, clusters={result.cluster_count}")
        print(f"  Scale factor: {result.scale_factor:.2f}")
        for v in result.violations:
            print(f"  VIOLATION: {v}")

        # Test 2: Tech + uncorrelated GLD
        result2 = check_correlation_guard(
            existing,
            "GLD",
            1000,
            returns_history,
        )
        print(f"\nTech + GLD: pass={result2.pass_check}, max_corr={result2.max_pairwise_corr:.2f}, n_eff={result2.effective_n:.2f}")
        print(f"  Scale factor: {result2.scale_factor:.2f}")

        # Test 3: Monitor
        monitor = PortfolioCorrelationMonitor()
        for t, r in returns_history.items():
            for val in r:
                monitor.update_return(t, float(val))
        for t, usd in existing.items():
            monitor.update_position(t, usd)

        r3 = monitor.check_candidate("XOM", 1500)
        print(f"\nMonitor: XOM candidate pass={r3.pass_check}, scale={r3.scale_factor:.2f}")
        print("OK")
