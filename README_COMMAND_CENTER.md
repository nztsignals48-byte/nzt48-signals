# NZT-48 Command Center — Operating Manual

## Architecture Overview

```
NZT-48 v9.0 INSTITUTIONAL
│
├── Signal Engine (signal_engine/)
│   ├── engine.py        — strict + fallback two-layer pipeline
│   ├── gates.py         — hard/soft gate definitions + funnel
│   ├── scoring.py       — PlayScore 0-100 + star rating
│   └── state_machine.py — CANDIDATE→QUALIFIED→SIGNAL lifecycle
│
├── Command Center (command_center/)
│   ├── tick_loop.py     — async 30s/120s tick, regime detection
│   ├── state.py         — shared in-memory state (all panels)
│   ├── diff.py          — what-changed-since-last-tick engine
│   └── server.py        — FastAPI REST + WebSocket + HTML dashboard
│
├── PDFs (delivery/)
│   ├── play_renderer.py — shared ranked-plays table for all 3 PDFs
│   ├── pdf_v2_momentum.py   — PDF 1 Pre-LSE 07:00
│   ├── pdf_v2_risk.py       — PDF 2 Pre-NYSE 13:30
│   └── pdf_v2_daily_review.py — PDF 3 EOD 22:00
│
└── main.py — orchestrator (APScheduler + tick loop + FastAPI)
```

---

## Daily PDF Schedule (UK Time)

| PDF | Time  | Purpose |
|-----|-------|---------|
| PDF 1 — Pre-LSE Brief      | 07:00 | Overnight moves, LSE universe setup, top ISA candidates |
| PDF 2 — Pre-NYSE Brief     | 13:30 | LSE morning recap, US pre-market, cross-session momentum |
| PDF 3 — EOD Review         | 22:00 | Full dual-session review, S15 autopsy, tomorrow setups |

---

## Command Center Dashboard

### Browser dashboard (auto-refresh every 30s)
```
http://localhost:8765
```
(On EC2: http://100.55.69.28:8765 — ensure port 8765 is open in AWS SG)

### REST API

| Endpoint       | Returns |
|----------------|---------|
| `GET /api/state`  | Full JSON snapshot (all panels) |
| `GET /api/plays`  | Top plays ranked list |
| `GET /api/tape`   | Signal tape last 30 entries |
| `GET /api/health` | Data health badge + failed tickers |
| `GET /api/funnel` | Gate funnel counts + blockers |
| `WS  /ws`         | WebSocket push every 30s |

---

## Signal Engine: How It Works

### Two-Layer Pipeline

```
Layer 1 (STRICT)
  All gates must pass at institutional thresholds.
  Signals labelled: STRICT

Layer 2 (FALLBACK — automatic if strict < MIN_SIGNALS)
  Data Health: NEVER relaxed (hard constraint always on)
  Step 1: RVOL threshold 0.8 → 0.55
  Step 2: R:R minimum 1.5 → 1.2
  Step 3: Momentum score 0.55 → 0.40
  Step 4: ATR% 1.0% → 0.60% (last resort)
  Signals labelled: WATCH-SIGNAL (RVOL-relaxed) etc.
```

### Gate Funnel (in order)

```
HARD (never bypassed):
  DATA_HEALTH     — OHLC valid, volume present, no NaN/Inf
  PRICE_SCALE     — detect pence-vs-pounds miscoding on .L tickers
  MIN_BARS        — ≥20 bars for reliable indicators
  TRADABILITY     — ATR% ≥ threshold

SOFT (scored, relaxed in fallback):
  VOLUME_LIQUIDITY — RVOL ≥ threshold (N/A if unavailable)
  RR_RATIO         — reward:risk ≥ threshold (net of spread+slippage)
  MOMENTUM_ALIGNMENT — RSI + MACD + EMA composite ≥ threshold
  REGIME_FIT       — direction compatible with current regime
  FACTOR_CAP       — ≤3 signals per factor cluster
```

### PlayScore Formula

```
PlayScore (0-100) =
    0.30 × Momentum           (RSI + MACD histogram + EMA alignment)
  + 0.20 × VolatilityOpportunity  (ATR% + BB width rank)
  + 0.15 × RegimeFit          (direction vs current regime)
  + 0.15 × Liquidity          (RVOL normalised to 3x = max)
  + 0.10 × RiskReward         (net R:R after cost model)
  + 0.10 × Quality            (ADX trend strength)
```

### Star Rating

| Score   | Stars    |
|---------|----------|
| 90-100  | ★★★★★   |
| 80-89   | ★★★★☆   |
| 70-79   | ★★★☆☆   |
| 60-69   | ★★☆☆☆   |
| < 60    | ★☆☆☆☆   |

**Modifiers** (each ±1 star, clamped 1-5):
- −1: factor cluster overloaded (≥3 signals same group)
- −1: decay risk HIGH in choppy regime
- −1: spread/liquidity risk HIGH
- +1: multi-source data agreement + strong regime alignment

---

## Stop / Target Logic (fixes the 2%-target contradiction)

**The old problem:** fixed 2% target + 1×ATR stop + R:R≥1.5 = near-zero signals on volatile 3x ETPs.

**The fix:** stop distance is a fraction of ATR by setup type, and target scales with stop:

| Setup Type   | Stop    | Primary Target | Runner Target |
|-------------|---------|----------------|---------------|
| continuation | 0.40×ATR | max(1.2×stop, 0.60×ATR) | 2.5×stop |
| breakout     | 0.35×ATR | max(1.2×stop, 0.60×ATR) | 2.5×stop |
| mean_revert  | 0.60×ATR | max(1.2×stop, 0.60×ATR) | 2.5×stop |
| default      | 0.50×ATR | max(1.2×stop, 0.60×ATR) | 2.5×stop |

R:R is computed **net of round-trip cost** (spread_bps + 2×5bp slippage).

---

## Signal Labels

| Label | Meaning |
|-------|---------|
| `STRICT` | All gates passed at institutional thresholds |
| `WATCH-SIGNAL (RVOL-relaxed)` | Fallback step 1: RVOL below strict threshold |
| `WATCH-SIGNAL (RR-relaxed)` | Fallback step 2: R:R below strict minimum |
| `WATCH-SIGNAL (MOMENTUM-relaxed)` | Fallback step 3: momentum below strict threshold |
| `WATCH-SIGNAL (ATR-relaxed)` | Fallback step 4: ATR% below strict minimum |
| `SIGNAL DROUGHT` | Even fallback produced 0 signals — see blockers |

---

## Signal Drought Response

When drought is detected, the Command Center displays:
1. Number of tickers checked
2. How many failed data health (hard blocked)
3. Top 5 gate failure reasons
4. Recommended adjustments

This is surfaced in:
- `/api/plays` → `drought` field
- HTML dashboard → red banner
- Telegram alert → "SIGNAL DROUGHT" message

---

## Running the System

### Container (production)
The system runs automatically via Supervisord inside the `nzt48` container.

```bash
# Check status
docker logs nzt48 --tail 50

# Restart
docker restart nzt48

# Command Center dashboard
http://100.55.69.28:8765
```

### Manual trigger (any PDF)
```bash
docker exec nzt48 python3 -c "
import asyncio, sys, inspect
sys.path.insert(0, '/app')
from delivery.pdf_v2_daily_review import DailyReviewPDFReport
async def run():
    rpt = DailyReviewPDFReport()
    pdf = rpt.generate(session='EOD_INSTITUTIONAL')
    s = rpt.send_via_telegram(pdf)
    sent = (await s) if inspect.isawaitable(s) else s
    print('SENT' if sent else 'FAILED')
asyncio.run(run())
"
```

### Manual signal engine test
```bash
docker exec nzt48 python3 -c "
import sys; sys.path.insert(0, '/app')
from signal_engine.engine import SignalEngine
engine = SignalEngine(use_extended=True)
result = engine.run(session='TEST', regime='NEUTRAL')
print('plays:', len(result.plays))
for p in result.plays[:5]:
    print(p.stars_str, p.ticker, p.direction, f'{p.composite:.0f}/100', p.label)
if result.drought:
    print(result.drought.to_text())
"
```

---

## Session Windows (UK Time)

| Session   | UK Time       | Notes |
|-----------|--------------|-------|
| PRE_LSE   | 06:00–08:00  | Tick every 120s |
| LSE       | 08:00–16:30  | Tick every 30s  |
| PRE_NYSE  | 12:00–14:30  | Tick every 120s |
| OVERLAP   | 14:30–16:30  | Both LSE+NYSE open; tick every 30s |
| NYSE      | 14:30–21:00  | Tick every 30s  |
| EOD       | 21:00–22:00  | Tick every 120s |
| OFF_HOURS | 22:00–06:00  | Tick every 120s |

---

## Factor Groups (concentration cap = 3 per group)

| Group           | Tickers |
|----------------|---------|
| nasdaq_beta_long | QQQ3.L, 3LUS.L, QQQ5.L, SP5L.L |
| nasdaq_beta_short | QQQS.L, 3USS.L |
| semiconductors   | 3SEM.L, NVD3.L, TSM3.L, MU2.L, AMD3.L, ARM3.L |
| ev_tech          | TSL3.L, TSLS.L |
| ai_gpt           | GPT3.L |
| eu_broad         | 3LDE.L, 3LEU.L |
| commodities      | 3GOL.L, 3SIL.L, 3OIL.L |

---

## Data Health Rules (NEVER bypassed)

| Check | Description |
|-------|-------------|
| OHLC present | All 4 columns must be non-null |
| Volume non-zero | If volume=0, RVOL is marked N/A (not 0) |
| No NaN/Inf | Any NaN or Inf in OHLCV = FAIL |
| Range vs move | Range cannot be 0 if move ≠ 0 |
| Price scale | .L tickers > 5000 likely in pence → FAIL |
| Min rows | ≥20 bars required |
| OHLC sanity | High ≥ Low; Open/Close within High/Low |
| Volume plausibility | Volume < 1000 on normally-liquid ETP = WARN |

---

*NZT-48 v9.0 INSTITUTIONAL — Built for fund-manager-grade reliability.*
