"""
Real-time VaR/CVaR Monitor

Computes portfolio-level Value-at-Risk and Conditional VaR in real time
using historical + parametric + Monte Carlo methods.

Reference:
- Jorion (2007) "Value at Risk" 3rd ed.
- Rockafellar & Uryasev (2000) "Optimization of Conditional VaR"
- Basel III FRTB expected shortfall

Methods:
1. Historical VaR — empirical quantile of portfolio returns
2. Parametric VaR — Gaussian approximation (fast)
3. Monte Carlo VaR — simulated returns (most accurate)
4. CVaR (Expected Shortfall) — mean loss in worst alpha% cases

Used for:
- Portfolio-level stop-loss (kill switch if VaR breached)
- Position sizing constraint (total VaR < cap)
- Tail-risk scaling (reduce sizing when CVaR spikes)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class RiskMetrics:
    var_95: float                  # 95% VaR (one-day, in portfolio currency)
    var_99: float                  # 99% VaR
    cvar_95: float                  # 95% CVaR / Expected Shortfall
    cvar_99: float                  # 99% CVaR
    volatility: float               # annualized portfolio vol
    max_drawdown: float             # largest peak-to-trough in window
    sharpe: float                   # realized Sharpe in window
    method: str                     # "historical" / "parametric" / "monte_carlo"


def historical_var_cvar(
    returns: np.ndarray,            # 1d array of period returns (as fractions)
    portfolio_value: float,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """
    Historical simulation VaR + CVaR.

    Returns:
        (VaR, CVaR) in same units as portfolio_value
    """
    if len(returns) == 0:
        return 0.0, 0.0

    alpha = 1 - confidence
    # VaR = -quantile of losses
    var_quantile = np.quantile(returns, alpha)
    var = -var_quantile * portfolio_value

    # CVaR = -mean of returns worse than VaR quantile
    tail = returns[returns <= var_quantile]
    cvar = -tail.mean() * portfolio_value if len(tail) > 0 else var

    return max(var, 0.0), max(cvar, 0.0)


def parametric_var_cvar(
    mu: float,                      # mean return
    sigma: float,                   # std dev
    portfolio_value: float,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """
    Gaussian parametric VaR + CVaR.

    Fast approximation assuming returns ~ N(mu, sigma).
    """
    alpha = 1 - confidence

    # Inverse normal CDF at alpha
    z = _inverse_normal_cdf(alpha)

    var = -(mu + z * sigma) * portfolio_value
    var = max(var, 0.0)

    # CVaR for Gaussian: sigma * phi(z) / alpha - mu
    pdf_z = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    cvar = -(mu - sigma * pdf_z / alpha) * portfolio_value
    cvar = max(cvar, 0.0)

    return var, cvar


def _inverse_normal_cdf(p: float) -> float:
    """Inverse standard normal CDF (approximation)."""
    # Beasley-Springer-Moro approximation
    if p <= 0 or p >= 1:
        return 0.0

    if p < 0.5:
        sign = -1
        p = p
    else:
        sign = 1
        p = 1 - p

    # Rational approximation
    t = math.sqrt(-2 * math.log(p))
    c = [2.515517, 0.802853, 0.010328]
    d = [1.432788, 0.189269, 0.001308]
    numerator = c[0] + c[1] * t + c[2] * t * t
    denominator = 1 + d[0] * t + d[1] * t * t + d[2] * t * t * t
    return sign * (t - numerator / denominator)


def monte_carlo_var_cvar(
    mu: float,
    sigma: float,
    portfolio_value: float,
    confidence: float = 0.95,
    n_sims: int = 10000,
    fat_tail_df: float | None = None,   # if set, use Student-t with this df
    seed: int | None = None,
) -> tuple[float, float]:
    """
    Monte Carlo VaR + CVaR with optional fat-tail distribution.

    fat_tail_df=None -> Gaussian
    fat_tail_df=5    -> Student-t with 5 dof (heavy tails)
    """
    rng = np.random.default_rng(seed)

    if fat_tail_df is None:
        sim_returns = rng.normal(mu, sigma, n_sims)
    else:
        # Student-t with df, rescaled to mu/sigma
        t_sample = rng.standard_t(fat_tail_df, n_sims)
        scale = sigma / math.sqrt(fat_tail_df / (fat_tail_df - 2)) if fat_tail_df > 2 else sigma
        sim_returns = mu + scale * t_sample

    return historical_var_cvar(sim_returns, portfolio_value, confidence)


def compute_portfolio_risk(
    positions_value: dict[str, float],     # ticker -> USD value
    returns_history: dict[str, np.ndarray], # ticker -> historical returns
    portfolio_value: float,
    method: str = "historical",
    confidence: float = 0.95,
) -> RiskMetrics:
    """
    Portfolio-level VaR/CVaR computation.

    positions_value: current USD value of each position
    returns_history: historical returns for each ticker
    """
    if not positions_value or portfolio_value <= 0:
        return RiskMetrics(0, 0, 0, 0, 0, 0, 0, method)

    # Align returns (use shortest common window)
    common_tickers = [t for t in positions_value if t in returns_history]
    if not common_tickers:
        return RiskMetrics(0, 0, 0, 0, 0, 0, 0, method)

    min_len = min(len(returns_history[t]) for t in common_tickers)
    if min_len < 10:
        return RiskMetrics(0, 0, 0, 0, 0, 0, 0, method)

    # Build aligned returns matrix
    returns_matrix = np.vstack([
        returns_history[t][-min_len:] for t in common_tickers
    ])  # (n_tickers, min_len)

    # Weights = position value / portfolio value
    weights = np.array([positions_value[t] / portfolio_value for t in common_tickers])

    # Portfolio returns = weighted sum of asset returns
    portfolio_returns = weights @ returns_matrix  # (min_len,)

    # Summary stats
    mu = float(portfolio_returns.mean())
    sigma = float(portfolio_returns.std())
    vol_annualized = sigma * math.sqrt(252)  # daily returns assumed

    # Max drawdown
    cum_returns = np.cumprod(1 + portfolio_returns)
    running_max = np.maximum.accumulate(cum_returns)
    drawdowns = (running_max - cum_returns) / np.maximum(running_max, 1e-9)
    max_dd = float(drawdowns.max())

    # Sharpe
    sharpe = mu / sigma * math.sqrt(252) if sigma > 0 else 0.0

    # VaR + CVaR for 95% and 99%
    if method == "historical":
        var_95, cvar_95 = historical_var_cvar(portfolio_returns, portfolio_value, 0.95)
        var_99, cvar_99 = historical_var_cvar(portfolio_returns, portfolio_value, 0.99)
    elif method == "parametric":
        var_95, cvar_95 = parametric_var_cvar(mu, sigma, portfolio_value, 0.95)
        var_99, cvar_99 = parametric_var_cvar(mu, sigma, portfolio_value, 0.99)
    elif method == "monte_carlo":
        var_95, cvar_95 = monte_carlo_var_cvar(mu, sigma, portfolio_value, 0.95, fat_tail_df=5)
        var_99, cvar_99 = monte_carlo_var_cvar(mu, sigma, portfolio_value, 0.99, fat_tail_df=5)
    else:
        var_95 = cvar_95 = var_99 = cvar_99 = 0.0

    return RiskMetrics(
        var_95=var_95,
        var_99=var_99,
        cvar_95=cvar_95,
        cvar_99=cvar_99,
        volatility=vol_annualized,
        max_drawdown=max_dd,
        sharpe=sharpe,
        method=method,
    )


class RealtimeRiskMonitor:
    """Incremental risk monitor that updates on every fill."""

    def __init__(
        self,
        portfolio_cap_var_usd: float = 1000.0,
        cvar_cap_usd: float = 2000.0,
        max_drawdown_halt: float = 0.08,
    ):
        self.portfolio_cap_var_usd = portfolio_cap_var_usd
        self.cvar_cap_usd = cvar_cap_usd
        self.max_drawdown_halt = max_drawdown_halt
        self.returns_history: dict[str, list[float]] = {}
        self.positions_value: dict[str, float] = {}
        self.last_metrics: RiskMetrics | None = None

    def update_return(self, ticker: str, daily_return: float) -> None:
        if ticker not in self.returns_history:
            self.returns_history[ticker] = []
        self.returns_history[ticker].append(daily_return)
        # Keep last 252 observations (1 year daily)
        if len(self.returns_history[ticker]) > 252:
            self.returns_history[ticker].pop(0)

    def update_position(self, ticker: str, usd_value: float) -> None:
        if usd_value == 0:
            self.positions_value.pop(ticker, None)
        else:
            self.positions_value[ticker] = usd_value

    def compute(self, portfolio_value: float, method: str = "historical") -> RiskMetrics:
        returns_arrays = {k: np.array(v) for k, v in self.returns_history.items() if len(v) >= 10}
        self.last_metrics = compute_portfolio_risk(
            self.positions_value,
            returns_arrays,
            portfolio_value,
            method=method,
        )
        return self.last_metrics

    def breach_check(self) -> list[str]:
        """Return list of breached risk limits."""
        breaches = []
        if self.last_metrics is None:
            return breaches

        m = self.last_metrics
        if m.var_95 > self.portfolio_cap_var_usd:
            breaches.append(f"VaR95 breach: {m.var_95:.2f} > {self.portfolio_cap_var_usd:.2f}")
        if m.cvar_95 > self.cvar_cap_usd:
            breaches.append(f"CVaR95 breach: {m.cvar_95:.2f} > {self.cvar_cap_usd:.2f}")
        if m.max_drawdown > self.max_drawdown_halt:
            breaches.append(f"Drawdown breach: {m.max_drawdown:.2%} > {self.max_drawdown_halt:.2%}")
        return breaches


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)

        # Simulate 100 days of returns for 3 tickers
        positions = {"AAPL": 5000, "MSFT": 3000, "NVDA": 2000}
        returns = {
            "AAPL": rng.normal(0.001, 0.015, 100),
            "MSFT": rng.normal(0.001, 0.012, 100),
            "NVDA": rng.normal(0.001, 0.025, 100),
        }

        metrics = compute_portfolio_risk(
            positions_value=positions,
            returns_history=returns,
            portfolio_value=10000,
            method="historical",
        )
        print(f"VaR95: ${metrics.var_95:.2f}")
        print(f"VaR99: ${metrics.var_99:.2f}")
        print(f"CVaR95: ${metrics.cvar_95:.2f}")
        print(f"CVaR99: ${metrics.cvar_99:.2f}")
        print(f"Volatility: {metrics.volatility:.2%}")
        print(f"Max DD: {metrics.max_drawdown:.2%}")
        print(f"Sharpe: {metrics.sharpe:.2f}")

        # Parametric
        metrics_p = compute_portfolio_risk(
            positions, returns, 10000, method="parametric",
        )
        print(f"Parametric VaR95: ${metrics_p.var_95:.2f}")

        # Monte Carlo with fat tails
        metrics_mc = compute_portfolio_risk(
            positions, returns, 10000, method="monte_carlo",
        )
        print(f"MC VaR95: ${metrics_mc.var_95:.2f}")

        # Monitor
        monitor = RealtimeRiskMonitor(portfolio_cap_var_usd=200, cvar_cap_usd=400)
        for t, r_arr in returns.items():
            for r in r_arr:
                monitor.update_return(t, float(r))
            monitor.update_position(t, positions[t])
        m = monitor.compute(10000)
        print(f"\nMonitor VaR95: ${m.var_95:.2f}")
        breaches = monitor.breach_check()
        print(f"Breaches: {breaches}")
        print("OK")
