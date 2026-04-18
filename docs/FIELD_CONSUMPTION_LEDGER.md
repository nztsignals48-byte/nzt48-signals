# FIELD CONSUMPTION LEDGER

> A field only counts as **consumed** if it is used in sizing, entry/exit, a risk-check `confidence_delta`, or a regime/portfolio classification that alters trades. Logging/metrics does NOT count.

## IBKR tick fields (42)

| Field | Producer | Consumed by | Proof metric | Status |
|---|---|---|---|---|
| bid | ibkr_broker | risk_arbiter, exit_engine, sim_fill | `risk_deltas_total{check="spread"}` | PENDING_P2A |
| ask | ibkr_broker | risk_arbiter, exit_engine, sim_fill | `risk_deltas_total{check="spread"}` | PENDING_P2A |
| last | ibkr_broker | bar_builder, indicators | `bars_completed_total` | PENDING_P2A |
| volume | ibkr_broker | indicators.rvol, sim_fill.participation | `risk_deltas_total{check="liquidity"}` | PENDING_P2A |
| bid_size | ibkr_broker | quant.book_imbalance | `risk_deltas_total{check="imbalance"}` | PENDING_P2B |
| ask_size | ibkr_broker | quant.book_imbalance | `risk_deltas_total{check="imbalance"}` | PENDING_P2B |
| high | ibkr_broker | indicators.atr, indicators.session_high | `indicator_atr{ticker}` | PENDING_P2A |
| low | ibkr_broker | indicators.atr, indicators.session_low | `indicator_atr{ticker}` | PENDING_P2A |
| open | ibkr_broker | overnight_return, gap-fade | `signals_generated_total{strategy="overnight_return"}` | PENDING_P5 |
| close | ibkr_broker | bar_builder, indicators | `bars_completed_total` | PENDING_P2A |
| vwap | ibkr_broker | indicators.vwap_distance | `risk_deltas_total{check="vwap_chase"}` | PENDING_P2A |
| trade_count | ibkr_broker | indicators.rvol | observed_only | UNWIRED |
| rt_hist_vol | ibkr_broker | quant.garch_prior, risk_arbiter | `risk_deltas_total{check="vol_regime"}` | PENDING_P2B |
| shortable | ibkr_broker | risk_arbiter.shortable_gate | `risk_deltas_total{check="shortable"}` | PENDING_P4 |
| halted | ibkr_broker | risk_arbiter.halted_gate | `risk_deltas_total{check="halted"}` | PENDING_P4 |
| mark_price | ibkr_broker | portfolio.mtm | `equity_total_gbp` | PENDING_P2A |
| auction_price | ibkr_broker | IEXAuctionFade (shadow) | observed_only | SHADOW |
| auction_volume | ibkr_broker | IEXAuctionFade (shadow) | observed_only | SHADOW |
| auction_imbalance | ibkr_broker | IEXAuctionFade (shadow) | observed_only | SHADOW |
| etf_nav_close | ibkr_broker | nav_arbitrage (shadow) | observed_only | SHADOW |
| etf_nav_last | ibkr_broker | nav_arbitrage (shadow) | observed_only | SHADOW |
| etf_nav_bid | ibkr_broker | nav_arbitrage (shadow) | observed_only | SHADOW |
| etf_nav_ask | ibkr_broker | nav_arbitrage (shadow) | observed_only | SHADOW |
| opt_call_oi | ibkr_broker | put_call_ratio overlay | observed_only | UNWIRED |
| opt_put_oi | ibkr_broker | put_call_ratio overlay | observed_only | UNWIRED |
| opt_call_vol | ibkr_broker | put_call_ratio overlay | observed_only | UNWIRED |
| opt_put_vol | ibkr_broker | put_call_ratio overlay | observed_only | UNWIRED |
| opt_iv | ibkr_broker | (future IV surface strategy) | observed_only | SHADOW |
| avg_volume | ibkr_broker | risk_arbiter.liquidity_gate | `risk_deltas_total{check="liquidity"}` | PENDING_P4 |
| last_size | ibkr_broker | indicators.vwap | observed_only | UNWIRED |
| volume_rate | ibkr_broker | indicators.rvol | `risk_deltas_total{check="volume_surge"}` | PENDING_P2A |
| trade_rate | ibkr_broker | quant.trade_intensity | observed_only | UNWIRED |
| bid_depth_L2_1..5 | ibkr_broker | quant.book_pressure | `risk_deltas_total{check="book_pressure"}` | PENDING_P2B |
| ask_depth_L2_1..5 | ibkr_broker | quant.book_pressure | `risk_deltas_total{check="book_pressure"}` | PENDING_P2B |
| exchange | ibkr_broker | clock.eod_phase, exchange_profile | `exit_triggered_total{method="EventWindow"}` | PENDING_P2A |
| timestamp_ns | ibkr_broker | all | all | PENDING_P2A |

## Intel JSON keys (5 agents, MVP)

| Agent | Primary key | Consumed by | Proof metric | Status |
|---|---|---|---|---|
| news_reactor | events[] | sentiment_long_short, conviction_engine | `signals_generated_total{strategy="sentiment_long_short"}` | PENDING_P7 |
| earnings_whisper | whispers[] | earnings_pattern | `signals_generated_total{strategy="earnings_pattern"}` | PENDING_P7 |
| sec_scanner | filings[] | filing_change_detect | `signals_generated_total{strategy="filing_change_detect"}` | PENDING_P7 |
| regime_council | regime_probs | conviction_engine, portfolio_constructor | `kelly_fraction{strategy}` | PENDING_P7 |
| thesis_monitor | invalidations[] | exit_engine.signal_reversal | `exit_triggered_total{method="thesis_invalid"}` | PENDING_P7 |

## LLM outputs

| Field | Producer | Consumed by | Proof metric | Status |
|---|---|---|---|---|
| llm_conviction | dual_ensemble | conviction_engine (bounded [-30,+15] pp) | `conviction_delta_vs_default{agent}` | PENDING_P6 |
| llm_regime_label | regime_council | portfolio_constructor.kelly_scale | `kelly_fraction{strategy}` | PENDING_P7 |
| llm_news_sentiment | news_reactor | sentiment_long_short.edge_estimate | `signals_ranked_top_n{strategy="sentiment_long_short"}` | PENDING_P7 |

## ML outputs (none at MVP — rule-based only)

| Field | Producer | Consumed by | Status |
|---|---|---|---|
| — | — | — | NOT_AT_MVP (per PART -0.5) |

---

**Filled rows target:** 100 % before Phase 11 (Anti-Dead-Code Sweep) closes.
**Observed-only rows:** must be promoted within one phase of capture or unsubscribed.
**CI:** `scripts/field_ledger_check.py --strict` fails the build if any non-SHADOW row lacks a proof metric that fired in the last 24 h.
