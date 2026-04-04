# AEGIS V2 — System Scope

**Version:** 2.0 | **Audit Date:** 2026-04-04 | **Branch:** `feat/tier-system-enhancements-full`

---

## What It Is

AEGIS V2 is a hybrid Rust + Python automated trading engine targeting ISA-eligible leveraged ETPs on the London Stock Exchange, with extended coverage across US, TSE, HKEX, XETRA, Euronext, and SGX exchanges.

**Execution model:** Rust owns execution. Python owns intelligence. Python SUGGESTS; Rust DECIDES.

## Codebase Scale

| Layer | Files | Lines | Language |
|-------|-------|-------|----------|
| Engine (execution, risk, exits, broker, WAL) | 78 | ~36,000 | Rust |
| Brain (signals, ML, risk, sizing, strategies) | 353 | ~160,000 | Python |
| Config (TOML) | 14 | ~2,300 | TOML |
| **Total** | **~445** | **~198,300** | |

## Architecture Diagram

```
                    ┌─────────────────────────────────────┐
                    │         IBKR IB Gateway              │
                    │   (paper:4003 / live:4001)           │
                    └──────────────┬──────────────────────┘
                                   │ 5s bars + L1 tick-by-tick
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        RUST ENGINE (36K lines)                       │
│                                                                      │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────┐   ┌───────────┐ │
│  │ Universe  │──▶│ Tick Router  │──▶│   Python    │──▶│ 39-CHECK  │ │
│  │ Filter    │   │ Vanguard/Apex│   │   Bridge    │   │ Risk Gate │ │
│  └──────────┘   └──────────────┘   └─────────────┘   └─────┬─────┘ │
│                                                              │       │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────┐         ▼       │
│  │ WAL      │◀──│ Exit Engine  │◀──│ Executioner  │◀── APPROVED    │
│  │ Writer   │   │ Chandelier   │   │ Order Mgmt   │   or REJECTED  │
│  └──────────┘   └──────────────┘   └─────────────┘                  │
│                                                                      │
│  Supporting: GARCH, EVT, Kalman, Hayashi-Yoshida, Regime Detector,  │
│  Portfolio State, ISA Gate, Sector Rotation, Reconciler, Clock,     │
│  Subscription Manager, Multi-frame Vol, Thompson Sampler            │
└──────────────────────────────────────────────────────────────────────┘
                         │ JSON stdin/stdout
                         ▼
┌��─────────────────────────────────────────────────────────────────────┐
│                      PYTHON BRAIN (160K lines)                       │
│                                                                      │
│  bridge.py (8,408 lines) — Central Signal Pipeline                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Ingest   │▶│Indicators│▶│ Quality  │▶│ Signal   │▶│  Output  │ │
│  │          │ │  RVOL,   │ │  Gates   │ │ Generate │ │ Bayesian │ │
│  │          │ │  Hurst,  │ │  (25+)   │ │  (34)   │ │  Agg +   │ │
│  │          │ │  VPIN,   │ │          │ │          │ │  Adjust  │ │
│  │          │ │  ADX...  │ │          │ │          │ │  (50+)   │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│                                                                      │
│  ML (40 files): numpy-only. No PyTorch/TF.                          │
│  Risk (19 files): Regime ensemble, drawdown recovery, correlation,  │
│    SPRT quarantine, adversarial detection, safety boundaries         │
│  Sizing (8 files): 12-factor Kelly, vol targeting, rolling Kelly,   │
│    meta-allocator, drawdown staging                                  │
│  Strategies (19 files): Vol compression, NAV arb, rebalancing flow, │
│    calendar anomalies, FOMC drift, pairs, lead-lag, HFT prob, etc.  │
│  Ouroboros (72 files): Nightly pipeline, config writer, backtester, │
│    persistent memory, Claude/Gemini integration                      │
└──────────────────────────────────────────────────────────────────────┘
```

## IPC Protocol

- **Transport:** stdin/stdout of subprocess (`python3 bridge.py`)
- **Format:** JSON lines (newline-delimited)
- **Timeout:** 5 seconds (prevents engine freeze)
- **Reader:** Dedicated thread (`aegis-bridge-reader`) with `mpsc::channel`

## Deployment Target

- **Phase:** Paper trading (IS_LIVE = false, compile-time constant)
- **Broker:** IBKR IB Gateway, client_id 101 (engine) + 102 (analytics)
- **Infra:** EC2 t3.medium (4GB RAM) via Docker compose
- **ISA constraints:** Long-only, 20K GBP annual limit, cash buffer 25%

## Key Design Decisions

1. **Fail-closed risk:** Every signal passes through 39 Rust checks synchronously in <1ms. Any failure = immediate rejection.
2. **ISA compliance hardcoded:** Short-sell blocked at CHECK 1, inverse mutual exclusion at CHECK 2, annual limit at CHECK 17.
3. **Numpy-only ML:** All 40 ML modules use pure numpy, no deep learning frameworks. Runs on 4GB EC2.
4. **Fail-open imports:** All Python strategy modules use `try/except ImportError: pass`. Missing module = graceful degradation, not crash.
5. **4-state regime hierarchy:** HALT > FLATTEN > REDUCE > NORMAL. Escalation one-way; de-escalation requires specific conditions.
6. **WAL persistence:** Every order, fill, exit, regime change persisted to NDJSON before action. 7-day archive rotation.
7. **Hot-reload:** SIGHUP reloads contracts.toml + dynamic_weights.toml + FX rates + recycles Python bridge. Zero downtime.
