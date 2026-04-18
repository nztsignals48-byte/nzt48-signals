# V5 Compliance Roadmap

## Current State (paper)

V5 is currently paper-only (IBKR account DUM983136). No client assets at risk.
Audit infrastructure exists and is capturing:

- Order activity (orders.submit archive)
- Fills (orders.filled archive)
- Rejections (signals.rejected archive)
- LLM council decisions (audit/llm_decisions.jsonl)
- Best execution rationale (audit/best_execution.jsonl)
- Daily compliance reports (docs/compliance/YYYY-MM-DD.md)
- VaR/CVaR snapshots (risk_var_cvar archive)

## Pre-Live Requirements (when moving to real capital)

### 1. Record retention
- **Current**: append-only WAL with SHA256 hash chain, stored on local disk
- **Required**: 7-year retention on immutable storage (S3 Glacier or equivalent)
- **Gap**: implement S3 sync for data/audit/ and data/wal/

### 2. Client asset segregation
- **Current**: N/A (paper)
- **Required**: legal entity structure, regulated broker, segregated accounts
- **Gap**: structure (MiFID II: CASS 7 rules)

### 3. Market data use policy
- **Current**: IBKR scanner data used internally
- **Required**: verify IBKR ToS allows use in algorithmic trading + no redistribution
- **Gap**: written confirmation from IBKR

### 4. MiFID II Article 17 (Algorithmic trading)
- **Current**: tamper-evident logs exist
- **Required**: annual self-assessment, stress tests, kill-switch drills
- **Gap**: formal annual review process, documented drills

### 5. Best execution (RTS 27/28)
- **Current**: `best_execution_logger.py` captures chosen venue + rationale
- **Required**: quarterly published execution quality reports
- **Gap**: publish-side (PDF generation + attestation)

### 6. SEC requirements (if US institutional)
- **Current**: N/A
- **Required**: Form PF (> $150M AUM), ADV, Regulation SCI
- **Gap**: only triggers above AUM threshold

## LLM-Specific Compliance (emerging area)

- **Prompt versioning**: git-versioned; each decision logged with prompt hash (done)
- **Model transparency**: using Claude Haiku 4.5; model version recorded
- **Hallucination risk**: `llm_uplift_tracker.py` measures whether LLM adds value
- **Veto power audit**: Risk Officer vetoes logged separately

## 90-Day Milestones

| Week | Milestone |
|---|---|
| 1-4 | Accumulate ≥ 2,500 order records for first MiFID-style sample report |
| 5-8 | Run quarterly stress drill; verify all kill switches fire correctly |
| 9-12 | Generate first quarterly best-execution report from logged data |

## Ownership

- Primary: system operator
- Audit trail integrity: automated (SHA256 hash chain)
- Review cadence: daily (automated) + weekly (manual spot check)
