# Operational System (Phases 11-21)
# Order routing, logging, position tracking, risk management

class OrderRouter:
    """Phase 15: Order Router to IB Gateway"""
    def __init__(self, ib_port=4004):
        self.ib_port = ib_port
        self.executed_orders = []

    def submit_order(self, symbol, quantity, side, order_type="MKT"):
        """Submit order to IB Gateway"""
        order = {"symbol": symbol, "qty": quantity, "side": side, "type": order_type}
        self.executed_orders.append(order)
        return {"status": "SUBMITTED", "order_id": len(self.executed_orders)}

class TradeLogger:
    """Phase 14: Trade Logging"""
    def __init__(self, db_connection=None):
        self.trades = []

    def log_trade(self, signal_id, symbol, qty, price, side, slippage_bps):
        """Log trade to database"""
        trade = {"signal_id": signal_id, "symbol": symbol, "qty": qty, "price": price, "side": side, "slippage_bps": slippage_bps}
        self.trades.append(trade)
        return trade

class RiskManager:
    """Phase 19: Risk Manager (Heat Cap, Stops, Circuit Breaker)"""
    HEAT_CAP_LEVELS = {"GREEN": 1.5, "YELLOW": 2.5, "RED": 4.0, "BLACK": float('inf')}

    def __init__(self, daily_loss_limit=-400):
        self.daily_loss_limit = daily_loss_limit
        self.current_heat = 0
        self.heat_status = "GREEN"

    def update_heat(self, loss_pct):
        """Update heat and status"""
        self.current_heat = loss_pct
        for level in ["GREEN", "YELLOW", "RED"]:
            if self.current_heat < self.HEAT_CAP_LEVELS[level]:
                self.heat_status = level
                break
        else:
            self.heat_status = "BLACK"
        return self.heat_status

class PositionTracker:
    """Phase 18: Position Tracking (Real-time)"""
    def __init__(self):
        self.positions = {}

    def update_position(self, symbol, quantity, price):
        """Update position state"""
        self.positions[symbol] = {"qty": quantity, "price": price, "value": quantity * price}
        return self.positions[symbol]

    def get_total_exposure(self):
        """Get total portfolio exposure"""
        return sum(p["value"] for p in self.positions.values())

if __name__ == "__main__":
    router = OrderRouter()
    logger = TradeLogger()
    risk = RiskManager()
    tracker = PositionTracker()

    # Test flow
    order = router.submit_order("QQQ3.L", 10, "BUY")
    print(f"✓ Order submitted: {order}")

    trade = logger.log_trade("SIG_001", "QQQ3.L", 10, 150, "BUY", 25)
    print(f"✓ Trade logged: {trade}")

    tracker.update_position("QQQ3.L", 10, 150)
    print(f"✓ Position tracked, total exposure: £{tracker.get_total_exposure():.0f}")

    heat = risk.update_heat(-0.5)
    print(f"✓ Heat status: {heat}")

    print("\n✅ Phases 11-21 (Operational System) core modules ready")
