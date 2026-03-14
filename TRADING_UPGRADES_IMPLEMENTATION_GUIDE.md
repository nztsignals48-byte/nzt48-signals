# Trading System Upgrades: Implementation Guide

**Target**: AEGIS V2 Integration Planning
**Document**: Concrete Implementation Patterns, Effort Estimates, Risk Assessments
**Date**: 2026-03-10

---

## CATEGORY 1: VOLATILITY MODELING (EGARCH)

### Implementation Pattern: EGARCH(1,1)

**Current Code Location**: `cross_asset_macro.py` (VIX forecast module)

**Standard GARCH(1,1)**:
```
σ_t² = ω + α·ε_{t-1}² + β·σ_{t-1}²
```

**EGARCH(1,1) (Nelson 1991)**:
```
log(σ_t²) = ω + α·(ε_{t-1}/σ_{t-1}) + γ·(|ε_{t-1}|/σ_{t-1} - √(2/π)) + β·log(σ_{t-1}²)
```

**Key Difference**: Asymmetric response to shocks
- Positive shock (+ε): multiplies α (limited impact)
- Negative shock (-ε): multiplies (α + γ) (larger impact)

**Implementation in Python** (statsmodels):

```python
from arch import arch_model
import pandas as pd

# Load daily returns (LSE ETPs)
returns = pd.read_csv('etp_returns.csv', index_col=0)  # shape: (750, 12)

# Fit EGARCH(1,1)
model = arch_model(returns['QQQ3.L'], vol='EGARCH', p=1, o=1, q=1)
fitted_model = model.fit(disp='off')

# Extract conditional volatility
conditional_vol = fitted_model.conditional_volatility
# Output: time series of σ_t (daily vol forecast for next day)

# Use in position sizing
heat_scalar = conditional_vol / conditional_vol.median()
# If σ_t is 2x median → reduce heat by 50%
# If σ_t is 0.5x median → increase heat by 2x
```

**Integration into AEGIS**:

```python
# In cross_asset_macro.py (VIX forecast module)

class EGARCHVolatilityScaler:
    def __init__(self, lookback=252):
        self.lookback = lookback
        self.models = {}  # One model per asset
        self.last_fit = None

    def fit_weekly(self, returns_dict):
        """Refit EGARCH for each asset weekly."""
        for asset_id, returns in returns_dict.items():
            model = arch_model(returns[-self.lookback:], vol='EGARCH', p=1, o=1, q=1)
            self.models[asset_id] = model.fit(disp='off')

    def get_conditional_vol(self, asset_id):
        """Return forecast volatility for position sizing."""
        if asset_id not in self.models:
            return None
        fitted = self.models[asset_id]
        return fitted.conditional_volatility.iloc[-1]  # Latest σ_t

    def heat_adjustment(self, asset_id, base_heat=0.3):
        """Adjust position heat based on EGARCH forecast."""
        vol_t = self.get_conditional_vol(asset_id)
        if vol_t is None:
            return base_heat

        vol_median = self.models[asset_id].conditional_volatility.median()
        vol_ratio = vol_t / vol_median

        # Cap adjustments to [0.5x, 2.0x]
        adjusted_heat = base_heat / np.clip(vol_ratio, 0.5, 2.0)
        return min(adjusted_heat, 0.5)  # Heat cap
```

**Effort Estimate**:
- Implementation: 15-20h (data pipeline, refit schedule, integration tests)
- Backtesting: 10-15h (parameter tuning, walk-forward validation)
- **Total: 25-35h**

**Expected Sharpe Improvement**: +12-18% (if volatility prediction accurate)

**Risk**: If EGARCH parameters unstable (market regime shift), can worsen performance. Mitigate with:
- Weekly refits (not daily)
- Parameter bounds (e.g., α ∈ [0.05, 0.3])
- Fallback to vanilla GARCH if likelihood collapses

---

## CATEGORY 2: EXECUTION (VWAP via Almgren-Chriss)

### VWAP Execution Algorithm

**Goal**: Execute large position over 10-minute window to minimize slippage

**Current AEGIS**: Market orders (implicit IB routing, ~3 bps slippage)

**Proposed: VWAP with Almgren-Chriss optimization**

```python
import numpy as np
from scipy.optimize import minimize

class VWAPExecutor:
    def __init__(self, ticker, target_shares, window_minutes=10, execution_points=10):
        self.ticker = ticker
        self.target_shares = target_shares
        self.window_minutes = window_minutes
        self.execution_points = execution_points
        self.execution_schedule = None

    def compute_vwap_weights(self, volume_profile):
        """
        Compute VWAP weights from expected volume profile.
        volume_profile: list of expected volumes at each time point
        """
        total_volume = sum(volume_profile)
        vwap_weights = [v / total_volume for v in volume_profile]
        return vwap_weights

    def almgren_chriss_arrival_price(self, execution_weights,
                                     current_price,
                                     volatility,
                                     liquidation_time,
                                     market_impact_lambda=0.1):
        """
        Compute expected arrival price under Almgren-Chriss model.

        Model:
        - Temporary impact: S_t increases as we buy
        - Permanent impact: Market learns we're a buyer, price drifts up
        - Timing risk: Volatility during execution

        minimize: cost = permanent_impact + 0.5*temporary_impact + timing_risk
        """
        n_trades = len(execution_weights)
        execution_fractions = [w * self.target_shares for w in execution_weights]

        # Permanent impact (market learns over time)
        permanent_cost = 0
        for i, frac in enumerate(execution_fractions):
            market_impact = market_impact_lambda * (frac / 1000000)  # Scale factor
            permanent_cost += market_impact * current_price

        # Temporary impact (bid-ask, urgency)
        temporary_cost = 0
        for frac in execution_fractions:
            # Wider spreads for larger orders
            spread = 0.001 + 0.0005 * (frac / self.target_shares)
            temporary_cost += spread * frac * current_price

        # Timing risk (variance of execution price)
        timing_cost = 0.5 * volatility * current_price * np.sqrt(liquidation_time / n_trades)

        return permanent_cost + 0.5 * temporary_cost + timing_cost

    def execute_vwap(self, order_type='VWAP', timeout_seconds=600):
        """
        Issue VWAP order via IB Gateway.
        IB handles execution (we just specify VWAP + time window).
        """
        # IB API: Order(orderId, action, totalQuantity, orderType, ...)
        order = {
            'orderId': self.next_order_id(),
            'action': 'BUY',
            'totalQuantity': self.target_shares,
            'orderType': 'VWAP',
            'tif': 'DAY',
            'vwapStartTime': self.get_market_open_hms(),
            'vwapEndTime': self.get_market_open_hms() + 600,  # +10 min
            'vwapMaxPercentVolume': 0.3,  # Don't exceed 30% of expected volume
        }

        # Submit via IB
        self.ib_client.placeOrder(order)

        # Monitor fill rate
        return self.monitor_vwap_fills(timeout_seconds)
```

**Integration into AEGIS**:

```python
# In daily_target.py (S15 strategy)

class S15WithVWAPExecution:
    def __init__(self):
        self.vwap_executor = VWAPExecutor(timeout_minutes=10)

    def execute_position_scale(self, tickers, shares_dict, is_entry=True):
        """
        Scale into/out of positions using VWAP instead of market orders.
        """
        for ticker in tickers:
            shares = shares_dict[ticker]

            if abs(shares) > 10000:  # Only VWAP for large orders
                self.vwap_executor.execute_vwap(
                    ticker=ticker,
                    target_shares=shares,
                    window_minutes=10,
                )
            else:
                # Market order for small fills
                self.ib_client.placeOrder(
                    action='BUY' if shares > 0 else 'SELL',
                    totalQuantity=abs(shares),
                    orderType='MKT',
                )
```

**Effort Estimate**:
- VWAP integration (IB API): 8-12h
- Almgren-Chriss optimizer: 6-10h
- Backtesting on 1-year fills: 3-5h
- **Total: 17-27h (assume IB VWAP orders do most lifting)**

**Expected Slippage Reduction**: 2-5 bps per large trade (~0.3-0.8% daily Sharpe improvement)

**Risk**: VWAP requires patient execution. If market gaps against us, fill rate drops.

---

## CATEGORY 3: RISK MANAGEMENT (EGARCH + CVaR Scaling)

### Dynamic Heat Scaling Based on EGARCH Volatility

**Current AEGIS**:
```python
# Fixed per-asset heat multipliers
heat = {
    'QQQ3.L': 0.3,
    '3LUS.L': 0.2,
    # ... etc
}
```

**Proposed: Dynamic Scaling**

```python
class DynamicHeatManager:
    def __init__(self, base_heat_dict, egarch_scaler):
        self.base_heat = base_heat_dict
        self.egarch = egarch_scaler
        self.max_heat = 0.5
        self.min_heat = 0.05

    def compute_heat_t(self, asset_id):
        """
        heat_t = base_heat / (σ_t / σ_median)

        If volatility is 2x normal → reduce heat by 50%
        If volatility is 0.5x normal → increase heat by 2x
        """
        vol_t = self.egarch.get_conditional_vol(asset_id)
        vol_median = self.egarch.models[asset_id].conditional_volatility.median()

        if vol_t is None or vol_median is None:
            return self.base_heat[asset_id]

        vol_ratio = vol_t / vol_median
        vol_ratio = np.clip(vol_ratio, 0.5, 2.5)  # Cap extreme ratios

        adjusted_heat = self.base_heat[asset_id] / vol_ratio
        return np.clip(adjusted_heat, self.min_heat, self.max_heat)

    def apply_regime_cap(self, asset_id, regime):
        """
        Further cap heat in high-volatility regimes.
        From v29-FIX-7: Regime Proxy for IPOs
        """
        heat = self.compute_heat_t(asset_id)

        if regime == 'EXTREME':
            heat *= 0.5  # Cut in half during extreme vol
        elif regime == 'HIGH':
            heat *= 0.75

        return heat
```

**Integration with S15 Daily Target Strategy**:

```python
# In daily_target.py

class S15V2(S15):
    def __init__(self, egarch_scaler, heat_manager, regime_detector):
        super().__init__()
        self.egarch = egarch_scaler
        self.heat_mgr = heat_manager
        self.regime = regime_detector

    def compute_allocation(self, data_snapshot):
        """Override parent's fixed heat allocation."""
        base_allocation = super().compute_allocation(data_snapshot)  # Get share counts

        current_regime = self.regime.detect()

        # Apply dynamic heat scaling
        adjusted_allocation = {}
        for ticker, shares in base_allocation.items():
            heat = self.heat_mgr.apply_regime_cap(ticker, current_regime)
            # Scale shares by heat ratio
            adjusted_allocation[ticker] = int(shares * heat / self.heat_mgr.base_heat[ticker])

        return adjusted_allocation
```

**Effort Estimate**:
- Dynamic heat engine: 12-18h
- Regime integration: 8-12h
- Backtesting: 5-8h
- **Total: 25-38h**

**Expected Improvement**: +3-8% Sharpe (reduced surprises during vol spikes, fewer liquidations)

---

## CATEGORY 4: MACHINE LEARNING (LSTM Volatility Forecasting)

### LSTM Attention Architecture for Volatility Prediction

**Setup**:
- **Input**: 20-day rolling window of [daily returns, realized vol, VIX, order book imbalance]
- **Output**: 5-day volatility forecast
- **Architecture**: LSTM(64) → Attention(8 heads) → Dense(5 outputs)

```python
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

class AttentionLSTM(nn.Module):
    def __init__(self, input_size=4, hidden_size=64, num_heads=8, output_size=5):
        super().__init__()
        self.hidden_size = hidden_size

        # LSTM layer
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True, dropout=0.3)

        # Multi-head attention
        self.attention = nn.MultiheadAttention(hidden_size, num_heads, batch_first=True)

        # Output layers
        self.fc1 = nn.Linear(hidden_size, 32)
        self.fc2 = nn.Linear(32, output_size)
        self.relu = nn.ReLU()

    def forward(self, x):
        """
        x: shape (batch, seq_len=20, features=4)
        """
        # LSTM forward
        lstm_out, (h_n, c_n) = self.lstm(x)  # shape: (batch, 20, 64)

        # Attention forward (self-attention over sequence)
        attn_out, attn_weights = self.attention(lstm_out, lstm_out, lstm_out)
        # attn_out: (batch, 20, 64), attn_weights: (batch, 20, 20)

        # Use last hidden state for prediction
        last_hidden = attn_out[:, -1, :]  # (batch, 64)

        # Fully connected
        x = self.relu(self.fc1(last_hidden))
        x = self.fc2(x)  # (batch, 5) = 5-day volatility forecast

        return x, attn_weights
```

**Training Loop**:

```python
class VolatilityForecaster:
    def __init__(self, model, device='cpu'):
        self.model = model.to(device)
        self.device = device
        self.optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
        self.criterion = nn.MSELoss()

    def prepare_dataset(self, returns_df, realized_vol_df, vix_ts, lookback=20, horizon=5):
        """
        Create rolling windows for training.
        Input: 20-day history
        Target: 5-day realized volatility (future)
        """
        X_list, y_list = [], []

        for i in range(lookback, len(returns_df) - horizon):
            # Features: returns, realized vol, VIX, order book imbalance
            window = returns_df.iloc[i-lookback:i]
            target = realized_vol_df.iloc[i:i+horizon].mean()  # Avg vol over horizon

            X_list.append(window.values)
            y_list.append(target)

        X = torch.FloatTensor(np.array(X_list))
        y = torch.FloatTensor(np.array(y_list)).unsqueeze(1)

        return TensorDataset(X, y)

    def train_epoch(self, train_loader):
        """Single training epoch."""
        self.model.train()
        total_loss = 0

        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(self.device), y_batch.to(self.device)

            # Forward pass
            y_pred, _ = self.model(X_batch)
            loss = self.criterion(y_pred, y_batch)

            # Backward pass
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            total_loss += loss.item()

        return total_loss / len(train_loader)

    def train(self, train_loader, val_loader, epochs=50, patience=10):
        """Train with early stopping."""
        best_val_loss = float('inf')
        patience_counter = 0

        for epoch in range(epochs):
            train_loss = self.train_epoch(train_loader)
            val_loss = self.validate(val_loader)

            print(f"Epoch {epoch}: Train={train_loss:.6f}, Val={val_loss:.6f}")

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                torch.save(self.model.state_dict(), 'best_model.pt')
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    print("Early stopping!")
                    break

        self.model.load_state_dict(torch.load('best_model.pt'))

    def validate(self, val_loader):
        """Compute validation loss."""
        self.model.eval()
        total_loss = 0

        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(self.device), y_batch.to(self.device)
                y_pred, _ = self.model(X_batch)
                loss = self.criterion(y_pred, y_batch)
                total_loss += loss.item()

        return total_loss / len(val_loader)
```

**Integration into AEGIS**:

```python
class LSTMVolatilityScaler:
    def __init__(self, model_path='best_model.pt'):
        self.model = AttentionLSTM()
        self.model.load_state_dict(torch.load(model_path))
        self.model.eval()
        self.scaler = StandardScaler()  # For input normalization

    def forecast_volatility(self, recent_returns, recent_vol, vix_ts):
        """
        Forecast next 5-day volatility.
        Returns: 5-element array [σ_{t+1}, σ_{t+2}, ..., σ_{t+5}]
        """
        # Prepare input (20-day window)
        X = np.column_stack([recent_returns[-20:], recent_vol[-20:], vix_ts[-20:], np.zeros(20)])
        X = self.scaler.transform(X)
        X_tensor = torch.FloatTensor(X).unsqueeze(0)

        with torch.no_grad():
            y_pred, attn_weights = self.model(X_tensor)

        forecast = y_pred[0].cpu().numpy()  # Shape: (5,)

        return forecast, attn_weights

    def refit_weekly(self, returns_df, vol_df, vix_ts):
        """Retrain model weekly."""
        dataset = self.prepare_dataset(returns_df, vol_df, vix_ts)
        train_loader = DataLoader(dataset, batch_size=32, shuffle=True)
        val_loader = DataLoader(dataset, batch_size=32)

        forecaster = VolatilityForecaster(self.model)
        forecaster.train(train_loader, val_loader, epochs=20)
```

**Effort Estimate**:
- LSTM architecture design: 15h
- Data pipeline (returns, vol, VIX) alignment: 20h
- Training loop + hyperparameter tuning: 25h
- Backtesting + walk-forward validation: 15h
- **Total: 75-85h**

**Expected Sharpe Improvement**: +15-25% (if forecasts calibrated well)

**Risks**:
- Overfitting on historical regime (use extensive cross-validation)
- Concept drift (retrain weekly, monitor prediction errors)
- Data leakage (ensure targets computed from future data only)

---

## CATEGORY 5: STRESS TESTING (Monte Carlo + Walk-Forward)

### Monte Carlo Simulation of Drawdowns

```python
import numpy as np
from scipy.stats import norm

class MonteCarloStressTest:
    def __init__(self, historical_daily_returns, num_simulations=1000):
        """
        historical_daily_returns: array of daily P&L (or returns)
        """
        self.returns = np.array(historical_daily_returns)
        self.num_sims = num_simulations
        self.mean_return = np.mean(self.returns)
        self.std_return = np.std(self.returns)

    def generate_paths(self, num_days=63):
        """
        Generate 1000 random price paths using GBM (Geometric Brownian Motion).
        Each path: 63 daily returns, cumulated into equity curve.
        """
        paths = np.zeros((self.num_sims, num_days))

        for sim in range(self.num_sims):
            shocks = np.random.normal(self.mean_return, self.std_return, num_days)
            cumulative_returns = np.cumprod(1 + shocks)
            paths[sim, :] = cumulative_returns

        return paths  # Shape: (1000, 63)

    def compute_drawdown_percentiles(self, paths):
        """
        For each simulated path, compute max drawdown.
        Then return percentiles: [5%, 25%, 50%, 75%, 95%]
        """
        max_drawdowns = []

        for path in paths:
            cumulative_max = np.maximum.accumulate(path)
            drawdown = (path - cumulative_max) / cumulative_max
            max_drawdown = np.min(drawdown)
            max_drawdowns.append(max_drawdown)

        max_drawdowns = np.array(max_drawdowns)
        percentiles = np.percentile(max_drawdowns, [5, 25, 50, 75, 95])

        return percentiles, max_drawdowns

    def stress_test_report(self):
        """Generate report: expected outcomes under 1000 scenarios."""
        paths = self.generate_paths(num_days=63)
        percentiles, all_dds = self.compute_drawdown_percentiles(paths)

        print("=" * 60)
        print("MONTE CARLO STRESS TEST: 1000 Simulated 63-Day Periods")
        print("=" * 60)
        print(f"Mean daily return: {self.mean_return*100:.2f}%")
        print(f"Std dev (daily): {self.std_return*100:.2f}%")
        print(f"\nMax Drawdown Percentiles:")
        print(f"  5th percentile (best case):  {percentiles[0]*100:+.1f}%")
        print(f"  25th percentile:             {percentiles[1]*100:+.1f}%")
        print(f"  Median (50th):               {percentiles[2]*100:+.1f}%")
        print(f"  75th percentile:             {percentiles[3]*100:+.1f}%")
        print(f"  95th percentile (worst case):{percentiles[4]*100:+.1f}%")

        # Probability of >10% drawdown
        prob_10pct_dd = np.mean(all_dds < -0.1)
        print(f"\nProbability of >10% drawdown: {prob_10pct_dd*100:.1f}%")

        return {
            'percentiles': percentiles,
            'all_drawdowns': all_dds,
            'prob_10pct_dd': prob_10pct_dd,
        }
```

### Walk-Forward Validation

```python
class WalkForwardBacktester:
    def __init__(self, strategy, data, train_window=90, test_window=10):
        """
        Roll forward: train on [t, t+90], test on [t+90, t+100], shift by 10
        """
        self.strategy = strategy
        self.data = data
        self.train_window = train_window
        self.test_window = test_window
        self.results = []

    def run_backtest(self):
        """Execute walk-forward loop."""
        for start_idx in range(0, len(self.data) - self.train_window - self.test_window, self.test_window):
            train_data = self.data[start_idx : start_idx + self.train_window]
            test_data = self.data[start_idx + self.train_window : start_idx + self.train_window + self.test_window]

            # Train strategy on train_data
            params = self.strategy.optimize(train_data)

            # Test on out-of-sample data
            trades = self.strategy.backtest(test_data, params)

            sharpe = self.compute_sharpe(trades)
            max_dd = self.compute_max_drawdown(trades)

            self.results.append({
                'period': start_idx,
                'sharpe': sharpe,
                'max_dd': max_dd,
                'trades': trades,
            })

        return self.results

    def analyze_stability(self):
        """Check if parameters stay stable across periods."""
        sharpes = [r['sharpe'] for r in self.results]

        print(f"Walk-Forward Sharpe Ratios:")
        print(f"  Mean: {np.mean(sharpes):.3f}")
        print(f"  Std:  {np.std(sharpes):.3f}")
        print(f"  Min:  {np.min(sharpes):.3f}")
        print(f"  Max:  {np.max(sharpes):.3f}")

        if np.std(sharpes) > np.mean(sharpes):
            print("  ⚠️  WARNING: High variance in Sharpe ratios (parameters unstable)")
        else:
            print("  ✓ Sharpe ratios stable across periods")
```

**Effort Estimate**:
- Monte Carlo simulator: 10-15h
- Walk-forward backtester: 15-20h
- Reporting dashboard: 8-12h
- **Total: 33-47h**

**Integration Target**: Phase 8 or Phase 14

---

## IMPLEMENTATION PRIORITY MATRIX

| Initiative | Category | Phase | Effort | Sharpe | Risk | Priority |
|-----------|----------|-------|--------|--------|------|----------|
| EGARCH volatility | 1 | 8/11 | 30h | +12-18% | Medium | 🔴 HIGH |
| VWAP execution | 2 | 11 | 25h | +0.5-1% | Low | 🟡 MED |
| Dynamic heat scaling | 3 | 11 | 30h | +3-8% | Low | 🔴 HIGH |
| LSTM forecasting | 4 | 12 | 80h | +15-25% | Medium | 🔴 HIGH |
| Stress testing | 5 | 8/14 | 40h | Confidence | Low | 🟡 MED |
| DCC-GARCH + copulas | 3 | 15 | 70h | +3-8% | Medium | 🟡 MED |
| Kelly sizing | 5 | 14 | 30h | +5-12% | Medium | 🟡 MED |
| Anomaly detection | 7 | 16 | 30h | +1-2% | Low | 🟢 LOW |

---

## QUICK START: Phase 8 Additions (This Week)

### Task 1: Add Slippage Monitoring Dashboard (10h)
```python
# Add to utils/monitoring.py
class SlippageMonitor:
    def track_order(self, order_id, order_price, fill_price):
        slippage_bps = abs(fill_price - order_price) / order_price * 10000
        self.log(f"Order {order_id}: {slippage_bps:.1f} bps")

    def daily_summary(self):
        return {
            'avg_slippage': np.mean(self.slippages),
            'max_slippage': np.max(self.slippages),
            'count': len(self.slippages),
        }
```

### Task 2: Add Basic Stress Test Module (20h)
```python
# Add to risk/stress_testing.py
class BasicStressTest:
    def run(self, current_position, historical_returns):
        scenarios = [
            {'name': 'Flash Crash -9%', 'return': -0.09},
            {'name': 'Lehman -20%', 'return': -0.20},
            {'name': 'VIX +100% spike', 'return': -0.15},
        ]

        for scenario in scenarios:
            impact = current_position * (1 + scenario['return'])
            print(f"{scenario['name']}: Portfolio = ${impact:,.0f}")
```

### Task 3: Connect EGARCH to Position Sizing (15h)
```python
# Hook into daily_target.py
egarch_scaler = EGARCHVolatilityScaler()
egarch_scaler.fit_weekly(returns_dict)

heat_mgr = DynamicHeatManager(base_heat_dict, egarch_scaler)
adjusted_position = heat_mgr.compute_heat_t('QQQ3.L')
```

**Total Phase 8 Addition**: ~45h (can be parallelized with infrastructure fixes)

---

## SUMMARY

**For AEGIS V2, recommended implementation sequence**:

1. **Week 1-2** (Phase 8): Slippage monitoring + basic stress tests
2. **Week 3-4** (Phase 11): EGARCH integration + VWAP execution
3. **Week 5-8** (Phase 12): LSTM volatility forecasting + walk-forward validation
4. **Week 9-12** (Phase 14-15): DCC-GARCH risk modeling + dynamic Kelly sizing

**Total Effort**: ~300-350 hours over 12 weeks (3 engineers × 4 hours/day = ~60h/week)

**Expected Cumulative Sharpe**: 0.15 → 0.40+ (167% improvement) by end of Phase 15

---

**Document Compiled**: 2026-03-10
**Audience**: AEGIS Development Team, Risk/Quant
**Next**: Prioritize Phase 8 additions, schedule code reviews
