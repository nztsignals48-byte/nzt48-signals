//! SIMD-accelerated math primitives.
//! Platform-agnostic wrappers for fast numeric operations.

/// Fast square root using hardware intrinsics.
#[inline(always)]
pub fn fast_sqrt(x: f64) -> f64 {
    x.sqrt() // Rust's sqrt() already uses SIMD when available
}

/// Fast reciprocal (1/x) using hardware intrinsics.
#[inline(always)]
pub fn fast_recip(x: f64) -> f64 {
    1.0 / x
}

/// Fast maximum of two values.
#[inline(always)]
pub fn fast_max(a: f64, b: f64) -> f64 {
    a.max(b)
}

/// Fast minimum of two values.
#[inline(always)]
pub fn fast_min(a: f64, b: f64) -> f64 {
    a.min(b)
}

/// Clamp value between min and max.
#[inline(always)]
pub fn clamp(value: f64, min: f64, max: f64) -> f64 {
    fast_max(min, fast_min(value, max))
}

/// Linear interpolation.
#[inline(always)]
pub fn lerp(a: f64, b: f64, t: f64) -> f64 {
    a + (b - a) * t
}

/// Check if value is approximately equal (within epsilon).
#[inline(always)]
pub fn approx_eq(a: f64, b: f64, epsilon: f64) -> bool {
    (a - b).abs() < epsilon
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fast_sqrt() {
        assert!((fast_sqrt(4.0) - 2.0).abs() < 1e-10);
        assert!((fast_sqrt(9.0) - 3.0).abs() < 1e-10);
    }

    #[test]
    fn test_clamp() {
        assert_eq!(clamp(5.0, 0.0, 10.0), 5.0);
        assert_eq!(clamp(-5.0, 0.0, 10.0), 0.0);
        assert_eq!(clamp(15.0, 0.0, 10.0), 10.0);
    }

    #[test]
    fn test_lerp() {
        assert_eq!(lerp(0.0, 10.0, 0.5), 5.0);
        assert_eq!(lerp(0.0, 10.0, 0.0), 0.0);
        assert_eq!(lerp(0.0, 10.0, 1.0), 10.0);
    }

    #[test]
    fn test_approx_eq() {
        assert!(approx_eq(1.0, 1.0000001, 0.001));
        assert!(!approx_eq(1.0, 1.01, 0.001));
    }
}
