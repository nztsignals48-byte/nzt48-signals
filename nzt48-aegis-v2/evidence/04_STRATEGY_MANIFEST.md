# AEGIS V2 — Complete Strategy Manifest

**Audit Date:** 2026-04-04 | **Commit:** Session 22

---

## Signal Generator Census

The live system (bridge.py `_generate_signals()`) has **34 signal generators**. The backtest exercises **14** of these (the bar-compatible subset). The remaining **20** require real-time tick data, L2 order book, cross-instrument state, or live ML model state.

## Backtest-Exercised Strategies (14)

| # | Strategy | Book(s) | Entry Logic | Status | Base Conf | Cooldown |
|---|----------|---------|------------|--------|-----------|----------|
| 1 | TypeA (DipRecovery) | entry_engine.rs | RSI < 40 + vol spike > 1.8x MA20 + 2 ATR drop | **ACTIVE** | 65% | 10 bars |
| 2 | TypeB (EarlyRunner) | entry_engine.rs | 3-bar rising RVOL + RSI 30-70 | **ACTIVE** | 82% | 5 bars |
| 3 | TypeD (SupportBounce) | entry_engine.rs | Price within 1% of daily low + RSI 20-40 | **ACTIVE** | 80% | 10 bars |
| 4 | TypeE (IBSMeanReversion) | Connors & Alvarez 2008 | IBS < 0.10 + RVOL > 1.0 | **ACTIVE** | 70% | 8 bars |
| 5 | TypeF (OBVDivergence) | OBV theory | OBV-RSI(5) < 30 + RVOL > 0.7 | **ACTIVE** | 68% | 8 bars |
| 6 | S2_Reversion | BB z-score | z < -1.5 + RSI(2) < 20, non-trending regime | **ACTIVE** | 62% | 10 bars |
| 7 | S3_MacroTrend | Moskowitz-Ooi-Pedersen | SMA5 > SMA20 + 12-bar mom > 0.5%, non-MR regime | **ACTIVE** | 60% | 20 bars |
| 8 | S5_OvernightCarry | Book 40 | Gap down >1% + IBS < 0.20 at day boundary | **ACTIVE** | 64% | 20 bars |
| 9 | VolCompression | Book 22 | Keltner squeeze score >= 0.7 + upward breakout | **ACTIVE** | 74% | 40 bars |
| 10 | FOmcDrift | Book 24 | 3rd Wed of FOMC months, 18:00-20:00 UTC | **ACTIVE** | 66% | 20 bars |
| 11 | NAVArbitrage | Book 132 | SMA20 discount > 2% + RVOL > 0.5, LSE .L only | **ACTIVE** | 62% | 15 bars |
| 12 | VolExpansion | bridge.py | RVOL > 2.0 + ADX proxy > 20 + 3+ up bars | **ACTIVE** | 66% | 8 bars |
| 13 | GapFade | bridge.py | Gap down > 1% + RVOL < 2.0 (liquidity gap) | **ACTIVE** | 60% | 10 bars |
| 14 | NightRider | Book 5 | Late-session decline > 1.5% + RVOL > 1.5 | **ACTIVE** | 62% | 15 bars |

## Live-Only Strategies (20 — require real-time data)

| # | Strategy | Book(s) | Module | Why Not Backtestable |
|---|----------|---------|--------|---------------------|
| 15 | VanguardSniper (Momentum) | -- | bridge.py inline | Needs real-time tick scoring |
| 16 | Orchestrator | -- | bridge.py inline | Multi-strategy meta-signal |
| 17 | IBS_MeanReversion (live) | Sprint C | bridge.py inline | Uses RSI-2 on 5s bars |
| 18 | ORB_Breakout | -- | bridge.py inline | Needs real-time US session open |
| 19 | GapFade (live) | -- | brain.gap_detector | Needs real-time gap classification |
| 20 | S4_VolPremium | VIX-driven | bridge.py inline | Needs live VIX feed |
| 21 | S7_TailHedge | Crisis hedge | bridge.py inline | Needs live VIX + regime |
| 22 | FOMC_PreDrift | Book 5 | bridge.py inline | Needs live event context |
| 23 | RebalancingFlow | Book 36 | strategies.rebalancing_flow | Needs real-time underlying returns |
| 24 | AlphaFactory | Books 121, 168 | alphas.alpha_factory | Runs in backtest (slow) |
| 25 | LeadLag | Books 77, 136 | strategies.lead_lag | Needs cross-instrument state |
| 26 | EMAT_Attention | Book 102 | ml.emat_model | Needs accumulated state |
| 27 | TemporalAttention | Book 157 | ml.attention_trading | Needs sequence state |
| 28 | SwarmPredictor | Book 151 | ml.swarm_predictor | Multi-agent simulation |
| 29 | HFT_Probability | Book 204 | strategies.hft_probability | Needs L2 order book |
| 30 | NegRiskArb | Book 206 | strategies.negrisk_arbitrage | Needs live tracking error |
| 31 | HighFlyer | Book 166 | ml.high_flyer_strategies | Needs retail flow data |
| 32 | PairsReversion | Books 125/126 | strategies.pairs | Needs cross-pair state |
| 33 | CointPairs | Book 125 | strategies.pairs_cointegration | Needs cointegration tracking |
| 34 | EventDrift | Book 24 | strategies.fomc_drift | Needs live event calendar |

Plus 5 more in bridge.py: CopyTrading (Book 203), LatencyArbitrage (Book 195), MacroNowcast (Book 84), MultiLegArb (Book 206), PairsStatArb (Book 168), NightRider (Book 5).

## Disabled Strategies (with evidence)

| Strategy | WR | PF | Reason | Decision |
|----------|----|----|--------|----------|
| TypeC (OverboughtFade) | 39.0% | 0.805x | Short-side fades conflict with ISA long-only | DISABLED |
| S1_Microstructure | 40.1% | 0.532x | Bar-level tick proxy too noisy | DISABLED — re-enable with IBKR L2 |
| S6_Catalyst | 13.0% | 0.007x | Gap continuation mean-reverts in liquid markets | AUTO-KILLED |

## Pre-Signal Quality Gates (25+ checks in bridge.py)

These run BEFORE any signal generator fires:

| Gate | Book | What It Does |
|------|------|-------------|
| VPIN toxicity | 162 | Block when informed flow > 0.80 |
| Liquidity pulse | 117 | Detect spoofing/manipulation |
| Micro-regime | 83 | Block TOXIC microstructure |
| Break-even vol | 46 | Block 3x ETP when vol > break-even |
| Turnover budget | 81 | Block if daily trade limit exceeded |
| TDA crash detector | 127 | Block if crash P > 0.70 |
| Adversarial detection | 103 | Block spoofing/wash trading |
| IBKR resilience | 44 | Block if broker degraded |
| Data quality | 176 | Block suspicious ticks |
| Structural break | 48 | Raise floor after structural break |
| Safety boundary | 190 | Block if sacred limits breached |

## Post-Signal Modifiers

| Modifier | Book | Effect |
|----------|------|--------|
| Calendar anomalies | 171 | +/- confidence by DOW, TOM, holiday |
| Squeeze boost | 22 | +15 confidence on squeeze release |
| TOM boost | 171 | +6 to +10 confidence T-1 to T+3 |
| Breakout validation | 22 | +10 if all 3 criteria met (68% WR) |
| Fractional diff | 135 | +2/-3 based on FD direction |
| LightGBM classifier | 23 | +5/-8 based on P(win) |
| MI reweighting | 119 | Dynamic indicator weight adjustment |
| Momentum adversarial | 170 | -5 for HFT front-running risk |

## ML Modules (40 files, numpy-only)

| Module | Book | Implementation |
|--------|------|----------------|
| LightGBM Classifier | 23 | 48-feature ONNX entry classifier |
| Conformal Signals | 144 | Distribution-free prediction intervals |
| Mamba/S4 | 161 | Structured State Space (numpy) |
| GNN Market Structure | 96 | Graph Convolution + Attention (numpy) |
| Gaussian Process | 114 | GP regression with RBF/Matern kernels |
| Reservoir Computing | 129 | Echo State Networks (numpy) |
| TDA Crash Detector | 127 | Topological crash detection (numpy) |
| Path Signatures | 128 | Path signature features (numpy) |
| Constrained PPO | 213 | RL agent for timing (stdlib + numpy) |
| Swarm Predictor | 151 | Multi-agent simulation (numpy) |
