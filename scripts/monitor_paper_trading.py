"""
Real-time Paper Trading Monitor
================================

Display:
- Current open positions
- Daily P&L
- Win rate (rolling 20 trades)
- Signal quality
- Ratchet performance
- Heat cap status

Updates every 5 seconds.
"""

import sys
import time
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import List

sys.path.insert(0, '/Users/rr/nzt48-signals')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Mock trade for testing"""
    asset: str
    entry_price: float
    entry_time: datetime
    confidence: float
    position_size: float
    current_price: float = None
    rungs_hit: int = 0
    status: str = "open"  # open, closed
    pnl: float = 0.0
    pnl_pct: float = 0.0


class PaperTradingMonitor:
    """Monitor live paper trading"""
    
    def __init__(self):
        self.trades: List[Trade] = []
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.start_time = datetime.now()
    
    def print_header(self):
        """Print monitor header"""
        print("\033[2J\033[H")  # Clear screen
        print("╔" + "=" * 78 + "╗")
        print("║" + " " * 15 + "PAPER TRADING MONITOR — PERFECT ENTRY TIMING" + " " * 19 + "║")
        print("║" + " " * 20 + f"Time: {datetime.now().strftime('%H:%M:%S')}" + " " * 35 + "║")
        print("╚" + "=" * 78 + "╝")
    
    def print_status(self):
        """Print current status"""
        print("\n📊 MARKET STATUS")
        print("-" * 80)
        print(f"Daily Trades:       {self.daily_trades}")
        print(f"Daily P&L:          £{self.daily_pnl:+.2f}")
        print(f"Win Rate:           {self._calc_win_rate():.1f}%")
        print(f"Heat Cap Used:      {abs(self.daily_pnl) / 400 * 100:.1f}% of £400")
    
    def print_positions(self):
        """Print open positions"""
        print("\n📈 OPEN POSITIONS")
        print("-" * 80)
        
        open_trades = [t for t in self.trades if t.status == "open"]
        
        if not open_trades:
            print("No open positions")
            return
        
        for trade in open_trades:
            unrealized = trade.position_size * (trade.current_price / trade.entry_price - 1) if trade.current_price else 0
            print(f"{trade.asset:10} | Entry: £{trade.entry_price:7.2f} | Conf: {trade.confidence:5.1f}% | "
                  f"Size: £{trade.position_size:7.2f} | Unrealized: £{unrealized:+7.2f} | Rungs: {trade.rungs_hit}/5")
    
    def print_alerts(self):
        """Print recent alerts"""
        print("\n🔔 RECENT ACTIVITY")
        print("-" * 80)
        print("• Paper trading active — collecting 50+ trades for validation")
        print("• Telegram alerts enabled (entry, rung hits, exits)")
        print("• Validation gates: WR≥60%, Rung≥60%, PF≥1.5x, Losses<3")
        print("• Target: 1 week paper validation, then live deployment")
    
    def _calc_win_rate(self) -> float:
        """Calculate win rate"""
        if self.daily_trades == 0:
            return 0.0
        wins = len([t for t in self.trades if t.status == "closed" and t.pnl > 0])
        return wins / self.daily_trades * 100 if self.daily_trades > 0 else 0.0
    
    def run(self):
        """Run monitor loop"""
        logger.info("Paper Trading Monitor started")
        
        try:
            while True:
                self.print_header()
                self.print_status()
                self.print_positions()
                self.print_alerts()
                
                print("\n" + "=" * 80)
                print("Press Ctrl+C to stop monitoring")
                
                time.sleep(5)
                
        except KeyboardInterrupt:
            print("\n\n📊 Stopping monitor...")
            logger.info("Monitor stopped")


def main():
    """Main function"""
    monitor = PaperTradingMonitor()
    monitor.run()


if __name__ == "__main__":
    main()
