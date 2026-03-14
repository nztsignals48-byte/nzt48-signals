//! PyO3 FFI module definition.
//! Exposes all #[pyclass] types to Python as a native module.

use pyo3::prelude::*;

use crate::python_bridge::{BrainSignal, TickContext};
use crate::types::*;

/// The Python module exposed as `rust_core`.
#[pymodule]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Newtypes
    m.add_class::<TickerId>()?;

    // Enums
    m.add_class::<Direction>()?;
    m.add_class::<StrategyId>()?;
    m.add_class::<RiskRegime>()?;
    m.add_class::<BrokerAckStatus>()?;
    m.add_class::<ExitReason>()?;
    m.add_class::<ExitPriority>()?;
    m.add_class::<ExitOrderType>()?;
    m.add_class::<OrderState>()?;
    m.add_class::<PyVetoReason>()?;

    // Core structs
    m.add_class::<MarketTick>()?;
    m.add_class::<OrderIntent>()?;
    m.add_class::<RiskDecision>()?;
    m.add_class::<FillEvent>()?;
    m.add_class::<PositionState>()?;
    m.add_class::<BrokerAck>()?;
    m.add_class::<ExitSignal>()?;

    // RM-3: Native FFI types (zero-copy Rust ↔ Python)
    m.add_class::<TickContext>()?;
    m.add_class::<BrainSignal>()?;

    Ok(())
}
