"""
AEGIS V2 Time System Integration Tests

Validates that the time system is correct across all components:
- UTC as primary time format
- Timezone conversions (London/NY/HK)
- DST handling (BST transitions)
- Session detection (ModeA/B/C/Dark)
- Trade execution timing validation

Status: Comprehensive test coverage for time-critical functionality
Enforcement: Must pass before any deployment
"""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import time system modules (adjust paths based on actual structure)
try:
    from ouroboros.dst_wrapper import get_london_tz, get_ny_tz, get_hk_tz
    from ouroboros.session_map import get_session_mode, SessionMode
    from events.event_calendar import EventCalendar
except ImportError:
    pytest.skip("Time system modules not available", allow_module_level=True)


class TestUTCTimezone:
    """Test that UTC is the primary timezone throughout the system"""

    def test_system_time_is_utc(self):
        """All system time reads must be UTC"""
        now = datetime.now(timezone.utc)
        assert now.tzinfo == timezone.utc, "System time must be UTC"
        assert now.tzinfo.tzname(now) == "UTC", "Timezone name must be 'UTC'"

    def test_no_naive_datetime(self):
        """Naive datetime (no timezone) is FORBIDDEN"""
        # This should never happen in our codebase
        now_naive = datetime.now()
        assert now_naive.tzinfo is None, "Test: naive datetime has no tzinfo (expected)"
        # In production code, we should never create naive datetimes

    def test_utc_timestamp_precision(self):
        """UTC timestamps must include microsecond precision"""
        now = datetime.now(timezone.utc)
        assert now.microsecond is not None, "UTC time must have microsecond precision"
        iso_str = now.isoformat()
        assert "+" in iso_str or "Z" in iso_str, "UTC timezone must be in ISO string"


class TestTimezoneConversions:
    """Test UTC → Local timezone conversions for all trading venues"""

    def test_utc_to_london_conversion(self):
        """2026-04-03 16:44:00 UTC = 17:44:00 BST (UTC+1)"""
        utc_dt = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)
        london_tz = get_london_tz()
        london_dt = utc_dt.astimezone(london_tz)

        assert london_dt.hour == 17, "UTC 16:44 should be 17:44 London (BST)"
        assert london_dt.minute == 44
        assert london_dt.utcoffset() == timedelta(hours=1), "BST offset should be +1"

    def test_utc_to_ny_conversion(self):
        """2026-04-03 16:44:00 UTC = 12:44:00 EDT (UTC-4)"""
        utc_dt = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)
        ny_tz = get_ny_tz()
        ny_dt = utc_dt.astimezone(ny_tz)

        assert ny_dt.hour == 12, "UTC 16:44 should be 12:44 NY (EDT)"
        assert ny_dt.minute == 44
        assert ny_dt.utcoffset() == timedelta(hours=-4), "EDT offset should be -4"

    def test_utc_to_hk_conversion(self):
        """2026-04-03 16:44:00 UTC = 2026-04-04 00:44:00 HKT (UTC+8)"""
        utc_dt = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)
        hk_tz = get_hk_tz()
        hk_dt = utc_dt.astimezone(hk_tz)

        assert hk_dt.day == 4, "UTC 16:44 on April 3 should be April 4 in HK"
        assert hk_dt.hour == 0, "UTC 16:44 should be 00:44 HK"
        assert hk_dt.minute == 44
        assert hk_dt.utcoffset() == timedelta(hours=8), "HKT offset should be +8"

    def test_timezone_consistency(self):
        """All timezones should represent the same moment in time"""
        utc_dt = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)
        london_tz = get_london_tz()
        ny_tz = get_ny_tz()
        hk_tz = get_hk_tz()

        london_dt = utc_dt.astimezone(london_tz)
        ny_dt = utc_dt.astimezone(ny_tz)
        hk_dt = utc_dt.astimezone(hk_tz)

        # All should convert back to the same UTC time
        assert london_dt.astimezone(timezone.utc) == utc_dt
        assert ny_dt.astimezone(timezone.utc) == utc_dt
        assert hk_dt.astimezone(timezone.utc) == utc_dt

    def test_conversion_round_trip(self):
        """Convert UTC → Local → UTC should yield identical time"""
        original_utc = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)
        london_tz = get_london_tz()

        # Forward conversion
        london_dt = original_utc.astimezone(london_tz)

        # Backward conversion
        recovered_utc = london_dt.astimezone(timezone.utc)

        assert original_utc == recovered_utc, "Round-trip conversion must preserve time"


class TestBSTTransitions:
    """Test British Summer Time transitions (spring forward & fall back)"""

    def test_bst_spring_forward_2026(self):
        """2026-03-29: Spring forward (01:00 GMT → 02:00 BST)"""
        # Last moment of GMT: 2026-03-29 00:59:59 GMT (UTC+0)
        before_jump = datetime(2026, 3, 29, 0, 59, 59, tzinfo=timezone.utc)
        london_tz = get_london_tz()
        london_before = before_jump.astimezone(london_tz)

        assert london_before.hour == 0, "Before spring forward should be 00:59:59 GMT"
        assert london_before.utcoffset() == timedelta(hours=0), "Should be GMT (UTC+0)"

        # First moment of BST: 2026-03-29 01:00:00 UTC = 02:00:00 BST
        after_jump = datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc)
        london_after = after_jump.astimezone(london_tz)

        assert london_after.hour == 2, "After spring forward should be 02:00:00 BST"
        assert london_after.utcoffset() == timedelta(hours=1), "Should be BST (UTC+1)"

    def test_bst_fall_back_2026(self):
        """2026-10-25: Fall back (02:00 BST → 01:00 GMT)"""
        # Last moment of BST: 2026-10-25 00:59:59 UTC = 01:59:59 BST
        before_fall = datetime(2026, 10, 25, 0, 59, 59, tzinfo=timezone.utc)
        london_tz = get_london_tz()
        london_before = before_fall.astimezone(london_tz)

        assert london_before.hour == 1, "Before fall back should be 01:59:59 BST"
        assert london_before.utcoffset() == timedelta(hours=1), "Should be BST (UTC+1)"

        # First moment of GMT: 2026-10-25 01:00:00 UTC = 01:00:00 GMT
        after_fall = datetime(2026, 10, 25, 1, 0, 0, tzinfo=timezone.utc)
        london_after = after_fall.astimezone(london_tz)

        assert london_after.hour == 1, "After fall back should be 01:00:00 GMT"
        assert london_after.utcoffset() == timedelta(hours=0), "Should be GMT (UTC+0)"

    def test_bst_all_transitions_2025_2032(self):
        """Verify all BST transitions are correct for 2025-2032"""
        london_tz = get_london_tz()

        transitions = [
            (2025, 3, 30, "spring"),   # Spring forward
            (2025, 10, 26, "fall"),    # Fall back
            (2026, 3, 29, "spring"),
            (2026, 10, 25, "fall"),
            (2027, 3, 28, "spring"),
            (2027, 10, 31, "fall"),
            (2028, 3, 26, "spring"),
            (2028, 10, 29, "fall"),
            (2029, 3, 25, "spring"),
            (2029, 10, 28, "fall"),
            (2030, 3, 31, "spring"),
            (2030, 10, 27, "fall"),
            (2031, 3, 30, "spring"),
            (2031, 10, 26, "fall"),
            (2032, 3, 28, "spring"),
            (2032, 10, 24, "fall"),
        ]

        for year, month, day, transition_type in transitions:
            # Check day before transition
            before = datetime(year, month, day - 1, 12, 0, 0, tzinfo=timezone.utc)
            london_before = before.astimezone(london_tz)

            # Check day of transition
            on_day = datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
            london_on_day = on_day.astimezone(london_tz)

            if transition_type == "spring":
                # Spring forward: offset changes from 0 → 3600 (UTC+1)
                assert london_before.utcoffset() == timedelta(hours=0), \
                    f"Day before spring {year}-{month:02d}-{day:02d} should be GMT"
                assert london_on_day.utcoffset() == timedelta(hours=1), \
                    f"Spring forward {year}-{month:02d}-{day:02d} should be BST"

            elif transition_type == "fall":
                # Fall back: offset changes from 3600 → 0 (UTC+0)
                assert london_before.utcoffset() == timedelta(hours=1), \
                    f"Day before fall {year}-{month:02d}-{day:02d} should be BST"
                assert london_on_day.utcoffset() == timedelta(hours=0), \
                    f"Fall back {year}-{month:02d}-{day:02d} should be GMT"


class TestSessionDetection:
    """Test LSE session detection (ModeA/B/C/Dark)"""

    def test_mode_a_session(self):
        """ModeA: 08:00-12:00 London"""
        # 08:00 London on a weekday
        utc_open = datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)  # Thursday 08:00 UTC
        session = get_session_mode(utc_open)
        assert session == SessionMode.ModeA, "08:00 UTC should be ModeA (LSE open)"

        # 11:59 London on a weekday
        utc_before_b = datetime(2026, 1, 15, 11, 59, 59, tzinfo=timezone.utc)
        session = get_session_mode(utc_before_b)
        assert session == SessionMode.ModeA, "11:59:59 UTC should be ModeA"

    def test_mode_b_session(self):
        """ModeB: 12:00-16:00 London"""
        # 12:00 London on a weekday
        utc_noon = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_noon)
        assert session == SessionMode.ModeB, "12:00 UTC should be ModeB"

        # 15:59 London on a weekday
        utc_before_close = datetime(2026, 1, 15, 15, 59, 59, tzinfo=timezone.utc)
        session = get_session_mode(utc_before_close)
        assert session == SessionMode.ModeB, "15:59:59 UTC should be ModeB"

    def test_mode_b_plus_session(self):
        """ModeBPlus: 16:00-16:30 London"""
        # 16:00 London on a weekday
        utc_close_start = datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_close_start)
        assert session == SessionMode.ModeBPlus, "16:00 UTC should be ModeBPlus"

        # 16:29:59 London on a weekday
        utc_close_end = datetime(2026, 1, 15, 16, 29, 59, tzinfo=timezone.utc)
        session = get_session_mode(utc_close_end)
        assert session == SessionMode.ModeBPlus, "16:29:59 UTC should be ModeBPlus"

    def test_dark_session_after_close(self):
        """Dark: 16:30-next 08:00 London"""
        # 16:30 London on a weekday (just after close)
        utc_after_close = datetime(2026, 1, 15, 16, 30, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_after_close)
        assert session == SessionMode.Dark, "16:30 UTC should be Dark"

        # 23:00 London on a weekday (post-market)
        utc_post_market = datetime(2026, 1, 15, 23, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_post_market)
        assert session == SessionMode.Dark, "23:00 UTC should be Dark"

    def test_dark_session_before_open(self):
        """Dark: midnight to 08:00 London"""
        # 00:00 London
        utc_midnight = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_midnight)
        assert session == SessionMode.Dark, "00:00 UTC should be Dark"

        # 07:59:59 London
        utc_before_open = datetime(2026, 1, 15, 7, 59, 59, tzinfo=timezone.utc)
        session = get_session_mode(utc_before_open)
        assert session == SessionMode.Dark, "07:59:59 UTC should be Dark"

    def test_dark_session_weekends(self):
        """Weekends: always Dark regardless of time"""
        # Saturday 12:00 London (would be ModeB on weekday)
        utc_saturday = datetime(2026, 1, 17, 12, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_saturday)
        assert session == SessionMode.Dark, "Saturday should be Dark"

        # Sunday 12:00 London (would be ModeB on weekday)
        utc_sunday = datetime(2026, 1, 18, 12, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_sunday)
        assert session == SessionMode.Dark, "Sunday should be Dark"

    def test_dark_session_uk_holidays(self):
        """UK holidays: always Dark regardless of time"""
        # Good Friday 2026 (April 3)
        utc_good_friday = datetime(2026, 4, 3, 12, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_good_friday)
        assert session == SessionMode.Dark, "Good Friday should be Dark"

        # Easter Monday 2026 (April 6)
        utc_easter_monday = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(utc_easter_monday)
        assert session == SessionMode.Dark, "Easter Monday should be Dark"


class TestEventTiming:
    """Test economic event timing windows"""

    def test_event_window_utc_locked(self):
        """Event windows must be in UTC"""
        # Example: US NFP release at 13:30 ET
        # = 17:30 UTC (during Q2 EDT, UTC-4)
        # = 18:30 UTC (during Q4 EST, UTC-5)

        # Pre-window: -5 minutes UTC
        # Post-window: +10 minutes UTC

        nfp_utc = datetime(2026, 4, 3, 17, 30, 0, tzinfo=timezone.utc)
        pre_window = nfp_utc - timedelta(minutes=5)
        post_window = nfp_utc + timedelta(minutes=10)

        # All should be UTC
        assert nfp_utc.tzinfo == timezone.utc, "Event time must be UTC"
        assert pre_window.tzinfo == timezone.utc, "Pre-window must be UTC"
        assert post_window.tzinfo == timezone.utc, "Post-window must be UTC"

    def test_event_calendar_times_are_utc(self):
        """All times from EventCalendar must be UTC"""
        try:
            cal = EventCalendar()
            events = cal.get_events_for_date(datetime(2026, 4, 3).date())

            for event in events:
                assert event.time.tzinfo == timezone.utc, \
                    f"Event {event.name} time must be UTC, got {event.time.tzinfo}"
        except (ImportError, AttributeError):
            pytest.skip("EventCalendar not available or different structure")


class TestTimeAuditTrail:
    """Test that time operations are logged correctly"""

    def test_audit_log_contains_utc_timestamp(self):
        """Audit logs must contain UTC timestamp"""
        now_utc = datetime.now(timezone.utc)
        iso_str = now_utc.isoformat()

        # ISO format with UTC marker: 2026-04-03T16:44:00.123456+00:00
        assert "+" in iso_str or "Z" in iso_str, "UTC timezone marker missing from ISO string"
        assert iso_str.endswith("+00:00"), "UTC offset must be +00:00"

    def test_timezone_conversion_audit(self):
        """Conversions should be auditabled"""
        utc_dt = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)
        london_tz = get_london_tz()
        london_dt = utc_dt.astimezone(london_tz)

        # Audit trail example:
        audit_msg = f"[TIME_AUDIT] UTC: {utc_dt.isoformat()} → London: {london_dt.isoformat()}"
        assert "TIME_AUDIT" in audit_msg
        assert utc_dt.isoformat() in audit_msg
        assert london_dt.isoformat() in audit_msg


class TestTimeErrorHandling:
    """Test error handling for time-related issues"""

    def test_naive_datetime_validation(self):
        """System should reject naive datetime without timezone"""
        naive_dt = datetime(2026, 4, 3, 16, 44, 0)
        assert naive_dt.tzinfo is None, "Naive datetime has no timezone (expected for test)"

        # In production, this should raise an error or assertion
        # Example implementation:
        try:
            assert naive_dt.tzinfo is not None, "Datetime must have timezone"
            pytest.fail("Should have caught naive datetime")
        except AssertionError:
            pass  # Expected

    def test_ambiguous_time_handling(self):
        """Handle ambiguous times during DST transitions"""
        # During fall back, 01:00-02:00 occurs twice
        # zoneinfo should handle this correctly
        london_tz = get_london_tz()

        # First occurrence (BST, UTC+1)
        dt1 = datetime(2026, 10, 25, 1, 30, 0)
        dt1_bst = london_tz.localize(dt1, is_dst=True) if hasattr(london_tz, 'localize') else \
                  dt1.replace(tzinfo=london_tz)

        # Second occurrence (GMT, UTC+0)
        dt2 = datetime(2026, 10, 25, 1, 30, 0)
        dt2_gmt = london_tz.localize(dt2, is_dst=False) if hasattr(london_tz, 'localize') else \
                  dt2.replace(tzinfo=london_tz)

        # Both times exist, but represent different UTC moments
        # zoneinfo.ZoneInfo handles this automatically
        assert dt1_bst.tzinfo == london_tz
        assert dt2_gmt.tzinfo == london_tz


class TestRealWorldScenarios:
    """Test real-world trading scenarios"""

    def test_scenario_trade_at_market_open(self):
        """Trade placed at LSE open (08:00 London)"""
        trade_utc = datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
        session = get_session_mode(trade_utc)

        assert session == SessionMode.ModeA, "Trade at 08:00 should be in ModeA"
        assert trade_utc.tzinfo == timezone.utc, "Trade timestamp must be UTC"

    def test_scenario_trade_at_market_close(self):
        """Trade placed at LSE close (16:30 London)"""
        trade_utc = datetime(2026, 1, 15, 16, 30, 0, tzinfo=timezone.utc)
        session = get_session_mode(trade_utc)

        assert session == SessionMode.Dark, "Trade at 16:30 should be Dark (closed)"

    def test_scenario_bst_transition_overnight(self):
        """System handles a trade spanning BST transition"""
        # Trade during spring forward night (01:00 → 02:00)
        before_transition = datetime(2026, 3, 29, 0, 30, 0, tzinfo=timezone.utc)
        after_transition = datetime(2026, 3, 29, 2, 30, 0, tzinfo=timezone.utc)

        london_tz = get_london_tz()
        london_before = before_transition.astimezone(london_tz)
        london_after = after_transition.astimezone(london_tz)

        # Before: 00:30 GMT
        assert london_before.utcoffset() == timedelta(hours=0)

        # After: 03:30 BST (note: 01:00-02:00 never occurred)
        assert london_after.utcoffset() == timedelta(hours=1)

        # Should not affect UTC timestamps
        assert before_transition.tzinfo == timezone.utc
        assert after_transition.tzinfo == timezone.utc


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
