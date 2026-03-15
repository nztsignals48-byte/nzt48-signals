"""
Market Session Scheduler — Usage Examples

Demonstrates Phase 2a integration patterns for:
- Getting current market session
- Scheduling universe refreshes
- Phase-aware trading logic
- DST-aware time handling
"""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from core.market_session_scheduler import (
    MarketSessionScheduler,
    get_market_scheduler,
    UK_TZ,
    ET_TZ,
    HK_TZ,
)


# ─── Example 1: Basic Session Detection ───────────────────────────────────

def example_current_session():
    """Get the current market session and trading window."""

    scheduler = get_market_scheduler()

    current = scheduler.get_current_session()
    print(f"Current session: {current}")

    if current == "LSE":
        print("✓ London Stock Exchange is open")
        print("  • Trading window: 09:00-15:15 UK time")
        print("  • Universe size: 15-25 tickers")

    elif current == "US":
        print("✓ US markets (NYSE/NASDAQ) are open")
        print("  • Trading window: 09:30-16:00 ET")
        print("  • Universe size: 25-40 tickers")

    elif current == "ASIA":
        print("✓ Asia markets (Hong Kong) are open")
        print("  • Trading window: 09:30-16:00 HKT")
        print("  • Universe size: 10-15 tickers (monitoring only)")

    else:
        print("✗ All markets closed")
        minutes = scheduler.get_time_until_market_close()
        print(f"  Minutes until next open: {minutes}")


# ─── Example 2: Phase Timings for Universe Sizing ───────────────────────

def example_phase_aware_trading():
    """Adapt trading rules based on current phase."""

    scheduler = get_market_scheduler()
    timings = scheduler.get_phase_timings()
    now = datetime.now(timezone.utc)

    print("Phase Boundaries (UTC):")
    for phase, (start, end) in timings.items():
        duration = (end - start).total_seconds() / 3600
        print(f"  {phase:20s}: {start.strftime('%H:%M')} - {end.strftime('%H:%M')} ({duration:.1f}h)")

    # Determine current phase
    current_phase = None
    for phase, (start, end) in timings.items():
        if start <= now < end:
            current_phase = phase
            break

    if current_phase:
        print(f"\nCurrent phase: {current_phase}")

        # Phase-specific rules
        phase_rules = {
            "Phase1_LSE_EU": {
                "universe_size": 15,
                "entry_types": ["A", "B"],  # Type A (dip) + B (runner)
                "max_position": 5,
                "exit_rule": "Chandelier 1.5x ATR"
            },
            "Phase2_LSE_US": {
                "universe_size": 25,
                "entry_types": ["A", "B", "C"],  # + Type C (fade)
                "max_position": 8,
                "exit_rule": "Chandelier 1.2x ATR"
            },
            "Phase3_US_only": {
                "universe_size": 40,
                "entry_types": ["A", "B", "C"],
                "max_position": 10,
                "exit_rule": "Chandelier 1.0x ATR"
            },
            "Phase4_US_Asia_warmup": {
                "universe_size": 20,
                "entry_types": ["B"],  # Runners only (lower confidence)
                "max_position": 5,
                "exit_rule": "5-min warning before US close"
            },
            "Phase5_Asia": {
                "universe_size": 10,
                "entry_types": ["A"],  # Conservative in Asia
                "max_position": 3,
                "exit_rule": "Before market close"
            }
        }

        rules = phase_rules.get(current_phase, {})
        print(f"  Universe size: {rules.get('universe_size')} tickers")
        print(f"  Entry types: {rules.get('entry_types')}")
        print(f"  Max position: {rules.get('max_position')}")
        print(f"  Exit rule: {rules.get('exit_rule')}")


# ─── Example 3: Scheduling Universe Refreshes ──────────────────────────

def example_schedule_refreshes():
    """Schedule data refreshes 15 minutes before each phase."""

    scheduler = get_market_scheduler()

    print("Scheduled Universe Refreshes (15 minutes before each phase):\n")

    phases = [
        "Phase1_LSE_EU",
        "Phase2_LSE_US",
        "Phase3_US_only",
        "Phase4_US_Asia_warmup",
        "Phase5_Asia"
    ]

    for phase in phases:
        refresh_time_utc = scheduler.schedule_universe_refresh(phase)
        if refresh_time_utc:
            # Show in multiple timezones
            refresh_uk = refresh_time_utc.astimezone(UK_TZ)
            refresh_et = refresh_time_utc.astimezone(ET_TZ)

            print(f"{phase}:")
            print(f"  UTC:  {refresh_time_utc.strftime('%H:%M:%S')}")
            print(f"  UK:   {refresh_uk.strftime('%H:%M:%S %Z')}")
            print(f"  ET:   {refresh_et.strftime('%H:%M:%S %Z')}")
            print()


# ─── Example 4: Market Close Monitoring for Tier 3 Exit ────────────────

def example_tier3_exit_enforcement():
    """Monitor time until market close for Tier 3 position exit enforcement."""

    scheduler = get_market_scheduler()

    print("Tier 3 Exit Enforcement — Time Until Market Close:\n")

    # Check time until close for each market
    for market in ["LSE", "US", "ASIA"]:
        minutes = scheduler.get_time_until_market_close(market)

        if minutes is None:
            print(f"{market}: Market closed or error")
            continue

        print(f"{market}: {minutes:3d} minutes until close")

        # Exit enforcement levels
        if 0 < minutes <= 5:
            print(f"  → CRITICAL: Force market close at {minutes}min")
        elif 0 < minutes <= 15:
            print(f"  → WARNING: Final exit window, 15-min warning")
        elif 0 < minutes <= 30:
            print(f"  → CAUTION: Approaching close, consider exits")
        elif minutes > 30:
            print(f"  → Normal trading, {minutes}min until close")

        print()


# ─── Example 5: Timezone-Aware Time Handling ──────────────────────────

def example_timezone_handling():
    """Demonstrate timezone-aware operations."""

    print("Timezone Handling:\n")

    scheduler = get_market_scheduler()
    timings = scheduler.get_phase_timings()

    phase1_start_utc = timings["Phase1_LSE_EU"][0]

    print(f"Phase 1 Start Time (multiple timezones):")
    print(f"  UTC:  {phase1_start_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  UK:   {phase1_start_utc.astimezone(UK_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  ET:   {phase1_start_utc.astimezone(ET_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  HK:   {phase1_start_utc.astimezone(HK_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Verify DST awareness
    print(f"\nDST Awareness:")
    print(f"  Current UK time: {datetime.now(UK_TZ).strftime('%H:%M %Z (UTC%z)')}")
    print(f"  Current ET time: {datetime.now(ET_TZ).strftime('%H:%M %Z (UTC%z)')}")
    print(f"  (Note: %Z shows GMT/BST/EST/EDT depending on DST status)")


# ─── Example 6: Approaching Close Detection ───────────────────────────

def example_approaching_close():
    """Check if approaching market close within threshold."""

    scheduler = get_market_scheduler()

    print("Approaching Market Close Detection:\n")

    # Check each market
    for market in ["LSE", "US", "ASIA"]:
        approaching_5min = scheduler.is_approaching_market_close(market, minutes_threshold=5)
        approaching_15min = scheduler.is_approaching_market_close(market, minutes_threshold=15)

        minutes = scheduler.get_time_until_market_close(market)

        print(f"{market}: {minutes:3d}min until close")
        print(f"  Within 15min? {approaching_15min}")
        print(f"  Within 5min?  {approaching_5min}")

        if approaching_5min and minutes and minutes > 0:
            print(f"  → ACTION: Force close all Tier 3 positions")
        elif approaching_15min and minutes and minutes > 0:
            print(f"  → ACTION: Begin systematic exit of Tier 3+")

        print()


# ─── Example 7: Diagnostic Information ────────────────────────────────

def example_diagnostics():
    """Get diagnostic information for troubleshooting."""

    scheduler = get_market_scheduler()
    info = scheduler.get_diagnostic_info()

    print("Market Session Scheduler Diagnostics:\n")
    print(f"Fallback mode: {info['fallback_mode']}")
    print(f"Cache valid: {info['cache_valid']}")
    print(f"Cache expiry: {info['cache_expiry']}")
    print(f"Current session: {info['current_session']}")
    print(f"Cached markets: {info['cached_markets']}")

    if info['fallback_mode']:
        print("\n⚠ WARNING: Scheduler is in fallback mode")
        print(f"  Last error: {info['last_query_error']}")
        print("  Times may be off by 1 hour during DST transitions")
        print("  Ensure IB Gateway is connected for accurate market hours")


# ─── Example 8: Integration with APScheduler ───────────────────────────

def example_apscheduler_integration():
    """Example of scheduling refreshes with APScheduler."""

    scheduler = get_market_scheduler()

    print("APScheduler Integration Example:\n")

    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        sched = BackgroundScheduler()

        # Schedule refreshes for each phase
        phases = [
            "Phase1_LSE_EU",
            "Phase2_LSE_US",
            "Phase3_US_only",
            "Phase4_US_Asia_warmup",
            "Phase5_Asia"
        ]

        for phase in phases:
            refresh_time = scheduler.schedule_universe_refresh(phase)
            if refresh_time:
                # Schedule job (approximate, valid for next 24 hours)
                sched.add_job(
                    lambda p=phase: print(f"Refreshing universe for {p}"),
                    'cron',
                    hour=refresh_time.hour,
                    minute=refresh_time.minute,
                    id=f'refresh_{phase}',
                    name=f'Refresh {phase}'
                )

        print("Jobs scheduled:")
        for job in sched.get_jobs():
            print(f"  {job.id}: {job.name} @ {job.trigger}")

    except ImportError:
        print("APScheduler not installed. Install with: pip install apscheduler")


# ─── Main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("Market Session Scheduler — Usage Examples")
    print("=" * 70)
    print()

    try:
        print("1. Current Session Detection")
        print("-" * 70)
        example_current_session()

        print("\n2. Phase-Aware Trading Rules")
        print("-" * 70)
        example_phase_aware_trading()

        print("\n3. Scheduled Universe Refreshes")
        print("-" * 70)
        example_schedule_refreshes()

        print("\n4. Tier 3 Exit Enforcement")
        print("-" * 70)
        example_tier3_exit_enforcement()

        print("\n5. Timezone Handling")
        print("-" * 70)
        example_timezone_handling()

        print("\n6. Approaching Close Detection")
        print("-" * 70)
        example_approaching_close()

        print("\n7. Diagnostics")
        print("-" * 70)
        example_diagnostics()

        print("\n8. APScheduler Integration")
        print("-" * 70)
        example_apscheduler_integration()

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 70)
    print("Examples complete")
    print("=" * 70)
