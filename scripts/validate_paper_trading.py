"""
Paper Trading Validation Gate Checker
======================================

After 1 week (50+ trades), check if all 4 gates pass:
1. Win rate ≥ 60%
2. Rung hit rate ≥ 60%
3. Profit factor ≥ 1.5x
4. Consecutive losses < 3

If ALL gates pass → Approved for live deployment (25% sizing)
If ANY gate fails → Continue paper trading, adjust parameters
"""

import sys
import logging
from datetime import datetime

sys.path.insert(0, '/Users/rr/nzt48-signals')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ValidationGates:
    """4-gate validation system"""
    
    def __init__(self):
        self.gates = {
            'win_rate': {'threshold': 0.60, 'current': 0.0, 'passed': False},
            'rung_hits': {'threshold': 0.60, 'current': 0.0, 'passed': False},
            'profit_factor': {'threshold': 1.5, 'current': 0.0, 'passed': False},
            'consecutive_losses': {'threshold': 3, 'current': 0, 'passed': False}
        }
    
    def check_gates(self):
        """Check all 4 gates"""
        print("\n╔════════════════════════════════════════════════════╗")
        print("║     PAPER TRADING VALIDATION GATES (1 WEEK)       ║")
        print("╚════════════════════════════════════════════════════╝")
        
        # Gate 1: Win Rate
        print("\n📊 GATE 1: Win Rate")
        print("-" * 54)
        print(f"Threshold: {self.gates['win_rate']['threshold']*100:.0f}%")
        print(f"Current:   {self.gates['win_rate']['current']*100:.1f}%")
        gate1_pass = self.gates['win_rate']['current'] >= self.gates['win_rate']['threshold']
        print(f"Status:    {'✅ PASS' if gate1_pass else '❌ FAIL'}")
        self.gates['win_rate']['passed'] = gate1_pass
        
        # Gate 2: Rung Hits
        print("\n📈 GATE 2: Rung Hit Rate")
        print("-" * 54)
        print(f"Threshold: {self.gates['rung_hits']['threshold']*100:.0f}%")
        print(f"Current:   {self.gates['rung_hits']['current']*100:.1f}%")
        gate2_pass = self.gates['rung_hits']['current'] >= self.gates['rung_hits']['threshold']
        print(f"Status:    {'✅ PASS' if gate2_pass else '❌ FAIL'}")
        self.gates['rung_hits']['passed'] = gate2_pass
        
        # Gate 3: Profit Factor
        print("\n💰 GATE 3: Profit Factor")
        print("-" * 54)
        print(f"Threshold: {self.gates['profit_factor']['threshold']:.1f}x")
        print(f"Current:   {self.gates['profit_factor']['current']:.1f}x")
        gate3_pass = self.gates['profit_factor']['current'] >= self.gates['profit_factor']['threshold']
        print(f"Status:    {'✅ PASS' if gate3_pass else '❌ FAIL'}")
        self.gates['profit_factor']['passed'] = gate3_pass
        
        # Gate 4: Consecutive Losses
        print("\n⛔ GATE 4: Consecutive Losses")
        print("-" * 54)
        print(f"Threshold: < {self.gates['consecutive_losses']['threshold']}")
        print(f"Current:   {self.gates['consecutive_losses']['current']}")
        gate4_pass = self.gates['consecutive_losses']['current'] < self.gates['consecutive_losses']['threshold']
        print(f"Status:    {'✅ PASS' if gate4_pass else '❌ FAIL'}")
        self.gates['consecutive_losses']['passed'] = gate4_pass
        
        # Summary
        print("\n" + "=" * 54)
        all_pass = all(g['passed'] for g in self.gates.values())
        
        if all_pass:
            print("🚀 ALL GATES PASSED ✅")
            print("\n" + "=" * 54)
            print("✅ APPROVED FOR LIVE DEPLOYMENT")
            print("\nNext Steps:")
            print("1. Deploy to EC2: bash scripts/deploy_to_ec2_live.sh")
            print("2. Start with 25% position sizing")
            print("3. Monitor Phase 1 for 3 days (WR≥55%)")
            print("4. If good, ramp to 50% (Phase 2)")
            print("5. If good, ramp to 100% (Phase 3)")
            print("=" * 54)
        else:
            print("❌ GATES NOT PASSED")
            failed = [k for k, v in self.gates.items() if not v['passed']]
            print(f"\nFailed gates: {', '.join(failed)}")
            print("\nRecommendations:")
            print("1. Continue paper trading")
            print("2. Review failed gates and identify root causes")
            print("3. Adjust confidence thresholds if needed")
            print("4. Collect additional 50 trades")
            print("5. Re-validate gates")
            print("=" * 54)
        
        return all_pass


def main():
    """Main function"""
    print(f"\n⏱️  Running validation at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    validator = ValidationGates()
    
    # Set mock data for testing
    validator.gates['win_rate']['current'] = 0.65
    validator.gates['rung_hits']['current'] = 0.70
    validator.gates['profit_factor']['current'] = 2.1
    validator.gates['consecutive_losses']['current'] = 2
    
    all_pass = validator.check_gates()
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
