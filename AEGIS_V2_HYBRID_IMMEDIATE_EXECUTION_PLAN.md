# AEGIS V2 HYBRID IMMEDIATE EXECUTION PLAN
## Complete 63-Day Implementation (Phases 1-32, All Wired)

**Date**: March 13, 2026
**Scope**: Full hybrid implementation combining base system + DQN + Global Markets in parallel
**Status**: Ready for immediate execution
**Total Timeline**: 63 days (9 weeks) to live trading with full hybrid stack

---

## EXECUTIVE SUMMARY

This is the **unified master execution plan** merging:
1. **AEGIS_V2_COMPLETE_EXECUTION_BLUEPRINT** (Phases 1-25, base system)
2. **DQN + Transformer Models** (Phases 26-29, parallel training during weeks 1-9)
3. **Global Market Expansion** (Phases 30-32, weeks 11-18)
4. **Full Risk/Compliance Management** (integrated throughout)

**Expected Outcome** (Week 9):
- ✅ Base system live with 100+ validated trades
- ✅ DQN model trained, validated, decision gate cleared
- ✅ Euronext + ASX ready for deployment
- ✅ Geopolitical monitoring operational
- **Daily return target**: 0.48-0.51% (152-168% CAGR)

---

## PART I: PHASES 1-25 (BASE SYSTEM BLUEPRINT)

These are from AEGIS_V2_COMPLETE_EXECUTION_BLUEPRINT. Executes alongside DQN training.

### Phase 1: Capital Preservation (Live day 1)
- **Purpose**: Ensure ruin probability <0.1% via Kelly Criterion
- **Input**: Historical daily returns (252 epochs), kelly_fraction=0.33
- **Decision**: kelly_size = (WR × payoff - LR) / payoff × 0.33
- **Output**: max_position_size, max_leverage, daily_loss_limit
- **Monitoring**: Daily heat consumption (target <3.5% of capital)
- **Recovery**: Auto-reduce positions if ruin probability spikes

### Phase 2: ISA Auditor (Every 5 minutes)
- **Purpose**: Binary gate preventing non-ISA trade execution
- **Input**: Account state (holdings, margin debt, cash)
- **Decision**: Margin debt = £0? Holdings ISA-eligible? No margin trading?
- **Output**: PASS (allow trading) or FAIL (halt trading)
- **Escalation**: If FAIL for >10 min, send alert and halt all trading
- **Recovery**: Manual review + re-enable after audit

### Phase 3: Compliance Gates (Pre-trade)
- **Purpose**: Pre-trade checks (margin, liquidity, halts)
- **Input**: Order request (symbol, quantity, side)
- **Decision**: Margin available? Spread reasonable? Trading halted?
- **Output**: PASS or FAIL (100ms before submission)
- **Escalation**: FAIL → log and reject order (no override)

### Phase 4: White Reality Check (Per signal)
- **Purpose**: Validate signal using Deflated Sharpe Ratio (DSR)
- **Input**: Signal history (50+ observations per regime)
- **Decision**: Compute DSR; require DSR >1.0 (world-class)
- **Output**: is_significant (boolean), DSR (score), pvalue
- **Metrics**: Bootstrap confidence interval (Efron 1979), regime-conditional testing
- **Escalation**: DSR <0.5 → disable signal for 1 week

### Phase 5: Regime Detection (5-state HMM)
- **Purpose**: Classify market regime (TRENDING_UP, RANGE, RISK_OFF, etc.)
- **Input**: VIX, realized vol (20-day), credit spreads, fear gauge
- **Decision Tree**:
  - TRENDING_UP: VIX <15, vol <15%, momentum >0
  - TRENDING_DOWN: VIX >18, momentum <0
  - RANGE: VIX 15-18, vol 10-20%, no momentum
  - HIGH_VOL: realized vol >25%, VIX >20
  - RISK_OFF: VIX >30, credit spreads >200 bps
- **Output**: per_market_regime (dict), transition_probability
- **Monitoring**: Track regime persistence (most regimes last 5-20 days)

### Phase 6: Volatility Scaler (Moreira-Muir)
- **Purpose**: Dynamic leverage based on realized volatility
- **Input**: Realized vol (20-day window), regime
- **Decision**: vol_scalar = 1.0 / (realized_vol / 15%)
  - Capped at [0.5, 1.5x]
  - In HIGH_VOL: cap at 1.0x
  - In RISK_OFF: cap at 0.5x
- **Output**: vol_scalar (0.5-1.5x), applied to all position sizes
- **Rationale**: Quiet markets → slightly increase leverage; spikes → reduce

### Phase 7: Confidence Scorer (8-indicator consensus)
- **Purpose**: Weighted consensus from 8 indicators
- **Indicators**:
  - VWAP momentum (1.8x weight)
  - RSI (1.2x)
  - EMA (0.8x)
  - ROC (1.0x)
  - MACD (1.0x)
  - ADX (1.5x)
  - Bollinger Bands (0.7x)
  - Volume (0.9x)
- **Calculation**: Weighted sum of normalized scores (0-10 per indicator)
- **Output**: confidence_score (0-10), scores_dict
- **Threshold**: ≥6.5 to trade (regime-dependent, adjusted nightly by Phase 24)
- **Rationale**: 8 indicators reduce noise; unequal weights reflect alpha

### Phase 8: Pre-Conditions Gate
- **Purpose**: Pre-trade operational checks
- **Input**: Account state, market status, order queue
- **Decision**:
  - ISA account ACTIVE?
  - Margin debt = £0?
  - Available cash sufficient?
  - Circuit breaker = GREEN?
  - Order queue <50?
- **Output**: PASS or FAIL
- **Escalation**: Any check fails → queue and retry (max 10 retries)

### Phase 9: Position Sizer (Leverage Prioritization)
- **Purpose**: Calculate order size + select optimal symbol with leverage
- **Input**: kelly_max, regime, vol_scalar, confidence, underlying_symbol
- **Decision Logic**:
  ```
  IF underlying in LEVERAGE_MAP AND LSE_OPEN AND confidence ≥7.0:
    symbol = get_5x_etp(underlying)  # e.g., QQQS.L for QQQ
    position_size = kelly_max × regime_mult × vol_scalar × 1.5

  ELIF underlying in LEVERAGE_MAP AND LSE_OPEN:
    symbol = get_3x_etp(underlying)  # e.g., QQQ3.L
    position_size = kelly_max × regime_mult × vol_scalar

  ELIF underlying in LEVERAGE_MAP AND NOT LSE_OPEN:
    symbol = underlying
    position_size = kelly_max × regime_mult × vol_scalar

  ELSE:
    symbol = underlying
    position_size = kelly_max × regime_mult × vol_scalar

  position_size_capped = min(position_size, max_daily_heat_remaining)
  ```
- **Leverage Prioritization Mapping**:
  - NVDA → NVD3.L (3x) or NVD5.L (5x)
  - QQQ → QQQ3.L (3x) or QQQS.L (5x)
  - SPX → 3LUS.L (3x) or 3USS.L (5x)
  - TSLA → TSL3.L (3x)
  - SOX → 3SEM.L (3x)
- **Output**: position_size, symbol, reason

### Phase 10: Execution Quality
- **Purpose**: Slippage modeling + entry timing optimization
- **Input**: order (symbol, size, side), market_data
- **Decision**:
  - Expected slippage: LSE 10-30 bps, US 8-20 bps, Euro 15-40 bps
  - Optimal timing: Pre-bell (08:00-08:15) for LSE, market open (14:30) for US
  - Participation rate: 20-30% of volume
- **Output**: expected_fill_price, entry_timing_score (0-1.0)
- **Monitoring**: Compare expected vs actual fill; track timing score

### Phases 11-18: (Additional operational phases)
- **Phase 11**: Order validation
- **Phase 12**: Risk limits check
- **Phase 13**: Margin availability
- **Phase 14**: Trade logging
- **Phase 15**: Order Router (IBKR submission)
- **Phase 16**: Execution confirmation
- **Phase 17**: Trade reconciliation
- **Phase 18**: Position tracking

### Phase 19: Risk Manager (Dynamic stops, heat cap, circuit breakers)
- **Purpose**: Manage stops, heat, circuit breakers
- **Input**: position, entry_price, regime, current_price, daily_P&L
- **Decision**:
  - Stop loss (regime-dependent):
    - TRENDING_UP: 3% stop
    - RANGE: 1.5% stop
    - HIGH_VOL: 2.0% stop
    - RISK_OFF: 1.0% stop
  - Portfolio heat cap:
    - L1 (-1.5%): yellow alert, no new positions
    - L2 (-2.5%): reduce positions 50%
    - L3 (-4.0%): FULL FLATTEN, circuit breaker
- **Output**: stop_loss_price, circuit_breaker_status
- **Monitoring**: Track stop-hit frequency (<5% of trades)

### Phase 20: Reconciliation Auditor (ISA compliance every 5 min)
- **Purpose**: ISA compliance audit
- **Input**: Account holdings, margin debt, cash balance
- **Decision**:
  - Margin debt = £0?
  - All holdings ISA-eligible?
  - No naked shorts?
  - No margin trading?
- **Output**: is_compliant (boolean), violations (list)
- **Escalation**: If non-compliant >5 min → halt all trading + alert

### Phase 21: Position Management
- **Purpose**: Track, rebalance, close positions
- **Input**: Current portfolio, market data
- **Decision**: Close positions at targets, stops, time-based exits
- **Output**: Closed positions, updated portfolio

### Phase 22: DQN Signal Weighting (HYBRID: Nightly, Phase 26 feeds into here)
- **Purpose**: Learn optimal indicator weighting via DQN
- **Input**: Daily trades (500+ per month), P&L breakdown
- **Decision**: Retrain 8-indicator weights (40 parameters × 5 regimes)
- **Output**: Updated indicator weights, saved to database
- **Frequency**: Nightly (22:00-23:00 UTC)
- **Dependencies**: Phase 26 (DQN training pipeline) feeds optimal weights

### Phase 23: Performance Attribution (Ouroboros, nightly)
- **Purpose**: Decompose trade returns by signal/regime/timing
- **Input**: All 500+ daily trades
- **Decision**: Calculate win rate by regime, attribution by source
- **Output**: Win rate per regime, confidence scores by signal
- **Timing**: 22:00-22:10 UTC

### Phase 24: ML Adaptation (Ouroboros, nightly)
- **Purpose**: Update thresholds based on performance
- **Input**: Win rates by regime (from Phase 23)
- **Decision**:
  - IF regime WR <40% → raise signal threshold +0.5
  - IF regime WR >50% → lower threshold -0.25
  - Keep thresholds in [5.5, 8.5] range
  - Adjust leverage multipliers
- **Output**: New thresholds, new leverage multipliers
- **Timing**: 22:10-22:20 UTC

### Phase 25: Live Orchestrator (Commits changes, restarts)
- **Purpose**: Commit all nightly changes, go live with updated parameters
- **Input**: All Phase 22-24 outputs
- **Decision**: Commit to database, prepare for 08:00 UTC start
- **Output**: New parameters live, confirmed via heartbeat
- **Timing**: 22:50-23:00 UTC (ready for LSE 08:00 open)
- **Dependencies**: All previous phases completed before restart

---

## PART II: PHASES 26-29 (DQN + TRANSFORMER, PARALLEL EXECUTION)

**Timeline**: Weeks 1-9 (parallel with Phases 1-25 validation)
**Resource**: 44 hours/week, split into 4 phases

### Phase 26: DQN Feature Engineering + Data Pipeline (Weeks 1-2, 80 hours)
- **Purpose**: Extract features, build input pipeline for Transformer
- **Team**: 1 ML engineer + 1 data engineer (40 hrs each)
- **Tasks**:

#### 26A: OHLCV Candle Extraction (20 hours)
- **Input**: Tick-level data from IBKR (last 2 years LSE 08:00-16:30)
- **Process**:
  - Extract 5-second candles (high, low, open, close, volume)
  - Extract 30-second candles
  - Extract 60-second candles
  - Aggregate into time windows (5 consecutive 5-sec candles = 25-second window, etc.)
- **Output**: OHLCV tensors, shape [timestamp, window, 5 features]
- **File**: `nzt48-aegis-v2/ml/data/extract_candles.py` (NEW)

#### 26B: Orderbook Microstructure (15 hours)
- **Input**: Level 1 bid/ask data (IBKR market data snapshot every 100ms)
- **Process**:
  - Top 5 bid/ask levels
  - Bid-ask spread (percentage)
  - Slope of orderbook (buying/selling pressure)
  - Volume profile (concentration at each level)
  - Order imbalance (buy volume / sell volume)
- **Output**: Microstructure tensors, shape [timestamp, 10 features]
- **File**: `nzt48-aegis-v2/ml/data/extract_orderbook.py` (NEW)

#### 26C: Volatility + Liquidity Metrics (20 hours)
- **Input**: Returns and volumes
- **Process**:
  - Realized volatility (20-period rolling window, sqrt(sum(log-returns²)/N))
  - Amihud illiquidity ratio (|return| / dollar_volume)
  - GARCH(1,1) conditional volatility forecast
  - High-Low range (HLC indicator)
- **Output**: Risk tensors, shape [timestamp, 4 features]
- **File**: `nzt48-aegis-v2/ml/data/extract_volatility.py` (NEW)

#### 26D: Time-of-Day + Sector Momentum (15 hours)
- **Input**: Time indices, sector returns
- **Process**:
  - 9 time-of-day buckets (pre-open, 5 1-hour windows, hour-end, post-close)
  - Sector momentum (cross-sectional returns of peers)
  - Intraday momentum (returns last 5 min / last 30 min)
  - Mean reversion indicator (distance from moving average)
- **Output**: Context tensors, shape [timestamp, 8 features]
- **File**: `nzt48-aegis-v2/ml/data/extract_context.py` (NEW)

#### 26E: Data Pipeline Assembly (10 hours)
- **Input**: All 4 feature sets
- **Process**:
  - Align by timestamp
  - Normalize features (zero-mean, unit-variance per feature)
  - Create rolling windows (last 20 time steps = 100 seconds at 5-sec resolution)
  - Generate labels (future 5-min return: -1 if <-0.5%, 0 if -0.5% to +0.5%, +1 if >+0.5%)
  - Split into train (60%), validation (20%), test (20%)
- **Output**: PyTorch dataset, ready for training
  - Shape: [N_samples, 20_timesteps, 32_features]
  - Labels: [N_samples, 1] ∈ {-1, 0, +1}
- **File**: `nzt48-aegis-v2/ml/data/dataset.py` (NEW)

**Deliverable (Week 2)**:
- ✅ Feature extraction pipeline validated
- ✅ 2 years LSE data loaded (2024-2026)
- ✅ Dataset splits confirmed (balanced classes)
- ✅ Sample data visualization (feature distributions, label distribution)

---

### Phase 27: Transformer Encoder + DQN Architecture (Weeks 3-5, 120 hours)
- **Purpose**: Build and train neural networks
- **Team**: 1-2 ML engineers (60 hrs each)
- **Tasks**:

#### 27A: Transformer Encoder Design (40 hours)
```python
class TransformerPriceEncoder(nn.Module):
    def __init__(self, input_dim=32, hidden_dim=64, num_heads=4, num_layers=2):
        super().__init__()

        # Positional encoding
        self.positional_enc = nn.Parameter(torch.randn(1, 20, hidden_dim))

        # Embedding layer
        self.input_proj = nn.Linear(input_dim, hidden_dim)

        # Multi-head self-attention layers
        self.attention_layers = nn.ModuleList([
            nn.MultiheadAttention(hidden_dim, num_heads, batch_first=True, dropout=0.1)
            for _ in range(num_layers)
        ])

        # Feed-forward networks after attention
        self.ffn_layers = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim * 4),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(hidden_dim * 4, hidden_dim)
            )
            for _ in range(num_layers)
        ])

        # Output projection
        self.output_proj = nn.Linear(hidden_dim, hidden_dim)
        self.layer_norms = nn.ModuleList([nn.LayerNorm(hidden_dim) for _ in range(num_layers * 2)])

    def forward(self, x):  # x: [batch_size, 20, 32]
        # Project input
        x = self.input_proj(x)  # [batch, 20, 64]
        x = x + self.positional_enc  # Add time awareness

        # Apply attention + feed-forward layers
        for i, (attn, ffn) in enumerate(zip(self.attention_layers, self.ffn_layers)):
            # Self-attention with residual
            attn_out, _ = attn(x, x, x)
            x = self.layer_norms[i*2](x + attn_out)

            # Feed-forward with residual
            ffn_out = ffn(x)
            x = self.layer_norms[i*2 + 1](x + ffn_out)

        # Use final timestep representation
        return self.output_proj(x[:, -1, :])  # [batch, 64]
```
- **Training**: Pre-train on 2 years LSE data
  - Optimizer: Adam (learning rate 1e-3)
  - Loss: MSE on next 5-min return (regression task)
  - Epochs: 50 (early stopping if val loss plateaus)
  - Batch size: 128
- **Validation**: Check that Transformer learns to predict future returns
  - Metric: Mean absolute error on validation set <0.3% (5-min horizon)
  - Sanity check: Transformer should beat random baseline by 10%+
- **File**: `nzt48-aegis-v2/ml/models/transformer_encoder.py` (NEW)

#### 27B: DQN Value Network Design (40 hours)
```python
class DQNValueNetwork(nn.Module):
    def __init__(self, hidden_dim=64, num_actions=3):  # BUY, SELL, HOLD
        super().__init__()

        self.transformer = TransformerPriceEncoder()

        # Dueling DQN: separate value + advantage streams
        self.value_stream = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(32, 1)  # Single value estimate
        )

        self.advantage_stream = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(32, num_actions)  # Q-value per action
        )

    def forward(self, x):  # x: [batch, 20, 32]
        # Encode with transformer
        encoded = self.transformer(x)  # [batch, 64]

        # Compute value and advantages
        value = self.value_stream(encoded)  # [batch, 1]
        advantages = self.advantage_stream(encoded)  # [batch, 3]

        # Dueling formula: Q = V + (A - mean(A))
        q_values = value + (advantages - advantages.mean(dim=1, keepdim=True))

        return q_values  # [batch, 3]
```
- **Training**: Reinforcement Learning loop
  - Reward: Daily P&L from executed trades (5-min horizon trade P&L)
  - Bellman: Q(s,a) ← Q(s,a) + α[r + γ·max_a'(Q(s',a')) - Q(s,a)]
    - α = 0.001 (learning rate)
    - γ = 0.99 (discount factor, slightly value future)
  - Replay buffer: Store (state, action, reward, next_state, done) tuples
    - Buffer size: 100,000 transitions
    - Batch size: 32
  - Epsilon-greedy exploration:
    - ε_start = 0.2 (20% random actions)
    - ε_end = 0.05 (5% at end)
    - ε_decay = 100,000 steps
- **File**: `nzt48-aegis-v2/ml/models/dqn_value_network.py` (NEW)

#### 27C: DQN Training Loop (30 hours)
- **Process**:
  ```python
  # Pseudo-code
  replay_buffer = ReplayBuffer(capacity=100000)
  q_network = DQNValueNetwork().to('cuda')
  optimizer = Adam(q_network.parameters(), lr=0.001)
  epsilon = 0.2

  for episode in range(100000):  # ~2 weeks of simulated trading
      state = env.reset()  # Random historical snapshot
      episode_reward = 0

      for step in range(100):  # Max 100 steps per episode (500-min trading day)
          # Epsilon-greedy action selection
          if random() < epsilon:
              action = random_action()
          else:
              with torch.no_grad():
                  q_values = q_network(state)
                  action = argmax(q_values)

          # Execute action, get reward
          next_state, reward, done = env.step(action)
          replay_buffer.push((state, action, reward, next_state, done))
          episode_reward += reward

          # Train on mini-batch
          if len(replay_buffer) >= 32:
              states, actions, rewards, next_states, dones = replay_buffer.sample(32)
              q_pred = q_network(states)[range(32), actions]
              q_target = rewards + 0.99 * ~dones * max(q_network(next_states))
              loss = MSE(q_pred, q_target)
              optimizer.zero_grad()
              loss.backward()
              optimizer.step()

          state = next_state
          if done:
              break

      # Decay epsilon
      epsilon = max(0.05, epsilon * 0.9999)

      # Validate every 1000 episodes
      if episode % 1000 == 0:
          val_sharpe = validate_on_val_set()
          print(f"Episode {episode}, Val Sharpe: {val_sharpe:.2f}")
          if val_sharpe > best_sharpe:
              torch.save(q_network.state_dict(), 'models/dqn_best.pt')
  ```
- **Metrics tracked**:
  - Episode reward (daily P&L)
  - Win rate (% profitable episodes)
  - Sharpe ratio on validation set
  - Q-value distribution (mean, std)
- **File**: `nzt48-aegis-v2/ml/train_dqn.py` (NEW)

#### 27D: Regime-Specific DQN Heads (30 hours)
- **Insight**: DQN learns better if it specializes per regime
- **Implementation**:
  - Train 5 separate DQN models, one per regime:
    - DQN_TRENDING_UP (trained only on TRENDING_UP data)
    - DQN_RANGE (trained only on RANGE-bound data)
    - DQN_HIGH_VOL (trained only on HIGH_VOL data)
    - DQN_RISK_OFF (trained only on RISK_OFF data)
    - DQN_TRENDING_DOWN (trained only on bearish data)
  - Share Transformer encoder weights (transfer learning)
  - Train regime-specific advantage/value heads
  - At inference time: Detect current regime → use appropriate DQN model
- **Expected benefit**: +0.002-0.005% daily improvement (fewer overfitting errors per regime)
- **File**: `nzt48-aegis-v2/ml/models/dqn_regime_ensemble.py` (NEW)

**Deliverable (Week 5)**:
- ✅ Transformer encoder trained, validates prediction MSE <0.3%
- ✅ DQN value network trained on 100k episodes
- ✅ 5 regime-specific DQN heads trained
- ✅ Backtest Sharpe ≥1.0 on validation set
- ✅ All models saved: `models/transformer_encoder.pt`, `models/dqn_regime_*.pt`

---

### Phase 28: Walk-Forward Validation + Overfitting Detection (Weeks 6-8, 100 hours)
- **Purpose**: Prove DQN isn't overfitted; estimate realistic live performance
- **Team**: 1 ML engineer + 1 quant analyst (50 hrs each)
- **Tasks**:

#### 28A: Walk-Forward Testing (40 hours)
- **Concept**: Test on data never seen during training
- **Process**:
  - Split 2 years data into 4 quarters
  - Train on Q1 + Q2, test on Q3
  - Train on Q1 + Q2 + Q3, test on Q4
  - Train on Q2 + Q3 + Q4, test on Q1 (2025)
  - Train on Q3 + Q4 + Q1 (2025), test on Q2 (2025)
  - Train on Q4 + Q1 (2025) + Q2 (2025), test on Q3 (2025)
- **Metrics per fold**:
  - Win rate
  - Sharpe ratio
  - Maximum drawdown
  - Profit factor
- **Expected**:
  - Backtest Sharpe: 1.0-1.5 (training set, likely overfitted)
  - Walk-forward Sharpe: 0.7-0.9 (deflated, more realistic)
  - Degradation: 25-30% (normal for ML systems)
- **File**: `nzt48-aegis-v2/ml/validate_walkforward.py` (NEW)

#### 28B: Deflated Sharpe Ratio Calculation (30 hours)
- **Method**: López de Prado's formula to account for multiple comparisons
  ```python
  def deflated_sharpe_ratio(sharpe_ratio, n_tests, n_obs):
      """
      Adjust Sharpe ratio for multiple comparisons.
      n_tests = number of parameter combinations tried (e.g., learning rates, architectures)
      n_obs = number of observations in backtest
      """
      sr_null = 0  # Null hypothesis: SR = 0
      var_sr_null = 1 / n_obs + (2 - 3) / (4 * n_obs)

      # Bonferroni correction for multiple tests
      z_crit = norm.ppf(1 - 0.05 / (2 * n_tests))  # Two-tailed

      # Deflated SR
      dsr = (sharpe_ratio - sr_null - z_crit * np.sqrt(var_sr_null)) / np.sqrt(var_sr_null)

      return dsr
  ```
- **Application**:
  - n_tests = 50 (Transformer architectures tried + DQN learning rates)
  - n_obs = ~50,000 (5-min trades over 2 years)
  - Backtest Sharpe 1.2 → Deflated Sharpe ~0.75
- **Validation gate**: DSR ≥0.7 required to proceed to live
- **File**: `nzt48-aegis-v2/ml/validate_deflated_sharpe.py` (NEW)

#### 28C: Regime-Specific Performance Validation (20 hours)
- **Check**: Does DQN work in ALL regimes, not just one?
- **Process**:
  - Win rate per regime (should be ≥42% in all regimes)
  - Sharpe per regime (should be ≥0.5 in all regimes)
  - Worst regime performance (identify weakness)
- **Gate**: If any regime has Sharpe <0.3, retrain that regime-specific model
- **File**: `nzt48-aegis-v2/ml/validate_per_regime.py` (NEW)

#### 28D: Live Paper Trading with Dual Signals (20 hours)
- **Setup**:
  - Run AEGIS V2 base system (8-indicator)
  - **IN PARALLEL**: Run DQN signal inference on every tick
  - Log both signals (never trade on DQN yet, just observe)
  - Compare confidence scores: DQN vs 8-indicator
- **Process** (Week 9):
  - Collect 100+ paper trades worth of data
  - Measure: DQN predictive accuracy on paper trades
    - Accuracy = % of times DQN's predicted action matches future price movement
    - Target: DQN accuracy ≥55% (beats 8-indicator baseline of 52%)
- **Metric**: Predictive accuracy, not P&L (proves DQN learns market structure)
- **File**: `nzt48-aegis-v2/ml/validate_paper_trading.py` (NEW)

**Deliverable (Week 8)**:
- ✅ Walk-forward Sharpe ≥0.8 confirmed
- ✅ Deflated Sharpe ≥0.7 confirmed (realistic live performance)
- ✅ Per-regime Sharpe ≥0.5 confirmed (works in all regimes)
- ✅ Paper trading predictive accuracy ≥55% (ready for live trading)
- ✅ Go/No-Go decision prepared for Week 9

---

### Phase 29: DQN Integration + Live Deployment (Week 9, 20 hours)
- **Purpose**: Wire DQN into production system, make go/no-go decision
- **Team**: 1 ML engineer + 1 systems engineer (10 hrs each)
- **Tasks**:

#### 29A: DQN Inference Integration (8 hours)
- **File to modify**: `python_brain/brain/strategies/dqn_transformer_v1.py` (NEW, 400 lines)
- **Changes**:
  ```python
  class DQNStrategy:
      def __init__(self, model_path='models/dqn_regime_ensemble.pt'):
          self.models = {}
          for regime in ['TRENDING_UP', 'RANGE', 'HIGH_VOL', 'RISK_OFF', 'TRENDING_DOWN']:
              self.models[regime] = torch.load(f'{model_path}/{regime}.pt')

      def evaluate(self, tick_context, current_regime):
          """
          Input: tick_context (OHLCV candles last 100 sec)
          Output: confidence (0-100), action (BUY/SELL/HOLD)
          """
          # Extract features (Phase 26)
          features = extract_features(tick_context)  # [1, 20, 32]
          features = torch.FloatTensor(features).cuda()

          # Get regime-specific DQN
          dqn_model = self.models[current_regime]

          # Forward pass
          with torch.no_grad():
              q_values = dqn_model(features)  # [1, 3] = [SELL, HOLD, BUY]

          # Map Q-values to confidence
          action = torch.argmax(q_values, dim=1).item()
          q_value = q_values[0, action].item()

          # Q-values typically [-1, +1], map to confidence [0-100]
          confidence = 50 + (q_value * 25)
          confidence = max(0, min(100, confidence))

          return {
              'action': ['SELL', 'HOLD', 'BUY'][action],
              'confidence': confidence,
              'q_values': q_values.detach().cpu().numpy(),
              'uncertainty': float(q_values.std())
          }
  ```
- **Integration point**: `python_brain/brain/strategies/vanguard_sniper.py` (MODIFIED)
  ```python
  # OLD:
  # confidence = confidence_score_8indicator(...)

  # NEW (dual signal):
  dqn_strategy = DQNStrategy()
  dqn_signal = dqn_strategy.evaluate(tick_context, current_regime)
  confidence_8ind = confidence_score_8indicator(...)
  confidence_dqn = dqn_signal['confidence']

  # Log both (never execute DQN yet)
  log_dual_signal(confidence_8ind, confidence_dqn, action_8ind=..., action_dqn=dqn_signal['action'])

  # Execute 8-indicator only
  confidence = confidence_8ind
  ```

#### 29B: Kelly Sizing Enhancement (5 hours)
- **File**: `python_brain/brain/sizing/kelly_12factor.py` (MODIFIED)
- **New factor**: DQN model uncertainty
  ```python
  # Factor 13 (new): DQN uncertainty penalty
  dqn_uncertainty = dqn_signal.get('uncertainty', 0)  # std(Q-values)
  if dqn_uncertainty > 0.3:  # High uncertainty
      uncertainty_penalty = 0.9  # Reduce position by 10%
  else:
      uncertainty_penalty = 1.0

  # Recalculate kelly
  kelly_final = kelly_base × uncertainty_penalty
  ```

#### 29C: Fallback Logic (5 hours)
- **File**: `python_brain/brain/strategies/vanguard_sniper.py` (MODIFIED)
- **Implementation**:
  ```python
  def evaluate_with_fallback(tick_context, current_regime):
      try:
          # Try DQN first (if validation passed)
          if DQN_APPROVED:
              dqn_signal = dqn_strategy.evaluate(tick_context, current_regime)
              confidence = dqn_signal['confidence']
              strategy = 'DQN'
          else:
              raise DQNNotApproved()
      except Exception as e:
          # Fallback to 8-indicator
          confidence = confidence_score_8indicator(...)
          strategy = '8-indicator'
          log_fallback_event(e, strategy)

      return {'confidence': confidence, 'strategy': strategy}
  ```

#### 29D: Monitoring + Alerting (2 hours)
- **File**: `rust_core/src/dqn_monitoring.rs` (NEW)
- **Monitors**:
  - DQN inference latency (should be <50ms per tick)
  - DQN confidence distribution (mean, std)
  - Q-value distribution (mean, std) per regime
  - If Q-value mean drifts >0.3 from 0 → alert (possible degradation)
  - If latency >100ms → log and consider reverting to 8-indicator
- **Output**: Daily DQN health report

#### 29E: Go/No-Go Decision (5 hours)
- **Gate criteria** (must ALL pass):
  1. ✅ Backtest Sharpe ≥1.0 (training set)
  2. ✅ Walk-forward Sharpe ≥0.8 (unseen data)
  3. ✅ Deflated Sharpe ≥0.7 (conservative estimate)
  4. ✅ Per-regime Sharpe ≥0.5 (works everywhere)
  5. ✅ Paper trading accuracy ≥55% (beats baseline)
  6. ✅ Inference latency <50ms (real-time feasible)
  7. ✅ No memory leaks over 100+ trades (monitoring stable)

**IF all gates pass**:
```
✅ APPROVE_DQN_GO_LIVE = True
→ Switch vanguard_sniper to dual-signal mode (execute 8-indicator, log DQN)
→ Run for 2 weeks, compare performance
→ IF DQN win rate ≥42% after 2 weeks: promote DQN to primary
```

**IF any gate fails**:
```
❌ APPROVE_DQN_GO_LIVE = False
→ Continue with 8-indicator system
→ Document why DQN failed
→ Optional: retrain on different hyperparameters (future work)
→ Proceed to Phase 30 (global market expansion) without DQN
```

**Deliverable (Week 9)**:
- ✅ DQN fully integrated into vanguard_sniper.py
- ✅ Go/No-Go decision made based on gate criteria
- ✅ Monitoring pipeline live
- ✅ Ready for Phase 2A (DQN go-live decision)

---

## PART III: PHASES 30-32 (GLOBAL MARKET EXPANSION)

**Timeline**: Weeks 11-18 (after base system + DQN decision validated)
**Resource**: 58 hours total (split across engineers)

### Phase 30: Multi-Exchange Infrastructure Setup (Weeks 11-12, 20 hours)
- **Purpose**: Add Euronext + ASX market support to engine
- **Team**: 1 systems engineer + 1 infrastructure engineer (10 hrs each)
- **Tasks**:

#### 30A: Extended Clock Module (4 hours)
- **File**: `rust_core/src/clock.rs` (MODIFIED)
- **Add market-specific time checks**:
  ```rust
  pub enum Market {
      LSE,
      NYSE,
      EURONEXT,
      ASX,
      TSE,
  }

  fn is_market_open(market: Market, time: DateTime) -> bool {
      match market {
          Market::LSE => {
              let london = time.with_timezone(&London);
              london.hour() >= 8 && london.hour() < 16
          }
          Market::EURONEXT => {
              let cet = time.with_timezone(&CET);
              cet.hour() >= 9 && cet.hour() < 17
          }
          Market::ASX => {
              let aedt = time.with_timezone(&AUS_EASTERN);
              aedt.hour() >= 10 && aedt.hour() < 16
          }
          // ... etc
      }
  }

  fn market_settlement_time(market: Market) -> u32 {
      match market {
          Market::LSE => 2,      // T+2
          Market::EURONEXT => 2, // T+2
          Market::ASX => 2,      // T+2
          Market::TSE => 3,      // T+3 (Japan)
      }
  }
  ```
- **Tests**: Verify time logic across all time zones

#### 30B: FX Manager (6 hours)
- **File**: `rust_core/src/fx_manager.rs` (NEW, 150 lines)
- **Implementation**:
  ```rust
  pub struct FXPosition {
      currency: Currency,  // EUR, AUD, JPY
      notional_gbp: f64,
      hedge_rate: f64,
      cost_bps_per_month: f64,
  }

  impl FXManager {
      fn calculate_fx_cost(&self, positions: &[Position]) -> f64 {
          let eur_exposure: f64 = positions.iter()
              .filter(|p| p.currency == EUR)
              .map(|p| p.notional_gbp)
              .sum();

          let aud_exposure: f64 = positions.iter()
              .filter(|p| p.currency == AUD)
              .map(|p| p.notional_gbp)
              .sum();

          // Monthly costs
          let eur_cost = eur_exposure * 0.0015;  // 15 bps/month
          let aud_cost = aud_exposure * 0.0025;  // 25 bps/month

          // Convert to daily
          (eur_cost + aud_cost) / 21.0
      }

      fn adjust_position_size(&self, base_size: f64, market: Market) -> f64 {
          let daily_fx_cost = self.calculate_fx_cost(&self.positions);
          let target_daily_return = 0.0045;  // 0.45%

          if daily_fx_cost > target_daily_return * 0.25 {
              // FX cost >25% of daily target, reduce position
              base_size * (1.0 - daily_fx_cost / target_daily_return)
          } else {
              base_size
          }
      }
  }
  ```

#### 30C: Geopolitical Risk Manager (6 hours)
- **File**: `rust_core/src/geopolitical_risk_manager.rs` (NEW, 200 lines)
- **Implementation**:
  ```rust
  pub enum GeopoliticalRiskLevel {
      LOW,      // 1.0x position multiplier
      MEDIUM,   // 0.7x position multiplier
      HIGH,     // 0.3x position multiplier
      HALT,     // 0.0x (no trading)
  }

  pub struct GeopoliticalRiskManager {
      risk_levels: HashMap<Market, GeopoliticalRiskLevel>,
      last_update: DateTime,
  }

  impl GeopoliticalRiskManager {
      fn monitor_newsapi(&mut self) {
          // Call NewsAPI daily for keywords
          let keywords = ["sanctions", "war", "coup", "capital control", "default", "emergency"];
          for market in [Market::EURONEXT, Market::TSE, Market::ASX] {
              let risk_score = newsapi.scan_for_keywords(market_name(market), keywords);
              self.update_risk_level(market, risk_score);
          }
      }

      fn position_multiplier(&self, market: Market) -> f64 {
          match self.risk_levels.get(&market) {
              Some(GeopoliticalRiskLevel::LOW) => 1.0,
              Some(GeopoliticalRiskLevel::MEDIUM) => 0.7,
              Some(GeopoliticalRiskLevel::HIGH) => 0.3,
              Some(GeopoliticalRiskLevel::HALT) => 0.0,
              None => 1.0,
          }
      }
  }
  ```
- **News source integration**:
  - Primary: NewsAPI (newsapi.org)
  - Secondary: Refinitiv alerts (via IBKR)
  - Polling: Daily at 07:00 UTC (before LSE open)

#### 30D: Universe Router Enhancement (4 hours)
- **File**: `rust_core/src/universe.rs` (MODIFIED)
- **Extend RouteResult enum**:
  ```rust
  pub enum RouteResult {
      LSE(String, Leverage),        // LSE ticker + leverage type
      NYSE(String),                  // US ticker
      EURONEXT(String),              // Euronext ticker
      ASX(String),                   // Australian ticker
      ERROR(String),
  }

  fn route_to_market(ticker: &str) -> RouteResult {
      match ticker {
          // Euronext
          "CAC_3x" => RouteResult::EURONEXT("CAC_3x"),
          "ASML" => RouteResult::EURONEXT("ASML"),
          // ASX
          "BHP" => RouteResult::ASX("BHP.AX"),
          // etc
          _ => route_to_lse_or_us(ticker),
      }
  }
  ```

**Deliverable (Week 12)**:
- ✅ Clock module supports 4 markets
- ✅ FX manager tracks EUR/AUD exposure
- ✅ Geopolitical risk manager integrated with NewsAPI
- ✅ Universe router extended for Euronext + ASX
- ✅ All modules tested with dummy market data

---

### Phase 31: Euronext Implementation (Weeks 13-14, 20 hours)
- **Purpose**: Deploy Euronext trading (30 European stocks)
- **Team**: 1 trader + 1 systems engineer (10 hrs each)
- **Tasks**:

#### 31A: Euronext Asset Universe (5 hours)
- **File**: `config/contracts.toml` (EXTENDED)
- **Add 30 Euronext stocks**:
  ```toml
  [euronext_core]
  count = 30
  assets = [
      # Luxury
      { ticker = "LVMH.PA", sector = "Luxury", market = "EURONEXT", leverage = 1.0 },
      { ticker = "KER.PA", sector = "Luxury", market = "EURONEXT", leverage = 1.0 },
      # Banks
      { ticker = "BNP.PA", sector = "Finance", market = "EURONEXT", leverage = 1.0 },
      # Tech
      { ticker = "CAP.PA", sector = "Tech", market = "EURONEXT", leverage = 1.0 },
      # ... 26 more
  ]
  trading_hours = "09:00-17:30 CET"
  fx_cost_bps_per_month = 15
  is_isa_eligible = false  # ISA covers only UK-domiciled assets
  ```
- **Data validation**: Check all tickers accessible via IBKR

#### 31B: Euronext Signal Evaluation (5 hours)
- **File**: `python_brain/brain/strategies/apex_scout.py` (MODIFIED)
- **CAC-specific volatility patterns**:
  ```python
  def evaluate_euronext(tick_context):
      """
      Euronext: Different volatility profile than LSE
      CAC40 is political-event sensitive
      """
      # Standard 8 indicators
      vwap_score = score_vwap(tick_context)
      rsi_score = score_rsi(tick_context)
      # ... etc

      # CAC-specific adjustments
      if is_trading_hours(17:30):  # Just before close
          vwap_score *= 0.9  # Lower confidence near close (thin liquidity)

      confidence = weighted_sum([vwap, rsi, ema, roc, macd, adx, bb, volume])
      return confidence
  ```

#### 31C: Euronext Universe Scan (5 hours)
- **File**: `nzt48-aegis-v2/scripts/nightly_universe_scan.py` (EXTENDED)
- **Add Euronext scanning**:
  ```python
  def scan_universe_nightly():
      # Existing: Scan LSE + US + Asia
      lse_signals = scan_lse()
      us_signals = scan_us()
      asia_signals = scan_asia()

      # NEW: Scan Euronext
      euronext_signals = scan_euronext()

      # NEW: Apply FX adjustment
      euronext_signals = [
          {
              'asset': s['asset'],
              'signal': s['signal'],
              'adjusted_signal': s['signal'] - 0.15/252,  # FX cost daily
              'market': 'EURONEXT',
          }
          for s in euronext_signals
      ]

      # Combine and rank
      all_signals = lse_signals + us_signals + asia_signals + euronext_signals
      all_signals.sort(key=lambda x: x['adjusted_signal'], reverse=True)

      # Tier by cutoff
      HIGH_CONVICTION = all_signals[:50]
      STANDARD = all_signals[51:200]
      WATCHLIST = all_signals[201:500]

      save_to_db(HIGH_CONVICTION, STANDARD, WATCHLIST)
  ```

#### 31D: Paper Trading Euronext (5 hours)
- **Execution**: 2-week paper trading period (Week 13-14)
  - Trade only Euronext assets
  - Target: 50+ Euronext trades
  - Validate: Win rate ≥40%, expected +0.015-0.02% daily contribution
  - Check: FX costs match 15 bps/month assumption
  - Monitor: Execution latency (<500ms acceptable for Euronext farther from IBKR)

**Deliverable (Week 14)**:
- ✅ 30 Euronext stocks tradeable
- ✅ 50+ paper trades executed
- ✅ Win rate ≥40% confirmed
- ✅ FX costs verified (15 bps/month)
- ✅ Ready for live trading

---

### Phase 32: ASX + Geopolitical Monitoring + TSE Optional (Weeks 15-18, 18 hours)
- **Purpose**: Deploy ASX overnight trading, add geopolitical monitoring, optionally TSE
- **Team**: 1 trader + 1 risk officer (9 hrs each)
- **Tasks**:

#### 32A: ASX Asset Universe + Paper Trading (6 hours)
- **File**: `config/contracts.toml` (EXTENDED)
- **Add 25 ASX stocks**:
  ```toml
  [asx_core]
  count = 25
  assets = [
      { ticker = "BHP.AX", sector = "Materials", market = "ASX", leverage = 1.0 },
      { ticker = "RIO.AX", sector = "Materials", market = "ASX", leverage = 1.0 },
      { ticker = "CBA.AX", sector = "Finance", market = "ASX", leverage = 1.0 },
      # ... 22 more
  ]
  trading_hours = "10:00-16:00 AEDT"
  fx_cost_bps_per_month = 25
  is_isa_eligible = false
  overnight_only = true  # Trade during ASX hours (23:00-05:00 UTC)
  ```
- **Overnight execution**: Setup separate monitoring for 23:00-05:00 UTC session

#### 32B: Geopolitical Monitoring Integration (6 hours)
- **Implement NewsAPI integration**:
  ```python
  # Daily scan at 07:00 UTC (before LSE open)
  from newsapi import NewsApiClient

  def scan_geopolitical_risk():
      client = NewsApiClient(api_key='...')

      # Monitor keywords per market
      risks = {}
      for market, keywords in {
          'EURONEXT': ['france', 'macron', 'eu sanctions'],
          'ASX': ['china', 'australia trade', 'aud'],
          'TSE': ['china', 'japan tensions', 'korea'],
      }.items():
          articles = client.get_everything(
              q=' OR '.join(keywords),
              sources='bloomberg,reuters',
              from_param=date.today() - timedelta(days=1),
              language='en',
              sort_by='relevancy',
          )
          risk_score = sentiment_analysis(articles)
          risks[market] = risk_score

      # Update risk levels
      for market, score in risks.items():
          if score > 0.7:
              geo_risk_mgr.set_level(market, 'HIGH')
          elif score > 0.4:
              geo_risk_mgr.set_level(market, 'MEDIUM')
          else:
              geo_risk_mgr.set_level(market, 'LOW')

      log_geopolitical_report(risks)
  ```
- **Position multiplier applied**:
  - LOW risk (score <0.4): 1.0x size
  - MEDIUM risk (0.4-0.7): 0.7x size (30% reduction)
  - HIGH risk (>0.7): 0.3x size (70% reduction)
  - HALT (market closed by regulator): 0.0x (no trading)

#### 32C: Monitoring + Alerts (4 hours)
- **Dashboard showing**:
  - Geopolitical risk per market (color-coded: green/yellow/red)
  - Current position multiplier per market
  - Recent news snippets
  - Risk alert history
- **Alert triggers**:
  - ✅ Risk escalates from LOW → MEDIUM: email alert
  - ✅ Risk escalates to HIGH: SMS + email + Slack
  - ✅ Market trading halts detected: immediate halt all positions in that market

#### 32D: Optional TSE Addition (2 hours, conditional)
- **Gate**: Only if DQN succeeded (Week 10) AND geopolitical monitoring stable (Week 17)
- **Implementation** (if gates pass):
  ```toml
  [tse_core]
  count = 50
  assets = [
      { ticker = "7203.T", sector = "Automotive", market = "TSE" },
      { ticker = "6758.T", sector = "Tech", market = "TSE" },
      # ... 48 more
  ]
  trading_hours = "09:00-15:00 JST"
  fx_cost_bps_per_month = 30
  geopolitical_risk = "MEDIUM"  # China-Japan tensions
  dqn_preferred = true  # Use DQN for regime learning
  ```
- **Expected benefit**: -0.02% daily WITHOUT DQN, but +0.02% WITH DQN (regime learning helps)

**Deliverable (Week 18)**:
- ✅ ASX 25 stocks deployed, paper tested
- ✅ Geopolitical monitoring live, integrated with position sizing
- ✅ Euronext + ASX combined: +0.025-0.03% daily confirmed
- ✅ TSE optional (approved only if DQN validated)
- ✅ Full hybrid system ready for scale-up

---

## PART IV: SYSTEM ARCHITECTURE & WIRING

### Full Data Flow (All Phases Integrated)

```
┌─────────────────────────────────────────────────────────────────┐
│                    REAL-TIME DATA FEEDS                         │
├─────────────────────────────────────────────────────────────────┤
│  IBKR (Primary)    │  yfinance (Secondary)  │  Polygon (Tertiary) │
│  LSE/NYSE/EUR/ASX  │  15-min delayed        │  1-min real-time    │
│  <100ms latency    │  99% uptime            │  99.5% uptime       │
└──────────────┬──────────────┬──────────────┬──────────────────────┘
               │              │              │
               └──────────────┴──────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Feed Manager      │
                    │  (Route by Market)  │
                    └──────────┬──────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
      ┌─────────┐       ┌─────────┐       ┌─────────────┐
      │ Phase 5 │       │ Phase 6 │       │ DQN Feature │
      │ Regime  │       │Volatility│       │  Extraction│
      │Detection│       │ Scaler  │       │(Phase 26-27)│
      └────┬────┘       └────┬────┘       └──────┬──────┘
           │                 │                   │
           └─────────────────┼───────────────────┘
                             │
                             ▼
                    ┌──────────────────────┐
                    │  Phase 7: Confidence │
                    │    Scorer (8-ind)    │
                    │   + DQN Alternative  │
                    │   (Phase 29, if live)│
                    └──────────┬───────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │  Phase 4: White      │
                    │  Reality Check (DSR) │
                    │  + Phase 8: Pre-Cond │
                    │       Gates          │
                    └──────────┬───────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │  Phase 9: Position   │
                    │   Sizer (Kelly 12)   │
                    │  Leverage Prioritize │
                    └──────────┬───────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │  Phase 10: Execution │
                    │    Quality Check     │
                    └──────────┬───────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
        ▼                     ▼                      ▼
    ┌─────────┐        ┌──────────────┐     ┌──────────────┐
    │Phase 30 │        │ Phase 2: ISA │     │ Phase 3: FX  │
    │ FX Adj  │        │   Auditor    │     │  Manager     │
    └────┬────┘        └──────┬───────┘     └──────┬───────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │ Phase 31: Geopolitical
                    │    Risk Manager      │
                    │ (Position Multiplier)│
                    └──────────┬───────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │  Phase 15: Order     │
                    │    Router (IBKR)     │
                    │    Submission        │
                    └──────────┬───────────┘
                              │
                              ▼
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
        ▼                     ▼                      ▼
    ┌─────────┐        ┌──────────────┐     ┌──────────────┐
    │Phase 19 │        │Phase 20: ISA │     │Phase 22-24:  │
    │Risk Mgr │        │ Reconciliator│     │Ouroboros (ML)│
    │Stops    │        │ (every 5 min)│     │ Nightly      │
    │Hedging  │        │              │     │              │
    └────┬────┘        └──────┬───────┘     └──────┬───────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │   DATABASE/LOGGING   │
                    │  Position tracking   │
                    │  P&L attribution     │
                    │  Model parameters    │
                    │  Alert history       │
                    └──────────────────────┘
```

### Phase Dependencies (Critical Wiring)

```
STARTUP (Week 0):
├─ Phase 1: Capital Preservation (Kelly limits)
├─ Phase 2: ISA Auditor initialized
├─ Phase 26: Feature pipeline ready (if DQN enabled)
└─ Phase 30: Multi-exchange infrastructure online

INTRADAY (Loop every 100ms):
├─ Feed Manager routes ticks to correct market module
├─ Phase 5: Regime detection (feeds Phase 6, 7, 9)
├─ Phase 6: Volatility scaler (feeds Phase 9)
├─ Phase 7: Confidence scorer (feeds Phase 4)
├─ Phase 26 (if enabled): DQN feature extraction (feeds Phase 29)
├─ Phase 4: White Reality Check (gates signal, feeds Phase 8)
├─ Phase 8: Pre-Conditions Gate (feeds Phase 9)
├─ Phase 9: Position Sizer (feeds Phase 10)
├─ Phase 10: Execution Quality (feeds Phase 15)
├─ Phase 30: FX Adjustment (reduces position size if needed)
├─ Phase 31: Geopolitical multiplier (reduces position size if needed)
├─ Phase 15: Order Router (submits to IBKR)
├─ Phase 3: Compliance Gates (blocks order if violations)
├─ Phase 19: Risk Manager (sets stops)
└─ Phase 20: ISA Auditor (every 5 min, halts if violations)

NIGHTLY (22:00-23:50 UTC):
├─ Phase 23: Performance Attribution (fetches trades from day)
├─ Phase 22: DQN Signal Weighting (retrains if new data significant)
├─ Phase 24: ML Adaptation (updates thresholds)
├─ Phase 25: Live Orchestrator (commits all changes)
└─ Universe Scan: Scores all 1,770+ assets for next day

DECISION GATES (Critical):
├─ Week 9: Phase 29 DQN Go/No-Go
│   ├─ IF passes: Enable Phase 29 DQN in Phase 7
│   └─ IF fails: Continue with 8-indicator, proceed to Phase 30
├─ Week 14: Phase 31 Euronext Go/No-Go
│   ├─ IF passes: Live trade Euronext
│   └─ IF fails: Close Euronext, investigate
├─ Week 18: Phase 32 ASX + Geopolitical Go/No-Go
│   └─ IF passes: Live trade ASX with geopolitical monitoring
└─ Week 19: Phase 32D TSE Optional (only if DQN validated)
```

---

## DETAILED 63-DAY EXECUTION SCHEDULE

### WEEK 1-2: Bootstrap + DQN Feature Engineering

| Day | Phase | Task | Owner | Hours | Deliverable |
|-----|-------|------|-------|-------|-------------|
| 1 | 0 | Finalize blueprint, EC2 setup | Infra | 8 | Live EC2, Rust engine up |
| 2 | 1-3 | Kelly 12-factor, ISA gate, compliance | Quant | 8 | Phase 1-3 live |
| 3-4 | 26A | OHLCV candle extraction | ML | 16 | 2 years LSE candles ready |
| 5 | 26B | Orderbook microstructure | ML | 8 | Bid-ask data extracted |
| 6 | 26C | Volatility + liquidity metrics | ML | 8 | Vol/Amihud data ready |
| 7 | 26D | Time-of-day + sector momentum | ML | 8 | Context features ready |
| 8 | 26E | Dataset pipeline assembly | ML | 8 | PyTorch dataset ready |
| 9-10 | 4-10 | White Reality, Regime, Volatility, Confidence, Execution Quality | Quant | 16 | Phases 4-10 live |
| 11-14 | 11-21 | Operational phases (routing, positions, exits, reconciliation) | Infra | 32 | Phases 11-21 live |

**Week 2 Deliverable**: All 26 base phases + DQN features ready, paper trading begins

### WEEK 3-5: DQN Transformer Training

| Day | Phase | Task | Owner | Hours | Deliverable |
|-----|-------|------|-------|-------|-------------|
| 15-19 | 27A | Transformer encoder architecture + training | ML | 40 | Transformer trained, Sharpe ≥1.0 |
| 20-24 | 27B | DQN value network architecture + training | ML | 40 | DQN trained on 100k episodes |
| 25-27 | 27C | DQN training loop (epsilon decay, replay buffer) | ML | 30 | Training pipeline stable |
| 28-35 | 27D | Regime-specific DQN heads (5 models) | ML | 30 | 5 regime models trained |

**Week 5 Deliverable**: DQN fully trained, regime-ensemble ready, Sharpe ≥1.0 confirmed

### WEEK 6-8: Validation + Paper Trading

| Day | Phase | Task | Owner | Hours | Deliverable |
|-----|-------|------|-------|-------|-------------|
| 36-40 | 28A | Walk-forward testing (4 folds) | ML | 20 | Walk-forward Sharpe ≥0.8 |
| 41-45 | 28B | Deflated Sharpe ratio calculation | ML | 15 | DSR ≥0.7 confirmed |
| 46-50 | 28C | Regime-specific performance validation | ML | 10 | Per-regime Sharpe ≥0.5 |
| 51-56 | 28D | Live paper trading with dual signals | Trader | 24 | 100+ trades, accuracy ≥55% |
| 57-60 | 1-25 | Base system paper trading (main thread) | Trader | 24 | 100+ LSE trades, WR ≥40% |

**Week 8 Deliverable**: All validation gates ready, decision made on DQN

### WEEK 9: Decision + Integration

| Day | Phase | Task | Owner | Hours | Deliverable |
|-----|-------|------|-------|-------|-------------|
| 61-63 | 29A-E | DQN integration, go/no-go decision | ML/Infra | 20 | DQN live or rolled back |
| 64 | 30 | Multi-exchange infrastructure (clocks, FX, Geopolitical) | Infra | 20 | Markets ready |

**Week 9 Deliverable**:
- ✅ Base system validated (Phase 1-25)
- ✅ DQN validated OR rolled back (Phase 26-29)
- ✅ Multi-exchange infrastructure ready (Phase 30)
- ✅ Ready for live trading + Euronext prep

### WEEK 10: Live Trading Begins

**Go-Live with:**
- Base system (8-indicator) + DQN (if validated)
- UK/US/Asia markets
- £10,000 capital, 5-10% position size max
- Daily monitoring, weekly reviews

### WEEKS 11-14: Euronext Expansion

| Week | Phase | Task | Owner | Hours | Deliverable |
|------|-------|------|-------|-------|-------------|
| 11-12 | 31A-D | Asset universe, signal eval, universe scan, paper trading | Trader/Infra | 20 | 50+ Euronext trades |
| 13-14 | Validation | Live Euronext deployment | Trader | - | +0.015-0.02% daily confirmed |

### WEEKS 15-18: ASX + Geopolitical Monitoring

| Week | Phase | Task | Owner | Hours | Deliverable |
|------|-------|------|-------|-------|-------------|
| 15-16 | 32A | ASX universe, paper trading | Trader/Infra | 12 | 30+ ASX trades |
| 17-18 | 32B-D | Geopolitical monitoring, alerts, optional TSE | Risk/ML | 6 | Full geopolitical hedging live |

**Week 18 Deliverable**: Full hybrid system live
- Base + DQN (if validated)
- Euronext (+0.02% daily)
- ASX (+0.01% daily)
- Geopolitical monitoring
- Optional TSE (if gates pass)

---

## VALIDATION GATES (GO/NO-GO CRITERIA)

### Gate 1: Base System Validation (End of Week 9)
```
MUST PASS:
├─ 100+ paper trades executed
├─ Win rate ≥40% (all regimes)
├─ Max drawdown <-8%
├─ ISA audit 100% compliant
├─ Data feed uptime ≥99.9%
└─ Ouroboros nightly cycle stable

CANNOT PROCEED TO LIVE WITHOUT THESE.
```

### Gate 2: DQN Validation (End of Week 9)
```
MUST PASS (all):
├─ Backtest Sharpe ≥1.0
├─ Walk-forward Sharpe ≥0.8
├─ Deflated Sharpe ≥0.7
├─ Per-regime Sharpe ≥0.5 (all 5 regimes)
├─ Paper trading predictive accuracy ≥55%
├─ Inference latency <50ms
└─ Memory/CPU stable over 100+ trades

IF PASS: Deploy DQN as primary (with 8-indicator fallback)
IF FAIL: Continue with 8-indicator, skip DQN overhead
(EITHER WAY, PROCEED TO EURONEXT)
```

### Gate 3: Euronext Validation (End of Week 14)
```
MUST PASS:
├─ 50+ paper trades executed
├─ Win rate ≥40%
├─ Data feed uptime ≥99.8%
├─ Execution latency <500ms
└─ FX costs = 15 bps/month verified

IF PASS: Go live with Euronext
IF FAIL: Investigate, retry paper trading
(CANNOT GO LIVE WITHOUT THESE)
```

### Gate 4: ASX Validation (End of Week 18)
```
MUST PASS:
├─ 30+ paper trades executed
├─ Win rate ≥35% (lower expected due to overnight/FX)
├─ Geopolitical monitoring stable (<1 alert/day)
├─ Position multiplier logic verified
└─ Overnight monitoring system robust

IF PASS: Go live with ASX
IF FAIL: Keep ASX in paper only
```

### Gate 5: TSE Optional (Week 19+)
```
CONDITIONAL (only if ALL true):
├─ DQN win rate ≥45% for 4+ weeks (proved valuable)
├─ Geopolitical manager proved valuable (position multipliers helped)
└─ Infrastructure stable (no major incidents)

IF ALL TRUE: Deploy TSE (with DQN regime learning)
IF ANY FALSE: Skip TSE, focus on scaling to £100M AUM
```

---

## RISK MITIGATION & FAILSAFES

### DQN Safeguards
1. **Always keep 8-indicator as fallback**
   - Log both signals on every tick
   - Alert if DQN diverges >20% from 8-indicator confidence
   - Auto-revert to 8-indicator if DQN win rate <40% over rolling 50 trades

2. **Ensemble with regime-specific models**
   - 5 separate DQN heads (one per regime)
   - Reduces overfitting to single regime

3. **Confidence calibration**
   - Q-values must stay in [-1, +1]
   - Alert if mean drifts >0.2 from 0 (possible degradation)
   - Force retraining if alert lasts >2 weeks

4. **Monthly retraining**
   - Retrain every 4 weeks on latest 6 months
   - Walk-forward validate before going live
   - Auto-rollback if new model underperforms

### Geopolitical Safeguards
1. **NewsAPI monitoring**
   - Daily scan at 07:00 UTC
   - Keyword-based risk scoring
   - Human-in-loop review (trader reviews flagged markets daily)

2. **Position multipliers**
   - LOW risk (score <0.4): 1.0x
   - MEDIUM risk (0.4-0.7): 0.7x (auto-applied)
   - HIGH risk (>0.7): 0.3x (auto-applied)
   - HALT: 0.0x (human decision required)

3. **Trading halt protection**
   - Monitor official halt feeds per market
   - If >3 halts/hour: reduce positions 50%
   - Reset when <1 halt/hour for 60 min

4. **FX hedging contingency**
   - Primary: Forward contracts via IBKR (15-30 bps/month)
   - Fallback: Inverse ETPs if forwards illiquid
   - Circuit breaker: If FX cost >25 bps/month → close non-primary market positions

### Global Liquidity Safeguards
1. **Liquidity failover**
   - Primary: Euronext direct
   - Secondary: IBKR dark pool (slower, better price)
   - Abort: If expected slippage >50 bps, skip trade

2. **Spread monitoring**
   - Euronext CAC 40: <30 bps target
   - ASX: 30-100 bps depending on stock
   - Alert if spread >2× historical average

---

## MONITORING & DAILY REPORTS

### Automated Monitoring (24/7)
```python
# Pseudo-code for monitoring dashboard
class SystemMonitor:
    def __init__(self):
        self.alerts = []

    def check_every_tick(self):
        # Data feed health
        check_ibkr_latency()  # Alert if >200ms
        check_ibkr_staleness()  # Alert if >2 min stale
        check_yfinance_staleness()  # Alert if >20 min stale

        # DQN health (if live)
        check_dqn_inference_latency()  # Alert if >100ms
        check_dqn_q_values()  # Alert if distribution drifts
        check_dqn_vs_8indicator()  # Alert if divergence >20%

        # Risk health
        check_portfolio_heat()  # Alert if >3.5% daily
        check_margin_debt()  # Alert if >0 (should be zero)
        check_circuit_breaker_status()  # Alert if triggered

    def check_every_5min(self):
        # ISA compliance
        check_isa_compliance()  # HALT if any violation
        check_trading_halts()  # Alert if frequent

    def check_every_hour(self):
        # Geopolitical risk
        check_geopolitical_risk()  # Update position multipliers
        check_liquidity()  # Alert if spreads > 2x average

    def generate_daily_report(self):
        return {
            'base_system_stats': {
                'trades': count,
                'win_rate': wr,
                'pnl': daily_pnl,
                'max_heat': max_heat,
                'data_uptime': uptime,
            },
            'dqn_stats': {
                'inference_latency_avg': latency,
                'accuracy': accuracy,
                'q_value_distribution': (mean, std),
                'vs_8indicator_divergence': div,
            },
            'global_market_stats': {
                'euronext_contribution': eur_pnl,
                'asx_contribution': asx_pnl,
                'fx_costs': fx_daily,
            },
            'geopolitical_risk': {
                'euronext': risk_level,
                'asx': risk_level,
                'tse': risk_level,
            },
            'alerts': self.alerts,
        }
```

### Daily Report (08:00 UTC, Post-Close)
```
═══════════════════════════════════════════════════════════════
AEGIS V2 HYBRID DAILY REPORT — 2026-03-13 (Thursday)
═══════════════════════════════════════════════════════════════

BASE SYSTEM (Phases 1-25):
  Trades executed: 47
  Win rate: 42.6% (target ≥40%) ✅
  Daily P&L: +£182 (+1.82%)
  Max single loss: -£28 (stopped)
  Portfolio heat: 2.1% / 3.5% cap
  Data uptime: 99.95%
  ISA audit: PASSED ✅

DQN SYSTEM (Phases 26-29, if live):
  Inference latency: 32ms (target <50ms) ✅
  Predictive accuracy: 56% (baseline 52%)
  Q-value mean: 0.02 (drift <0.2) ✅
  vs 8-indicator divergence: 8% (threshold 20%) ✅
  Fallback events: 0
  Status: OPERATIONAL ✅

EURONEXT (Phase 31):
  Status: PAPER TRADING (Week 13/14)
  Trades executed: 12
  Win rate: 41.7%
  FX cost realized: 14 bps/month (vs 15 budgeted) ✅
  Expected contribution: +0.02% daily

ASX (Phase 32):
  Status: PAPER TRADING (Week 15/18)
  Expected live: Week 17

GEOPOLITICAL MONITORING (Phase 32B):
  Euronext risk: LOW (1.0x multiplier)
  ASX risk: LOW (1.0x multiplier)
  TSE risk: MEDIUM (0.7x if deployed)
  Recent alerts: None

ALERTS:
  [None]

NEXT ACTIONS:
  Week 10: Go live with base + DQN (if validated)
  Week 13: Deploy Euronext (confirmed)
  Week 17: Deploy ASX (pending validation)

═══════════════════════════════════════════════════════════════
```

---

## FINAL EXECUTION CHECKLIST

### Before Week 1
- [ ] Finalize AEGIS_V2_COMPLETE_EXECUTION_BLUEPRINT
- [ ] Provision EC2 (2 vCPU, 4GB RAM, 100GB SSD)
- [ ] Set up Docker Compose (Rust engine, IBKR, Redis)
- [ ] Setup monitoring dashboard
- [ ] Obtain NewsAPI key for geopolitical monitoring
- [ ] Verify IBKR connection (paper mode enabled)
- [ ] Verify all ISA eligible assets whitelist loaded
- [ ] Create deployment checklists for Euronext + ASX

### During Week 1-2
- [ ] Phases 1-3 online (Kelly, ISA, compliance)
- [ ] Phase 26 feature extraction complete
- [ ] Paper trading starts (8-indicator + DQN features in parallel)

### During Week 3-5
- [ ] Phase 27 DQN training progressing (Sharpe ≥1.0 target)
- [ ] Continue base system paper trading (hit 100-trade gate)

### During Week 6-8
- [ ] Phase 28 validation gates checked weekly
- [ ] Base system wins 100+ trades, WR ≥40%
- [ ] DQN walks forward, DSR ≥0.7 confirmed
- [ ] Paper trading accuracy ≥55% confirmed

### During Week 9
- [ ] Phase 29 go/no-go decision MADE
- [ ] If go: DQN deployed with 8-indicator fallback
- [ ] If no-go: Continue with 8-indicator, proceed
- [ ] Phase 30 infrastructure tested
- [ ] Ready to go live week 10

### Week 10+
- [ ] Go live with base system + optional DQN
- [ ] Deploy Euronext (weeks 11-14)
- [ ] Deploy ASX + geopolitical (weeks 15-18)
- [ ] Optional TSE (week 19+ if gates pass)
- [ ] Scale to £100M AUM (ongoing)

---

## EXPECTED OUTCOMES

### Conservative Case (DQN Fails, Markets Work)
- Base system: 0.45% daily
- Euronext: +0.015% daily
- ASX: +0.008% daily
- **Total**: 0.473% daily (149% CAGR)
- Effort: 180h base + 58h markets = 238h

### Realistic Case (DQN Partial, All Markets)
- Base system: 0.45% daily
- DQN: +0.005% daily (overfitting worse than expected)
- Euronext: +0.015% daily
- ASX: +0.008% daily
- **Total**: 0.478% daily (152% CAGR)
- Effort: 458h total

### Optimistic Case (DQN Works, All Markets + TSE)
- Base system: 0.45% daily
- DQN: +0.01% daily
- Euronext: +0.02% daily
- ASX: +0.01% daily
- TSE: +0.02% daily (with DQN regime learning)
- **Total**: 0.51% daily (168% CAGR)
- Effort: 458h + 20h TSE = 478h

**On £100M AUM:**
- Conservative: £119M annual gross (vs £113M baseline)
- Realistic: £120.5M annual gross (+£7.1M)
- Optimistic: £128.5M annual gross (+£15.1M)

---

## APPROVAL GATE

**This unified hybrid plan is ready for execution starting Monday, March 17, 2026.**

All phases wired, all gates specified, all deliverables defined.

**Execute immediately?** Y/N

