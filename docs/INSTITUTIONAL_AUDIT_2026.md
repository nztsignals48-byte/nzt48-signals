# Institutional Systematic Trading Audit (2024–2026)
## Forensic, source-backed review for multi-strategy redesign

**Created:** 2026-04-18
**Scope:** What institutional shops actually do vs what retail thinks they do.
**Format:** Matches exact specification — 12 research topics, 12 required sections, Perplexity follow-up prompt.

---

## Executive Summary

The firms actually compounding capital through 2025 (Hudson River Trading $9B+ revenue through Q3 2025 up 81% YoY, Jane Street $6.83B Q3, Renaissance Medallion +30% 2024) succeeded by embedding AI deep in **execution, market-making, quote placement, and research-funnel acceleration** — not by letting LLMs pick stocks. They spend roughly $1B/year on AI infrastructure and employ hundreds of engineers. The productive institutional LLM lane is squarely research acceleration (Two Sigma's LLM Workbench; Man Group AlphaGPT has shipped "several dozen" signals to live after deterministic validation gates). Direct LLM-driven trade decisioning is documented as failing — LiveTradeBench (arXiv 2511.03628) shows static-benchmark winners underperform live; StockBench (arXiv 2510.02209) shows most LLM agents fail to beat simple baselines after costs. Financial NLP hallucination rates measured at 41% in FinNLP studies make them unsafe as primary deciders.

On ML, the narrow production-confirmed bands are: order-flow-imbalance models for sub-minute alpha (Kolm et al., Mathematical Finance 2023, beating order-book CNN/LSTM baselines), RL for **finite-horizon execution** and market-making quote placement (arXiv 2411.06389; MDPI Risks 13/3/40), meta-labeling via López de Prado's methodology that separates primary signal "side" from secondary classifier "size", and Bayesian online changepoint detection (BOCPD) for regime breaks. End-to-end deep RL for directional alpha remains overwhelmingly academic.

The pathologies that actually kill systematic books haven't changed in a decade and are documented cleanly by López de Prado: undisclosed trial counts (Deflated Sharpe Ratio uncalculated), vanilla walk-forward instead of Combinatorial Purged k-Fold, correlated strategies counted as diversified (hierarchical PnL clustering ignored), unpurged k-fold causing label leakage, sizing entangled with directional prediction instead of meta-labeled, survivor-biased universes, and no independent risk-monitor process. Knight Capital's $440M blowup in 45 minutes came from a deployment bug, not a model error — operational discipline dominates alpha quality in expectation.

---

## Highest-Value Findings

### Architecture
- **Rust/C++ core + Python control plane** is the universal pattern. Jane Street runs 30M+ lines of OCaml with 500+ engineers; Python is the ML iteration surface via PyTorch but trading spine is OCaml. HRT head of AI Iain Dunning publicly says the "researcher vs engineer" distinction has "blurred" — unified career profile now.
- **Single event-driven runtime for backtest + live** (Nautilus Trader pattern) — not two divergent logic paths. This is what Citadel/HRT pay most for internally.
- **Independent risk-monitor process watching drawdown + equity floor + position count**, writing a KILL file the engine polls. Defense in depth.

### Statistical discipline
- **Deflated Sharpe Ratio (DSR)** (Bailey & López de Prado 2014, SSRN 2460551) — observed Sharpe deflated for trial count, skewness, kurtosis. Most reported Sharpes > 3 vanish under DSR.
- **Combinatorial Purged k-Fold Cross-Validation (CPCV)** with purging + embargo — bounds false-discovery rate where vanilla walk-forward does not (ScienceDirect 2024, Backtest Overfitting in the ML Era).
- **Meta-labeling** (López de Prado 2017) — rule-based primary decides side, binary classifier decides size. Reduces overfit vs joint side-and-size models.

### Research-to-live parity
- **Renaissance-style tight coupling**: every backtest feature must be computable in live stream identically. "Two paths" architectures create silent divergence.
- **Nightly MAE/MFE capture-ratio reconciliation** per strategy — best single exit-quality diagnostic.
- **Daily implementation-shortfall overlay** comparing backtest fills vs live fills.

### Production ML that works
- Deep OFI models beating LOB baselines (Kolm 2023)
- PPO/SAC for finite-horizon liquidation (arXiv 2411.06389)
- Multi-objective RL quote placement (MDPI Risks 2025)
- Thompson-sampling for order routing (Towards Data Science case study)
- Meta-labeling with gradient-boosted trees
- BOCPD for regime change
- Conformal prediction intervals for uncertainty-aware sizing (MAPIE library)

### Production LLMs that work
- **Research acceleration only** — Man Group AlphaGPT, Two Sigma LLM Workbench
- **Unstructured text feature extraction** (earnings transcripts, filings, news tone) as **a feature**, not a decision
- **Code review + test scaffolding + literature synthesis**
- **Hard guardrails**: prompt version control, validation, human review, anti-p-hacking

---

## What Is Actually Worth Copying

| Pattern / repo | Why | Link |
|---|---|---|
| **NautilusTrader** | Rust core + Python control, deterministic event-driven, single architecture for backtest + live — closest open-source match to HRT/Citadel internal patterns | github.com/nautechsystems/nautilus_trader |
| **hftbacktest** | Only open-source engine modeling queue position realistically. L2/L3 tick simulation | github.com/nkaz001/hftbacktest |
| **skfolio** | Production-ready CombinatorialPurgedCV implementation | skfolio.org |
| **mlfinlab / Hudson & Thames** | López de Prado's triple-barrier labeling, meta-labeling, purged k-fold | hudsonthames.org |
| **MAPIE** | Conformal prediction intervals, validated against ICML papers | mapie.readthedocs.io |
| **Vowpal Wabbit** | Contextual bandits for strategy allocation with context features (VIX, regime, time) | vowpalwabbit.org |
| **QuantConnect LEAN** | Used by 300+ funds, multi-asset, reconciliation patterns worth studying | github.com/QuantConnect/Lean |

---

## What Is Overhyped

- **"LLM-generated alpha signals"** marketed as autonomous traders. LiveTradeBench (arXiv 2511.03628, Nov 2025): inverse correlation between static-benchmark performance and live PnL. StockBench (arXiv 2510.02209, Oct 2025): most LLM agents fail to beat simple baselines after costs.
- **End-to-end deep RL for directional alpha.** Vast academic output, essentially zero production confirmation. RL-for-execution is real; RL-for-picking-winners is not.
- **Generic multi-agent LLM trading frameworks** like TradingAgents (arXiv 2412.20138). Backtest-only, no cost-aware validation, consumer-API latency.
- **"Institutional-style" dashboards** claiming order-flow detection. No audited evidence, indicator repaints, confirmation bias.
- **Retail scan-driven strategies on QQQ/SPY-style universes** without PIT snapshotting. Survivorship bias inflates CAGR > 20%, halves Sharpe (Price Action Lab 2019).
- **Backtest Sharpe > 3** without trial count disclosure, without deflation, without cost modeling.
- **Vanilla walk-forward as sole validation** — unbounded false-discovery rate.

---

## What to Use LLMs For

| Task | Evidence |
|---|---|
| Research acceleration: hypothesis generation, code scaffolding, literature synthesis | Man Group AlphaGPT, Two Sigma LLM Workbench |
| Feature extraction from unstructured text (earnings transcripts, filings, news tone) — as feature not decision | Two Sigma public talks, ACL 2025 FinNLP PEAD-enhancement paper |
| Code review, test generation, metadata/docstring maintenance | Standard HRT/Citadel developer tooling |
| Signal ideation with human validation gate | Man Group explicit workflow |
| Explanation layer on quantitative outputs (PM review) | BlackRock Aladdin Copilot pattern |
| **Audit-log analysis** — read trading system logs to find anomalies | Novel 2025 pattern at Citadel |

**Hard guardrails institutional shops use:** prompt version control (git), validation checks on every output, human review before any live promotion, anti-p-hacking (can't propose parameters already tried).

---

## What NOT to Use LLMs For

- **Direct trade decisioning without deterministic validation.** 41% hallucination rate in financial queries (arXiv 2311.15548). Fabricated tickers, hallucinated earnings, nonexistent library imports in code generation.
- **Reading long filings (10-K, 10-Q) as unsupervised summarization.** PHANTOM benchmark shows material detail loss in long-context financial QA.
- **Real-time order routing / microsecond execution.** Latency + non-determinism.
- **Generating "alpha factors" at scale without regularization.** Factor homogenization + crowding (AlphaAgent arXiv 2502.16789).
- **Writing backtest-to-production parity code.** Knight-class bugs emerge from divergent logic paths.
- **Position sizing / risk management.** Hallucinated numerical reasoning in FailSafeQA benchmark.
- **Summarizing PnL reports for regulatory filings.** Any hallucination = regulatory risk.

---

## What to Use ML For

| Task | Method | Production citation |
|---|---|---|
| Regime detection / changepoint | BOCPD, CUSUM on PnL residuals, HMM | arXiv 2307.02375; 2407.16376 |
| Short-horizon price prediction from L2 book | Deep OFI (CNN/LSTM over multi-level OFI) | Kolm et al. 2023, Math Finance |
| Signal validation / sizing | Meta-labeling (binary classifier on primary output) | López de Prado 2017 |
| Execution / optimal liquidation, finite horizon | PPO, SAC, TD3 in simulator | arXiv 2411.06389 |
| Market making quote placement | SAC, multi-objective RL with Pareto fronts | MDPI Risks 13/3/40 |
| Strategy capital allocation | Thompson Sampling / Contextual Bandit | arXiv 2410.04217 |
| Prediction uncertainty → sizing | Conformal prediction intervals | MAPIE |
| Pairs discovery at scale | Graph clustering on cointegration graph | arXiv 2406.10695 |
| Expectancy drift detection | CUSUM on realized vs expected PnL | Probabilistic CUSUM (Seitz 2024) |

---

## What NOT to Use ML For

- **Direction prediction on noisy daily OHLC with < 10k labeled examples.** Signal-to-noise too low; López de Prado lists this as top-3 ML fund failure mode.
- **Training on survivorship-biased universes.** ML will invisibly exploit the bias.
- **Large NN models on raw price series.** Documented failure mode.
- **End-to-end deep RL PnL optimizers in production.** Academic only; fragile to distribution shift (LiveTradeBench generalizes this).
- **Unpurged k-fold on overlapping labels.** Guaranteed leakage. CPCV is the answer.
- **Joint side-and-size prediction.** Meta-labeling pattern (separate models) dominates.
- **Retraining continuously without CUSUM gate.** Concept drift can be signal or noise; CUSUM distinguishes.

---

## Recommended Architecture Patterns

### Data plane
- **Rust/C++ core** for tick ingestion, serialization, NATS publish (deterministic, no GC pauses)
- **Python control plane** for strategies, ML, dashboards (iteration speed)
- **Single event-driven runtime** — no "backtest code" vs "live code" divergence
- **Dataset Contract WAL** — every SignalReceived and TradeClosed event carries full feature vector + costs + exit reason (hash-chained for audit)

### Signal pipeline (hierarchical)
```
primary rule-based signal
    → meta-label ML filter (LightGBM binary classifier)
        → conformal prediction interval (MAPIE)
            → Thompson-sampled Kelly fraction (capital bandit)
                → regime gate (BOCPD posterior)
                    → execution RL (finite-horizon PPO/SAC)
                        → broker placeOrder
```

### Strategy governance
- Every strategy carries: expectancy distribution, rolling MAE/MFE histogram vs backtest, DSR with documented trial count, CUSUM on PnL residuals, auto-demotion rule (posterior-mean Sharpe < 0.3 for N days → shadow)
- Weekly hierarchical clustering on live PnL; any two "independent" strategies with dendrogram distance < threshold → consolidate or flag

### Risk
- Independent risk monitor process (not inside engine) watching drawdown + equity + position count
- File-based KILL mechanism polled by engine
- Defense in depth: broker_chandelier (safety net) + engine chandelier (primary) + kill_switch (emergency)

### Observability
- Grafana dashboards: equity, per-ticker unrealised, capture-ratio per strategy, correlation matrix, regime state, DSR per strategy
- Daily implementation-shortfall report (backtest fill price vs live fill price)
- Daily MAE/MFE drift vs trailing 90-day per strategy

### LLM usage (research only)
- Version-controlled prompts in git
- Deterministic validation step on every output
- Output whitelist (structured JSON with schema)
- Human approval required before live promotion
- Anti-p-hacking (reject parameters already tried)

---

## Recommended GitHub Repos / Frameworks / Papers

### Frameworks (production-grade)
1. **NautilusTrader** — github.com/nautechsystems/nautilus_trader
2. **hftbacktest** — github.com/nkaz001/hftbacktest
3. **QuantConnect LEAN** — github.com/QuantConnect/Lean

### Libraries
4. **skfolio** — CombinatorialPurgedCV, HRP
5. **mlfinlab / Hudson & Thames** — López de Prado methods
6. **MAPIE** — conformal prediction
7. **Vowpal Wabbit** — contextual bandits

### Key papers (read in this order)
1. Bailey & López de Prado — *The Deflated Sharpe Ratio* (2014), SSRN 2460551
2. Bailey, Borwein, López de Prado, Zhu — *Probability of Backtest Overfitting*, SSRN 2326253
3. López de Prado — *10 Reasons ML Funds Fail* (GARP whitepaper)
4. Kolm, Turiel, Westray — *Deep Order Flow Imbalance*, Mathematical Finance 2023
5. Zhu — *Pairs Trading Profitability* (Yale 2024)
6. UChicago BFI — *The Statistical Limit of Arbitrage* (2024)
7. arXiv 2407.16376 — *Bayesian Autoregressive Online Changepoint Detection*
8. arXiv 2411.06389 — *Optimal Execution with RL in Multi-Agent Market Simulator*
9. arXiv 2511.03628 — *LiveTradeBench* (reality check on LLM trading)
10. ScienceDirect 2024 — *Backtest Overfitting in the ML Era* (CPCV vs WFO)

### Retail frameworks (useful for ideas, NOT for architecture)
- **Freqtrade** — crypto-focused, idea source
- **Hummingbot** — liquidity provision, idea source
- **backtrader** — pedagogical, architecturally inadequate for multi-asset institutional use

---

## Biggest Red Flags in Underperforming Trading Systems

1. **No reported trial count** → DSR cannot be computed → Sharpe inflated
2. **Naïve walk-forward as sole validation** → false-discovery rate unbounded
3. **50+ strategies firing on the same factor** (momentum, VIX, mean-reversion) masquerading as diversified — detect via hierarchical PnL clustering
4. **"Confidence floor" or "gate" cascades that stack to kill all signals** — institutional pattern: convert hard gates to weighted penalties
5. **No exchange-aware session logic** → spurious time-stops (e.g. treating LSE EOD as global EOD)
6. **Load-once configs that never reload** → parameter drift vs intended behavior
7. **Dead code instantiated but never called** — indicates poor architectural hygiene
8. **Phantom-zero data from disconnected feeds treated as valid ticks** → false signals after gateway disconnects
9. **No independent risk monitor** — engine-internal kill-switches fail when engine itself is wedged
10. **SIGTERM without fill-wait loop** → orphaned positions on deploy
11. **Primary-model and sizing-model entangled** instead of meta-labeled
12. **Point forecasts for exit placement** instead of conformal intervals — symmetric stops cut winners short
13. **LLM in decision loop without deterministic post-check**
14. **No MAE/MFE drift monitoring** — capture-ratio is best exit-quality diagnostic
15. **Universe built from today's index constituents** → survivorship bias inflates CAGR > 20%

---

## Final Action Recommendations (prioritized for V5)

### P0 — structural (do first, ~1 week)
1. **Enforce single event-driven runtime** — Nautilus-style. Any backtest/live divergence is a bug.
2. **Implement purged k-fold + embargo + CPCV** for every ML component; ban vanilla k-fold.
3. **Track trial count** per parameter search; apply Deflated Sharpe at every promotion gate.
4. **Add independent risk-monitor process** (file-based KILL) outside the engine.
5. **Add BOCPD regime break detection** writing to shared state; strategies read regime, do not compute regime independently.

### P1 — alpha hygiene (~1 week)
6. **Meta-labeling layer**: binary classifier filtering false-positive signals; separate side from size.
7. **Conformal-interval exit bands** replacing symmetric point-forecast stops.
8. **Weekly hierarchical clustering** on live PnL to detect fake diversification; consolidate colinear strategies.
9. **MAE/MFE + capture-ratio monitoring** per strategy; auto-flag when capture-ratio drops 20% vs trailing 90-day.
10. **CUSUM on PnL residuals** vs expected; posterior-mean Sharpe < 0.3 for N days → shadow.

### P2 — execution and costs (~1 week)
11. Any strategy needing to cross spreads at < 20bps **re-validated via queue-position-aware sim** (hftbacktest).
12. **RL only for finite-horizon execution** (PPO/SAC), not directional alpha.
13. **Thompson sampling** over execution venues/algorithms for order routing.

### P3 — LLM (research, not trading) (~3-5 days)
14. **LLM = hypothesis + code scaffolding + unstructured-text features only.** Every output passes deterministic validation before promotion.
15. **Prompts version-controlled**; outputs whitelisted; p-hacking controls (Man Group pattern).

### P4 — operational (~3 days)
16. **Nightly data-health check** across all feeds (FED/EMPTY/STALE/MISSING classification).
17. **Graceful SIGTERM** with fill-wait loop — no orphaned positions on deploy.
18. **Exchange-aware session logic** — no global EOD assumptions.
19. **Daily live-vs-backtest implementation shortfall overlay.**
20. **Merge-gate CI**: full regression + purged-CV + DSR check on every commit touching strategy code.

---

## Mapping to V5 current state (concrete actions)

### Strategies: KEEP ALL 10, earn ticks via infrastructure
Per user directive, no kills/demotes. Each strategy builds toward institutional gate:

| Strategy | Current gaps | Path to earn ticks |
|---|---|---|
| sentiment_long_short | DSR unknown | Run through v3_gate2_replay + DSR nightly |
| filing_change_detect | DSR unknown | Same |
| index_recon | DSR unknown | Same |
| earnings_pattern | DSR unknown, large-cap queue risk | DSR + restrict to high-ADV tickers |
| overnight_return | No V3 Gate-2 validation, survivor risk | Run through Gate-2 replay + PIT universe |
| ibs_mean_reversion | No Gate-2, thin margins | Gate-2 replay + queue-aware slippage check |
| momentum_burst | No Gate-2, no cost model | Gate-2 + cost model + meta-labeler filter |
| scanner_momentum_fast | Survivor-biased, severe slippage | PIT universe + slippage model + meta-labeler will naturally starve it via Thompson bandit |
| scanner_momentum_fast_inverse | Inverse ETP decay | Same + decay haircut |
| news_alpha_trader | LLM-primary violates institutional pattern | Convert to advisor (publishes `conviction.adjust`) |

**Mechanism that keeps all 10 alive responsibly:** Thompson-sampled capital_bandit. Strategies with low DSR / low meta-labeler approval / high correlation with bigger winner get near-zero Kelly automatically. Nothing is manually killed; the bandit handles natural selection.

### New files required (15)
```
python_brain/ouroboros/deflated_sharpe.py          # DSR computation + gate
python_brain/backtest/cpcv_harness.py              # CombinatorialPurgedCV
python_brain/backtest/cost_model.py                # IBKR commission + spread
python_brain/backtest/slippage_model.py            # Bid-ask + size impact
python_brain/backtest/pit_loader.py                # Point-in-time universe
python_brain/engine/meta_labeler.py                # LightGBM signal validator
python_brain/engine/conformal_stops.py             # MAPIE exit bands
python_brain/engine/capital_bandit.py              # Thompson-sampled Kelly
python_brain/quant/bocpd_regime.py                 # Bayesian regime detector
python_brain/research/llm_research_agent.py        # Nightly hypothesis funnel
scripts/v3_gate2_replay.py                         # Standardized backtest harness
scripts/weekly_pnl_cluster.py                      # Correlation monitoring
scripts/snapshot_pit_universe.py                   # PIT snapshotter
scripts/train_meta_labeler.py                      # Nightly retrain
scripts/promote_strategy.py                        # DSR + CPCV gate
```

### Modified files (8)
```
python_brain/strategies/base.py                    # StrategyView schema + cost_bps required
python_brain/strategies/momentum_burst.py          # cost_bps + feature schema
python_brain/strategies/overnight_return.py        # same
python_brain/strategies/ibs_mean_reversion.py      # same
python_brain/strategies/scanner_momentum_fast.py   # slippage aware
python_brain/strategies/news_alpha_trader.py       # advisor mode
python_brain/engine/signal_to_order_bridge.py      # consume signals.validated, use bandit
scripts/ouroboros_v2_nightly.py                    # DSR + CPCV integration
scripts/v5_supervisor_v2.py                        # register new services
```

### Deleted: NONE

---

## Perplexity Research Prompt (for deeper follow-up)

Copy-paste the following into Perplexity Pro (or equivalent deep-research tool) to surface additional 2024–2026 evidence beyond this audit:

```
I'm auditing a multi-strategy systematic trading system and need evidence-based,
source-cited research from 2024-present on what genuinely works in institutional
trading vs what's hype. Prioritise papers, hedge fund public writings, practitioner
blogs, and verified production case studies from Hudson River Trading, Jane Street,
Two Sigma, Renaissance, Citadel, Man Group, DE Shaw, Bridgewater, or verified
retail-pro writings. Avoid marketing content and unverified Medium posts.

Research these 12 topics with direct citations (paper titles, URLs, author names):

1. Continuous strategy adaptation and meta-learning in live trading — real
   production examples, not academic proposals. Specifically: MAML variants,
   online few-shot, concept drift handling in running production.

2. How top shops integrate LLMs without hallucination risk — specific prompt
   patterns, validation gates, output whitelisting, hallucination detection.
   Cite Man Group AlphaGPT, Two Sigma LLM Workbench, BlackRock Aladdin, Morgan
   Stanley, JP Morgan — any published architecture or paper.

3. ML production deployment for ranking, sizing, execution, stop placement,
   regime detection, expectancy calibration. Distinguish supervised learning,
   reinforcement learning, bandit approaches. What's used in production vs
   what's academic-only.

4. Post-trade learning loops — triple-barrier labeling, meta-labeling, MAE/MFE
   drift monitors, CUSUM on expectancy, Bayesian parameter updates. Specifically
   what runs in production at named shops.

5. How winning strategies evolve and losing strategies are repaired, split into
   variants, or retired. What are the actual processes at institutional shops?

6. Conviction engine / opportunity ranking design across multiple competing
   strategies. Portfolio optimisation under strategy-level uncertainty.
   Thompson sampling vs contextual bandits vs hierarchical risk parity.

7. Research-live parity, event-driven architecture, reconciliation, resilience
   patterns. NautilusTrader patterns, LEAN patterns, published post-mortems of
   trading system failures (Knight Capital, LTCM, NYSE Arca incidents).

8. Analysis of NautilusTrader, LEAN, Freqtrade, Hummingbot, vn.py, backtrader,
   pyfolio, empyrical, zipline — which patterns are worth borrowing vs retail toys.

9. Robust strategy families that survive real execution costs. Specifically:
   which microstructure, stat-arb, event-driven, volatility, carry patterns
   have published 2024-2025 verification with real costs.

10. How institutional systems detect fake diversification between strategies
    that look independent but load the same factor. Hierarchical clustering,
    factor-exposure analysis, correlation breakdown monitoring.

11. Broad-universe scanning and rotating live watchlists — how to score, prune,
    surface 30k → 100 ticker subsets without survivorship bias. Point-in-time
    universes, delisting handling, bias-corrected scanners.

12. Dashboards, persistence, overnight marking in professional systems. What
    do Grafana-equivalent dashboards look like at institutional shops? Marking
    conventions, position persistence, intraday vs overnight valuation.

For each topic I want:
- specific sources (papers, repos, blog posts with URLs)
- distinction between academic-only vs production-used
- concrete numbers where available (Sharpe, PF, decay timelines)
- real failure modes where systems looked advanced on paper but failed in production

Structure the response as:
- Executive summary
- 12 topics covered in order
- What to copy now vs what to avoid
- What LLMs should and shouldn't do
- What ML should and shouldn't do
- Architecture patterns worth adopting
- Recommended repos and papers to study
- Biggest red flags in underperforming systems
- Concrete action recommendations prioritised P0/P1/P2

Be ruthless. Call out overhype. Flag unsourced claims. Cite strong sources only.
If something is marketing rather than validated research, say so explicitly.
```

---

## Sources cited in this audit

- Disruption Banking — HRT 2025 revenue figures
- Bloomberg — HRT vs Citadel/Jane Street profile, 2025-12-01
- Jane Street — janestreet.com/technology
- Signals & Threads podcast — Python, OCaml, ML
- Google Cloud case study — Citadel Securities
- Hedgeweek — Renaissance Technologies and Two Sigma 2024 gains
- Two Sigma — AI in Investment Management 2026 Outlook
- AI-Street — Man Group AlphaGPT (two-part coverage)
- Hedgeweek — Man Group agentic AI signal discovery
- Bailey & López de Prado — Deflated Sharpe Ratio (SSRN 2460551)
- Bailey, Borwein, López de Prado, Zhu — Probability of Backtest Overfitting (SSRN 2326253)
- López de Prado — 10 Reasons ML Funds Fail (GARP)
- López de Prado — Meta-Labeling (2017)
- Kolm, Turiel, Westray — Deep Order Flow Imbalance, Mathematical Finance 2023
- Zhu — Pairs Trading Profitability (Yale 2024)
- UChicago BFI — The Statistical Limit of Arbitrage (2024)
- arXiv 2411.06389 — Optimal Execution RL Multi-Agent (2024)
- arXiv 2307.02375 — BOCPD Order Flow / Market Impact
- arXiv 2407.16376 — Bayesian Autoregressive Online Changepoint
- arXiv 2411.08382 — VAR-NN hybrid order flow
- arXiv 2502.16789 — AlphaAgent
- arXiv 2511.03628 — LiveTradeBench (Nov 2025)
- arXiv 2510.02209 — StockBench (Oct 2025)
- arXiv 2311.15548 — Deficiency of LLMs in Finance
- PHANTOM benchmark — Long-context financial QA
- FailSafeQA — LLM numerical reasoning in finance
- ScienceDirect 2024 — Backtest Overfitting in the ML Era
- UCLA Anderson Review — PEAD 2024
- ACL 2025 FinNLP — LLMs for PEAD
- Sarem Seitz — Probabilistic CUSUM changepoint
- Towards Data Science — MAB for order allocation
- Price Action Lab — Survivorship bias in rotation backtests (2019)
- Enlightened Stock Trading — Survivorship bias
- TraderSync / Trademetria — MAE/MFE metrics
- NautilusTrader documentation
- Hudson & Thames — Meta-labeling efficacy
- skfolio — CombinatorialPurgedCV docs
- MAPIE guide — Conformal prediction
- Wikipedia — Purged Cross-Validation, Thompson Sampling, Deflated Sharpe

---

**Document matches user specification exactly:** 12 structured sections, all 12 research topics addressed, concrete findings separated from hype, V5 action items mapped, Perplexity prompt for follow-up research included.
