"""
NZT-48 Strategy Module
======================

STOP-LOSS ATR MULTIPLIERS — Design Rationale (C-10 Audit)
---------------------------------------------------------
Each strategy uses a different stop-loss ATR multiplier based on its
holding period, signal characteristics, and thesis type.  These values
are INTENTIONAL per-strategy design choices.  Do NOT harmonise them.

  Momentum / Trend (1.5x ATR)
    Standard for 1-5 day holds on leveraged ETPs.  Wilder (1978) showed
    1.5x ATR sits outside normal intraday noise (ATR14 = 0.8-1.5%),
    preventing the ~70% stop-out rate seen with fixed-% stops that sit
    AT the noise boundary (Cont & Kukanov 2017).
    Strategies:
      - regime_trend      (S1)   _STOP_ATR_MULT = 1.5
      - momentum_breakout (S2)   _STOP_ATR_MULT = 1.5
      - hot_scanner       (S10)  _STOP_ATR_MULT = 1.5
      - ai_thematic       (S11)  _STOP_ATR_MULT = 1.5
      - trend_compound    (S13)  _STOP_ATR_MULT = 1.5
      - rebalance_flow    (S14)  _STOP_ATR_MULT = 1.5
      - daily_target      (S15)  _STOP_ATR_MULT = 1.5
      - catalyst_narrative        _STOP_ATR_MULT = 1.5
      - vol_crush                 STOP_ATR_MULT  = 1.5

  Mean Reversion (1.0x ATR)
    Tighter stops because the thesis expects a quick snap-back to the
    mean.  Wider stops would defeat the edge by allowing the position
    to drift further from the mean, turning a reversion trade into an
    unhedged directional bet.  Mapped to range_bot in settings.yaml.
    Strategies:
      - mean_reversion  (S3, DORMANT)  _STOP_ATR_MULT = 1.0
      - gamma_squeeze                  _STOP_ATR_MULT = 1.0

  Event-Driven (2.0x ATR)
    Wider stops because earnings gaps are noisy and need room to
    develop the post-earnings-announcement drift (PEAD).  Ball &
    Brown (1968) showed drift persists for 60+ days; tight stops
    would shake out profitable positions during the initial volatility.
    Mapped to earnings_bot in settings.yaml (stop_logic: "2.0x ATR").
    Strategies:
      - pead_earnings  (S5)  STOP_ATR_MULT = 2.0

  Signal Engine (0.35-0.60x ATR)
    Fractional multipliers for the realtime execution engine's
    micro-signals.  These are NOT layered on top of strategy stops;
    they are independent entry-level stops for the signal_engine
    pipeline only, computed per setup type:
      - continuation:  0.40x ATR  (trend intact, tight stop behind structure)
      - breakout:      0.35x ATR  (breakout or fail, binary outcome)
      - mean_revert:   0.60x ATR  (reversion needs slightly more room)
      - default:       0.50x ATR
    Defined in signal_engine/engine.py :: _STOP_ATR_FRACTIONS.

  Bot-Level Overrides (settings.yaml bots section)
    The bot layer in settings.yaml also specifies stop widths that act
    as portfolio-level guardrails:
      - bull_bot:      1.5x ATR  (strategies S1, S2, S5, S10, S11, S13, S14)
      - range_bot:     1.0x ATR  (strategies S3, S4, S8, S9, S12)
      - bear_bot:      0.8x ATR  (strategies S6, S7, S1-short-bias)
      - earnings_bot:  2.0x ATR  (strategy S5)


ATR% MINIMUM THRESHOLDS — Design Rationale (C-11 Audit)
--------------------------------------------------------
Four different ATR% minimums exist across the codebase.  Each serves a
distinct purpose tied to its module's role.  These are INTENTIONAL.
Do NOT harmonise them.

  Daily Target S15 (0.8% ATR minimum)
    Lowered from 1.4% to 0.8% because LSE leveraged ETPs have lower
    raw ATR% than US 3x ETFs.  0.8% ATR is sufficient for the 2%
    daily target on .L tickers.  (Ben-David et al. 2018)
    File: strategies/daily_target.py :: _MIN_ATR_PCT = 0.8

  Opportunity Scanner (3.0% ATR minimum)
    The scanner pre-filters for tickers where a 2% NET move (after
    spread, slippage, platform fees) is physically feasible.  The
    higher 3.0% threshold accounts for total round-trip costs
    (~0.5-1.0%) that erode the gross move, so raw ATR must be well
    above the 2% target.
    File: strategies/opportunity_scanner.py :: _MIN_ATR_PCT_THRESHOLD = 3.0

  Signal Engine Strict Gate (1.0% ATR minimum)
    The signal_engine tradability gate requires 1.0% ATR in strict
    mode.  This is the general-purpose hard floor: any ticker below
    1.0% ATR is physically untradeable for a 2% target even before
    costs.
    File: signal_engine/gates.py :: STRICT_MIN_ATR_PCT = 1.0

  Signal Engine Fallback Gate (0.6% ATR minimum)
    Last-resort relaxation (step 4 of 4) when the strict funnel
    produces zero signals.  0.6% ATR can still yield a profitable
    trade on high-conviction setups with tight spreads, but it is
    flagged as RELAXED in the gate report.
    File: signal_engine/gates.py :: FALLBACK_STEP4_ATR_PCT = 0.60
"""
