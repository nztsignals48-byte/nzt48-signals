"""
Integration test for adaptive learning system.
Verifies all modules load and can be instantiated.
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))


def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")
    try:
        from learning.daily_optimization import DailyOptimizer
        print("  ✓ DailyOptimizer imported")

        from learning.signal_decay_detector import SignalDecayDetector
        print("  ✓ SignalDecayDetector imported")

        from learning.weekly_backtest import WeeklyBacktester
        print("  ✓ WeeklyBacktester imported")

        from monitoring.performance_report import GeneratePerformanceReport
        print("  ✓ GeneratePerformanceReport imported")

        from scripts.adaptive_learning_scheduler import AdaptiveLearningScheduler, get_scheduler
        print("  ✓ AdaptiveLearningScheduler imported")

        return True
    except ImportError as exc:
        print(f"  ✗ Import failed: {exc}")
        return False


def test_instantiation():
    """Test that all classes can be instantiated."""
    print("\nTesting instantiation...")
    try:
        from learning.daily_optimization import DailyOptimizer
        opt = DailyOptimizer()
        print("  ✓ DailyOptimizer instantiated")

        from learning.signal_decay_detector import SignalDecayDetector
        detector = SignalDecayDetector()
        print("  ✓ SignalDecayDetector instantiated")

        from learning.weekly_backtest import WeeklyBacktester
        backtest = WeeklyBacktester()
        print("  ✓ WeeklyBacktester instantiated")

        from monitoring.performance_report import GeneratePerformanceReport
        reporter = GeneratePerformanceReport()
        print("  ✓ GeneratePerformanceReport instantiated")

        from scripts.adaptive_learning_scheduler import AdaptiveLearningScheduler
        scheduler = AdaptiveLearningScheduler()
        print("  ✓ AdaptiveLearningScheduler instantiated")

        return True
    except Exception as exc:
        print(f"  ✗ Instantiation failed: {exc}")
        return False


def test_database_tables():
    """Test that database tables are created."""
    print("\nTesting database table creation...")
    try:
        import sqlite3
        from pathlib import Path

        db_path = Path(__file__).parent / "data" / "nzt48.db"

        # Force table creation
        from learning.daily_optimization import DailyOptimizer
        DailyOptimizer(db_path)

        from learning.signal_decay_detector import SignalDecayDetector
        SignalDecayDetector(db_path)

        from learning.weekly_backtest import WeeklyBacktester
        WeeklyBacktester(db_path)

        from monitoring.performance_report import GeneratePerformanceReport
        GeneratePerformanceReport(db_path)

        # Verify tables exist
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%optimization%' OR name LIKE '%daily%' OR name LIKE '%weekly%' OR name LIKE '%monthly%' OR name LIKE '%signal%' OR name LIKE '%decay%' OR name LIKE '%backtest%'"
        )
        tables = cursor.fetchall()
        conn.close()

        expected_tables = [
            "learning_audit_log",
            "daily_metrics_history",
            "trade_factor_analysis",
            "optimization_recommendations",
            "signal_decay_history",
            "signal_disabled_log",
            "weekly_backtest_results",
            "weekly_performance_reports",
            "daily_summary_reports",
            "weekly_summary_reports",
            "monthly_summary_reports",
        ]

        created_tables = [t[0] for t in tables]
        for table in expected_tables:
            if table in created_tables:
                print(f"  ✓ {table}")
            else:
                print(f"  ✗ {table} NOT CREATED")
                return False

        return True

    except Exception as exc:
        print(f"  ✗ Database test failed: {exc}")
        return False


def test_scheduler_jobs():
    """Test that scheduler has all jobs registered."""
    print("\nTesting scheduler job registration...")
    try:
        from scripts.adaptive_learning_scheduler import AdaptiveLearningScheduler

        scheduler = AdaptiveLearningScheduler()
        jobs = scheduler.get_jobs()

        expected_job_ids = [
            "daily_optimization",
            "daily_summary",
            "weekly_backtest",
            "weekly_summary",
            "monthly_summary",
            "signal_decay_detection",
        ]

        job_ids = [job.id for job in jobs]

        for expected_id in expected_job_ids:
            if expected_id in job_ids:
                job = [j for j in jobs if j.id == expected_id][0]
                print(f"  ✓ {expected_id} → {job.next_run_time}")
            else:
                print(f"  ✗ {expected_id} NOT REGISTERED")
                return False

        return True

    except Exception as exc:
        print(f"  ✗ Scheduler test failed: {exc}")
        return False


def test_audit_log():
    """Test audit log functionality."""
    print("\nTesting audit log...")
    try:
        from learning.daily_optimization import DailyOptimizer
        import json
        from datetime import datetime, timezone

        opt = DailyOptimizer()

        # Verify audit log table exists
        import sqlite3
        conn = sqlite3.connect(opt.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM learning_audit_log")
        count = cursor.fetchone()[0]
        conn.close()

        print(f"  ✓ Audit log operational ({count} entries)")
        return True

    except Exception as exc:
        print(f"  ✗ Audit log test failed: {exc}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("ADAPTIVE LEARNING SYSTEM - INTEGRATION TEST")
    print("=" * 60)

    tests = [
        test_imports,
        test_instantiation,
        test_database_tables,
        test_scheduler_jobs,
        test_audit_log,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as exc:
            print(f"\nTest {test.__name__} crashed: {exc}")
            results.append(False)

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)

    if all(results):
        print(f"✓ ALL TESTS PASSED ({passed}/{total})")
        print("=" * 60)
        print("\nSYSTEM READY FOR DEPLOYMENT")
        print("\nNext steps:")
        print("  1. Start scheduler: python3 -m scripts.adaptive_learning_scheduler")
        print("  2. Or integrate into main.py: from scripts.adaptive_learning_scheduler import get_scheduler")
        print("  3. Monitor jobs: scheduler.get_jobs()")
        print("  4. View reports: ls reports/")
        sys.exit(0)
    else:
        print(f"✗ {total - passed} TEST(S) FAILED ({passed}/{total})")
        print("=" * 60)
        sys.exit(1)
