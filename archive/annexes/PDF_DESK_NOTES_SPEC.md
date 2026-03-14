# PDF DESK NOTES -- COMPLETE QUALITY SPECIFICATION

**Document ID**: NZT48-ANNEX-004
**Version**: 1.0
**Date**: 2026-02-27
**Status**: BINDING -- All PDF generation MUST conform to this specification (target: 100/100 desk notes standard)
**Scope**: PDF types, schedules, content rules, QA gates, pre-flight audit, lane separation, acceptance tests

---

## 1. OBJECTIVE

Define the complete quality specification for all NZT-48 PDF reports, targeting institutional desk-notes quality (100/100). Every PDF MUST pass an automated pre-flight QA gate before delivery. Failed QA produces a DRAFT-watermarked PDF with failure reasons. This specification addresses cross-PDF consistency, data freshness, count integrity, and lane separation issues identified in FORENSICS_MAP sections 5.1-5.3.

---

## 2. PDF TYPE REGISTRY

### 2.1 Full PDF Schedule (7 report types)

| # | PDF Type | File Prefix | Generation Time (UK) | Delivery | Source File | Lane |
|---|----------|------------|---------------------|----------|-------------|------|
| P1 | Momentum & Opportunity | `momentum_` | 07:00 | Telegram + archive | `delivery/pdf_v2_momentum.py` | MOMENTUM |
| P2 | Risk & Structural | `risk_` | 13:30 | Telegram + archive | `delivery/pdf_v2_risk.py` | RISK |
| P3 | EOD Review | `daily_review_` | 22:00 | Telegram + archive | `delivery/pdf_v2_daily_review.py` | REVIEW |
| P4 | MEGA PDF | `mega_` | 22:30 | Telegram (document) + archive | `delivery/mega_report.py` | ALL |
| P5 | Overnight Risk | `NZT48_OVERNIGHT_` | 06:30 | Telegram + archive | `delivery/pdf_overnight_risk.py` | RISK |
| P6 | Mid-Session Risk | `NZT48_MID_SESSION_` | 16:40 | Telegram + archive | `delivery/pdf_mid_session.py` | RISK |
| P7 | Master Spec | `NZT48_MASTER_SPEC_` | 00:00 | Archive only | `delivery/pdf_master_spec.py` | AUDIT |

### 2.2 Output Locations

| PDF Type | Output Directory | Archive Pattern |
|----------|-----------------|-----------------|
| P1 Momentum | `data/reports/` | `momentum_YYYYMMDD_HHMMSS.pdf` |
| P2 Risk | `data/reports/pdf2/` | `risk_YYYYMMDD_HHMMSS.pdf` |
| P3 EOD Review | `data/reports/` | `daily_review_YYYYMMDD_HHMMSS.pdf` |
| P4 MEGA | `data/reports/` | `mega_YYYYMMDD_HHMMSS.pdf` |
| P5 Overnight | `data/reports/` | `NZT48_OVERNIGHT_YYYYMMDD_HHMMSS.pdf` |
| P6 Mid-Session | `data/reports/` | `NZT48_MID_SESSION_YYYYMMDD_HHMMSS.pdf` |
| P7 Master Spec | `data/reports/` | `NZT48_MASTER_SPEC_YYYYMMDD_HHMMSS.pdf` |

---

## 3. LANE SEPARATION RULES (BINDING)

### 3.1 Lane Definitions

| Lane | Description | PDF Types |
|------|-------------|-----------|
| MOMENTUM | Opportunities, entries, targets, upside bias, candidate ranking | P1 only |
| RISK | Drawdown, decay, correlation stress, downside protection, exit urgency | P2, P5, P6 |
| REVIEW | Post-session analysis, trade autopsy, missed trades, lessons | P3 only |
| ALL | Comprehensive daily report (all lanes combined) | P4 only |
| AUDIT | System state, configuration, artifact inventory, truth manifest | P7 only |

### 3.2 Cross-Lane Contamination Rules

| Rule # | Rule | Violation Example | Enforcement |
|--------|------|-------------------|-------------|
| L1 | P1 (Momentum) MUST NOT contain risk warnings, decay analysis, or exit recommendations | P1 says "WARNING: NVD3.L has 12% vol decay risk" | QA gate FAIL |
| L2 | P2 (Risk) MUST NOT contain entry recommendations, target prices, or buy signals | P2 says "BUY NVD3.L at 142.50 with target 145.00" | QA gate FAIL |
| L3 | P2/P5/P6 (Risk lane) MAY reference tickers in momentum context ONLY to assess risk | P2 says "NVD3.L is in a strong uptrend" (acceptable if followed by risk assessment) | Permitted with risk framing |
| L4 | P3 (Review) MUST NOT contain forward-looking entry recommendations | P3 says "Tomorrow, buy QQQ3.L" | QA gate FAIL |
| L5 | P3 (Review) MAY contain "Tomorrow's Setup Watchlist" as a planning section | Watchlist is permitted; specific entries are not | Permitted |
| L6 | P4 (MEGA) is exempt from lane separation (it covers everything) | N/A | No lane enforcement |
| L7 | P7 (Master Spec) MUST NOT contain trading recommendations | P7 says "Consider buying NVD3.L" | QA gate FAIL |

### 3.3 Lane Enforcement Implementation

Each PDF generator MUST include a `validate_lane_separation()` function that scans the generated content for lane violations:

```python
MOMENTUM_KEYWORDS = ["entry", "buy", "target", "upside", "long candidate", "opportunity score"]
RISK_KEYWORDS = ["decay risk", "drawdown", "exit score", "correlation stress", "risk warning", "stop loss breach"]
ENTRY_KEYWORDS = ["buy at", "sell at", "entry price", "recommended entry"]

def validate_lane_separation(pdf_type: str, content_sections: list[str]) -> list[str]:
    """Return list of lane violation warnings. Empty list = PASS."""
    violations = []
    if pdf_type == "P1_MOMENTUM":
        for section in content_sections:
            for keyword in RISK_KEYWORDS:
                if keyword.lower() in section.lower():
                    violations.append(f"L1 violation: RISK keyword '{keyword}' found in Momentum PDF")
    elif pdf_type in ("P2_RISK", "P5_OVERNIGHT", "P6_MID_SESSION"):
        for section in content_sections:
            for keyword in ENTRY_KEYWORDS:
                if keyword.lower() in section.lower():
                    violations.append(f"L2 violation: ENTRY keyword '{keyword}' found in Risk PDF")
    return violations
```

---

## 4. QA RULES (7 mandatory checks per PDF)

### QA Rule 1: Ticker Timestamp Integrity

**Rule**: Every ticker mentioned in any data table, chart, or analysis section MUST have an associated "as-of" timestamp showing when the data was fetched.

**Implementation**:
```
Format: "Data as of 14:30 UTC 2026-02-27"
Location: Footer of every data table, subtitle of every chart
```

**QA Check**:
```python
def qa_ticker_timestamps(sections: list) -> QAResult:
    """Every ticker mention must have a corresponding timestamp."""
    tickers_mentioned = extract_tickers(sections)
    tickers_with_timestamps = extract_timestamped_tickers(sections)
    missing = tickers_mentioned - tickers_with_timestamps
    if missing:
        return QAResult(passed=False, message=f"Tickers missing timestamps: {missing}")
    return QAResult(passed=True)
```

**Acceptance Test**: Generate a PDF. Grep for each ISA_UNIVERSE ticker. For each match, verify a timestamp exists within 5 lines (in the same table/section).

### QA Rule 2: No Intra-PDF Contradictions

**Rule**: A single ticker MUST NOT be labeled both BULLISH and BEARISH within the same PDF. Direction bias must be consistent across all sections of the same report.

**Implementation**:
```python
def qa_no_contradictions(sections: list) -> QAResult:
    """Check that no ticker has contradictory direction labels."""
    ticker_directions = {}  # ticker -> set of directions
    for section in sections:
        for ticker, direction in extract_ticker_directions(section):
            ticker_directions.setdefault(ticker, set()).add(direction)
    contradictions = {t: dirs for t, dirs in ticker_directions.items() if len(dirs) > 1}
    if contradictions:
        return QAResult(passed=False, message=f"Contradictions: {contradictions}")
    return QAResult(passed=True)
```

**Allowed exception**: A ticker can be labeled "BULLISH (short-term)" and "BEARISH (long-term)" IF the timeframe qualifier is explicitly stated in both mentions.

**Acceptance Test**: Generate P1. Inject a ticker that is BULLISH in one section and BEARISH in another. QA gate MUST fail with a clear error message.

### QA Rule 3: Lane Separation

**Rule**: See Section 3.2. PDF content must stay within its designated lane.

**QA Check**: `validate_lane_separation()` as defined in Section 3.3.

**Acceptance Test**: Inject a risk warning into P1. QA gate MUST fail with lane violation message.

### QA Rule 4: Count Integrity

**Rule**: If any PDF section states a count (e.g., "3 TIER_1 signals", "5 candidates"), the listed items MUST exactly match that count.

**Implementation**:
```python
def qa_count_integrity(sections: list) -> QAResult:
    """If a section says 'N items', exactly N must be listed."""
    errors = []
    for section in sections:
        claimed_counts = extract_claimed_counts(section)
        for label, claimed, actual in claimed_counts:
            if claimed != actual:
                errors.append(f"Count mismatch: '{label}' claims {claimed} but lists {actual}")
    if errors:
        return QAResult(passed=False, message="; ".join(errors))
    return QAResult(passed=True)
```

**Patterns to detect**:
- "Top N candidates" followed by a table -- table rows MUST equal N.
- "N TIER_1 signals" -- exactly N items with TIER_1 label MUST follow.
- "Showing N of M" -- N items MUST be listed.

**Acceptance Test**: Generate P1 with 3 candidates. Manually remove 1 from the table. QA gate MUST fail.

### QA Rule 5: Closest Misses Section

**Rule**: Every P1 (Momentum) and P3 (EOD Review) PDF MUST include a "Closest Misses" section showing the top 3 signals that ALMOST qualified but were rejected, with the specific rejection reason for each.

**Required Fields Per Miss**:
| Field | Description | Example |
|-------|-------------|---------|
| `ticker` | Instrument symbol | NVD3.L |
| `strategy` | Strategy that generated the signal | S2 Momentum Breakout |
| `confidence` | Score at rejection | 57/100 |
| `rejection_gate` | Which of the 7 gates rejected it | Gate 5: Min Confidence (60) |
| `rejection_reason` | Human-readable explanation | Confidence 57 < threshold 60 |
| `gap_to_qualify` | How close it was | 3 points below threshold |
| `would_have_worked` | If available, did price reach target? | YES (+1.8R) or UNKNOWN |

**QA Check**:
```python
def qa_closest_misses(pdf_type: str, sections: list) -> QAResult:
    """P1 and P3 must have Closest Misses section with 3 entries."""
    if pdf_type not in ("P1_MOMENTUM", "P3_EOD_REVIEW"):
        return QAResult(passed=True)  # Not applicable
    misses_section = find_section(sections, "Closest Misses")
    if not misses_section:
        return QAResult(passed=False, message="Closest Misses section missing")
    miss_count = count_entries(misses_section)
    if miss_count < 1:
        return QAResult(passed=False, message=f"Closest Misses has {miss_count} entries, need >= 1")
    if miss_count > 3:
        return QAResult(passed=False, message=f"Closest Misses has {miss_count} entries, max is 3")
    return QAResult(passed=True)
```

**Acceptance Test**: Generate P1 with at least 5 rejected signals. Verify Closest Misses section shows exactly 3, sorted by gap_to_qualify ascending.

### QA Rule 6: Data Health Section

**Rule**: Every PDF (all 7 types) MUST include a "Data Health" section showing per-ticker data completeness percentage.

**Required Fields**:
| Field | Description | Example |
|-------|-------------|---------|
| `ticker` | Instrument symbol | QQQ3.L |
| `data_completeness_pct` | % of expected data points available | 95.2% |
| `health_status` | PASS (>=80%), WARN (>=50%), FAIL (<50%) | PASS |
| `missing_fields` | List of missing/stale data fields | ["volume (stale 2h)", "ADX (missing)"] |
| `last_update` | Timestamp of most recent data point | 14:28 UTC |

**QA Check**:
```python
def qa_data_health(sections: list) -> QAResult:
    """Data Health section must exist with per-ticker completeness."""
    health_section = find_section(sections, "Data Health")
    if not health_section:
        return QAResult(passed=False, message="Data Health section missing")
    tickers_reported = extract_health_tickers(health_section)
    if len(tickers_reported) == 0:
        return QAResult(passed=False, message="Data Health section has no tickers")
    return QAResult(passed=True)
```

**Acceptance Test**: Generate P1 with one ticker returning empty data from yfinance. Verify Data Health section shows FAIL for that ticker with explanation.

### QA Rule 7: Regime Section Consistency

**Rule**: The regime label displayed in any PDF MUST match the current system regime at the time of PDF generation. No stale regime labels allowed.

**Implementation**:
```python
def qa_regime_consistency(pdf_regime: str, system_regime: str) -> QAResult:
    """PDF regime must match current system regime."""
    if pdf_regime != system_regime:
        return QAResult(
            passed=False,
            message=f"Regime mismatch: PDF shows '{pdf_regime}', system is '{system_regime}'"
        )
    return QAResult(passed=True)
```

**Data Source**: System regime read from `regime_history` table (most recent entry) at PDF generation time.

**Acceptance Test**: Change system regime via DB injection. Generate PDF immediately. Verify PDF shows the NEW regime, not the old one.

---

## 5. PRE-FLIGHT AUDIT GATE

### 5.1 Gate Architecture

Every PDF generation follows this pipeline:

```
Data Collection -> Content Assembly -> QA Pre-Flight -> Render PDF -> Delivery
                                           |
                                      PASS? --YES--> Clean PDF
                                           |
                                      NO -------> DRAFT PDF (watermarked) + QA failure log
```

### 5.2 Pre-Flight Sequence

```python
def run_preflight_audit(pdf_type: str, assembled_content: dict) -> PreflightResult:
    """Run all QA checks before rendering PDF."""
    results = []

    # QA Rule 1: Ticker timestamps
    results.append(("TICKER_TIMESTAMPS", qa_ticker_timestamps(assembled_content["sections"])))

    # QA Rule 2: No contradictions
    results.append(("NO_CONTRADICTIONS", qa_no_contradictions(assembled_content["sections"])))

    # QA Rule 3: Lane separation
    lane_violations = validate_lane_separation(pdf_type, assembled_content["sections"])
    results.append(("LANE_SEPARATION", QAResult(passed=len(lane_violations)==0, message="; ".join(lane_violations) if lane_violations else "OK")))

    # QA Rule 4: Count integrity
    results.append(("COUNT_INTEGRITY", qa_count_integrity(assembled_content["sections"])))

    # QA Rule 5: Closest misses
    results.append(("CLOSEST_MISSES", qa_closest_misses(pdf_type, assembled_content["sections"])))

    # QA Rule 6: Data health
    results.append(("DATA_HEALTH", qa_data_health(assembled_content["sections"])))

    # QA Rule 7: Regime consistency
    system_regime = get_current_system_regime()
    pdf_regime = assembled_content.get("regime_label", "UNKNOWN")
    results.append(("REGIME_CONSISTENCY", qa_regime_consistency(pdf_regime, system_regime)))

    all_passed = all(r[1].passed for r in results)
    failed_checks = [(name, r.message) for name, r in results if not r.passed]

    return PreflightResult(
        passed=all_passed,
        results=results,
        failed_checks=failed_checks,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
```

### 5.3 DRAFT Watermark Behaviour

When QA fails:
1. PDF is still generated (operators need to see the data).
2. Every page has a diagonal red "DRAFT -- QA FAILED" watermark across the center.
3. Page 1 includes a red "QA FAILURE REPORT" box listing all failed checks with reasons.
4. The PDF filename includes `_DRAFT_` suffix: `momentum_20260227_070000_DRAFT.pdf`.
5. Telegram delivery caption includes: "QA FAILED: {failed_check_names}".
6. A `[SYSTEM]` Telegram message is sent: `"PDF QA FAILED: {pdf_type} -- {failure_reasons}"`.
7. QA failure is logged to `data/pdf_qa_log.jsonl`.

### 5.4 QA Failure Log Format

```json
{
  "ts": "2026-02-27T07:00:05.000Z",
  "pdf_type": "P1_MOMENTUM",
  "pdf_file": "data/reports/momentum_20260227_070000_DRAFT.pdf",
  "qa_passed": false,
  "failed_checks": [
    {
      "check": "COUNT_INTEGRITY",
      "message": "Count mismatch: 'Top 5 candidates' claims 5 but lists 4"
    }
  ],
  "passed_checks": ["TICKER_TIMESTAMPS", "NO_CONTRADICTIONS", "LANE_SEPARATION", "CLOSEST_MISSES", "DATA_HEALTH", "REGIME_CONSISTENCY"],
  "generation_time_ms": 4523,
  "page_count": 12
}
```

---

## 6. PER-PDF CONTENT REQUIREMENTS

### 6.1 P1 Momentum & Opportunity (07:00 UK)

**Mandatory Sections** (in order):
1. **Cover Page**: Date, time, system version, market regime, data health summary.
2. **Market Overview**: SPX, NDX, DAX, VIX, DXY -- each with price, change%, as-of timestamp.
3. **Regime Summary**: market_regime (Layer 1) + vol_regime distribution (Layer 2) + consistency status.
4. **Top Long Candidates**: Ranked table with ticker, direction, confidence, entry, stop, target, R:R, vol_regime, RVOL.
5. **Top Short Candidates**: Same format as #4 but for shorts (inverse ETPs).
6. **Volatility Breakout Watchlist**: Tickers in COMPRESSION regime with expansion probability.
7. **S15 2% Daily Target Section**: Today's best candidate, reachability score, compounding tracker.
8. **Sector Rotation Map**: Sector leadership/lagging summary.
9. **Bias Scores Table**: Full ISA universe with long_score, short_score, net_bias, as-of timestamp per ticker.
10. **Closest Misses**: Top 3 signals that almost qualified (QA Rule 5).
11. **Data Health**: Per-ticker completeness (QA Rule 6).
12. **Footer**: Generation timestamp (UTC + UK), system version, QA status.

**Forbidden Content**: Risk warnings, decay analysis, exit recommendations, drawdown metrics (lane separation).

### 6.2 P2 Risk & Structural (13:30 UK)

**Mandatory Sections** (in order):
1. **Cover Page**: Date, time, market regime, risk level summary (LOW/MEDIUM/HIGH/CRITICAL).
2. **Market Risk Overview**: VIX level, VIX term structure assessment, SPX trend, fear/greed composite.
3. **Leveraged ETP Decay Risk**: Per-ticker decay analysis with leverage factor, holding period decay%, recommended max hold.
4. **Volatility Clustering Warnings**: GARCH-proxy signals, vol-of-vol, regime transition probability.
5. **Liquidity Deterioration**: RVOL drops, spread widening proxy, volume anomalies.
6. **Sector Rotation Risk**: Leadership shift summary, sector under-/over-weight.
7. **Correlation Stress**: Cross-ticker correlation breakdown vs normal, regime correlation matrix.
8. **Per-Instrument Risk Scores**: Table with ticker, risk_score (0-100), risk_level, top_risk_factor.
9. **Portfolio Risk Matrix**: Aggregate exposure, direction concentration, heat score, drawdown status.
10. **Data Health**: Per-ticker completeness.
11. **Footer**: Generation timestamp, QA status.

**Forbidden Content**: Entry recommendations, buy/sell signals, target prices, opportunity scores (lane separation).

### 6.3 P3 EOD Review (22:00 UK)

**Mandatory Sections** (in order):
1. **Cover Page**: Date, session summary (trades taken, P&L, win rate).
2. **Full Price Action Table**: All ISA tickers with open, high, low, close, change%, volume, RVOL, MFE, MAE.
3. **S15 Gate Diagnostics**: Today's S15 candidate outcome (did it hit 2%? How close?).
4. **Trade Autopsy**: Each trade taken today with 5-grade analysis (entry timing, sizing, exit, strategy fit, overall).
5. **Missed Trade Analysis**: Signals blocked by firewall/gates that would have worked.
6. **High-Conviction Play Review**: Revisit today's highest-confidence signals -- were they correct?
7. **Tomorrow's Setup Watchlist**: Tickers showing setup formation (watchlist only, NOT entries).
8. **Factor Concentration Check**: Are we over-exposed to any single factor?
9. **Signal Engine Accuracy Audit**: Predicted vs actual outcomes for all signals.
10. **Closest Misses**: Top 3 near-misses (QA Rule 5).
11. **Action Items**: Operator checklist for tomorrow.
12. **Data Health**: Per-ticker completeness.
13. **Footer**: Generation timestamp, QA status.

### 6.4 P4 MEGA PDF (22:30 UK)

**Mandatory Sections** (14 sections, 40-80 pages):
1. Cover + Table of Contents
2. Executive Brief
3. System Architecture Overview
4. Signal Engine: Module-by-Module
5. Data Quality Audit
6. Gate Funnel Analysis
7. Strategy Design & Stop/Target Logic
8. Scoring Explainability
9. All Sessions: Ranked Play Archive
10. Command Center Status
11. Factor Concentration & Regime Intelligence
12. Performance Calibration & 2% Compounding Law
13. Testing, Deployment & Operational Status
14. Roadmap & Next Enhancements

**Lane exemption**: MEGA PDF covers all lanes. No lane separation enforcement.

### 6.5 P5 Overnight Risk (06:30 UK)

**Mandatory Sections**:
1. Overnight futures snapshot (ES, NQ, FTSE).
2. Asia session close (Nikkei, HSI, ASX).
3. Macro calendar for the day ahead.
4. VIX term structure assessment.
5. Risk-on/Risk-off composite assessment.
6. ISA portfolio overnight implications.
7. Data Health.

### 6.6 P6 Mid-Session Risk (16:40 UK)

**Mandatory Sections**:
1. Open positions status with exit scores.
2. Regime shift detection (since morning).
3. P&L snapshot (daily).
4. Exit recommendations for high exit-score positions.
5. Remaining opportunity window assessment.
6. Risk metrics (drawdown, consecutive losses, portfolio heat).
7. Data Health.

### 6.7 P7 Master Spec (00:00 UK)

**Mandatory Sections**:
1. System state summary.
2. Artifact inventory (all generated files today).
3. Configuration snapshot (non-sensitive).
4. Signal summary (by tier and decision).
5. Truth manifest (all session hashes).
6. Performance summary (daily P&L, win/loss, best/worst trade).
7. Learning status (edge ledger, drift).
8. Bibliography (academic references).

---

## 7. CROSS-PDF CONSISTENCY CHECKS

### 7.1 Same-Day Consistency Rules

When multiple PDFs are generated on the same day, the following must be consistent across all of them:

| Check | Description | Enforcement |
|-------|-------------|-------------|
| C1 | Same ticker must have same regime label in all PDFs generated within 30 minutes | Automated check at generation time |
| C2 | Equity and P&L figures must not decrease between P1 (morning) and P3 (evening) unless trades were closed at a loss | Logical consistency check |
| C3 | ISA universe must be identical across all PDFs on the same day | Hardcoded constant in shared module |
| C4 | Market regime transitions must be logged -- if P1 shows TRENDING_UP and P2 shows RANGE_BOUND, a transition MUST be recorded in regime_history | Cross-reference with DB |
| C5 | Data timestamps must be monotonically increasing: P5 (06:30) < P1 (07:00) < P2 (13:30) < P6 (16:40) < P3 (22:00) | Timestamp ordering check |

### 7.2 Cross-PDF Audit Log

After each PDF generation, write a cross-PDF audit entry to `data/pdf_cross_audit.jsonl`:

```json
{
  "ts": "2026-02-27T13:30:05.000Z",
  "pdf_type": "P2_RISK",
  "regime_at_generation": "TRENDING_UP_MOD",
  "tickers_mentioned": ["QQQ3.L", "NVD3.L", "TSL3.L"],
  "ticker_directions": {"QQQ3.L": "BULLISH", "NVD3.L": "BULLISH", "TSL3.L": "NEUTRAL"},
  "equity_snapshot": 10245.50,
  "previous_pdf_type": "P1_MOMENTUM",
  "previous_pdf_regime": "TRENDING_UP_MOD",
  "consistency_with_previous": "PASS"
}
```

---

## 8. DIVISION BY ZERO PROTECTION

The following known division-by-zero risks (from FORENSICS_MAP) MUST be guarded in all PDF generators:

| File | Line(s) | Expression | Guard Required |
|------|---------|-----------|---------------|
| `pdf_v2_momentum.py` | 314, 320 | `return_pct = (close[-1] - close[-2]) / close[-2]` | `if close[-2] == 0: return 0.0` |
| `pdf_v2_momentum.py` | 429 | `long_pts / max_pts * 100` | `if max_pts == 0: return 0.0` |
| `pdf_v2_momentum.py` | 382-383 | `bb_width = (upper - lower) / sma` | `if sma == 0: bb_width = 0.0` |
| `pdf_v2_risk.py` | 399 | `rvol = last_vol / avg_vol_20` | `if avg_vol_20 == 0: rvol = 0.0` |
| `volatility_regime.py` | 254 | `ann_vol / self._vix_ann_vol` | `if self._vix_ann_vol == 0: vix_ratio = 1.0` (already guarded with `> 0` check) |

**Rule**: Every division operation involving market data MUST have a zero-denominator guard. No exceptions.

---

## 9. FAILURE MODES

| # | Failure Mode | Detection | Impact | Mitigation |
|---|-------------|-----------|--------|------------|
| F1 | yfinance returns empty DataFrame for a ticker | `df.empty` or `len(df) < N` check | Missing ticker in PDF | Show "DATA UNAVAILABLE" in ticker row. Data Health shows FAIL. |
| F2 | yfinance rate-limited during PDF generation | HTTP 429, timeout | Partial data | Use cached data. Mark as "CACHED (stale)" in timestamp. |
| F3 | SQLite locked during PDF generation | `SQLITE_BUSY` after timeout | Missing DB-sourced sections | Retry once. If fails, show "DB UNAVAILABLE" in section. |
| F4 | PDF generation crashes mid-way | Unhandled exception | No PDF delivered | Outer try/except catches all. Generate minimal error PDF. Send [ERROR] to Telegram. |
| F5 | PDF file too large (> 10 MB) | File size check after render | Telegram upload fails | Reduce image quality. Split into parts if > 10 MB. |
| F6 | FPDF2 library not available | ImportError at module load | No PDF generation | Log error. Skip PDF generation. Send [ERROR] to Telegram. |
| F7 | QA gate itself crashes | try/except around preflight | QA result unknown | Treat as QA PASS with warning: "QA gate error -- results unverified". |
| F8 | Cross-PDF consistency check finds mismatch | Audit log analysis | Contradictory reports delivered | Append "CONSISTENCY WARNING" banner to later PDF. Log to cross-audit. |

---

## 10. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| PDF arrives with DRAFT watermark | Read QA failure report on page 1. Check which rule failed. Fix data source if needed. |
| PDF does not arrive at scheduled time | Check engine logs: `docker logs nzt48 --tail 50`. Check `data/reports/` for partially generated files. Check Telegram delivery log. |
| Count mismatch in PDF | Review the data source table. Likely a filtering bug. File issue. |
| Lane contamination detected | Review the offending section. Remove cross-lane content in the generator code. |
| Regime mismatch between PDFs | Check `regime_history` table for transitions. Expected if regime changed between PDF generation times. |
| yfinance data missing for ticker | Check if ticker is delisted. Try manual yfinance fetch. Update ISA universe if delisted. |

---

## 11. ACCEPTANCE TESTS

### 11.1 QA Gate Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T1 | Generate P1 with all data available | QA passes, clean PDF generated | No DRAFT watermark, all 7 QA checks PASS |
| T2 | Generate P1 with one ticker missing timestamp | QA fails on TICKER_TIMESTAMPS | DRAFT watermark, failure reason in QA report |
| T3 | Generate P1 with BULLISH + BEARISH for same ticker | QA fails on NO_CONTRADICTIONS | DRAFT watermark, contradiction listed |
| T4 | Generate P1 with risk warning text injected | QA fails on LANE_SEPARATION | L1 violation reported |
| T5 | Generate P1 with "Top 5 candidates" but only 4 listed | QA fails on COUNT_INTEGRITY | Count mismatch reported |
| T6 | Generate P1 without Closest Misses section | QA fails on CLOSEST_MISSES | Missing section reported |
| T7 | Generate P1 without Data Health section | QA fails on DATA_HEALTH | Missing section reported |
| T8 | Generate P1 when system regime changed 1 minute ago | QA passes with new regime | PDF shows new regime |
| T9 | Generate P2 with entry recommendation injected | QA fails on LANE_SEPARATION | L2 violation reported |

### 11.2 Content Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T10 | Generate P1 and verify all 12 mandatory sections present | All sections exist | Manual section-by-section verification |
| T11 | Generate P2 and verify all 11 mandatory sections present | All sections exist | Manual verification |
| T12 | Generate P3 and verify all 13 mandatory sections present | All sections exist | Manual verification |
| T13 | Generate P4 (MEGA) and verify 14 sections, 40+ pages | All sections, page count >= 40 | Automated page count + section detection |
| T14 | Generate P5, P6, P7 and verify mandatory sections | All sections present | Manual verification |
| T15 | Verify Closest Misses shows 3 entries sorted by gap | 3 entries, ascending gap | Automated content extraction |

### 11.3 Cross-PDF Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T16 | Generate P1 and P2 on same day, compare regime labels | Same regime (or transition recorded) | Automated comparison |
| T17 | Generate P1 and P2, compare ticker direction labels | No contradictions (or transition recorded) | Automated comparison |
| T18 | Generate all 7 PDFs in sequence, verify timestamps are monotonically increasing | P5 < P1 < P2 < P6 < P3 < P4 < P7 | Timestamp extraction and comparison |

### 11.4 Division-by-Zero Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T19 | Generate P1 with a ticker where close[-2] = 0 | No crash, return_pct = 0.0 | PDF generated successfully |
| T20 | Generate P2 with a ticker where avg_vol_20 = 0 | No crash, RVOL = 0.0 | PDF generated successfully |
| T21 | Generate P1 with a ticker where SMA(20) = 0 | No crash, BB_width = 0.0 | PDF generated successfully |

### 11.5 Delivery Tests

| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T22 | Generate P1 and verify Telegram delivery | PDF received in Telegram chat | File visible in chat |
| T23 | Generate P4 (MEGA, large file) and verify delivery | PDF received as document | File downloadable |
| T24 | Generate DRAFT PDF and verify Telegram caption | Caption includes "QA FAILED" | Caption text verified |

---

## 12. PROOF ARTIFACTS

| # | Artifact | Location | Description |
|---|----------|----------|-------------|
| A1 | QA gate test results | `artifacts/pdf_qa_test_results.json` | Pass/fail for each QA rule, each PDF type |
| A2 | Lane separation audit | `artifacts/pdf_lane_audit.json` | Results of lane enforcement for all 7 PDF types |
| A3 | Cross-PDF consistency audit | `data/pdf_cross_audit.jsonl` | Full audit trail for same-day consistency |
| A4 | Division-by-zero guard audit | `artifacts/div_zero_audit.txt` | grep output showing all division operations have guards |
| A5 | Sample PDFs (one per type) | `artifacts/sample_pdfs/` | One clean PDF and one DRAFT PDF per type |
| A6 | QA failure log sample | `data/pdf_qa_log.jsonl` | Sample entries showing failure logging |
| A7 | Content section inventory | `artifacts/pdf_section_inventory.json` | List of all mandatory sections per PDF type, with present/absent status |

---

## 13. CONFIGURATION PARAMETERS

All parameters MUST be configurable via `config/settings.yaml` under a `pdf_quality` section:

```yaml
pdf_quality:
  # QA gate
  qa_enabled: true
  qa_strict_mode: false  # true = QA failure blocks delivery entirely (not just watermark)

  # Data health thresholds
  data_health:
    pass_threshold: 0.80    # >= 80% completeness
    warn_threshold: 0.50    # >= 50% completeness
    fail_threshold: 0.0     # < 50% = FAIL

  # Closest misses
  closest_misses:
    count: 3
    applicable_pdfs: ["P1_MOMENTUM", "P3_EOD_REVIEW"]

  # Lane separation
  lane_enforcement:
    enabled: true
    strict_keywords: true  # Use keyword scanning

  # DRAFT watermark
  draft_watermark:
    text: "DRAFT -- QA FAILED"
    colour: [220, 30, 30]  # RGB red
    opacity: 0.3
    angle: 45

  # Cross-PDF consistency
  cross_pdf:
    regime_mismatch_window_minutes: 30
    audit_log_path: "data/pdf_cross_audit.jsonl"

  # Generation schedule (UK time, 24h format)
  schedule:
    p1_momentum: "07:00"
    p2_risk: "13:30"
    p3_eod_review: "22:00"
    p4_mega: "22:30"
    p5_overnight: "06:30"
    p6_mid_session: "16:40"
    p7_master_spec: "00:00"

  # QA failure log
  qa_log_path: "data/pdf_qa_log.jsonl"
  qa_log_retention_days: 30
```

---

## REVISION HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | NZT-48 Spec Engine | Initial binding specification |
