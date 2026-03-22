# Simulation Digest — 2026-03-22

## Monte Carlo Result: TypeB+C Aggressive
- £10K → £46,266 median (10,000 paths)
- 115% CAGR, Sharpe 2.66, 6.2% median max DD
- 0% chance of loss, 100% chance of 2x, 98.2% chance of 3x
- Net PF 1.65 after 0.26% total costs per trade

## Entry Type Verdicts
- **TypeB: ONLY ALPHA** — 52.4% WR, PF 1.96, 5.8M trades
- **TypeC: Break-even** — 45.1% WR, PF 0.98
- **TypeA: DISABLE** — 29.5% WR, PF 0.04
- **TypeD: DISABLE** — 24.1% WR, PF 0.03

## Exchange Verdicts
- EURONEXT PF 1.05, XETRA PF 1.10 — POSITIVE
- TSE PF 0.89 — near break-even
- US PF 0.57, HKEX PF 0.37 — NEGATIVE (bear market)

## Next Backtests Needed
1. **BT-006: Walk-forward** (train 2023-24, test 2025) — MOST IMPORTANT
2. **BT-001: TypeB-only with FX normalization** — definitive GBP P&L
3. **BT-003: Chandelier ATR sweep** (1.0-3.0) for TypeB optimal
4. **BT-002: Regime overlay** — does TypeB work in bear AND bull?
5. **BT-010: Full bridge.py production-parity** — authoritative result
