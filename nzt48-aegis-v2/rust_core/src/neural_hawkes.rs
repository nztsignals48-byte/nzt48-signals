//! Neural Hawkes Processes for order flow prediction

use std::collections::VecDeque;

pub struct NeuralHawkesProcess {
    order_history: VecDeque<OrderEvent>,
    intensity_baseline: f64,
    decay_rate: f64,
    max_history: usize,
}

#[derive(Clone, Copy)]
pub struct OrderEvent {
    pub timestamp_ns: u64,
    pub side: Side,  // Buy or Sell
    pub volume: u32,
    pub impact: f64,
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Side {
    Buy,
    Sell,
}

impl NeuralHawkesProcess {
    pub fn new() -> Self {
        NeuralHawkesProcess {
            order_history: VecDeque::new(),
            intensity_baseline: 1.0,
            decay_rate: 0.5,  // Hawkes self-exciting decay
            max_history: 100,
        }
    }

    pub fn record_order(&mut self, event: OrderEvent) {
        self.order_history.push_back(event);
        if self.order_history.len() > self.max_history {
            self.order_history.pop_front();
        }
    }

    pub fn predict_next_order_side(&self, now_ns: u64) -> Option<(Side, f64)> {
        if self.order_history.is_empty() {
            return None;
        }

        // Compute Hawkes intensity: baseline + sum of exponential decay terms
        let mut buy_intensity = self.intensity_baseline;
        let mut sell_intensity = self.intensity_baseline;

        for event in &self.order_history {
            let time_since_event_s = (now_ns.saturating_sub(event.timestamp_ns)) as f64 / 1e9;
            if time_since_event_s < 0.0 {
                continue;  // Skip future events
            }

            let decay = (-self.decay_rate * time_since_event_s).exp();
            let contribution = event.volume as f64 * decay;

            match event.side {
                Side::Buy => buy_intensity += contribution,
                Side::Sell => sell_intensity += contribution,
            }
        }

        // Predict whichever side has higher intensity
        let side = if buy_intensity > sell_intensity {
            Side::Buy
        } else {
            Side::Sell
        };

        let confidence = (buy_intensity.max(sell_intensity) - self.intensity_baseline)
            / (buy_intensity.max(sell_intensity) + 1e-9);

        Some((side, confidence.min(1.0)))  // Clamp to [0, 1]
    }

    pub fn compute_order_clustering(&self, _now_ns: u64) -> f64 {
        // Measure how clustered orders are in time
        if self.order_history.len() < 2 {
            return 0.0;
        }

        let mut time_gaps = Vec::new();
        for i in 1..self.order_history.len() {
            let gap = self.order_history[i].timestamp_ns.saturating_sub(self.order_history[i - 1].timestamp_ns);
            time_gaps.push(gap as f64);
        }

        // Coefficient of variation (std / mean) — high = clustered, low = uniform
        let mean_gap = time_gaps.iter().sum::<f64>() / time_gaps.len() as f64;
        let variance = time_gaps
            .iter()
            .map(|g| (g - mean_gap).powi(2))
            .sum::<f64>()
            / time_gaps.len() as f64;

        (variance.sqrt() / (mean_gap + 1e-9)).min(10.0)  // Clamp to [0, 10]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test NeuralHawkesProcess initialization
    #[test]
    fn test_neural_hawkes_init() {
        let hawkes = NeuralHawkesProcess::new();
        assert_eq!(hawkes.intensity_baseline, 1.0, "Baseline intensity should be 1.0");
        assert_eq!(hawkes.decay_rate, 0.5, "Decay rate should be 0.5");
        assert_eq!(hawkes.max_history, 100, "Max history should be 100");
        assert!(hawkes.order_history.is_empty(), "History should be empty initially");
    }

    /// Test recording single order event
    #[test]
    fn test_record_single_order() {
        let mut hawkes = NeuralHawkesProcess::new();

        let event = OrderEvent {
            timestamp_ns: 1_000_000,
            side: Side::Buy,
            volume: 1000,
            impact: 0.5,
        };

        hawkes.record_order(event);
        assert_eq!(hawkes.order_history.len(), 1, "Should have 1 order in history");
    }

    /// Test max history enforcement (100 events)
    #[test]
    fn test_max_history_limit() {
        let mut hawkes = NeuralHawkesProcess::new();

        // Add 150 orders
        for i in 0..150 {
            let event = OrderEvent {
                timestamp_ns: i * 1_000_000,
                side: Side::Buy,
                volume: 1000,
                impact: 0.5,
            };
            hawkes.record_order(event);
        }

        // Should only keep last 100
        assert_eq!(hawkes.order_history.len(), 100, "History should be capped at 100");
    }

    /// Test prediction with no order history
    #[test]
    fn test_predict_empty_history() {
        let hawkes = NeuralHawkesProcess::new();
        let prediction = hawkes.predict_next_order_side(1_000_000);
        assert!(prediction.is_none(), "No prediction with empty history");
    }

    /// Test prediction with buy-dominant history
    #[test]
    fn test_predict_buy_dominant() {
        let mut hawkes = NeuralHawkesProcess::new();

        // Add 5 buy orders
        for i in 0..5 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: i * 1_000_000_000,
                side: Side::Buy,
                volume: 1000,
                impact: 1.0,
            });
        }

        // Add 1 sell order
        hawkes.record_order(OrderEvent {
            timestamp_ns: 5_000_000_000,
            side: Side::Sell,
            volume: 500,
            impact: 0.5,
        });

        let prediction = hawkes.predict_next_order_side(5_100_000_000);
        assert!(prediction.is_some(), "Should make prediction with history");

        let (side, confidence) = prediction.unwrap();
        assert_eq!(side, Side::Buy, "Should predict Buy (dominant side)");
        assert!(confidence > 0.0 && confidence <= 1.0, "Confidence should be in [0,1]");
    }

    /// Test prediction with sell-dominant history
    #[test]
    fn test_predict_sell_dominant() {
        let mut hawkes = NeuralHawkesProcess::new();

        // Add 7 sell orders
        for i in 0..7 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: i * 1_000_000_000,
                side: Side::Sell,
                volume: 2000,
                impact: 1.0,
            });
        }

        // Add 2 buy orders
        for i in 7..9 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: i as u64 * 1_000_000_000,
                side: Side::Buy,
                volume: 500,
                impact: 0.5,
            });
        }

        let prediction = hawkes.predict_next_order_side(9_100_000_000);
        let (side, _confidence) = prediction.unwrap();
        assert_eq!(side, Side::Sell, "Should predict Sell (dominant side)");
    }

    /// Test clustering coefficient with uniform orders
    #[test]
    fn test_clustering_uniform_orders() {
        let mut hawkes = NeuralHawkesProcess::new();

        // Add 10 evenly-spaced orders
        for i in 0..10 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: i as u64 * 1_000_000_000,  // 1 second apart
                side: Side::Buy,
                volume: 1000,
                impact: 0.5,
            });
        }

        let clustering = hawkes.compute_order_clustering(10_000_000_000);
        assert!(clustering < 1.0, "Uniform orders should have low clustering");
    }

    /// Test clustering coefficient with clustered orders
    #[test]
    fn test_clustering_clustered_orders() {
        let mut hawkes = NeuralHawkesProcess::new();

        // Add 5 closely-spaced orders
        for i in 0..5 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: i as u64 * 10_000_000,  // 10ms apart
                side: Side::Buy,
                volume: 1000,
                impact: 0.5,
            });
        }

        // Add 5 more far away
        for i in 5..10 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: 100_000_000_000 + i as u64 * 10_000_000,
                side: Side::Sell,
                volume: 1000,
                impact: 0.5,
            });
        }

        let clustering = hawkes.compute_order_clustering(100_100_000_000);
        assert!(clustering > 0.0, "Mixed clustering should be measurable");
    }

    /// Test decay effect on recent vs old orders
    #[test]
    fn test_decay_effect() {
        let mut hawkes = NeuralHawkesProcess::new();

        // Old buy orders
        for i in 0..3 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: i as u64 * 1_000_000_000,
                side: Side::Buy,
                volume: 1000,
                impact: 1.0,
            });
        }

        // Recent sell orders
        for i in 0..5 {
            hawkes.record_order(OrderEvent {
                timestamp_ns: 5_000_000_000 + i as u64 * 100_000_000,
                side: Side::Sell,
                volume: 1000,
                impact: 1.0,
            });
        }

        // At recent time, recent orders should dominate
        let prediction = hawkes.predict_next_order_side(5_600_000_000);
        let (side, _confidence) = prediction.unwrap();
        // Recent Sell orders should dominate due to decay
        assert_eq!(side, Side::Sell, "Recent orders should dominate prediction");
    }
}
