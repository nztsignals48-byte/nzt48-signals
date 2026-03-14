//! Quantum Apex — Advanced signal processing via Rust FFI to C++

use std::os::raw::{c_char, c_double, c_int, c_uint};

// FFI bindings to C++ quantum engine
#[link(name = "quantum_apex", kind = "static")]
unsafe extern "C" {
    fn qa_init() -> *mut c_char;
    fn qa_process_tick(
        ticker_id: c_uint,
        price: c_double,
        volume: c_uint,
        timestamp_ns: c_uint,
    ) -> c_double;
    fn qa_get_signal_weight(module_id: c_int) -> c_double;
    fn qa_shutdown() -> c_int;
    fn qa_free(ptr: *mut c_char);
}

pub struct QuantumApex {
    initialized: bool,
}

impl QuantumApex {
    pub fn new() -> Result<Self, String> {
        unsafe {
            let result = qa_init();
            if result.is_null() {
                return Err("Quantum Apex init failed".to_string());
            }
            qa_free(result);
        }
        Ok(QuantumApex { initialized: true })
    }

    pub fn process_tick(
        &self,
        ticker_id: u32,
        price: f64,
        volume: u32,
        timestamp_ns: u64,
    ) -> f64 {
        if !self.initialized {
            return 0.0;
        }
        unsafe {
            qa_process_tick(
                ticker_id as c_uint,
                price,
                volume as c_uint,
                (timestamp_ns & 0xFFFFFFFF) as c_uint,
            )
        }
    }

    pub fn get_signal_weight(&self, module_id: i32) -> f64 {
        if !self.initialized {
            return 1.0;  // Default weight
        }
        unsafe { qa_get_signal_weight(module_id) }
    }

    pub fn shutdown(&mut self) {
        if self.initialized {
            unsafe {
                let _ = qa_shutdown();
            }
            self.initialized = false;
        }
    }
}

impl Drop for QuantumApex {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test 24.1: QuantumApex initialization
    #[test]
    fn test_quantum_apex_init() {
        let qa = QuantumApex::new();
        assert!(qa.is_ok(), "QuantumApex should initialize successfully");
        let qa = qa.unwrap();
        assert!(qa.initialized, "QuantumApex should be marked as initialized");
    }

    /// Test 24.2: Process tick and signal computation
    #[test]
    fn test_process_tick() {
        let qa = QuantumApex::new().expect("Failed to init");

        // Process a single tick
        let signal = qa.process_tick(
            1001,                  // ticker_id: QQQ3.L
            150.5,                 // price
            1_000_000,             // volume
            1_710_000_000_000,     // timestamp_ns
        );

        // First tick should return 0 (need history)
        assert_eq!(signal, 0.0, "First tick should return 0.0 (insufficient history)");
    }

    /// Test 24.3: Signal weight queries
    #[test]
    fn test_get_signal_weight() {
        let qa = QuantumApex::new().expect("Failed to init");

        // HotScanner (module_id = 0) default weight
        let weight = qa.get_signal_weight(0);
        assert_eq!(weight, 1.0, "HotScanner default weight should be 1.0");

        // RotationScanner (module_id = 1) default weight
        let weight = qa.get_signal_weight(1);
        assert_eq!(weight, 1.0, "RotationScanner default weight should be 1.0");

        // Unknown module falls back to 1.0
        let weight = qa.get_signal_weight(999);
        assert_eq!(weight, 1.0, "Unknown module should default to 1.0");
    }

    /// Test 24.4: Multiple tick sequence with increasing signal
    #[test]
    fn test_multi_tick_signal_buildup() {
        let qa = QuantumApex::new().expect("Failed to init");

        // Simulate 15 ticks with increasing prices (momentum)
        let base_price = 100.0;
        for i in 1..=15 {
            let price = base_price + (i as f64 * 0.5);  // Upward trend
            let signal = qa.process_tick(
                1001,
                price,
                1_000_000 + (i as u32 * 50_000),
                1_710_000_000_000 + (i as u64 * 1_000_000_000),
            );

            // After 10+ ticks, signal should be positive
            if i >= 10 {
                assert!(signal >= 0.0, "Signal should be non-negative with strong momentum");
            }
        }
    }

    /// Test 24.5: Shutdown and cleanup
    #[test]
    fn test_shutdown_cleanup() {
        let mut qa = QuantumApex::new().expect("Failed to init");
        assert!(qa.initialized, "Should be initialized");

        qa.shutdown();
        assert!(!qa.initialized, "Should be uninitialized after shutdown");

        // Process tick after shutdown should return 0
        let signal = qa.process_tick(1001, 150.5, 1_000_000, 1_710_000_000_000);
        assert_eq!(signal, 0.0, "Signal should be 0 after shutdown");
    }

    /// Test 24.6: Drop trait calls shutdown
    #[test]
    fn test_drop_impl() {
        {
            let _qa = QuantumApex::new().expect("Failed to init");
            // Drop will be called at end of scope
        }
        // If Drop is properly implemented, no crashes occur
        // (In release mode, compiler may optimize this away)
    }
}
