"""
Phase 2: ISA Auditor - Continuous 5-Minute Compliance Gating
Purpose: Ensure ISA compliance every 5 minutes (not post-hoc)

The 7-Point Compliance Checklist:
1. Margin debt == £0? (no borrowed money)
2. All holdings in ISA-eligible list? (12 LSE ETPs + Euronext + ASX + Japan)
3. No margin trading? (all cash positions)
4. No borrowed shorts? (no short selling)
5. No non-UK residency violations? (account rules)
6. Total leverage ≤ 5.0x? (ISA limit)
7. No crypto ETNs post-April 6, 2026? (FCA rule)

If ANY check fails for >5 minutes: HALT TRADING immediately.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


ISA_ELIGIBLE_ASSETS = {
    # LSE Leveraged ETPs (12 core)
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",

    # Placeholder for Euronext/ASX/Japan (will be expanded)
    # Phase 30 will add EURONEXT_ELIGIBLE assets
    # Phase 31 will add ASX_ELIGIBLE assets
    # Phase 33 will add JAPAN_ELIGIBLE assets
}


@dataclass
class AuditCheck:
    """Result of a single audit check"""
    check_name: str
    passed: bool
    timestamp: datetime
    message: str = ""


@dataclass
class AuditResult:
    """Result of full 7-point audit"""
    audit_id: str
    timestamp: datetime
    passed: bool
    checks: List[AuditCheck]
    failed_checks: List[str]
    violation_duration: float = 0.0  # seconds


class ISAAuditor:
    """
    Continuous ISA compliance auditor.

    Maintains state of violations and halts trading if non-compliance
    persists >5 minutes (300 seconds).
    """

    VIOLATION_THRESHOLD = 300  # 5 minutes in seconds
    NUM_CHECKS = 7

    def __init__(self):
        self.last_violation_time = None
        self.violation_active = False
        self.audit_history = []  # Log all audits

    def audit(
        self,
        margin_debt: float,
        current_holdings: Dict[str, float],  # {symbol: quantity}
        leverage_ratio: float,
        is_margin_trading: bool = False,
        has_borrowed_shorts: bool = False,
        uk_residency: bool = True,
        has_crypto_etn: bool = False,
    ) -> AuditResult:
        """
        Perform 7-point ISA compliance audit.

        Args:
            margin_debt: Current margin debt (should be 0)
            current_holdings: Dict of symbol -> quantity
            leverage_ratio: Current total leverage (should be ≤5.0x)
            is_margin_trading: Is any margin trading active?
            has_borrowed_shorts: Any short positions?
            uk_residency: Is account UK resident?
            has_crypto_etn: Any crypto ETN positions (post-April 6, 2026)?

        Returns:
            AuditResult with pass/fail and all 7 check results
        """
        now = datetime.now()
        audit_id = f"AUDIT_{int(now.timestamp())}"
        checks = []
        failed = []

        # Check 1: Margin debt == 0
        check1 = AuditCheck(
            check_name="Margin Debt Zero",
            passed=margin_debt == 0,
            timestamp=now,
            message=f"Margin debt: £{margin_debt:.2f}"
        )
        checks.append(check1)
        if not check1.passed:
            failed.append("Margin Debt Non-Zero")

        # Check 2: All holdings ISA-eligible
        ineligible = [symbol for symbol in current_holdings.keys() if symbol not in ISA_ELIGIBLE_ASSETS]
        check2 = AuditCheck(
            check_name="Eligible Holdings",
            passed=len(ineligible) == 0,
            timestamp=now,
            message=f"Ineligible holdings: {ineligible}" if ineligible else "All holdings eligible"
        )
        checks.append(check2)
        if not check2.passed:
            failed.append("Ineligible Holdings")

        # Check 3: No margin trading
        check3 = AuditCheck(
            check_name="No Margin Trading",
            passed=not is_margin_trading,
            timestamp=now,
            message="Margin trading active" if is_margin_trading else "No margin trading"
        )
        checks.append(check3)
        if not check3.passed:
            failed.append("Margin Trading Active")

        # Check 4: No borrowed shorts
        check4 = AuditCheck(
            check_name="No Borrowed Shorts",
            passed=not has_borrowed_shorts,
            timestamp=now,
            message="Borrowed shorts exist" if has_borrowed_shorts else "No borrowed shorts"
        )
        checks.append(check4)
        if not check4.passed:
            failed.append("Borrowed Shorts Exist")

        # Check 5: UK residency
        check5 = AuditCheck(
            check_name="UK Residency",
            passed=uk_residency,
            timestamp=now,
            message="UK resident" if uk_residency else "Non-UK resident"
        )
        checks.append(check5)
        if not check5.passed:
            failed.append("Non-UK Residency")

        # Check 6: Total leverage ≤ 5.0x
        check6 = AuditCheck(
            check_name="Leverage Limit",
            passed=leverage_ratio <= 5.0,
            timestamp=now,
            message=f"Leverage: {leverage_ratio:.2f}x"
        )
        checks.append(check6)
        if not check6.passed:
            failed.append("Leverage Exceeded")

        # Check 7: No crypto ETNs (post-April 6, 2026)
        check7 = AuditCheck(
            check_name="No Crypto ETNs",
            passed=not has_crypto_etn,
            timestamp=now,
            message="Crypto ETN held" if has_crypto_etn else "No crypto ETNs"
        )
        checks.append(check7)
        if not check7.passed:
            failed.append("Crypto ETN Held")

        # Determine overall pass/fail
        overall_pass = len(failed) == 0

        # Track violation duration
        violation_duration = 0.0
        if not overall_pass:
            if self.last_violation_time is None:
                self.last_violation_time = now
            violation_duration = (now - self.last_violation_time).total_seconds()
        else:
            # Clear violation if audit passes
            self.last_violation_time = None

        result = AuditResult(
            audit_id=audit_id,
            timestamp=now,
            passed=overall_pass,
            checks=checks,
            failed_checks=failed,
            violation_duration=violation_duration
        )

        # Log audit
        self.audit_history.append(result)

        # Check if violation persists >5 min
        if not overall_pass and violation_duration > self.VIOLATION_THRESHOLD:
            self.violation_active = True
            logger.error(f"ISA violation persisting >5 min: {failed}. HALTING TRADING.")
        else:
            self.violation_active = False

        return result

    def is_trading_halted(self) -> bool:
        """Check if trading should be halted due to ISA violation"""
        return self.violation_active

    def get_audit_summary(self) -> str:
        """Return human-readable audit summary"""
        if not self.audit_history:
            return "No audits performed yet"

        latest = self.audit_history[-1]
        status = "✅ PASS" if latest.passed else "❌ FAIL"

        summary = f"\n{status} ISA Audit at {latest.timestamp.strftime('%H:%M:%S')}\n"
        summary += f"Audit ID: {latest.audit_id}\n"
        summary += f"Checks: {len([c for c in latest.checks if c.passed])}/{self.NUM_CHECKS} passed\n"

        if latest.failed_checks:
            summary += f"Failed: {', '.join(latest.failed_checks)}\n"
            summary += f"Violation duration: {latest.violation_duration:.1f}s\n"

        summary += f"Halt trading: {self.is_trading_halted()}\n"

        return summary


# Unit tests
def test_all_checks_pass():
    """Test when all 7 checks pass"""
    auditor = ISAAuditor()

    result = auditor.audit(
        margin_debt=0,
        current_holdings={"QQQ3.L": 10},
        leverage_ratio=2.0,
        is_margin_trading=False,
        has_borrowed_shorts=False,
        uk_residency=True,
        has_crypto_etn=False,
    )

    assert result.passed, "All checks should pass"
    assert len(result.failed_checks) == 0, "No checks should fail"
    assert not auditor.is_trading_halted(), "Trading should not be halted"

    print("✓ All checks pass test passed")


def test_single_check_fails():
    """Test when single check fails"""
    auditor = ISAAuditor()

    result = auditor.audit(
        margin_debt=100,  # FAIL: has margin debt
        current_holdings={"QQQ3.L": 10},
        leverage_ratio=2.0,
        is_margin_trading=False,
        has_borrowed_shorts=False,
        uk_residency=True,
        has_crypto_etn=False,
    )

    assert not result.passed, "Should fail if margin debt >0"
    assert "Margin Debt Non-Zero" in result.failed_checks
    assert not auditor.is_trading_halted(), "Should not halt on first failure"

    print("✓ Single check fail test passed")


def test_violation_escalation():
    """Test that violation escalates to halt after 5 min (simulated)"""
    auditor = ISAAuditor()

    # Simulate violation for >5 minutes by manipulating last_violation_time
    auditor.last_violation_time = datetime.now() - timedelta(seconds=310)

    result = auditor.audit(
        margin_debt=100,  # Violation
        current_holdings={"QQQ3.L": 10},
        leverage_ratio=2.0,
        is_margin_trading=False,
        has_borrowed_shorts=False,
        uk_residency=True,
        has_crypto_etn=False,
    )

    assert not result.passed, "Should fail"
    assert result.violation_duration > 300, "Violation should persist >300s"
    assert auditor.is_trading_halted(), "Trading should be halted after 5+ min violation"

    print(f"✓ Violation escalation test passed (violated for {result.violation_duration:.1f}s)")


def test_ineligible_assets():
    """Test detection of ineligible assets"""
    auditor = ISAAuditor()

    result = auditor.audit(
        margin_debt=0,
        current_holdings={"BTC": 1, "QQQ3.L": 10},  # BTC is ineligible
        leverage_ratio=2.0,
        is_margin_trading=False,
        has_borrowed_shorts=False,
        uk_residency=True,
        has_crypto_etn=False,
    )

    assert not result.passed, "Should fail with ineligible asset"
    assert "Ineligible Holdings" in result.failed_checks

    print("✓ Ineligible assets test passed")


def test_leverage_breach():
    """Test detection of leverage >5.0x"""
    auditor = ISAAuditor()

    result = auditor.audit(
        margin_debt=0,
        current_holdings={"QQQ3.L": 100},
        leverage_ratio=5.5,  # Exceeds ISA limit
        is_margin_trading=False,
        has_borrowed_shorts=False,
        uk_residency=True,
        has_crypto_etn=False,
    )

    assert not result.passed, "Should fail if leverage >5.0x"
    assert "Leverage Exceeded" in result.failed_checks

    print("✓ Leverage breach test passed")


if __name__ == "__main__":
    test_all_checks_pass()
    test_single_check_fails()
    test_violation_escalation()
    test_ineligible_assets()
    test_leverage_breach()

    print("\n" + "="*60)
    print("PHASE 2: ISA AUDITOR - EXAMPLE OUTPUT")
    print("="*60)

    auditor = ISAAuditor()

    # Good scenario
    result_good = auditor.audit(
        margin_debt=0,
        current_holdings={"QQQ3.L": 10, "3LUS.L": 5},
        leverage_ratio=1.5,
        is_margin_trading=False,
        has_borrowed_shorts=False,
        uk_residency=True,
        has_crypto_etn=False,
    )

    print("\n✅ AUDIT 1 (Compliant):")
    print(auditor.get_audit_summary())

    # Bad scenario
    result_bad = auditor.audit(
        margin_debt=500,
        current_holdings={"QQQ3.L": 10, "BTC": 1},
        leverage_ratio=6.0,
        is_margin_trading=True,
        has_borrowed_shorts=True,
        uk_residency=False,
        has_crypto_etn=True,
    )

    print("\n❌ AUDIT 2 (Non-Compliant):")
    print(auditor.get_audit_summary())

    print("\n✅ Phase 2 (ISA Auditor) complete and tested")
