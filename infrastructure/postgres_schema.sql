-- PostgreSQL Schema for NZT-48 V2.0
-- Phase Q3: Migration Infrastructure
-- All tables include audit trails and indexing for 1000+ trades/day capacity

CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(255) UNIQUE NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price NUMERIC(12,6) NOT NULL,
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL,
    exit_price NUMERIC(12,6),
    exit_time TIMESTAMP WITH TIME ZONE,
    pnl_dollars NUMERIC(12,2),
    pnl_r_multiple NUMERIC(10,4),
    strategy VARCHAR(50) NOT NULL,
    confidence NUMERIC(5,2) CHECK (confidence >= 0 AND confidence <= 100),
    status VARCHAR(20) NOT NULL CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT valid_exit CHECK ((exit_price IS NULL AND status = 'OPEN') OR (exit_price IS NOT NULL AND status = 'CLOSED'))
);

CREATE INDEX idx_trades_ticker ON trades(ticker);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_trades_strategy ON trades(strategy);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_created_at ON trades(created_at);

CREATE TABLE circuit_breaker_state (
    id SERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    daily_pnl NUMERIC(12,2),
    consecutive_losses INT DEFAULT 0 CHECK (consecutive_losses >= 0),
    halted_for_session BOOLEAN DEFAULT FALSE,
    halt_reason VARCHAR(255),
    level INT CHECK (level >= 0 AND level <= 3),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_circuit_breaker_date ON circuit_breaker_state(date);
CREATE INDEX idx_circuit_breaker_halted ON circuit_breaker_state(halted_for_session);

CREATE TABLE chandelier_state (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(255) UNIQUE NOT NULL REFERENCES trades(trade_id) ON DELETE CASCADE,
    ticker VARCHAR(10) NOT NULL,
    entry_price NUMERIC(12,6) NOT NULL,
    highest_high NUMERIC(12,6),
    trailing_stop NUMERIC(12,6),
    current_rung INT CHECK (current_rung >= 0 AND current_rung <= 5),
    active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chandelier_ticker ON chandelier_state(ticker);
CREATE INDEX idx_chandelier_active ON chandelier_state(active);
CREATE INDEX idx_chandelier_trade_id ON chandelier_state(trade_id);

CREATE TABLE signal_decay_history (
    id SERIAL PRIMARY KEY,
    signal_name VARCHAR(100) NOT NULL,
    date DATE NOT NULL,
    trades_count INT CHECK (trades_count >= 0),
    win_rate NUMERIC(5,2) CHECK (win_rate >= 0 AND win_rate <= 100),
    sharpe_ratio NUMERIC(10,4),
    deflated_sharpe_ratio NUMERIC(10,4),
    decay_detected BOOLEAN DEFAULT FALSE,
    status VARCHAR(20) CHECK (status IN ('ACTIVE', 'DECAYED', 'PAUSED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(signal_name, date)
);

CREATE INDEX idx_signal_decay_name ON signal_decay_history(signal_name);
CREATE INDEX idx_signal_decay_date ON signal_decay_history(date);
CREATE INDEX idx_signal_decay_status ON signal_decay_history(status);

CREATE TABLE vpin_history (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    vpin_score NUMERIC(5,4) CHECK (vpin_score >= 0 AND vpin_score <= 1),
    toxicity_level VARCHAR(20) CHECK (toxicity_level IN ('LOW', 'MODERATE', 'HIGH', 'EXTREME')),
    confidence NUMERIC(5,2) CHECK (confidence >= 0 AND confidence <= 100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_vpin_ticker ON vpin_history(ticker);
CREATE INDEX idx_vpin_timestamp ON vpin_history(timestamp);
CREATE INDEX idx_vpin_toxicity ON vpin_history(toxicity_level);

CREATE TABLE order_flow_events (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    volume_buy NUMERIC(15,2),
    volume_sell NUMERIC(15,2),
    ofi NUMERIC(15,2),
    price NUMERIC(12,6),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ofi_ticker ON order_flow_events(ticker);
CREATE INDEX idx_ofi_timestamp ON order_flow_events(timestamp);

CREATE TABLE cross_impact_log (
    id SERIAL PRIMARY KEY,
    source_ticker VARCHAR(10) NOT NULL,
    target_ticker VARCHAR(10) NOT NULL,
    impact_score NUMERIC(10,4),
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cross_impact_source ON cross_impact_log(source_ticker);
CREATE INDEX idx_cross_impact_target ON cross_impact_log(target_ticker);
CREATE INDEX idx_cross_impact_timestamp ON cross_impact_log(timestamp);

-- Trigger for automatic updated_at
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_trades_timestamp BEFORE UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER update_circuit_breaker_timestamp BEFORE UPDATE ON circuit_breaker_state
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER update_chandelier_timestamp BEFORE UPDATE ON chandelier_state
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- View for daily trading summary
CREATE OR REPLACE VIEW daily_trading_summary AS
SELECT 
    DATE(entry_time) as trading_date,
    COUNT(*) as total_trades,
    SUM(CASE WHEN pnl_dollars > 0 THEN 1 ELSE 0 END) as winning_trades,
    ROUND(100.0 * SUM(CASE WHEN pnl_dollars > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) as win_rate,
    SUM(pnl_dollars) as daily_pnl,
    AVG(pnl_r_multiple) as avg_r_multiple,
    MAX(pnl_dollars) as best_trade,
    MIN(pnl_dollars) as worst_trade
FROM trades
WHERE status = 'CLOSED'
GROUP BY DATE(entry_time)
ORDER BY trading_date DESC;

-- View for strategy performance
CREATE OR REPLACE VIEW strategy_performance AS
SELECT 
    strategy,
    COUNT(*) as trades,
    SUM(CASE WHEN pnl_dollars > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(100.0 * SUM(CASE WHEN pnl_dollars > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) as win_rate,
    SUM(pnl_dollars) as total_pnl,
    ROUND(AVG(pnl_dollars), 2) as avg_pnl,
    ROUND(STDDEV(pnl_dollars), 2) as std_dev,
    MAX(pnl_dollars) as best_trade,
    MIN(pnl_dollars) as worst_trade
FROM trades
WHERE status = 'CLOSED'
GROUP BY strategy
ORDER BY total_pnl DESC;
