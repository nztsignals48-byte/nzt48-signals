"""Dataset-contract test: every SignalReceived / TradeClosed must have the full schema.

Phase 2A fills with real WAL reading; scaffold only validates schema shape.
"""


REQUIRED_SIGNAL_FIELDS = {
    "schema_version", "signal_id", "strategy_name", "strategy_version",
    "ticker", "exchange", "account", "timestamp_ns",
    "feature_vector", "conviction_score", "portfolio_rank",
    "account_route_chosen", "expected_fill_price",
    "risk_deltas", "risk_final_confidence",
}

REQUIRED_CLOSE_FIELDS = {
    "schema_version", "signal_id", "entry_timestamp_ns", "exit_timestamp_ns",
    "entry_price", "exit_price", "size_shares",
    "spread_cost_bps", "commission_abs", "stamp_duty_abs", "financing_cost_abs",
    "slippage_bps_vs_arrival",
    "realized_pnl_abs", "realized_pnl_bps", "mae_bps", "mfe_bps",
    "regime_at_entry", "regime_at_exit", "exit_reason",
}


def test_signal_schema_shape():
    assert len(REQUIRED_SIGNAL_FIELDS) == 15

def test_close_schema_shape():
    assert len(REQUIRED_CLOSE_FIELDS) == 19
