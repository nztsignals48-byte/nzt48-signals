// Student-t Kalman filter — Phase 2B.
pub struct Kalman { pub state: f64, pub residual: f64 }
impl Kalman { pub fn new() -> Self { Self { state: 0.0, residual: 0.0 } } pub fn step(&mut self, _z: f64) {} }
