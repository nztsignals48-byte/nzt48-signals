# NZT-48 Scope Alignment Audit

**Date:** 2026-02-27
**Auditor:** System Architecture Review
**Core Mission:** CORE-first leveraged ISA intraday trading via the 2% daily compounding strategy (S15)
**Target:** Paper-to-live migration readiness for UK ISA leveraged ETP universe

---

## Classification Criteria

| Label | Meaning | Action |
|---|---|---|
| **KEEP** | Directly supports ISA leveraged ETP trading, signal generation, risk management, or operational safety | Retain as-is. Required for paper-to-live. |
| **QUARANTINE** | Useful but not critical for paper-to-live migration. Can be re-enabled later. | Disable/gate behind feature flag. Do not delete. |
| **REMOVE** | Dead code, delisted ticker references, US-only features with no ISA mapping, abandoned experiments | Delete from codebase and config. |

---

## 1. Strategies (S1-S15)

### S15 -- Daily Target 2% (daily_target.py)

**Classification: KEEP**

The compounding machine. This is the core mission strategy. Scans all tickers, ranks by 2% reachability, emits one signal per day for the best candidate. Already ISA-native: hardcoded CORE_TICKERS are the 12 ISA ETPs, direction is LONG-only (ISA buy-only constraint enforced at line 211), inverse ETPs handled correctly. Runner mode activates for CORE tickers in strong regimes. No changes needed.

### S12 -- Rebalance Flow (rebalance_flow.py)

**Classification: KEEP**

Described in its own docstring as "THE KEY SIGNAL for Bot A (ISA)." Exploits forced sponsor delta-hedge rebalancing at 19:00 UK when underlying moves >1.5%. Directly maps to ISA ETP instruments. Entry window 14:30-16:00 GMT is the US-LSE overlap. This is a high-edge ISA-native strategy.

### S1 -- Regime Trend Following (regime_trend.py)

**Classification: KEEP**

Trend-following on pullbacks. Strategy is ticker-agnostic (operates on whatever tickers are passed to `scan()`). Works with ISA universe when fed .L tickers. Assigned to Bull-Bot and Bear-Bot. Provides trend context that S15 relies on for regime assessment.

### S2 -- Momentum Breakout (momentum_breakout.py)

**Classification: KEEP**

BB squeeze release + volume spike. Ticker-agnostic. Fires at US open (14:30 UK) which aligns with ISA ETP trading hours. Assigned to Bull-Bot. Detects explosive moves that S15 also looks for but from a different angle (squeeze mechanics vs. reachability scoring).

### S3 -- Mean Reversion (mean_reversion.py)

**Classification: QUARANTINE (already DORMANT)**

Already disabled via `_STRATEGY_DORMANT = True` at line 52. Preserved for range-bound regimes. The V2.0 upgrade correctly moved to momentum-first. Confirm dormancy is respected. No action needed beyond verification.

### S4 -- Catalyst Narrative (catalyst_narrative.py)

**Classification: QUARANTINE**

News-driven strategy relying on Layer 5 (Narrative). Requires NewsAPI headlines matching to ticker-specific catalysts. Problem: news sentiment for ISA leveraged ETPs is indirect (the ETP itself rarely has news; the underlying does). The ISA mapping from underlying catalyst to ETP ticker adds latency and complexity. Not needed for paper-to-live. Re-enable when underlying-to-ETP catalyst mapping is validated.

### S5 -- PEAD / Post-Earnings Announcement Drift (pead_earnings.py)

**Classification: QUARANTINE**

Fires on earnings gaps >5%. Problem: leveraged ETPs on LSE do not have "earnings." The underlying stocks do, but the earnings gap on the ETP is filtered through leverage mechanics and LSE hours (earnings typically reported pre-US-market). The ETP may not gap the same way. Assigned to Earnings-Bot which activates in quarterly windows. Not needed for paper-to-live. Park for future underlying-to-ETP drift study.

### S6 -- Macro Regime Shift (macro_regime.py)

**Classification: KEEP**

Monitors DXY, yields, gold, oil for macro rotations. Maps directly to QQQ3/QQQS leveraged ETPs. This is a regime-level signal that informs all other strategies and the inverse ETP selection (risk-off = buy QQQS.L, 3USS.L). Critical for knowing when to be in long vs. inverse ETPs.

### S7 -- Sector Rotation (sector_rotation.py)

**Classification: KEEP**

Weekly RS ranking of sector ETFs vs SPY. Maps directly to ISA instruments: semis strong -> 3SEM.L, tech strong -> QQQ3.L, semis weak -> SC3S.L. Weekly rebalance cadence (Sunday scan) is operationally clean. Feeds the strategic allocation layer.

### S8 -- Volatility Crush (vol_crush.py)

**Classification: QUARANTINE**

Buys highest-beta names after VIX spike recedes. Strategy targets individual stocks (TSLA, SMCI, AMD) not leveraged ETPs. The ISA mapping is indirect (TSLA drop -> buy TSL3.L on recovery). Logic is sound but the VIX -> ETP recovery timing needs validation. Not blocking paper-to-live.

### S9 -- Pairs Trading (pairs_trade.py)

**Classification: QUARANTINE**

Z-score mean reversion on defined pairs. Three of four pairs are US equities (NVDA/AMD, AVGO/MRVL, TSM/ASML). The ISA pair (QQQ3/QQQS) is interesting but requires both legs executed simultaneously on T212 with no shorting -- both are BUY orders which is correct for ISA. However, this is a market-neutral strategy that contradicts the directional 2% daily target mission. Park for later exploration.

### S10 -- AI Thematic (ai_thematic.py)

**Classification: QUARANTINE**

Monitors SOXX/SMH/BOTZ relative strength. Focus tickers are US equities: `_AI_TICKERS = {"NVDA", "AVGO", "MRVL", "ARM"}`. No ISA ticker awareness. Would need refactoring to map AI theme strength to GPT3.L, 3SEM.L, NVD3.L. Not blocking paper-to-live.

### S11 -- Hot Stock Scanner (hot_scanner.py)

**Classification: QUARANTINE**

Finviz screener for pre-market gappers. Entirely US equity focused (Finviz covers NYSE/NASDAQ only). No LSE coverage. No path to ISA ETP scanning. Useful only for Bot B (US equities). Complete mismatch with ISA-first mission.

### S13 -- Trend Compounding (trend_compound.py)

**Classification: KEEP**

Multi-day swing holds (1-5 days) on 10-week EMA + ADX. Docstring explicitly states "Bot A maps to 3x ETPs for leveraged trend compounding in ISA." Ticker-agnostic. Complements S15 (intraday) with a swing layer for trending regimes. Important for capturing multi-day moves that exceed the 2% daily target.

### S14 -- Gamma Squeeze (gamma_squeeze.py)

**Classification: QUARANTINE**

Requires GEX (gamma exposure) data which comes from SqueezeMetrics. GEX applies to US options market. LSE leveraged ETPs have no options chain; their gamma exposure is a derivative of the underlying's GEX. The causal chain (negative GEX on NVDA options -> amplified move -> NVD3.L 3x amplification) is plausible but unvalidated. Not blocking paper-to-live.

### Opportunity Scanner (opportunity_scanner.py)

**Classification: KEEP**

Not a numbered strategy but a scanning module. Finds tickers capable of +2% NET AFTER FEES intraday. This is the S15 support layer. Accounts for spread BPS, slippage, platform fees. Directly serves the core mission.

---

## 2. UK ISA Modules (uk_isa/)

| Module | Classification | Justification |
|---|---|---|
| `isa_universe.py` | **KEEP** | Defines the canonical ISA ticker list. Source of truth. |
| `universe_manager.py` | **KEEP** | Manages CORE/PEER/FULL_SCAN tiers with compute budgets. Critical infrastructure. |
| `lse_registry.py` | **KEEP** | LSE leveraged product catalog. Auto-classifies all ETPs. **Contains delisted tickers that need cleanup (see Section 7).** |
| `multiframe_analytics.py` | **KEEP** | Multi-timeframe indicator computation for ISA tickers. V2.0 core. |
| `volatility_regime.py` | **KEEP** | Volatility regime classification tuned for leveraged ETP behavior. V2.0 core. |
| `predictive_scoring.py` | **KEEP** | Predictive scoring model for ISA universe. V2.0 core. |
| `sector_rotation.py` | **KEEP** | ISA-specific sector rotation. **Contains delisted tickers in sector mappings that need cleanup (see Section 7).** |
| `correlation_engine.py` | **KEEP** | Cross-correlation tracking between ISA instruments. Portfolio risk management. |
| `peer_finder.py` | **KEEP** | Discovers peer instruments for ISA tickers. |
| `data_health.py` | **KEEP** | Data quality monitoring for ISA feeds. Operational safety. |
| `gate_diagnostics.py` | **KEEP** | Diagnostics for signal qualification gates. Operational safety. |

---

## 3. Delivery Modules (delivery/)

| Module | Classification | Justification |
|---|---|---|
| `telegram_bot.py` | **KEEP** | Signal delivery + PDF dispatch. Primary output channel. |
| `pdf_v2_momentum.py` | **KEEP** | PDF1: Momentum & Opportunity. V2.0 daily intelligence. |
| `pdf_v2_risk.py` | **KEEP** | PDF2: Risk & Structural. V2.0 daily intelligence. |
| `pdf_v2_daily_review.py` | **KEEP** | EOD Daily Review PDF. Performance tracking. |
| `pdf_master_spec.py` | **KEEP** | Master Spec reference PDF. |
| `pdf_mid_session.py` | **KEEP** | Mid-session risk update PDF. |
| `pdf_overnight_risk.py` | **KEEP** | Overnight risk assessment PDF. |
| `mega_report.py` | **KEEP** | Comprehensive reporting engine. |
| `pdf_intelligence.py` | **QUARANTINE** | Legacy V1 PDF intelligence report. Superseded by pdf_v2_momentum and pdf_v2_risk. Preserved for reference only. |
| `play_renderer.py` | **KEEP** | Renders trade play cards for PDFs. |
| `database.py` | **KEEP** | SQLite trade/signal database. Persistence layer. |
| `report_generator.py` | **QUARANTINE** | Legacy V1 report generator. Superseded by V2 PDFs. |
| `sheets_logger.py` | **KEEP** | Google Sheets trade log. Audit trail. |
| `dst_anchor.py` | **KEEP** | DST-aware scheduling for UK/US timezone transitions. |

---

## 4. Signal Engine (signal_engine/)

| Module | Classification | Justification |
|---|---|---|
| `engine.py` | **KEEP** | Core signal processing engine. |
| `pipeline_runner.py` | **KEEP** | Tiered pipeline runner (CORE -> PEER -> FULL_SCAN). |
| `strategy_router.py` | **KEEP** | Regime -> active strategy selection. Central routing logic. |
| `gates.py` | **KEEP** | Signal qualification gates. Risk control. |
| `scoring.py` | **KEEP** | Signal scoring engine. |
| `signal_card.py` | **KEEP** | Signal card data structure. |
| `intel_card.py` | **KEEP** | Intelligence card data structure. |
| `state_machine.py` | **KEEP** | Signal lifecycle state machine. |
| `unified_risk_gate.py` | **KEEP** | Unified risk gate aggregator. |
| `adapters/` | **KEEP** | Strategy-to-engine adapters. |

---

## 5. Feeds (feeds/)

| Module | Classification | Justification |
|---|---|---|
| `data_feeds.py` | **KEEP** | Primary OHLCV fetcher (yfinance + Alpha Vantage). Serves both ISA and context tickers. **Note: BOT_B_TICKERS hardcoded at top -- only used when config unavailable.** |
| `indicators.py` | **KEEP** | 22 core indicator calculations. Universal, ticker-agnostic. |
| `regime_classifier.py` | **KEEP** | 8-state regime classification. Core perception layer. |
| `market_structure.py` | **KEEP** | GEX/DIX from SqueezeMetrics + VIX term structure + market internals. GEX data is US-only but informs ISA ETP regime indirectly (leveraged ETPs amplify US gamma dynamics). Keep for regime context. |
| `calendar_feed.py` | **KEEP** | Earnings calendar (Finnhub) + economic calendar (ForexFactory). Blocks trading during FOMC/CPI/NFP. Critical operational safety regardless of universe. |
| `news_feed.py` | **QUARANTINE** | NewsAPI catalyst detection. Only feeds S4 (Catalyst Narrative) which is QUARANTINE. No other consumer. |
| `screener.py` | **QUARANTINE** | Finviz screener. Only feeds S11 (Hot Scanner) which is QUARANTINE. US-equity only. No LSE coverage. |
| `correlation_matrix.py` | **KEEP** | Cross-ticker correlation. Portfolio risk management. |
| `data_validator.py` | **KEEP** | Data quality validation. Operational safety. |
| `pattern_detector.py` | **KEEP** | 12 chart pattern detections. Used by multiple KEEP strategies. |
| `volume_profile.py` | **KEEP** | Volume profile analysis. Used by scoring and qualification. |
| `attention_detector.py` | **KEEP** | Attention exhaustion lifecycle tracking. Contrarian entry detection. |
| `holdings_decomposition.py` | **KEEP** | Maps ETP -> underlying constituents. Critical for understanding what ISA ETPs actually hold. |
| `premarket_intelligence.py` | **KEEP** | Pre-market briefing engine. Produces daily PreMarketBrief for Telegram. ISA-aware (scans what is inside each ETP). |

---

## 6. Data Feeds Configuration (settings.yaml data_feeds section)

| Provider | Classification | Justification |
|---|---|---|
| yfinance | **KEEP** | Primary data source. Covers .L tickers and US context. No API key. |
| Alpha Vantage | **KEEP** | Backup feed for stale/failed yfinance. 25 calls/day free tier. |
| SqueezeMetrics | **KEEP** | GEX/DIX for regime context. US-only data but informs ISA indirectly. |
| CBOE | **KEEP** | VIX and VIX3M. Regime classification dependency. |
| Finviz | **QUARANTINE** | Only feeds S11 (Hot Scanner). US equity screener. No ISA use. |
| Finnhub | **KEEP** | Earnings calendar + company financials. Blocks ISA trading during earnings of underlying stocks. |
| ForexFactory | **KEEP** | FOMC/CPI/NFP economic calendar. Critical operational safety. |
| NewsAPI | **QUARANTINE** | Only feeds S4 (Catalyst Narrative). No direct ISA consumer. |
| TwelveData | **QUARANTINE** | Real-time quotes + intraday bars. Free tier (8 calls/min). Currently not a critical dependency for any KEEP module. yfinance covers the same data. Re-enable if yfinance becomes unreliable. |
| FMP (Financial Modeling Prep) | **QUARANTINE** | Company profiles + bulk quotes + financial statements. No ISA-specific consumer. US equity focus. |

---

## 7. Dead Code and Delisted Ticker References

### 7a. Delisted Tickers (confirmed no yfinance data as of 2026-02-26)

The following 9 tickers are confirmed delisted/unavailable and must be REMOVED from all config and code:

| Ticker | Product | Status |
|---|---|---|
| AVGO3.L | GraniteShares Broadcom 3x Long | Delisted |
| PLTR3.L | GraniteShares Palantir 3x Long | Delisted |
| META3.L | 3x Meta | Delisted |
| AMZN3.L | 3x Amazon | Delisted |
| MSFT3.L | 3x Microsoft | Delisted |
| AAPL3.L | 3x Apple | Delisted |
| 3LIT.L | 3x FTSE MIB | Delisted |
| ARMS.L | -3x ARM Holdings | Delisted |
| 3SIT.L | -3x FTSE MIB | Delisted |

Additionally, the following tickers were removed from `universe.yaml` peer candidates but still appear in Python code:

| Ticker | Still Referenced In | Action |
|---|---|---|
| MFAS.L | `uk_isa/universe_manager.py` (lines 63, 89), `uk_isa/sector_rotation.py` (line 50) | REMOVE |
| MSFL.L | `uk_isa/universe_manager.py` (line 61, 90), `uk_isa/sector_rotation.py` (line 50) | REMOVE |
| GOOGL3.L | `uk_isa/sector_rotation.py` (line 50) | REMOVE |
| AAPLL.L | `uk_isa/sector_rotation.py` (line 50) | REMOVE |
| COIN3.L | `uk_isa/lse_registry.py` (line 82), `uk_isa/universe_manager.py` (lines 62, 96), `uk_isa/sector_rotation.py` (line 60) | REMOVE |
| MSTRL.L | `uk_isa/lse_registry.py` (line 83), `uk_isa/universe_manager.py` (lines 62, 96), `uk_isa/sector_rotation.py` (line 60) | REMOVE |
| BAC3.L | `uk_isa/lse_registry.py` (line 84), `uk_isa/universe_manager.py` (lines 62, 97), `uk_isa/sector_rotation.py` (line 55) | REMOVE |
| GS3.L | `uk_isa/lse_registry.py` (line 85), `uk_isa/universe_manager.py` (lines 62, 97), `uk_isa/sector_rotation.py` (line 55) | REMOVE |

### 7b. Files Containing Delisted Ticker References

**Python files requiring cleanup (REMOVE references):**

| File | Lines | Delisted Tickers Present |
|---|---|---|
| `uk_isa/lse_registry.py` | 73-85 | MFAS.L, MSFL.L, AAPLL.L, GOOGL3.L, AVGO3.L, PLTR3.L, COIN3.L, MSTRL.L, BAC3.L, GS3.L |
| `uk_isa/universe_manager.py` | 60-63, 88-97 | AVGO3.L, PLTR3.L, AMZL.L, MSFL.L, COIN3.L, MSTRL.L, BAC3.L, GS3.L, MFAS.L |
| `uk_isa/sector_rotation.py` | 50-60 | PLTR3.L, MFAS.L, MSFL.L, GOOGL3.L, AAPLL.L, AVGO3.L, COIN3.L, MSTRL.L, BAC3.L, GS3.L |
| `scripts/verify_core_expansion.py` | 15-35 | All 9 original delisted tickers |

**Documentation/artifact files (REMOVE or mark as historical):**

| File | Action |
|---|---|
| `reports/FINAL_AUDIT_READY_FOR_MORNING.md` | Historical record. Leave as-is with note. |
| `reports/MANUAL_ACTIONS_REQUIRED.md` | Historical record. Leave as-is with note. |
| `reports/DELIVERY_BATCH_PROOF.md` | Historical record. Leave as-is with note. |
| `docs/UNIVERSE_CHANGE_PROPOSAL.md` | Historical record. Leave as-is with note. |
| `docs/INSTITUTIONAL_PLAN_110.md` | Historical record. Leave as-is with note. |
| `artifacts/universe/core_expansion_verification.json` | Historical artifact. Leave as-is. |
| `artifacts/universe/expansion_v2_verification.json` | Historical artifact. Leave as-is. |

**Config files (already clean):**

| File | Status |
|---|---|
| `config/universe.yaml` | Already cleaned. Delisted tickers in comment only (line 26-27). No action needed. |

---

## 8. Execution Modules (execution/)

| Module | Classification | Justification |
|---|---|---|
| `virtual_trader.py` | **KEEP** | Paper trading engine. Core for paper-to-live validation. |
| `exit_engine.py` | **KEEP** | Exit logic (profit ladder, trailing stops). |
| `session_manager.py` | **KEEP** | Trading session lifecycle management. |
| `smart_routing.py` | **KEEP** | Liquidity scoring, slippage prediction, ETP-specific risk. ISA-aware. |
| `cost_model.py` | **KEEP** | Spread BPS and transaction cost model. Used by opportunity_scanner. |
| `order_rules.py` | **KEEP** | Order validation rules. |
| `planner.py` | **KEEP** | Trade plan construction. |

---

## 9. Qualification Modules (qualification/)

| Module | Classification | Justification |
|---|---|---|
| `circuit_breakers.py` | **KEEP** | Emergency stops. Constitutional safety. |
| `qualifier.py` | **KEEP** | 7-stage qualification pipeline. |
| `confidence_scorer.py` | **KEEP** | 5-layer confidence scoring. |
| `confluence_scorer.py` | **KEEP** | Multi-strategy confluence scoring. |
| `dynamic_sizer.py` | **KEEP** | Position sizing with drawdown adjustment. |
| `portfolio_risk.py` | **KEEP** | Portfolio-level risk management. |
| `risk_sizer.py` | **KEEP** | Per-trade risk sizing (0.75% rule). |
| `go_nogo.py` | **KEEP** | Go/No-Go pre-trade checklist. |
| `profit_ladder.py` | **KEEP** | 7-rung profit extraction ladder. |
| `pdt_tracker.py` | **QUARANTINE** | Pattern Day Trader compliance. US-only regulation (accounts <$25K). Does NOT apply to UK ISA (explicitly stated in docstring line 10). Only needed for Bot B. |

---

## 10. Bots (bots/)

| Module | Classification | Justification |
|---|---|---|
| `bot_base.py` | **KEEP** | Base class for all bots. |
| `portfolio_overseer.py` | **KEEP** | Cross-bot risk aggregation. |
| `specialist_bots.py` | **QUARANTINE** | Bot B specialist implementations. US equity focused. |
| `earnings_specialist.py` | **QUARANTINE** | Earnings-Bot. Feeds S5 (PEAD) which is QUARANTINE. |
| `sector_meta_bot.py` | **KEEP** | Sector rotation meta-bot. Feeds S7 (KEEP). |
| `timeframe_stacking.py` | **KEEP** | Multi-timeframe alignment. Used by multiple KEEP strategies. |
| `kelly_sizer.py` | **KEEP** | Kelly Criterion position sizing. Universal math. |

---

## 11. Core Modules (core/)

| Module | Classification | Justification |
|---|---|---|
| `schemas.py` | **KEEP** | Signal record schemas + cryptographic truth anchors. |
| `artifact_loader.py` | **KEEP** | Artifact file I/O. |
| `data_health_provider.py` | **KEEP** | Data health status provider. |
| `regime_provider.py` | **KEEP** | Regime state provider. |
| `replay.py` | **KEEP** | Signal replay for backtesting. |
| `scan_health.py` | **KEEP** | Scan health monitoring. |
| `universe_governance.py` | **KEEP** | Universe governance rules. |

---

## 12. Learning Modules (learning/)

| Module | Classification | Justification |
|---|---|---|
| `learning_engine.py` | **KEEP** | Guardrailed self-improvement (40-trade review window). |
| `outcomes_engine.py` | **KEEP** | Trade outcome tracking. |
| `signal_logger.py` | **KEEP** | Signal audit log. |
| `performance_analytics.py` | **KEEP** | Performance metrics. |
| `performance_attribution.py` | **KEEP** | Attribution analysis (which factors drove P&L). |
| `adaptive_intelligence.py` | **KEEP** | Adaptive parameter tuning within guardrails. |
| `edge_decay_engine.py` | **KEEP** | Detects strategy edge decay. |
| `edge_ledger.py` | **KEEP** | Edge tracking ledger. |
| `strategy_tournament.py` | **KEEP** | Strategy performance ranking. |
| `strategy_tracker.py` | **KEEP** | Per-strategy stats tracking. |
| `trade_autopsy.py` | **KEEP** | Post-trade analysis. |
| `decay_detector.py` | **KEEP** | Win rate decay detection. |
| `correlation_tracker.py` | **KEEP** | Learning-layer correlation tracking. |
| `failure_analysis.py` | **KEEP** | Failed trade pattern analysis. |
| `indicator_tracker.py` | **KEEP** | Indicator effectiveness tracking. |
| `missed_trade_journal.py` | **KEEP** | Tracks signals that were filtered out but would have won. |
| `param_optimizer.py` | **KEEP** | Parameter optimization within guardrails. |
| `pattern_tracker.py` | **KEEP** | Chart pattern effectiveness tracking. |
| `move_attribution.py` | **KEEP** | Price move attribution. |
| `system_iq.py` | **KEEP** | System IQ composite score. |
| `weight_optimizer.py` | **KEEP** | Confidence weight optimization. |
| `drift.py` | **KEEP** | Concept drift detection. |
| `calibration.py` | **KEEP** | Confidence calibration (predicted vs actual). |
| `attribution.py` | **KEEP** | Factor attribution. |
| `execution_quality_model.py` | **KEEP** | Execution quality tracking (slippage, fill rate). |
| `expectancy_model.py` | **KEEP** | Expected value model. |
| `meta_learner.py` | **KEEP** | Meta-learning across strategies. |
| `guardrails.py` | **KEEP** | Learning guardrail enforcement. |
| `schemas.py` | **KEEP** | Learning data schemas. |

---

## 13. Data Hub (data_hub/)

| Module | Classification | Justification |
|---|---|---|
| `hub.py` | **KEEP** | Central data hub. |
| `models.py` | **KEEP** | Data models. |
| `normalization/instrument_map.py` | **KEEP** | Maps tickers across providers. |
| `normalization/corporate_actions.py` | **KEEP** | Corporate action adjustments (splits, dividends). |
| `normalization/price_units.py` | **KEEP** | GBp-to-GBP and pence-to-pounds conversion. Critical for LSE. |
| `sources/yfinance_source.py` | **KEEP** | yfinance data source adapter. |
| `sources/validator_source.py` | **KEEP** | Data validation source. |
| `sources/ibkr_source.py` | **QUARANTINE** | IBKR TWS API stub. `IS_AVAILABLE = False`. Not configured. Needed for live trading (future) but not for paper. |

---

## 14. Risk Officer (risk_officer/)

| Module | Classification | Justification |
|---|---|---|
| `officer.py` | **KEEP** | Central risk officer. Constitutional rule enforcement. |
| `rules/correlation.py` | **KEEP** | Correlation risk rules. |
| `rules/data_reliability.py` | **KEEP** | Data reliability rules. |
| `rules/drawdown.py` | **KEEP** | Drawdown protection rules. |
| `rules/event_window.py` | **KEEP** | Event window blocking (FOMC, earnings). |
| `rules/liquidity.py` | **KEEP** | Liquidity rules. |
| `rules/vol_shock.py` | **KEEP** | Volatility shock rules. |

---

## 15. Infrastructure and Operations

| Module | Classification | Justification |
|---|---|---|
| `main.py` | **KEEP** | Orchestrator with APScheduler. |
| `models.py` | **KEEP** | Core data models. |
| `config/` | **KEEP** | All configuration. |
| `scheduled_jobs.py` | **KEEP** | Scheduled PDF generation + delivery. |
| `system_watchdog.py` | **KEEP** | System health monitoring (OK -> DEGRADED -> HALTED). |
| `diagnostics_live.py` | **KEEP** | Live system diagnostics. |
| `diagnostics_setup.py` | **KEEP** | Setup verification diagnostics. |
| `generate_pdf.py` | **KEEP** | Standalone PDF generator. |
| `exceptions.py` | **KEEP** | Custom exception classes. |
| `command_center/server.py` | **KEEP** | War Room API server. |
| `command_center/state.py` | **KEEP** | War Room state management. |
| `command_center/tick_loop.py` | **KEEP** | War Room real-time tick loop. |
| `command_center/diff.py` | **KEEP** | Configuration diff tracking. |
| `command_center/copilot/` | **KEEP** | War Room copilot. |
| `command_center/ui/` | **KEEP** | War Room UI assets. |
| `dashboard/api.py` | **KEEP** | Dashboard API. War Room operational requirement. |
| `dashboard/frontend/` | **KEEP** | Dashboard frontend. War Room operational requirement. |
| `scripts/backfill_5y.py` | **KEEP** | 5-year historical data backfill. |
| `scripts/backup_db.sh` | **KEEP** | Database backup. |
| `scripts/incident_drills.py` | **KEEP** | Incident response drills. |
| `scripts/send_all_pdfs.py` | **KEEP** | Bulk PDF delivery. |
| `scripts/start_local.py` | **KEEP** | Local development startup. |
| `scripts/verify_core_expansion.py` | **REMOVE** | References all 9 delisted tickers. Expansion verification already completed (results in `artifacts/`). Dead script. |
| `fix_unicode.py` | **QUARANTINE** | One-time unicode fix utility. |
| `supervisord.conf` | **KEEP** | Process supervision. |
| `Dockerfile` | **KEEP** | Container build. |
| `docker-compose.yml` | **KEEP** | Container orchestration. |
| `deploy.sh` | **KEEP** | Deployment script. |
| `requirements.txt` | **KEEP** | Python dependencies. |

---

## 16. Configuration Sections (settings.yaml)

| Section | Classification | Justification |
|---|---|---|
| `system` | **KEEP** | Core system config (mode, timezone, equity). |
| `bot_b_universe` (tickers + overrides) | **QUARANTINE** | US equities universe. Not ISA. Bot B is dormant for ISA-first. |
| `bot_a_universe` | **KEEP** | ISA ETP universe definition. Source of truth. |
| `indicators` | **KEEP** | 22 core indicator parameters. Universal. |
| `patterns` | **KEEP** | 12 pattern detection parameters. Universal. |
| `regime` | **KEEP** | 8-state regime classification. Critical. |
| `market_structure` | **KEEP** | GEX/DIX config. Regime dependency. |
| `market_internals` | **KEEP** | TICK/TRIN/ADD/VOLD composite. Regime dependency. |
| `time_windows` | **KEEP** | Time-of-day engine (ET). Critical for ISA (maps to UK trading hours). |
| `schedule` | **KEEP** | Strategy schedule (UK times). |
| `confidence` | **KEEP** | 5-layer confidence engine. Core scoring. |
| `qualification` | **KEEP** | 7-stage pipeline. Core risk control. |
| `profit_ladder` | **KEEP** | Profit extraction rules for stocks and ETPs. |
| `session_protection` | **KEEP** | Daily/weekly PnL limits. Constitutional. |
| `immutable_rules` | **KEEP** | 17 constitutional risk rules. NEVER MODIFY. |
| `emotional_firewall` | **KEEP** | 12 blocked behavioral patterns. |
| `learning` | **KEEP** | Guardrailed self-improvement parameters. |
| `data_feeds` | **KEEP** (with QUARANTINE for individual feeds as noted in Section 6) | Feed provider config. |
| `telegram` | **KEEP** | Signal delivery format. |
| `sheets` | **KEEP** | Google Sheets audit trail. |
| `drawdown_recovery` | **KEEP** | 5-level drawdown protocol. Constitutional. |
| `correlation` | **KEEP** | Cross-correlation matrix and portfolio heat. |
| `pdt` | **QUARANTINE** | PDT tracker config. US-only regulation. |
| `bots.bull_bot` | **KEEP** | Bull-Bot config. Uses KEEP strategies (S1, S2, S13). |
| `bots.range_bot` | **QUARANTINE** | Range-Bot config. Uses mostly QUARANTINE strategies (S3, S4, S8, S9). Only S12 is KEEP. |
| `bots.bear_bot` | **KEEP** | Bear-Bot config. Uses KEEP strategies (S6, S7, S1). |
| `bots.earnings_bot` | **QUARANTINE** | Earnings-Bot config. Uses S5 (QUARANTINE). |
| `overseer` | **KEEP** | Cross-bot portfolio overseer config. |
| `kelly` | **KEEP** | Kelly Criterion config. |

---

## 17. Universe Tickers Classification

### CORE (12 active -- eligible for TRADE signals)

| Ticker | Product | Leverage | Classification |
|---|---|---|---|
| QQQ3.L | Nasdaq 100 3x Long | 3x | **KEEP** |
| 3LUS.L | S&P 500 3x Long | 3x | **KEEP** |
| 3SEM.L | Semiconductors 3x Long | 3x | **KEEP** |
| GPT3.L | AI/GPT 3x Long | 3x | **KEEP** |
| NVD3.L | NVIDIA 3x Long | 3x | **KEEP** |
| TSL3.L | Tesla 3x Long | 3x | **KEEP** |
| TSM3.L | TSMC 3x Long | 3x | **KEEP** |
| MU2.L | Micron 2x Long | 2x | **KEEP** |
| QQQS.L | Nasdaq 100 3x Short | -3x | **KEEP** |
| 3USS.L | S&P 500 3x Short | -3x | **KEEP** |
| QQQ5.L | Nasdaq 100 5x Long | 5x | **KEEP** |
| SP5L.L | S&P 500 5x Long | 5x | **KEEP** |

### PEER Candidates (10 active in universe.yaml)

| Ticker | Product | Classification | Notes |
|---|---|---|---|
| AMD3.L | AMD 3x Long | **KEEP** | Verified tradable. Semi peer. |
| ARM3.L | ARM 3x Long | **KEEP** | Verified tradable. Semi peer. |
| NVDS.L | NVIDIA 3x Short | **KEEP** | Verified tradable. Inverse peer. |
| TSLS.L | Tesla 3x Short | **KEEP** | Verified tradable. Inverse peer. |
| 3LDE.L | DAX 3x Long | **KEEP** | Verified. European index diversification. |
| 3LEU.L | Euro Stoxx 50 3x Long | **KEEP** | Verified. European index diversification. |
| 3GOL.L | Gold 3x Long | **KEEP** | Verified. Commodity hedge. |
| 3SIL.L | Silver 3x Long | **KEEP** | Verified. Commodity hedge. |
| 3OIL.L | Oil 3x Long | **KEEP** | Verified. Commodity. |
| LLY3.L | Eli Lilly 3x Long | **KEEP** | Verified. Pharma diversification. |

### FULL_SCAN (context only -- never traded)

All full_scan tickers in universe.yaml are **KEEP**. They provide regime context (QQQ, SPY, VIX, TLT, etc.) and underlying price data for ISA ETP analysis. BTC-USD is present for macro correlation context only, not for trading.

### Bot B Universe (18 US equities in settings.yaml)

**Classification: QUARANTINE (entire section)**

Bot B is the US equities trading bot. It is technically active but ISA is PRIMARY. The 18 US equities (NVDA, TSLA, MU, etc.) serve two purposes:
1. Direct trading in US accounts (QUARANTINE -- not ISA)
2. Underlying reference prices for ISA ETP analysis (this function is also served by full_scan tickers in universe.yaml)

Recommend gating Bot B behind a feature flag. Do not delete -- the ISA mapping table (`isa_mapping` in settings.yaml) depends on Bot B signal generation to map US signals to ISA instruments.

---

## 18. Out-of-Scope Features

| Feature | Location | Classification | Justification |
|---|---|---|---|
| Crypto trading | `uk_isa/sector_rotation.py` line 60 (CRYPTO_TECH sector), `uk_isa/lse_registry.py` lines 82-83 (COIN3.L, MSTRL.L) | **REMOVE** | COIN3.L and MSTRL.L are delisted. The CRYPTO_TECH sector mapping references dead tickers. Remove sector and references. |
| Forex trading | None | N/A | No forex trading code exists. ForexFactory is used only for economic calendar scraping (FOMC/CPI/NFP dates). This is KEEP. |
| Options trading | None | N/A | No options trading code exists. References to "options" in qualifier.py (line 540) and vol_crush.py docstring are comments about the strategy concept, not options execution. GEX from SqueezeMetrics uses US options market data as an input signal, not for options trading. |
| BTC-USD | `config/universe.yaml` line 60 | **KEEP (as context)** | Present in full_scan_list only. Never traded. Used for macro correlation context. |

---

## Summary Table

| Module/Feature | Classification | Justification | Action Required |
|---|---|---|---|
| S15 Daily Target 2% | KEEP | Core mission strategy | None |
| S12 Rebalance Flow | KEEP | ISA-native key signal | None |
| S1 Regime Trend | KEEP | Ticker-agnostic trend following | None |
| S2 Momentum Breakout | KEEP | Ticker-agnostic squeeze detection | None |
| S3 Mean Reversion | QUARANTINE | Already DORMANT | Verify dormancy |
| S4 Catalyst Narrative | QUARANTINE | News-driven, ISA mapping unvalidated | Add feature flag |
| S5 PEAD Earnings | QUARANTINE | ETPs have no earnings events | Add feature flag |
| S6 Macro Regime Shift | KEEP | Informs long vs inverse ETP selection | None |
| S7 Sector Rotation | KEEP | Direct ISA mapping (3SEM.L, QQQ3.L) | None |
| S8 Volatility Crush | QUARANTINE | US stock focus, indirect ISA mapping | Add feature flag |
| S9 Pairs Trading | QUARANTINE | 3/4 pairs are US equities | Add feature flag |
| S10 AI Thematic | QUARANTINE | US ticker hardcoded, no ISA awareness | Add feature flag |
| S11 Hot Scanner | QUARANTINE | Finviz US-only, no LSE coverage | Add feature flag |
| S13 Trend Compounding | KEEP | ISA-aware swing strategy | None |
| S14 Gamma Squeeze | QUARANTINE | GEX is US options, indirect ISA chain | Add feature flag |
| Opportunity Scanner | KEEP | S15 support, fee-aware feasibility | None |
| uk_isa/* (all) | KEEP | V2.0 core modules | Clean delisted tickers |
| delivery/pdf_intelligence.py | QUARANTINE | Legacy V1 PDF | None (preserve) |
| delivery/report_generator.py | QUARANTINE | Legacy V1 report | None (preserve) |
| feeds/news_feed.py | QUARANTINE | Only feeds QUARANTINE S4 | Add feature flag |
| feeds/screener.py | QUARANTINE | Only feeds QUARANTINE S11 | Add feature flag |
| Finviz data feed | QUARANTINE | US equity screener only | Disable in config |
| NewsAPI data feed | QUARANTINE | Only feeds QUARANTINE strategy | Disable in config |
| TwelveData data feed | QUARANTINE | No critical ISA consumer | Disable in config |
| FMP data feed | QUARANTINE | No ISA consumer | Disable in config |
| pdt_tracker.py | QUARANTINE | US-only PDT regulation | Add feature flag |
| PDT config section | QUARANTINE | US-only | None (already inactive for Bot A) |
| bot_b_universe section | QUARANTINE | US equities | Gate behind feature flag |
| bots.range_bot | QUARANTINE | Mostly QUARANTINE strategies | Gate behind feature flag |
| bots.earnings_bot | QUARANTINE | QUARANTINE S5 | Gate behind feature flag |
| specialist_bots.py | QUARANTINE | Bot B specialists | None (preserve) |
| earnings_specialist.py | QUARANTINE | Bot B earnings specialist | None (preserve) |
| data_hub/ibkr_source.py | QUARANTINE | IBKR stub, not configured | None (already inactive) |
| fix_unicode.py | QUARANTINE | One-time utility | None |
| scripts/verify_core_expansion.py | REMOVE | Dead script, all delisted tickers | Delete file |
| Delisted tickers in lse_registry.py | REMOVE | 10+ delisted tickers in registry | Remove entries |
| Delisted tickers in universe_manager.py | REMOVE | Delisted tickers in tier/sector maps | Remove entries |
| Delisted tickers in sector_rotation.py | REMOVE | Delisted tickers in sector groups | Remove entries |
| CRYPTO_TECH sector mapping | REMOVE | All tickers delisted | Remove sector |

---

## Risk Assessment

### What breaks if we QUARANTINE something that is actually needed?

| QUARANTINE Item | Risk if Disabled | Mitigation |
|---|---|---|
| S4 Catalyst Narrative | No news-driven signals. S15 and S2 still detect catalyst-driven moves via volume/momentum. | Low risk. Volume spike detection in KEEP strategies covers most catalyst events. |
| S5 PEAD Earnings | No earnings drift signals. Calendar feed still blocks trading during earnings (safety preserved). | Low risk. S15 still scans during earnings -- it just uses technical signals not drift logic. |
| S8 Vol Crush | No post-VIX-spike recovery signals. S6 (Macro Regime Shift) covers the regime transition. S15 naturally finds high-ATR candidates during recovery. | Low risk. |
| S9 Pairs Trading | No market-neutral pairs. Not aligned with directional 2% target anyway. | Zero risk. |
| S10 AI Thematic | No AI-sector-specific weighting. S7 (Sector Rotation) provides sector RS. S15 ranks by reachability not theme. | Low risk. |
| S11 Hot Scanner | No Finviz hot stock signals. ISA ETPs are not on Finviz. | Zero risk for ISA. |
| S14 Gamma Squeeze | No GEX-triggered signals. GEX data still flows into regime classification via market_structure.py. | Low risk. Regime context preserved. |
| Range-Bot | S12 (Rebalance Flow) loses its bot assignment. **Fix: reassign S12 to Bull-Bot or create ISA-Bot.** | Medium risk. Must reassign S12 before quarantining Range-Bot. |
| Earnings-Bot | No quarterly activation. Not relevant for ISA ETPs. | Zero risk. |
| PDT Tracker | PDT does not apply to UK ISA. | Zero risk. |
| Finviz feed | S11 loses its data source. S11 is already QUARANTINE. | Zero risk. |
| NewsAPI feed | S4 loses its data source. S4 is already QUARANTINE. | Zero risk. |

### What is the blast radius of REMOVING dead code?

| REMOVE Item | Blast Radius | Risk Level |
|---|---|---|
| Delisted tickers from lse_registry.py | Registry becomes smaller. No code depends on specific entries (lookup misses return None gracefully). | **Minimal.** |
| Delisted tickers from universe_manager.py | Tier lists and sector maps shrink. Some sectors may become empty (e.g., "financials" had only BAC3.L and GS3.L). Empty sector -> no rotation signal for that sector. | **Minimal.** Remove the sector entirely when all tickers are delisted. |
| Delisted tickers from sector_rotation.py | Sector group arrays shrink. AI_TECH sector loses 4/6 tickers (only GPT3.L remains). FINANCIALS and CRYPTO_TECH become empty. | **Low.** Remove empty sectors. AI_TECH retains GPT3.L. |
| CRYPTO_TECH sector removal | Sector rotation no longer considers crypto-adjacent ETPs. | **None.** All tickers were delisted. |
| scripts/verify_core_expansion.py deletion | Loss of expansion verification script. Already completed -- results in artifacts/. | **None.** |

### Recommended Cleanup Sequence

Execute in this order to minimize risk:

1. **Phase 1 -- Dead ticker cleanup (REMOVE)**
   - Remove delisted ticker entries from `uk_isa/lse_registry.py`
   - Remove delisted ticker entries from `uk_isa/universe_manager.py`
   - Remove delisted ticker entries + empty sectors from `uk_isa/sector_rotation.py`
   - Delete `scripts/verify_core_expansion.py`
   - Run full test suite (`pytest tests/`)
   - Verify PDF generation still works (no broken ticker lookups)

2. **Phase 2 -- Feature flags for QUARANTINE strategies**
   - Add `_STRATEGY_DORMANT = True` pattern (matching S3) to: S4, S5, S8, S9, S10, S11, S14
   - Verify each returns `[]` immediately when dormant
   - Run test suite

3. **Phase 3 -- Feed quarantine**
   - Set `enabled: false` for Finviz, NewsAPI, TwelveData, FMP in settings.yaml
   - Verify system starts cleanly without these feeds
   - Verify no KEEP module depends on quarantined feeds

4. **Phase 4 -- Bot quarantine**
   - Reassign S12 (Rebalance Flow) from Range-Bot to Bull-Bot or new ISA-Bot
   - Gate Range-Bot and Earnings-Bot behind `enabled: false`
   - Gate Bot B universe behind `enabled: false`
   - Run test suite + full scheduled session

5. **Phase 5 -- Validation**
   - Run 3 consecutive scheduled sessions (PRE_LSE, PRE_NYSE, EOD)
   - Verify all PDFs generate correctly
   - Verify Telegram delivery succeeds
   - Verify S15 still fires and signal pipeline is intact
   - Verify virtual trader processes signals correctly

---

## Appendix: Active Strategy Map After Cleanup

Post-cleanup, the active strategy roster for ISA-first trading:

| Strategy | Bot Assignment | Role | Fires When |
|---|---|---|---|
| S15 Daily Target 2% | All (primary) | Core compounding machine | Every scan cycle, 1 signal/day |
| S1 Regime Trend | Bull-Bot, Bear-Bot | Trend pullback entries | Pre-market + weekly |
| S2 Momentum Breakout | Bull-Bot | Squeeze release + volume | US open + mid-session |
| S6 Macro Regime Shift | Bear-Bot | Long/inverse ETP switching | Continuous |
| S7 Sector Rotation | Bear-Bot | Weekly sector RS ranking | Weekly + midday |
| S12 Rebalance Flow | Bull-Bot (reassigned) | ETP sponsor rebalance front-run | 19:00 UK |
| S13 Trend Compounding | Bull-Bot | Multi-day swing (1-5 days) | Late session + weekly |
| Opportunity Scanner | Pipeline | Fee-aware 2% feasibility screening | Every scan cycle |

**7 active strategies + 1 scanner = lean, focused, ISA-aligned.**

---

*End of audit.*
