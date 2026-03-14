# NZT-48 PDF Desk Notes -- Quality Standard

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-PDNS-001           |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- IC/PM Quality Gate |
| Related         | PDF_DESK_NOTES_SPEC.md (NZT48-ANNEX-004) -- technical QA specification |

---

## 1. PURPOSE

This document defines the **quality standard** that all NZT-48 PDF reports must meet to achieve "100/100 desk notes" status. While PDF_DESK_NOTES_SPEC.md defines the 7 QA rules, lane separation, and pre-flight audit gate in technical detail, this document answers the governance questions:

- What does "100/100 desk notes" actually mean?
- What are the quality dimensions?
- What is the pre-send QA gate from an IC/PM approval perspective?
- What are the pass/fail criteria and consequences?

**Relationship to PDF_DESK_NOTES_SPEC.md:** This document sets the standard; PDF_DESK_NOTES_SPEC.md implements it. Any PDF that passes the technical QA gate but fails the quality dimensions defined here is not 100/100.

---

## 2. WHAT 100/100 DESK NOTES MEANS

A 100/100 desk note is a PDF report that an institutional portfolio manager would trust to make intraday decisions from without independently verifying the data. This is the standard NZT-48 targets. The following properties are mandatory:

### 2.1 Every Number Is Verified

- All prices, returns, volumes, and scores are sourced from the system's data pipeline with provenance.
- No number is hardcoded, estimated, or rounded without disclosure.
- Every percentage shows the numerator and denominator basis (e.g., "return: +2.3% (close 142.50 vs open 139.30)").

### 2.2 Every Chart Is Sourced

- Charts display their data source and time range.
- No chart uses placeholder or synthetic data.
- Chart axes are labelled with units and scale.

### 2.3 Every Recommendation Is Actionable

- Entry/stop/target prices are specific numbers, not ranges (unless the range is justified).
- The strategy name, direction, and confidence score accompany every recommendation.
- Risk/reward ratios are stated explicitly.

### 2.4 No Contradictions

- A ticker cannot be BULLISH in one section and BEARISH in another within the same PDF (unless timeframe-qualified).
- Regime labels are consistent across all sections.
- Count labels match the actual number of items listed.

### 2.5 Timestamps on All Data

- Every data table has an "as-of" timestamp in UTC.
- Every chart has a time range label.
- The PDF header states the generation timestamp in both UTC and UK local time.

### 2.6 Regime-Consistent

- The current market regime (TRENDING_UP, RANGE_BOUND, HIGH_VOLATILITY, etc.) is stated prominently.
- All analysis and recommendations are framed within the current regime context.
- No recommendation contradicts the stated regime (e.g., no aggressive long entries during RISK_OFF).

### 2.7 Lane-Separated

- Momentum PDFs contain only opportunities, entries, and upside analysis.
- Risk PDFs contain only risk assessments, decay analysis, and defensive positioning.
- Review PDFs contain only post-session analysis and lessons.
- Violations are automatically detected and the PDF is watermarked DRAFT.

---

## 3. PRE-SEND QA GATE

### 3.1 The 7-Check Automated Gate

Before any PDF is delivered (to Telegram, to archive, or to any consumer), it passes through a 7-check automated QA gate. The gate runs after content assembly but before final rendering.

| Check # | Check Name | What It Validates | Failure Consequence |
|---------|------------|-------------------|---------------------|
| QA-1 | Ticker Timestamps | Every ticker mentioned has an associated "as-of" timestamp | DRAFT watermark |
| QA-2 | No Contradictions | No ticker has contradictory direction labels (BULLISH + BEARISH) within the same PDF | DRAFT watermark |
| QA-3 | Lane Separation | PDF content stays within its designated lane (momentum, risk, review, audit) | DRAFT watermark |
| QA-4 | Count Integrity | If a section claims "N items", exactly N items are listed | DRAFT watermark |
| QA-5 | Closest Misses | P1 (Momentum) and P3 (EOD Review) include a Closest Misses section with 1-3 entries | DRAFT watermark |
| QA-6 | Data Health | Every PDF includes a Data Health section showing per-ticker completeness | DRAFT watermark |
| QA-7 | Regime Consistency | The regime label in the PDF matches the current system regime at generation time | DRAFT watermark |

### 3.2 Gate Pipeline

```
Data Collection --> Content Assembly --> QA Pre-Flight --> Decision
                                              |
                                         ALL 7 PASS? --YES--> Clean PDF --> Deliver
                                              |
                                         ANY FAIL? ------> DRAFT PDF (watermarked) + QA failure log
                                                           |
                                                           +--> Operator notified via [SYSTEM] Telegram
                                                           +--> QA failure logged to data/pdf_qa_log.jsonl
```

### 3.3 DRAFT Watermark Specification

When QA fails:

1. The PDF is still generated (operators need to review the data).
2. Every page has a diagonal red "DRAFT -- QA FAILED" watermark.
3. Page 1 includes a red QA FAILURE REPORT box listing all failed checks.
4. The filename includes `_DRAFT_` suffix.
5. The Telegram caption includes "QA FAILED: {failed_check_names}".

---

## 4. QA PASS/FAIL CRITERIA

### 4.1 PASS

- All 7 checks return green.
- Clean PDF generated without watermark.
- Normal delivery to all channels.
- QA result logged as PASS with generation time.

### 4.2 FAIL

- One or more checks return red.
- DRAFT-watermarked PDF generated.
- Operator notified via `[SYSTEM]` Telegram message.
- QA failure logged to `data/pdf_qa_log.jsonl` with:
  - Timestamp
  - PDF type
  - Failed checks with reasons
  - Passed checks (for context)
  - Generation time
  - Page count

### 4.3 Consequence of Repeated Failures

| Failure Pattern | Escalation |
|----------------|------------|
| Single DRAFT PDF | Operator reviews QA report, fixes if possible. Normal operations continue |
| 3 consecutive DRAFT PDFs of the same type | Operator MUST investigate root cause. Log in incident register |
| DRAFT PDFs for 2+ different PDF types in same session | System-wide data quality investigation. PM notified |
| All PDFs DRAFT for an entire day | PM review mandatory. Potential DEGRADED mode declaration |

---

## 5. QUALITY DIMENSIONS

Each PDF is evaluated across 6 quality dimensions. These dimensions define what IC/PM reviewers assess during periodic quality audits (weekly during paper mode, monthly during live).

### 5.1 Accuracy

- All numbers are mathematically correct and sourced from verified data.
- Return calculations apply leverage once and only once.
- Division-by-zero guards produce reasonable fallback values, not NaN or Infinity.
- Score and confidence values are within valid bounds [0, 100].

### 5.2 Completeness

- All mandatory sections for the PDF type are present (P1: 12 sections, P2: 11, P3: 13, P4: 14, P5: 7, P6: 7, P7: 8).
- Every ticker in the active universe appears in the Data Health section.
- Closest Misses section is populated (for P1 and P3).
- No "TODO", "PLACEHOLDER", or empty sections.

### 5.3 Consistency

- No intra-PDF contradictions (direction, regime, count).
- Cross-PDF consistency checks pass for same-day reports.
- Ticker data timestamps are monotonically increasing across the daily PDF sequence.

### 5.4 Timeliness

- PDFs generate within 5 minutes of their scheduled time.
- Data timestamps are within the defined TTL for each field.
- No stale cached data presented as current.

### 5.5 Actionability

- Every recommendation includes specific entry, stop, and target prices.
- Risk/reward ratios are stated.
- Confidence scores contextualize each recommendation.
- The S15 2% Daily Target candidate is highlighted with reachability score.

### 5.6 Presentation

- Tables are properly formatted with aligned columns.
- Charts render correctly with labelled axes.
- Text is readable (font size, contrast).
- Page count is within expected range for each PDF type.
- Cover page contains all required metadata.

---

## 6. ACCEPTANCE TESTS

| Test ID | Scenario | Expected Result | Pass Criteria |
|---------|----------|-----------------|---------------|
| PDNS-T01 | Generate P1 with all data available; run QA gate | QA passes, clean PDF with all 12 sections | No DRAFT watermark; all 7 QA checks PASS; all 6 quality dimensions met |
| PDNS-T02 | Generate P1 with one ticker missing timestamp; verify QA catches it | QA fails on TICKER_TIMESTAMPS | DRAFT watermark applied; QA failure report identifies the specific ticker |
| PDNS-T03 | Generate P1 with count mismatch ("Top 5" but lists 4); verify QA catches it | QA fails on COUNT_INTEGRITY | DRAFT watermark; failure report shows claimed vs actual count |
| PDNS-T04 | Generate 3 consecutive DRAFT P1 PDFs; verify escalation | Operator notified of repeated failure pattern | Investigation required per Section 4.3 escalation rules |
| PDNS-T05 | Generate all 7 PDF types in sequence; verify cross-PDF consistency | No regime contradictions; timestamps monotonically increasing | Cross-PDF audit log shows all consistency checks PASS |
| PDNS-T06 | Inject a risk warning into P1 content; verify lane separation catches it | QA fails on LANE_SEPARATION | L1 violation reported; DRAFT watermark applied |
| PDNS-T07 | Generate P1 and verify Closest Misses section contains 1-3 entries sorted by gap | Section present with correct entry count and sort order | Entries show ticker, rejection gate, gap to qualify |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial quality standard |

---

*End of Document NZT48-ANNEX-PDNS-001*
