# LIVE READINESS CHECKLIST

Must be 100% green before Phase 13 (first pound of real capital).

- [ ] 60 consecutive paper days with zero `zero-trade-day` incidents
- [ ] 3 strategies with >=500 trades, Sharpe >0.5, PF >1.05, MDD <2x backtest, DSR >0
- [ ] Each graduating strategy passes stress-window replay (2020-03 or 2024-08)
- [ ] All 11 personas signed off via `tests/acceptance/<persona>.py` exit 0
- [ ] Monte Carlo stress passed (2020-03, 2021-01, 2024-08) — MDD <10% simulated
- [ ] Kill drill passed 8 consecutive Sundays
- [ ] External watchdog delivered live Telegram alert in last 7 days
- [ ] Broker reconcile zero discrepancies 14 consecutive days
- [ ] Cost governor halted LLM spend in live $15/day drill
- [ ] Golden signal test green for 14 consecutive days
- [ ] Every non-SHADOW row of `FIELD_CONSUMPTION_LEDGER.md` has last_seen <24h
- [ ] Every LLM agent `is_alpha_positive()` is True
- [ ] `bounds.toml` validated; `learned.toml` updated from trade data >=30 times
- [ ] Hetzner CX32 deployed; Mac removed from production path
- [ ] ISA/GIA/IG account splits configured; stamp duty + IG financing verified

Initial live allocation: £500 at 25% Kelly. Scale only after 500 live trades meet criteria.
