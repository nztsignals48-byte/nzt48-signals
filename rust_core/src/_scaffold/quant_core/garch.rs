// GARCH(1,1) — Phase 2B.
pub struct Garch { pub vol: f64 }
impl Garch { pub fn new() -> Self { Self { vol: 0.0 } } pub fn step(&mut self, _r: f64) {} }
