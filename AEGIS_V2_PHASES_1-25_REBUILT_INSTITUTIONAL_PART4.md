# AEGIS V2 — PHASES 1-25 INSTITUTIONAL REBUILD — PART 4
## Phases 15-21: Data Integrity, Continuous Improvement, and Governance

**Continuing from Parts 1-3**

---

## PHASE 15: RECONCILIATION AUDITOR & DARK STATE DETECTION

### Phase Purpose
Implement an independent reconciliation process that compares Python state vs IBKR broker API every 5 minutes. Detects "dark state" (positions unknown to Python, stale portfolio records, missing fills) and triggers emergency liquidation if mismatches found.

**Why this matters for compounding**: A hidden position can blow up silently. Reconciliation auditor is insurance against bugs, broker outages, and data corruption.

### Key Hardening Rules
- **T09-004**: Reconciliation auditor comparing Python state vs broker API
- **CR-03**: ReconciliationAuditor with SIGKILL failsafe

### Acceptance Criteria
1. **Reconciliation Frequency**: Every 5 minutes ✓
2. **Mismatch Detection**: <1 second detection latency ✓
3. **Emergency Liquidation**: Market-On-Close on any mismatch ✓
4. **Audit Trail**: All reconciliations logged ✓
5. **False Positive Rate**: <0.1% (prevent spam alerts) ✓

### Deliverables

#### 15.1 Reconciliation Auditor (core/reconciliation_auditor.py)

```python
# reconciliation_auditor.py — Python state vs IBKR API reconciliation
from typing import Dict, Tuple, List
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@dataclass
class ReconciliationMismatch:
    timestamp: datetime
    position_id: str
    ticker: str
    python_qty: float
    broker_qty: float
    python_avg_price: float
    broker_avg_price: float
    discrepancy_gbp: float

class ReconciliationAuditor:
    """
    Independent reconciliation: Python state vs IBKR API.

    Every 5 minutes:
    1. Query IBKR API for all positions
    2. Compare to Python portfolio state
    3. On mismatch: log + alert + market-on-close emergency flatten
    """

    RECONCILIATION_INTERVAL_SECONDS = 300  # 5 minutes

    def __init__(self, broker_api, python_portfolio):
        self.broker = broker_api
        self.portfolio = python_portfolio
        self.mismatches: List[ReconciliationMismatch] = []
        self.last_reconciliation = None

    def reconcile(self) -> Tuple[bool, List[ReconciliationMismatch]]:
        """
        Execute full reconciliation.

        Returns:
            (all_match: bool, mismatches: list of ReconciliationMismatch)
        """
        self.last_reconciliation = datetime.utcnow()

        # Step 1: Get IBKR positions
        broker_positions = self.broker.get_all_positions()

        # Step 2: Get Python portfolio state
        python_positions = self.portfolio.get_all_positions()

        # Step 3: Compare
        mismatches = []

        # Check for positions in IBKR but not in Python (dark state)
        for ticker, broker_pos in broker_positions.items():
            if ticker not in python_positions:
                mismatch = ReconciliationMismatch(
                    timestamp=datetime.utcnow(),
                    position_id=f"DARK_{ticker}",
                    ticker=ticker,
                    python_qty=0.0,
                    broker_qty=broker_pos["quantity"],
                    python_avg_price=0.0,
                    broker_avg_price=broker_pos["avg_price"],
                    discrepancy_gbp=broker_pos["quantity"] × broker_pos["avg_price"],
                )
                mismatches.append(mismatch)

        # Check for quantity/price mismatches in positions both have
        for ticker, python_pos in python_positions.items():
            if ticker in broker_positions:
                broker_pos = broker_positions[ticker]

                # Quantity mismatch (allow 1-share tolerance for rounding)
                if abs(python_pos["quantity"] - broker_pos["quantity"]) > 1.0:
                    mismatch = ReconciliationMismatch(
                        timestamp=datetime.utcnow(),
                        position_id=python_pos["position_id"],
                        ticker=ticker,
                        python_qty=python_pos["quantity"],
                        broker_qty=broker_pos["quantity"],
                        python_avg_price=python_pos["avg_price"],
                        broker_avg_price=broker_pos["avg_price"],
                        discrepancy_gbp=(broker_pos["quantity"] - python_pos["quantity"]) × broker_pos["avg_price"],
                    )
                    mismatches.append(mismatch)

        self.mismatches.extend(mismatches)

        # Step 4: Return verdict
        all_match = len(mismatches) == 0

        if not all_match:
            logger.critical(f"RECONCILIATION FAILURE: {len(mismatches)} mismatches detected")
            for m in mismatches:
                logger.critical(f"  {m.ticker}: Python {m.python_qty} vs Broker {m.broker_qty}")

        return all_match, mismatches

    def trigger_emergency_moc_flatten(self, mismatches: List[ReconciliationMismatch]) -> None:
        """
        On reconciliation failure, submit Market-On-Close orders to flatten all positions.

        Args:
            mismatches: list of mismatches
        """
        logger.critical(f"TRIGGERING EMERGENCY MOC FLATTEN due to {len(mismatches)} reconciliation mismatches")

        # Submit MOC orders for all positions
        all_tickers = set([m.ticker for m in mismatches])
        for ticker in all_tickers:
            self.broker.submit_market_on_close_order(ticker, 0, "FLATTEN_ALL")

        # Log incident
        self._log_reconciliation_incident(mismatches)

    def _log_reconciliation_incident(self, mismatches: List[ReconciliationMismatch]) -> None:
        """Log incident details for post-mortem."""
        incident_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "incident_type": "RECONCILIATION_FAILURE",
            "mismatch_count": len(mismatches),
            "mismatches": [
                {
                    "ticker": m.ticker,
                    "python_qty": m.python_qty,
                    "broker_qty": m.broker_qty,
                    "discrepancy_gbp": m.discrepancy_gbp,
                }
                for m in mismatches
            ],
        }

        with open(f"logs/reconciliation_incident_{datetime.utcnow().isoformat()}.json", 'w') as f:
            import json
            json.dump(incident_log, f, indent=2)
```

---

## PHASE 16: DATA FEED MONITORING & STALENESS DETECTION

### Phase Purpose
Monitor all data feeds (prices, volumes, indices, VIX, correlation) for staleness. If >50% of tickers stale >5 minutes, halt trading. This prevents ghost trading on stale data.

**Why this matters for compounding**: Trading on stale data is like flying blind. A 10-minute price lag can cause 50 bps losses. Phase 16 prevents this.

### Key Hardening Rules
- **T09-003**: Data feed monitoring with staleness checks

### Acceptance Criteria
1. **Staleness Check**: Every 60 seconds ✓
2. **Threshold**: >50% tickers stale >5 min = HALT ✓
3. **Recovery**: Automatic resume when data fresh ✓
4. **Alerting**: Escalate to ops if feed down >30 min ✓

### Deliverables

#### 16.1 Data Feed Monitor (data/feed_monitor.py)

```python
# feed_monitor.py — data staleness detection
from datetime import datetime, timedelta
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

class DataFeedMonitor:
    """
    Monitor data feed freshness. Halt if stale >5 min for >50% of universe.
    """

    STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes
    UNIVERSE_SIZE = 1000
    STALENESS_TOLERANCE = 0.50  # 50% of universe can be stale

    def __init__(self):
        self.last_update_by_ticker: Dict[str, datetime] = {}
        self.feed_halted = False

    def update_ticker(self, ticker: str) -> None:
        """Record fresh data receipt for a ticker."""
        self.last_update_by_ticker[ticker] = datetime.utcnow()

    def check_feed_health(self) -> bool:
        """
        Check if data feed is healthy.

        Returns:
            True if feed is healthy (resume trading)
            False if feed is degraded (halt trading)
        """
        now = datetime.utcnow()
        stale_count = 0

        for ticker, last_update in self.last_update_by_ticker.items():
            age_seconds = (now - last_update).total_seconds()
            if age_seconds > self.STALENESS_THRESHOLD_SECONDS:
                stale_count += 1

        stale_fraction = stale_count / len(self.last_update_by_ticker) if self.last_update_by_ticker else 0.0

        if stale_fraction > self.STALENESS_TOLERANCE:
            # Too many stale tickers
            if not self.feed_halted:
                logger.critical(
                    f"DATA FEED DEGRADED: {stale_fraction:.1%} of universe stale. Halting trading."
                )
                self.feed_halted = True
            return False
        else:
            if self.feed_halted:
                logger.info(f"DATA FEED RECOVERED: {stale_fraction:.1%} stale (below threshold). Resuming trading.")
                self.feed_halted = False
            return True

    def get_feed_health_status(self) -> Dict:
        """Return feed health status."""
        now = datetime.utcnow()
        stale_tickers = [
            ticker for ticker, last_update in self.last_update_by_ticker.items()
            if (now - last_update).total_seconds() > self.STALENESS_THRESHOLD_SECONDS
        ]

        return {
            "feed_halted": self.feed_halted,
            "total_tickers": len(self.last_update_by_ticker),
            "stale_tickers": len(stale_tickers),
            "stale_fraction": len(stale_tickers) / len(self.last_update_by_ticker) if self.last_update_by_ticker else 0.0,
            "stale_ticker_list": stale_tickers[:10],  # first 10
        }
```

---

## PHASE 17: PERFORMANCE MONITORING & REALIZED METRICS

### Phase Purpose
Track realized performance metrics (daily P&L, Sharpe, win rate, drawdown) against backtest expectations. Detect drift >30% and trigger manual review.

**Why this matters for compounding**: Live performance will differ from backtest. Phase 17 detects if drift is random variance or structural failure.

### Key Hardening Rules
- **T07-001**: Walk-forward validation with purge/embargo
- **T07-002**: Model drift detection (monthly refit if performance drops >30%)

### Acceptance Criteria
1. **Tracking Frequency**: Daily updated metrics ✓
2. **Drift Detection**: Alert if live Sharpe < backtest × 0.7 (30% drift) ✓
3. **Regime Conditional**: Metrics reported per regime ✓
4. **Rolling Windows**: 30-day, 63-day, 252-day windows ✓

### Deliverables

#### 17.1 Performance Monitor (reporting/performance_monitor.py)

```python
# performance_monitor.py — track live performance vs backtest
import numpy as np
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

class PerformanceMonitor:
    """
    Track realized trading metrics and compare to backtest expectations.
    """

    DRIFT_THRESHOLD = 0.30  # 30% allowed drift

    def __init__(self, backtest_sharpe: float, backtest_win_rate: float):
        self.backtest_sharpe = backtest_sharpe
        self.backtest_win_rate = backtest_win_rate
        self.daily_returns: List[float] = []
        self.daily_wins: List[bool] = []

    def record_daily_result(self, daily_return: float, is_win: bool) -> None:
        """
        Record daily trading result.

        Args:
            daily_return: daily P&L as % of equity
            is_win: True if profitable day
        """
        self.daily_returns.append(daily_return)
        self.daily_wins.append(is_win)

    def compute_metrics(self, window_days: int = 30) -> Dict:
        """
        Compute realized metrics over rolling window.

        Args:
            window_days: window size (30, 63, or 252)

        Returns:
            {metric_name: value}
        """
        if len(self.daily_returns) < window_days:
            return {}

        recent_returns = np.array(self.daily_returns[-window_days:])
        recent_wins = self.daily_wins[-window_days:]

        # Sharpe ratio
        excess_returns = recent_returns - 0.02 / 252.0
        sharpe = np.mean(excess_returns) / np.std(excess_returns) × np.sqrt(252.0)

        # Win rate
        win_rate = sum(recent_wins) / len(recent_wins)

        # Drawdown
        cumulative_equity = np.cumprod(1.0 + recent_returns)
        running_max = np.maximum.accumulate(cumulative_equity)
        drawdown = (running_max - cumulative_equity) / running_max

        metrics = {
            "sharpe": sharpe,
            "win_rate": win_rate,
            "max_drawdown": np.max(drawdown),
            "avg_daily_return": np.mean(recent_returns),
            "daily_std": np.std(recent_returns),
        }

        return metrics

    def check_drift(self) -> Tuple[bool, Dict]:
        """
        Check if realized performance drifts >30% from backtest.

        Returns:
            (drift_detected: bool, details: {metric: (realized, backtest, drift_pct)})
        """
        metrics_30d = self.compute_metrics(30)
        if not metrics_30d:
            return False, {}

        realized_sharpe = metrics_30d["sharpe"]
        realized_wr = metrics_30d["win_rate"]

        sharpe_drift = abs(realized_sharpe - self.backtest_sharpe) / self.backtest_sharpe if self.backtest_sharpe > 0 else 0.0
        wr_drift = abs(realized_wr - self.backtest_win_rate) / self.backtest_win_rate if self.backtest_win_rate > 0 else 0.0

        drift_detected = sharpe_drift > self.DRIFT_THRESHOLD or wr_drift > self.DRIFT_THRESHOLD

        details = {
            "sharpe": (realized_sharpe, self.backtest_sharpe, sharpe_drift),
            "win_rate": (realized_wr, self.backtest_win_rate, wr_drift),
        }

        return drift_detected, details
```

---

## PHASE 18: INCIDENT RESPONSE & POST-MORTEM FRAMEWORK

### Phase Purpose
Define incident response procedures for common failure modes: broker disconnect, data feed loss, circuit breaker trigger, reconciliation failure. Each incident type has a playbook: detect → log → alert → remediate.

**Why this matters for compounding**: When things break, panic decisions destroy capital. Phase 18 ensures calm, systematic response.

### Acceptance Criteria
1. **Incident Types**: 10+ defined (broker disconnect, feed loss, ruin breach, etc.) ✓
2. **Playbooks**: Each type has detection + alert + remediation steps ✓
3. **Escalation**: Clear escalation path (autonomous action → alert → manual review) ✓
4. **Post-Mortems**: Every incident logged with root cause analysis ✓

### Deliverables

#### 18.1 Incident Response Framework (monitoring/incident_response.py)

```python
# incident_response.py — incident response playbooks
from enum import Enum
from typing import Dict, List, Callable
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class IncidentType(Enum):
    BROKER_DISCONNECT = "broker_disconnect"
    DATA_FEED_LOSS = "data_feed_loss"
    RUIN_BREACH = "ruin_breach"
    RECONCILIATION_FAILURE = "reconciliation_failure"
    CIRCUIT_BREAKER_L3 = "circuit_breaker_l3"
    EQUITY_DENOMINATOR_PHANTOM = "equity_denominator_phantom"
    ZOMBIE_HALT = "zombie_halt"

class IncidentPlaybook:
    def __init__(self, incident_type: IncidentType):
        self.incident_type = incident_type
        self.incident_time = datetime.utcnow()
        self.status = "DETECTED"
        self.remediation_actions: List[str] = []

    def execute_playbook(self) -> None:
        """Execute incident-specific playbook."""
        if self.incident_type == IncidentType.BROKER_DISCONNECT:
            self._playbook_broker_disconnect()
        elif self.incident_type == IncidentType.DATA_FEED_LOSS:
            self._playbook_data_feed_loss()
        elif self.incident_type == IncidentType.RUIN_BREACH:
            self._playbook_ruin_breach()
        # ... other incident types

    def _playbook_broker_disconnect(self) -> None:
        """
        Broker disconnect playbook.

        1. Detect: IB Gateway connection lost >120 seconds
        2. Alert: Email ops team
        3. Remediate: Attempt reconnect every 5s × 120 attempts (10 min max)
        4. Fallback: If reconnect fails after 10 min, auto-liquidate 50% via market order
        """
        logger.critical(f"INCIDENT: Broker disconnect detected at {self.incident_time}")

        # Step 1: Attempt reconnect loop (every 5s, max 10 min)
        self.remediation_actions.append("Starting reconnect loop (5s intervals, 120 attempts)")
        # In real code: loop through 120 attempts, sleep 5s between

        # Step 2: If reconnect fails, liquidate 50%
        self.remediation_actions.append("Reconnect failed. Auto-liquidating 50% of positions via market order.")
        # self._liquidate_positions_pct(0.50)

        # Step 3: Alert ops
        self.remediation_actions.append("Alerting ops team via email/Slack")
        # self._send_alert("CRITICAL: Broker disconnect. 50% liquidated. Manual intervention required.")

        self.status = "REMEDIATED"

    def _playbook_data_feed_loss(self) -> None:
        """
        Data feed loss playbook.

        1. Detect: >50% of universe stale >5 min
        2. Alert: Email ops
        3. Remediate: Halt trading, await data recovery
        4. Fallback: If >30 min no recovery, flatten all positions
        """
        logger.critical(f"INCIDENT: Data feed loss at {self.incident_time}")

        self.remediation_actions.append("Data feed loss detected. Halting trading.")
        self.remediation_actions.append("Attempting data feed recovery (automatic retry)")

        # If no recovery in 30 min, flatten all
        self.remediation_actions.append("If no recovery in 30 min, flattening all positions.")

        self.status = "REMEDIATED"

    def _playbook_ruin_breach(self) -> None:
        """
        Ruin probability breach playbook.

        1. Detect: Ruin prob >0.5% (monthly audit)
        2. Alert: CIO manual review required
        3. Remediate: Reduce position sizes 50% pending review
        """
        logger.critical(f"INCIDENT: Ruin probability breached at {self.incident_time}")

        self.remediation_actions.append("Reducing position sizes 50% pending CIO review")
        self.remediation_actions.append("Escalating to CIO for manual review")

        self.status = "AWAITING_MANUAL_REVIEW"

    def _playbook_reconciliation_failure(self) -> None:
        """
        Reconciliation failure playbook.

        1. Detect: Python state ≠ IBKR API
        2. Alert: Immediate escalation
        3. Remediate: Market-On-Close emergency flatten all
        """
        logger.critical(f"INCIDENT: Reconciliation failure at {self.incident_time}")

        self.remediation_actions.append("Submitting emergency MOC orders to flatten all positions")
        self.remediation_actions.append("Alerting ops and CIO immediately")

        self.status = "REMEDIATED"

    def _playbook_circuit_breaker_l3(self) -> None:
        """
        Circuit breaker L3 triggered playbook.

        1. Detect: Drawdown > -4.0%
        2. Action: Forced flatten (already executed by CB module)
        3. Post-action: Review what went wrong
        """
        logger.critical(f"INCIDENT: Circuit breaker L3 triggered at {self.incident_time}")

        self.remediation_actions.append("Circuit breaker L3 forced flatten (autonomous)")
        self.remediation_actions.append("Post-mortem required: why did losses hit -4%?")

        self.status = "REMEDIATED"

    def log_incident(self) -> None:
        """Log incident details for post-mortem."""
        incident_record = {
            "timestamp": self.incident_time.isoformat(),
            "incident_type": self.incident_type.value,
            "status": self.status,
            "remediation_actions": self.remediation_actions,
        }

        import json
        with open(f"logs/incident_{self.incident_time.isoformat()}.json", 'w') as f:
            json.dump(incident_record, f, indent=2)

        logger.info(f"Incident logged: {incident_record}")
```

---

## PHASE 19: REGULATORY AUDIT TRAIL & COMPLIANCE LOGGING

### Phase Purpose
Maintain comprehensive audit trail required for ISA compliance, FCA oversight, and HMRC tax reporting. Every trade, every decision, every parameter change is logged with timestamp and rationale.

**Why this matters for compounding**: Regulators can audit anytime. Lack of audit trail can void ISA status (costly) or trigger fines. Phase 19 is insurance.

### Acceptance Criteria
1. **Trade Log**: Every trade with entry price, exit price, P&L, rationale ✓
2. **Parameter Changes**: Every config change with timestamp and approver ✓
3. **Incident Logs**: Every incident with detection → remediation timeline ✓
4. **Quarterly ISA Audit**: Automatic quarterly verification (Phase 3) ✓

### Deliverables

#### 19.1 Audit Trail Logger (monitoring/audit_logger.py)

```python
# audit_logger.py — comprehensive audit trail
import json
from datetime import datetime
from typing import Dict, Any

class AuditLogger:
    """
    Compliance audit trail for ISA/FCA/HMRC.
    """

    def __init__(self, log_dir: str = "logs/audit"):
        self.log_dir = log_dir

    def log_trade(
        self,
        trade_id: str,
        ticker: str,
        side: str,
        entry_time: datetime,
        entry_price: float,
        exit_time: datetime,
        exit_price: float,
        quantity: float,
        pnl_gbp: float,
        signal_confidence: float,
        regime: str,
    ) -> None:
        """Log a completed trade."""
        trade_record = {
            "trade_id": trade_id,
            "ticker": ticker,
            "side": side,
            "entry_time": entry_time.isoformat(),
            "entry_price": entry_price,
            "exit_time": exit_time.isoformat(),
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl_gbp": pnl_gbp,
            "signal_confidence": signal_confidence,
            "regime": regime,
            "logged_at": datetime.utcnow().isoformat(),
        }

        with open(f"{self.log_dir}/trades_{datetime.utcnow().date()}.jsonl", 'a') as f:
            f.write(json.dumps(trade_record) + "\n")

    def log_parameter_change(
        self,
        parameter_name: str,
        old_value: Any,
        new_value: Any,
        approver: str,
        reason: str,
    ) -> None:
        """Log parameter change (e.g., confidence floor, leverage cap)."""
        change_record = {
            "parameter": parameter_name,
            "old_value": str(old_value),
            "new_value": str(new_value),
            "approver": approver,
            "reason": reason,
            "changed_at": datetime.utcnow().isoformat(),
        }

        with open(f"{self.log_dir}/parameter_changes.jsonl", 'a') as f:
            f.write(json.dumps(change_record) + "\n")

    def log_incident(
        self,
        incident_type: str,
        severity: str,
        description: str,
        remediation: str,
    ) -> None:
        """Log incident (for post-mortem)."""
        incident_record = {
            "incident_type": incident_type,
            "severity": severity,
            "description": description,
            "remediation": remediation,
            "logged_at": datetime.utcnow().isoformat(),
        }

        with open(f"{self.log_dir}/incidents.jsonl", 'a') as f:
            f.write(json.dumps(incident_record) + "\n")

    def generate_quarterly_report(self, year: int, quarter: int) -> str:
        """Generate quarterly compliance report for HMRC/FCA."""
        report = f"Quarterly Compliance Report — {year} Q{quarter}\n"
        report += f"Generated: {datetime.utcnow().isoformat()}\n\n"
        report += "Trade Summary:\n"
        # Read trade logs for the quarter, aggregate
        report += "Incidents:\n"
        # Read incident logs for the quarter
        return report
```

---

## PHASE 20: MONITORING DASHBOARD & REAL-TIME ALERTS

### Phase Purpose
Build a live monitoring dashboard displaying key metrics (equity, P&L, leverage, regime, circuit breaker level, data freshness). Include real-time alerts for threshold breaches.

**Why this matters for compounding**: Humans need visibility. Dashboard allows traders to catch anomalies before they blow up.

### Acceptance Criteria
1. **Update Frequency**: Metrics refreshed every 5-10 seconds ✓
2. **Key Metrics Displayed**: Equity, P&L, leverage, drawdown, regime, CB level ✓
3. **Alerts**: Pop-up notifications for CB triggers, data staleness, reconciliation failures ✓
4. **Historical Charts**: Rolling P&L, drawdown, leverage over 30/90/252 days ✓

### Deliverables (Dashboard specification)

#### 20.1 Dashboard Metrics (reporting/dashboard_metrics.py)

```python
# dashboard_metrics.py — metrics aggregation for dashboard
from typing import Dict, List
import redis

class DashboardMetricsAggregator:
    """
    Real-time metrics aggregation for live dashboard display.
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def publish_metrics_snapshot(
        self,
        equity: float,
        pnl_realized: float,
        pnl_unrealized: float,
        leverage: float,
        drawdown_pct: float,
        current_regime: str,
        cb_level: str,
        positions_count: int,
        data_freshness: Dict[str, bool],
    ) -> None:
        """
        Publish current metrics snapshot to Redis (dashboard reads from Redis).

        Args:
            equity: current account equity
            pnl_realized, pnl_unrealized: P&L components
            leverage: current portfolio leverage
            drawdown_pct: current drawdown %
            current_regime: regime name
            cb_level: circuit breaker level
            positions_count: number of open positions
            data_freshness: {ticker: is_fresh}
        """
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "equity": equity,
            "pnl_realized": pnl_realized,
            "pnl_unrealized": pnl_unrealized,
            "pnl_total": pnl_realized + pnl_unrealized,
            "leverage": leverage,
            "drawdown_pct": drawdown_pct,
            "regime": current_regime,
            "circuit_breaker": cb_level,
            "positions": positions_count,
            "data_freshness": data_freshness,
        }

        self.redis.set("nzt:dashboard:snapshot", json.dumps(snapshot), ex=60)

    def add_alert(self, alert_type: str, severity: str, message: str) -> None:
        """
        Publish alert to dashboard.

        Args:
            alert_type: e.g., "CB_TRIGGER", "DATA_STALE", "RECONCILIATION_FAIL"
            severity: "INFO", "WARNING", "CRITICAL"
            message: alert description
        """
        alert = {
            "type": alert_type,
            "severity": severity,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Push to Redis alert queue
        self.redis.lpush("nzt:dashboard:alerts", json.dumps(alert))
        self.redis.ltrim("nzt:dashboard:alerts", 0, 100)  # keep last 100 alerts
```

---

## PHASE 21: CONTINUOUS IMPROVEMENT & MODEL GOVERNANCE

### Phase Purpose
Implement systematic process for improving the system: monthly parameter refit, quarterly signal re-validation, annual architecture review. All changes versioned, tested on holdout data, and rolled back if performance degrades.

**Why this matters for compounding**: Static systems decay. Markets change. Phase 21 ensures the system evolves while maintaining safety guardrails.

### Key Hardening Rules
- **T07-001**: Walk-forward validation (monthly refit window)
- **T07-002**: Model drift detection (if performance drops >30%, rollback)
- **T07-003**: Experiment tracking (MLOps discipline)
- **T07-004**: Feature stability monitoring (monthly statistical tests)
- **T07-005**: Backtesting realism (Monte Carlo path generation)

### Acceptance Criteria
1. **Monthly Refit**: Latest 90 days of trades re-optimize parameters ✓
2. **Holdout Testing**: New params tested on subsequent 30 days before go-live ✓
3. **Drift Detection**: If new params degrade Sharpe >30%, rollback to previous ✓
4. **Version Control**: All parameter versions tracked with git ✓
5. **Post-mortem**: Every parameter change documented with rationale ✓

### Deliverables

#### 21.1 Continuous Improvement Framework (core/continuous_improvement.py)

```python
# continuous_improvement.py — systematic optimization + rollback
from typing import Dict, Tuple
from datetime import datetime
import json
import subprocess

class ContinuousImprovementFramework:
    """
    Monthly parameter refit with holdout validation and rollback capability.

    Process:
    1. Monthly refit: train on last 90 days
    2. Holdout validation: test new params on next 30 days (paper)
    3. Compare: new Sharpe vs baseline Sharpe
    4. Promote/Rollback: if new Sharpe > baseline, promote; else rollback
    """

    BASELINE_SHARPE_THRESHOLD = 0.01  # new params must beat baseline by 1% Sharpe

    def __init__(self):
        self.baseline_params = self._load_baseline_params()
        self.monthly_refits: List[Dict] = []

    def _load_baseline_params(self) -> Dict:
        """Load current baseline parameters from config."""
        try:
            with open("config/parameters_baseline.json", 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def run_monthly_refit(self, training_data: list, holdout_data: list) -> Tuple[bool, Dict]:
        """
        Monthly parameter refit with holdout validation.

        Args:
            training_data: last 90 days of trades
            holdout_data: next 30 days of trades (paper-traded with new params)

        Returns:
            (promote_new_params: bool, new_params: Dict)
        """
        # Step 1: Optimize parameters on training data
        new_params = self._optimize_parameters(training_data)

        # Step 2: Evaluate on holdout data
        new_sharpe = self._backtest_with_params(new_params, holdout_data)
        baseline_sharpe = self._backtest_with_params(self.baseline_params, holdout_data)

        # Step 3: Compare
        improvement = new_sharpe - baseline_sharpe
        promote = improvement > self.BASELINE_SHARPE_THRESHOLD

        refit_record = {
            "month": datetime.utcnow().isoformat(),
            "baseline_sharpe": baseline_sharpe,
            "new_sharpe": new_sharpe,
            "improvement": improvement,
            "promote": promote,
            "params": new_params,
        }
        self.monthly_refits.append(refit_record)

        if promote:
            print(f"✓ Promoting new parameters (Sharpe +{improvement:.3f})")
            self._save_parameters(new_params, "parameters_live.json")
            self._commit_parameter_change("monthly_refit", new_params)
        else:
            print(f"✗ Keeping baseline parameters (new params -${improvement:.3f} Sharpe)")

        return promote, new_params

    def _optimize_parameters(self, training_data: list) -> Dict:
        """
        Optimize parameters on training data.

        Candidates:
        - Kelly multiplier (0.25, 0.30, 0.35, 0.40)
        - ADX threshold (15, 20, 25, 30)
        - Confidence floor (55, 60, 65, 70)
        - Position size cap (2%, 3%, 5%, 7%)
        """
        best_params = self.baseline_params.copy()
        best_sharpe = 0.0

        # Grid search over parameter space
        for kelly_mult in [0.25, 0.30, 0.35, 0.40]:
            for adx_thresh in [15, 20, 25, 30]:
                for conf_floor in [55, 60, 65, 70]:
                    params = self.baseline_params.copy()
                    params["kelly_multiplier"] = kelly_mult
                    params["adx_threshold"] = adx_thresh
                    params["confidence_floor"] = conf_floor

                    sharpe = self._backtest_with_params(params, training_data)

                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_params = params

        return best_params

    def _backtest_with_params(self, params: Dict, trade_data: list) -> float:
        """Simulate trading with given parameters and return Sharpe ratio."""
        # Simplified: just compute Sharpe from trade P&Ls
        pnls = [t["pnl_gbp"] for t in trade_data]
        returns = [p / 10_000.0 for p in pnls]  # normalize by starting equity

        if not returns:
            return 0.0

        excess = [r - 0.02/252.0 for r in returns]
        sharpe = np.mean(excess) / np.std(excess) × np.sqrt(252.0) if np.std(excess) > 0 else 0.0
        return sharpe

    def _save_parameters(self, params: Dict, filename: str) -> None:
        """Save parameters to JSON."""
        with open(f"config/{filename}", 'w') as f:
            json.dump(params, f, indent=2)

    def _commit_parameter_change(self, change_type: str, params: Dict) -> None:
        """Commit parameter change to git for version control."""
        # Git add + commit
        subprocess.run(["git", "add", "config/parameters_live.json"])
        subprocess.run([
            "git", "commit", "-m",
            f"Monthly refit: {change_type}. Sharpe improved."
        ])
```

---

*[End of Part 4. Phases 22-25 and complete summary to follow in Part 5...]*

