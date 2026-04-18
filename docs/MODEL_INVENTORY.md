# MODEL INVENTORY

| Name | Type | Inputs | Outputs | Training data | Validation | Owner | Last review |
|---|---|---|---|---|---|---|---|
| GARCH(1,1) | Statistical | log returns (rolling 100 bars) | conditional vol annualised | Online, no training | output wired to sizing (Phase 2B) | Simons | SCAFFOLD |
| GARCH-EVT | Statistical | GARCH residuals | VaR/CVaR at 95% | Online | wired to risk_arbiter (Phase 2B) | Simons | SCAFFOLD |
| Student-t Kalman | Statistical | last price, prior state | filtered price, residual z-score | Online | wired to risk_arbiter + quant.micro_price_proxy (Phase 2B) | Simons | SCAFFOLD |
| HMM Regime | Statistical | vol, return, volume | regime_probs[4] = [steady, trending, crisis, rotation] | Weekly refit on 252-day rolling | wired to conviction_engine + Kelly scaling | Simons | SCAFFOLD |
| Hayashi-Yoshida | Statistical | async ticks two symbols | rolling correlation | Online | wired to risk_arbiter.correlation_gate | Simons | SCAFFOLD |
| Dual-LLM conviction | LLM ensemble | strategy_default + context | conviction delta [-30, +15] pp | Pinned model versions | A/B harness N>=200 (Phase 7) | Dario | SCAFFOLD |
| LNN / micro-price / DRL | ML (ONNX) | — | — | FORBIDDEN AT MVP (PART -0.5) | — | — | NOT_AT_MVP |

**Rule:** no model ships without a row here. No row ships without an acceptance test.
