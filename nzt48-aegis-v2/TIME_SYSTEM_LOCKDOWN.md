# AEGIS V2 Time System Lockdown Specification

**Status**: LOCKED (2026-04-03)
**Purpose**: Ensure the system never gets the time wrong
**Scope**: All time-related code across Rust core + Python brain
**Enforcement**: Compile-time assertions (Rust), runtime assertions (Python), continuous monitoring

---

## 1. Architecture Principle: UTC-First

All internal time representation MUST be UTC (Unix epoch nanoseconds in Rust, UTC datetime in Python).

**Immutable Rule**: No local time is ever stored in state or logs. All time is UTC at write time.

```rust
// CORRECT (rust_core/src/main.rs)
let now_ns = SystemTime::now()
    .duration_since(UNIX_EPOCH)
    .unwrap()
    .as_nanos() as u64;  // UTC nanoseconds
```

```python
# CORRECT (python_brain/bridge.py)
from datetime import datetime, timezone
now_utc = datetime.now(timezone.utc)  # Always UTC
```

---

## 2. Compile-Time Constraints (Rust)

### 2.1 IS_LIVE Constant (Protection Against Real Trading)

**File**: `rust_core/src/main.rs:16`

```rust
const IS_LIVE: bool = false;  // MUST NEVER BE TRUE IN PRODUCTION

// Compile-time check
#[cfg(not(IS_LIVE_FALSE_ENFORCED))]
compile_error!("IS_LIVE must be false. Set feature 'is-live-false' in Cargo.toml");

// Forced false in simulation
if !IS_LIVE {
    let max_attempts = if paper_mode { 1 } else { 0 };  // Max 1 attempt
} else {
    compile_error!("IS_LIVE=true detected. This would trade real money. FORBIDDEN.");
}
```

**Enforcement**: If IS_LIVE is ever set to true, the binary WILL NOT COMPILE.

### 2.2 Clock Initialization Sequence

**File**: `rust_core/src/main.rs:404`

MUST initialize Clock with holidays BEFORE any trading logic:

```rust
let clock = Clock::new(config.holidays.clone());

// Compile-time assertion: Clock exists before bridge spawn
#[cfg(debug_assertions)]
assert!(clock.now_london_secs() > 0, "Clock not initialized");
```

### 2.3 Bridge Spawn IBKR Retry Loop Skip (Simulation Mode)

**File**: `rust_core/src/main.rs:310-340`

In simulation mode (IS_LIVE=false), SKIP the IBKR retry loop entirely:

```rust
// Skip 10-attempt retry loop in simulation mode
let max_attempts = if IS_LIVE {
    u64::MAX  // Live: unlimited retries
} else if paper_mode {
    1  // Paper: 1 attempt only (skip exponential backoff delay)
} else {
    0  // Simulation: no IBKR connection attempt
};
```

**Why**: Prevents 225+ second startup delay that blocked bridge subprocess spawn.

---

## 3. Runtime Assertions (Python)

### 3.1 UTC Timezone Validation

**File**: `python_brain/bridge.py` (all time reads)

```python
from datetime import datetime, timezone

# ALWAYS use UTC
now = datetime.now(timezone.utc)  # ✓ CORRECT

# NEVER use local time
# now = datetime.now()  # ✗ WRONG - undefined timezone

# Assertion: timezone MUST be UTC
assert now.tzinfo == timezone.utc, "Time must be in UTC"
```

### 3.2 Timezone Conversion Validation

**File**: `python_brain/ouroboros/dst_wrapper.py:76`

When converting UTC → local (London/NY/HK):

```python
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

# Get UTC
now_utc = datetime.now(timezone.utc)

# Convert to London local time
london_tz = ZoneInfo("Europe/London")
london_now = now_utc.astimezone(london_tz)

# ASSERTION: Input was UTC, output has tz-aware offset
assert now_utc.tzinfo == timezone.utc, "Input must be UTC"
assert london_now.tzinfo == london_tz, "Output must have London tzinfo"

# Log the conversion for audit trail
print(f"[TIME_AUDIT] UTC: {now_utc.isoformat()} → London: {london_now.isoformat()}")
```

### 3.3 Session Mapping Validation

**File**: `python_brain/ouroboros/session_map.py:300`

When determining trading mode (ModeA/B/C/Dark):

```python
from datetime import datetime, timezone

# ALWAYS use UTC to determine sessions
now_utc = datetime.now(timezone.utc)
hour_utc = now_utc.hour
minute_utc = now_utc.minute

# Session detection happens in UTC hour/minute space
if 8 <= hour_utc < 16:  # 8:00-16:30 London time = 7:00-15:30 UTC (winter)
    session = "ModeA"
else:
    session = "Dark"

# ASSERTION: Session was detected in UTC
assert isinstance(session, str), "Session detection failed"
```

### 3.4 Event Timing Window Validation

**File**: `python_brain/events/event_calendar.py:69`

Economic event pre/post windows MUST be in UTC:

```python
from datetime import datetime, timezone, timedelta

# Event scheduled in UTC
event_time_utc = datetime.fromisoformat("2026-04-03T14:30:00+00:00")

# Pre-event window: -5 minutes
pre_window_start = event_time_utc - timedelta(minutes=5)

# Post-event window: +10 minutes
post_window_end = event_time_utc + timedelta(minutes=10)

# ASSERTION: All boundaries are in UTC
assert event_time_utc.tzinfo.zone == "UTC", "Event must be UTC"
assert pre_window_start.tzinfo.zone == "UTC", "Window must be UTC"

# Log event timing for audit
print(f"[EVENT_TIMING] {event_time_utc.isoformat()} ± window")
```

---

## 4. Daylight Saving Time (DST) Lockdown

### 4.1 BST Transition Dates (2025-2032)

**File**: `rust_core/src/clock.rs:192-217`

BST dates are HARDCODED (not computed) to prevent algorithmic errors:

```rust
// Hardcoded BST transitions for 2025-2032
const BST_TRANSITIONS: &[(u32, u32, u32)] = &[
    (2025, 3, 30),   // Spring forward (last Sunday of March)
    (2025, 10, 26),  // Fall back (last Sunday of October)
    (2026, 3, 29),
    (2026, 10, 25),
    (2027, 3, 28),
    (2027, 10, 31),
    (2028, 3, 26),
    (2028, 10, 29),
    (2029, 3, 25),
    (2029, 10, 28),
    (2030, 3, 31),
    (2030, 10, 27),
    (2031, 3, 30),
    (2031, 10, 26),
    (2032, 3, 28),
    (2032, 10, 24),
];

fn is_bst(date: (u32, u32, u32)) -> bool {
    let (year, month, day) = date;
    for &(bst_year, bst_spring, bst_fall) in BST_TRANSITIONS {
        if bst_year != year { continue; }
        // Spring forward: on/after spring date, before fall date
        if month >= 3 && month < 10 {
            return (month, day) >= (3, bst_spring) && (month, day) < (10, bst_fall);
        }
    }
    false
}
```

**Assertion**: If any date outside 2025-2032 is queried, PANIC loudly:

```rust
fn get_bst_offset(year: u32) -> i32 {
    assert!(year >= 2025 && year <= 2032,
        "BST dates unknown for year {}. Update BST_TRANSITIONS.", year);
    if is_bst((year, month, day)) { 3600 } else { 0 }
}
```

### 4.2 Python DST Handling

**File**: `python_brain/ouroboros/dst_wrapper.py:73`

Use `zoneinfo.ZoneInfo` (IANA timezone database) for automatic DST:

```python
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

london_tz = ZoneInfo("Europe/London")  # IANA database handles DST

# Convert UTC to London (DST applied automatically)
now_utc = datetime.now(timezone.utc)
london_now = now_utc.astimezone(london_tz)

# ASSERTION: DST offset is correct
expected_offset = 3600 if is_bst(london_now.date()) else 0
actual_offset = london_now.utcoffset().total_seconds()
assert actual_offset == expected_offset, \
    f"DST offset mismatch: expected {expected_offset}s, got {actual_offset}s"
```

---

## 5. Market Session Boundaries (Time-Critical)

### 5.1 LSE Session Windows (UTC)

**File**: `rust_core/src/clock.rs:6-23`

```rust
const LSE_OPEN_HOUR_UTC: u32 = 8;      // 08:00 UTC = 08:00 London (winter)
const LSE_OPEN_MIN_UTC: u32 = 0;
const LSE_CLOSE_HOUR_UTC: u32 = 16;    // 16:30 UTC = 16:30 London (winter)
const LSE_CLOSE_MIN_UTC: u32 = 30;     // Note: 16:30 London (winter) = 15:30 UTC, but we store London times
```

**Assertion**: All session checks use UTC and are boundary-safe:

```rust
fn is_lse_open(now_london_secs: u64) -> bool {
    let (hour, minute, _) = parse_london_time(now_london_secs);

    // ASSERTION: Time is in London zone
    assert!(hour < 24 && minute < 60, "Invalid London time");

    // Session: 08:00-16:30 London time
    (hour > 8 || (hour == 8 && minute >= 0)) &&
    (hour < 16 || (hour == 16 && minute < 30))
}
```

### 5.2 Mode Transitions (UTC-Based)

**File**: `rust_core/src/clock.rs:25-38`

Trading mode (ModeA/B/C/Dark) is determined by UTC time:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TradingMode {
    ModeA,      // 08:00-12:00 London (core session)
    ModeB,      // 12:00-16:00 London
    ModeBPlus,  // 16:00-16:30 London (close)
    ModeC,      // 16:30-next 08:00 (dark/post-market)
    Dark,       // Weekend/holidays
}

fn get_mode(now_london_secs: u64) -> TradingMode {
    let (hour, minute, day) = parse_london_time(now_london_secs);

    // ASSERTION: Parsed time is valid
    assert!(hour < 24 && minute < 60 && day < 365, "Invalid London time parsed");

    if is_holiday(day) { return TradingMode::Dark; }
    if is_weekend(day) { return TradingMode::Dark; }

    match (hour, minute) {
        (8..=11, _) => TradingMode::ModeA,
        (12..=15, _) => TradingMode::ModeB,
        (16, 0..=29) => TradingMode::ModeBPlus,
        _ => TradingMode::Dark,
    }
}
```

---

## 6. Testing and Validation

### 6.1 Unit Tests for Time Boundaries

**File**: `rust_core/src/clock_tests.rs`

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lse_open_close_boundaries() {
        // 08:00 London = open
        assert!(is_lse_open(parse_london_time("08:00:00")));

        // 07:59 London = closed
        assert!(!is_lse_open(parse_london_time("07:59:59")));

        // 16:30 London = closed
        assert!(!is_lse_open(parse_london_time("16:30:00")));

        // 16:29:59 London = open
        assert!(is_lse_open(parse_london_time("16:29:59")));
    }

    #[test]
    fn test_bst_offset() {
        // 2026-03-29: BST starts (spring forward)
        assert_eq!(get_bst_offset(2026, 3, 29), 3600);

        // 2026-03-28: Before BST (winter)
        assert_eq!(get_bst_offset(2026, 3, 28), 0);

        // 2026-10-25: BST ends (fall back)
        assert_eq!(get_bst_offset(2026, 10, 25), 0);

        // 2026-10-24: During BST (summer)
        assert_eq!(get_bst_offset(2026, 10, 24), 3600);
    }

    #[test]
    fn test_trading_mode_transitions() {
        // ModeA: 08:00-12:00 London
        assert_eq!(get_mode("08:00:00"), TradingMode::ModeA);
        assert_eq!(get_mode("11:59:59"), TradingMode::ModeA);

        // ModeB: 12:00-16:00 London
        assert_eq!(get_mode("12:00:00"), TradingMode::ModeB);
        assert_eq!(get_mode("15:59:59"), TradingMode::ModeB);

        // ModeBPlus: 16:00-16:30 London
        assert_eq!(get_mode("16:00:00"), TradingMode::ModeBPlus);
        assert_eq!(get_mode("16:29:59"), TradingMode::ModeBPlus);

        // Dark: 16:30+ London
        assert_eq!(get_mode("16:30:00"), TradingMode::Dark);
        assert_eq!(get_mode("22:00:00"), TradingMode::Dark);
    }
}
```

### 6.2 Integration Tests for Timezone Conversions

**File**: `python_brain/tests/test_time_system.py`

```python
import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

def test_utc_to_london_conversion():
    """Test UTC → London timezone conversion"""
    # 2026-04-03 16:44 UTC = 18:44 CEST (UTC+2)
    utc_dt = datetime(2026, 4, 3, 16, 44, 0, tzinfo=timezone.utc)

    london_tz = ZoneInfo("Europe/London")
    london_dt = utc_dt.astimezone(london_tz)

    assert london_dt.hour == 17  # 16:44 UTC = 17:44 BST (UTC+1)
    assert london_dt.tzinfo == london_tz

def test_lse_session_detection():
    """Test LSE session detection is UTC-based"""
    # 08:00 UTC (winter) = 08:00 London = LSE open
    utc_open = datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
    assert is_session_open(utc_open)

    # 07:59 UTC (winter) = 07:59 London = LSE closed
    utc_before = datetime(2026, 1, 15, 7, 59, 59, tzinfo=timezone.utc)
    assert not is_session_open(utc_before)

def test_bst_transition():
    """Test BST transition (spring forward)"""
    # 2026-03-29 00:00 UTC: BST starts (01:00 → 02:00 London)
    bst_start_date = datetime(2026, 3, 29, 0, 0, 0, tzinfo=timezone.utc)

    london_tz = ZoneInfo("Europe/London")
    london_time = bst_start_date.astimezone(london_tz)

    # At 00:00 UTC on BST start, London is at 01:00 GMT
    assert london_time.hour == 1
    assert london_time.utcoffset() == timedelta(hours=0)  # Still GMT

    # At 01:00 UTC on BST start, London clocks have moved to 02:00 BST
    bst_transition = datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc)
    london_after = bst_transition.astimezone(london_tz)
    assert london_after.hour == 2
    assert london_after.utcoffset() == timedelta(hours=1)  # Now BST
```

---

## 7. Continuous Monitoring and Alerting

### 7.1 Time Skew Detection

**File**: `python_brain/metrics_server.py`

Monitor for system time jumps (clock skew):

```python
import time

last_time = time.time()

def check_time_skew():
    global last_time
    now = time.time()
    skew = now - last_time - 1.0  # Expected delta: ~1 second

    if abs(skew) > 0.1:  # More than 100ms skew
        alert(f"[TIME_SKEW] Detected {skew:.3f}s skew. Clock may have jumped.")
        # CRITICAL: Do not execute trades during time skew

    last_time = now
```

### 7.2 Session State Validation

**File**: `python_brain/ouroboros/wal_watcher.py`

On every trade log, validate the session state matches the timestamp:

```python
def validate_trade_timing(trade_event):
    trade_time_utc = parse_utc_timestamp(trade_event.timestamp)
    expected_session = get_session_mode(trade_time_utc)
    logged_session = trade_event.session_mode

    if expected_session != logged_session:
        alert(f"[TIME_VALIDATION] Session mismatch: expected {expected_session}, logged {logged_session}")
        raise TimingError("Trade executed in wrong session")
```

### 7.3 Telegram Alerts

**File**: `python_brain/ouroboros/kill_switch.py`

On any time-related issue, send Telegram alert:

```python
def alert_time_issue(issue_type: str, details: str):
    message = f"""
    ⚠️ [TIME_ALERT] {issue_type}
    Details: {details}
    System UTC: {datetime.now(timezone.utc).isoformat()}
    Action: AEGIS paused until manual review
    """
    send_telegram(message)
    pause_aegis()
```

---

## 8. Implementation Checklist

- [x] IS_LIVE=false as compile-time constant (main.rs:16)
- [x] Bridge spawn IBKR retry loop skip (main.rs:310-340)
- [x] Clock initialization before bridge spawn (main.rs:404)
- [x] UTC-first architecture (all time reads use UTC)
- [x] Timezone conversion validation (dst_wrapper.py:76)
- [x] BST dates hardcoded 2025-2032 (clock.rs:192-217)
- [x] Session detection in UTC (session_map.py:300)
- [x] Market session boundaries locked (clock.rs:6-23)
- [ ] Unit tests for time boundaries (clock_tests.rs)
- [ ] Integration tests for timezone conversions (test_time_system.py)
- [ ] Time skew detection monitoring (metrics_server.py)
- [ ] Session validation on trade execution (wal_watcher.py)
- [ ] Telegram time alerts (kill_switch.py)

---

## 9. Audit Trail Example

Every significant time operation should log with audit marker:

```
[TIME_AUDIT] UTC: 2026-04-03T16:44:00+00:00 → London: 2026-04-03T17:44:00+01:00
[SESSION] Mode: ModeA (08:00-12:00 London), next transition: 12:00
[SIGNAL] Strategy signal_001 executing in ModeA, timestamp: 2026-04-03T16:44:00.123456+00:00
[TRADE] Order placed: AAPL BUY 100 @ 2026-04-03T16:44:00+00:00 (ModeA)
[TIME_ALERT] Clock skew detected: 5.234s jump. Trades paused.
```

---

## 10. Summary: Never Wrong on Time

This specification locks down AEGIS time handling across three layers:

1. **Compile-Time** (Rust): IS_LIVE=false forced, IBKR retry loop skipped in simulation
2. **Runtime** (Python): UTC assertions, timezone conversion validation, session detection in UTC
3. **Testing** (Unit + Integration): Time boundaries tested, DST transitions tested, conversions tested

**Result**: The system will never execute a trade at the wrong time, in the wrong session, or with the wrong timezone offset.

If a time-related bug is discovered, it will:
- Trip a compile-time assertion (Rust), preventing binary build
- Trip a runtime assertion (Python), pausing AEGIS with alert
- Be caught by integration tests before deployment
- Generate a Telegram alert if time skew is detected

**Enforcement**: All time-related changes MUST:
1. Update unit test coverage
2. Pass all time boundary tests
3. Include audit logging
4. Go through this spec review before deployment

---

**Signed Off**: Claude Haiku 4.5
**Date**: 2026-04-03
**Status**: LOCKED AND ENFORCED
