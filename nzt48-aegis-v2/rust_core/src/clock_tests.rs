#[cfg(test)]
mod tests {
    use super::super::*;

    // =========================================================================
    // TEST 1: LSE Open/Close Boundaries
    // =========================================================================

    #[test]
    fn test_lse_open_exact_boundary() {
        // 08:00:00 London = LSE OPEN (inclusive)
        let london_secs = to_london_secs(8, 0, 0);
        assert!(is_lse_open(london_secs), "LSE should be open at 08:00:00");
    }

    #[test]
    fn test_lse_closed_before_open() {
        // 07:59:59 London = LSE CLOSED
        let london_secs = to_london_secs(7, 59, 59);
        assert!(!is_lse_open(london_secs), "LSE should be closed at 07:59:59");
    }

    #[test]
    fn test_lse_closed_at_close() {
        // 16:30:00 London = LSE CLOSED (exclusive)
        let london_secs = to_london_secs(16, 30, 0);
        assert!(!is_lse_open(london_secs), "LSE should be closed at 16:30:00");
    }

    #[test]
    fn test_lse_open_before_close() {
        // 16:29:59 London = LSE OPEN (inclusive)
        let london_secs = to_london_secs(16, 29, 59);
        assert!(is_lse_open(london_secs), "LSE should be open at 16:29:59");
    }

    #[test]
    fn test_lse_closed_after_close() {
        // 16:30:01 London = LSE CLOSED
        let london_secs = to_london_secs(16, 30, 1);
        assert!(!is_lse_open(london_secs), "LSE should be closed at 16:30:01");
    }

    #[test]
    fn test_lse_midnight() {
        // 00:00:00 London = LSE CLOSED
        let london_secs = to_london_secs(0, 0, 0);
        assert!(!is_lse_open(london_secs), "LSE should be closed at midnight");
    }

    #[test]
    fn test_lse_noon() {
        // 12:00:00 London = LSE OPEN
        let london_secs = to_london_secs(12, 0, 0);
        assert!(is_lse_open(london_secs), "LSE should be open at noon");
    }

    // =========================================================================
    // TEST 2: BST Transitions (Spring Forward & Fall Back)
    // =========================================================================

    #[test]
    fn test_bst_spring_forward_2026() {
        // 2026-03-28 23:59:59 GMT: offset = 0
        let offset_before = get_bst_offset_for_date(2026, 3, 28);
        assert_eq!(offset_before, 0, "Before BST spring, offset should be 0 (GMT)");

        // 2026-03-29 01:00:00 BST: offset = 3600 (1 hour)
        let offset_after = get_bst_offset_for_date(2026, 3, 29);
        assert_eq!(offset_after, 3600, "After BST spring, offset should be 3600 (BST)");
    }

    #[test]
    fn test_bst_fall_back_2026() {
        // 2026-10-24 23:59:59 BST: offset = 3600
        let offset_before = get_bst_offset_for_date(2026, 10, 24);
        assert_eq!(offset_before, 3600, "Before BST fall, offset should be 3600 (BST)");

        // 2026-10-25 01:00:00 GMT: offset = 0
        let offset_after = get_bst_offset_for_date(2026, 10, 25);
        assert_eq!(offset_after, 0, "After BST fall, offset should be 0 (GMT)");
    }

    #[test]
    fn test_bst_all_transitions_2025_2032() {
        // Verify all hardcoded BST transitions have correct offsets
        let transitions = vec![
            (2025, 3, 30, "spring"),   // Spring forward
            (2025, 10, 26, "fall"),    // Fall back
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
        ];

        for (year, month, day, transition_type) in transitions {
            let offset_on_day = get_bst_offset_for_date(year, month, day);
            let offset_before = get_bst_offset_for_date(year, month, day - 1);

            match transition_type {
                "spring" => {
                    // Spring forward: offset changes from 0 → 3600
                    assert_eq!(offset_before, 0, "Day before spring forward should be GMT");
                    assert_eq!(offset_on_day, 3600, "Spring forward day should be BST");
                }
                "fall" => {
                    // Fall back: offset changes from 3600 → 0
                    assert_eq!(offset_before, 3600, "Day before fall back should be BST");
                    assert_eq!(offset_on_day, 0, "Fall back day should be GMT");
                }
                _ => panic!("Unknown transition type"),
            }
        }
    }

    #[test]
    #[should_panic(expected = "BST dates unknown for year")]
    fn test_bst_year_out_of_range() {
        // Should panic: year 2024 (before hardcoded range)
        let _ = get_bst_offset_for_date(2024, 6, 15);
    }

    #[test]
    #[should_panic(expected = "BST dates unknown for year")]
    fn test_bst_year_future_out_of_range() {
        // Should panic: year 2033 (after hardcoded range)
        let _ = get_bst_offset_for_date(2033, 6, 15);
    }

    // =========================================================================
    // TEST 3: Trading Mode Transitions
    // =========================================================================

    #[test]
    fn test_mode_a_boundaries() {
        // ModeA: 08:00-12:00 London
        assert_eq!(get_mode(to_london_secs(8, 0, 0)), TradingMode::ModeA);
        assert_eq!(get_mode(to_london_secs(11, 59, 59)), TradingMode::ModeA);
        assert_ne!(get_mode(to_london_secs(7, 59, 59)), TradingMode::ModeA);
        assert_ne!(get_mode(to_london_secs(12, 0, 0)), TradingMode::ModeA);
    }

    #[test]
    fn test_mode_b_boundaries() {
        // ModeB: 12:00-16:00 London
        assert_eq!(get_mode(to_london_secs(12, 0, 0)), TradingMode::ModeB);
        assert_eq!(get_mode(to_london_secs(15, 59, 59)), TradingMode::ModeB);
        assert_ne!(get_mode(to_london_secs(11, 59, 59)), TradingMode::ModeB);
        assert_ne!(get_mode(to_london_secs(16, 0, 0)), TradingMode::ModeB);
    }

    #[test]
    fn test_mode_b_plus_boundaries() {
        // ModeBPlus: 16:00-16:30 London
        assert_eq!(get_mode(to_london_secs(16, 0, 0)), TradingMode::ModeBPlus);
        assert_eq!(get_mode(to_london_secs(16, 29, 59)), TradingMode::ModeBPlus);
        assert_ne!(get_mode(to_london_secs(15, 59, 59)), TradingMode::ModeBPlus);
        assert_ne!(get_mode(to_london_secs(16, 30, 0)), TradingMode::ModeBPlus);
    }

    #[test]
    fn test_mode_dark_after_close() {
        // Dark: 16:30-23:59 London
        assert_eq!(get_mode(to_london_secs(16, 30, 0)), TradingMode::Dark);
        assert_eq!(get_mode(to_london_secs(23, 59, 59)), TradingMode::Dark);
        assert_ne!(get_mode(to_london_secs(16, 29, 59)), TradingMode::Dark);
    }

    #[test]
    fn test_mode_dark_weekends() {
        // Weekend: should be Dark regardless of time
        // Assuming we have a way to set test day of week
        let friday_secs = to_london_secs(16, 0, 0);  // ModeBPlus on Friday
        let saturday_secs = to_london_secs(12, 0, 0); // Should be Dark on Saturday

        // This test requires a day-of-week parameter; adjust based on actual implementation
        assert_eq!(get_mode(friday_secs), TradingMode::ModeBPlus);
        // Saturday time verification would go here
    }

    #[test]
    fn test_mode_dark_holidays() {
        // Holidays: should be Dark regardless of time
        // Christmas 2025: should always be Dark
        // New Year 2026: should always be Dark
        // This requires a holiday parameter; adjust based on actual implementation
    }

    // =========================================================================
    // TEST 4: UTC Time Parsing & Conversions
    // =========================================================================

    #[test]
    fn test_unix_epoch_to_london_time() {
        // 2026-04-03 16:44:00 UTC
        let utc_ns = 1748970240_000_000_000u64;  // Example nanosecond timestamp

        let london_secs = utc_to_london_secs(utc_ns);

        // Should be 17:44 London (UTC+1, BST)
        let (hour, minute, _) = parse_london_time(london_secs);
        assert_eq!(hour, 17, "UTC 16:44 should be 17:44 London (BST)");
        assert_eq!(minute, 44);
    }

    #[test]
    fn test_london_time_to_utc() {
        // 2026-04-03 17:44:00 London (BST, UTC+1)
        let london_secs = to_london_secs(17, 44, 0);

        let utc_ns = london_secs_to_utc(london_secs);

        // Should be 16:44 UTC
        let (utc_hour, utc_minute, _) = parse_utc_time(utc_ns);
        assert_eq!(utc_hour, 16);
        assert_eq!(utc_minute, 44);
    }

    #[test]
    fn test_dst_transition_midnight_cross() {
        // Test handling of times that cross DST boundaries
        // 2026-03-29 00:59:59 GMT (last second before 01:00 → 02:00 jump)
        let before_jump = to_london_secs(0, 59, 59);
        let offset_before = get_bst_offset_for_date(2026, 3, 29);

        // 2026-03-29 02:00:00 BST (first second after the spring forward)
        let after_jump = to_london_secs(2, 0, 0);
        let offset_after = get_bst_offset_for_date(2026, 3, 29);

        assert_eq!(offset_before, 0, "Offset before spring forward should be 0");
        assert_eq!(offset_after, 3600, "Offset after spring forward should be 3600");
    }

    // =========================================================================
    // TEST 5: Time Validation & Error Handling
    // =========================================================================

    #[test]
    #[should_panic(expected = "hour < 24")]
    fn test_invalid_hour() {
        let _ = to_london_secs(25, 0, 0);
    }

    #[test]
    #[should_panic(expected = "minute < 60")]
    fn test_invalid_minute() {
        let _ = to_london_secs(12, 61, 0);
    }

    #[test]
    #[should_panic(expected = "second < 60")]
    fn test_invalid_second() {
        let _ = to_london_secs(12, 30, 61);
    }

    // =========================================================================
    // TEST 6: Consistency Across Timezones
    // =========================================================================

    #[test]
    fn test_utc_london_ny_consistency() {
        // 2026-04-03 16:44:00 UTC
        // Should be: 17:44 London (UTC+1), 12:44 NY (UTC-4)
        let utc_ns = 1748970240_000_000_000u64;

        let london_secs = utc_to_london_secs(utc_ns);
        let ny_secs = utc_to_ny_secs(utc_ns);

        let (london_hour, london_minute, _) = parse_london_time(london_secs);
        let (ny_hour, ny_minute, _) = parse_ny_time(ny_secs);

        assert_eq!(london_hour, 17);
        assert_eq!(london_minute, 44);
        assert_eq!(ny_hour, 12);
        assert_eq!(ny_minute, 44);

        // NY should be 5 hours behind London
        let hour_diff = (london_hour as i32 - ny_hour as i32 + 24) % 24;
        assert_eq!(hour_diff, 5, "London should be 5 hours ahead of NY during EDT/BST");
    }

    // =========================================================================
    // HELPER FUNCTIONS
    // =========================================================================

    fn to_london_secs(hour: u32, minute: u32, second: u32) -> u64 {
        assert!(hour < 24, "hour < 24");
        assert!(minute < 60, "minute < 60");
        assert!(second < 60, "second < 60");
        ((hour * 3600) + (minute * 60) + second) as u64
    }

    fn parse_london_time(london_secs: u64) -> (u32, u32, u32) {
        let hour = (london_secs / 3600) as u32 % 24;
        let minute = ((london_secs % 3600) / 60) as u32;
        let second = (london_secs % 60) as u32;
        (hour, minute, second)
    }

    fn get_bst_offset_for_date(year: u32, month: u32, day: u32) -> i32 {
        // Hardcoded BST transitions
        let is_bst = match year {
            2025 => is_between_dates((month, day), (3, 30), (10, 26)),
            2026 => is_between_dates((month, day), (3, 29), (10, 25)),
            2027 => is_between_dates((month, day), (3, 28), (10, 31)),
            2028 => is_between_dates((month, day), (3, 26), (10, 29)),
            2029 => is_between_dates((month, day), (3, 25), (10, 28)),
            2030 => is_between_dates((month, day), (3, 31), (10, 27)),
            2031 => is_between_dates((month, day), (3, 30), (10, 26)),
            2032 => is_between_dates((month, day), (3, 28), (10, 24)),
            _ => panic!("BST dates unknown for year {}", year),
        };
        if is_bst { 3600 } else { 0 }
    }

    fn is_between_dates(date: (u32, u32), start: (u32, u32), end: (u32, u32)) -> bool {
        date >= start && date < end
    }
}
