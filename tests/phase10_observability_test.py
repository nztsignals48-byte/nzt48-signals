"""Phase 10: metrics registry + 3 critical alerts."""
import time


def test_metrics_renders_prometheus():
    from python_brain.core.metrics import REGISTRY
    REGISTRY.inc("ticks_received_total")
    REGISTRY.set("equity_total_gbp", 20000.0)
    out = REGISTRY.render_prometheus()
    assert "ticks_received_total" in out
    assert "equity_total_gbp 20000.0" in out


def test_alert_engine_crash_fires_when_no_ticks():
    from python_brain.core.alerts import AlertEvaluator
    ev = AlertEvaluator(last_tick_ts=time.time() - 120)
    alerts = ev.evaluate()
    a = {a.name: a for a in alerts}
    assert a["engine_crash"].fired is True


def test_alert_drawdown_fires():
    from python_brain.core.metrics import REGISTRY
    from python_brain.core.alerts import AlertEvaluator
    REGISTRY.set("equity_hwm_gbp", 20000.0)
    REGISTRY.set("equity_total_gbp", 18900.0)   # -5.5%
    alerts = AlertEvaluator(last_tick_ts=time.time()).evaluate()
    assert [a for a in alerts if a.name == "drawdown_over_5pct"][0].fired is True
    REGISTRY.set("equity_total_gbp", 20000.0)   # reset for other tests


def test_alert_ibkr_disconnect_requires_60s():
    from python_brain.core.metrics import REGISTRY
    from python_brain.core.alerts import AlertEvaluator
    REGISTRY.set("ibkr_session_up", 0.0)
    base = 1_000_000.0
    ev = AlertEvaluator(last_tick_ts=base)
    alerts_now = ev.evaluate(now=base)        # just disconnected
    assert [a for a in alerts_now if a.name == "ibkr_disconnected"][0].fired is False
    alerts_later = ev.evaluate(now=base + 120)  # 2 minutes later
    assert [a for a in alerts_later if a.name == "ibkr_disconnected"][0].fired is True
    REGISTRY.set("ibkr_session_up", 1.0)
