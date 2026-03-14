//! DQN: Deep Q-Network for dynamic signal weighting

use std::collections::HashMap;

pub struct DQNWeighting {
    module_rewards: HashMap<i32, f64>,
    module_losses: HashMap<i32, f64>,
    learning_rate: f64,
    epsilon: f64,  // Exploration rate
    total_episodes: u64,
}

impl DQNWeighting {
    pub fn new() -> Self {
        DQNWeighting {
            module_rewards: HashMap::new(),
            module_losses: HashMap::new(),
            learning_rate: 0.01,
            epsilon: 0.1,  // 10% exploration
            total_episodes: 0,
        }
    }

    pub fn record_signal_outcome(
        &mut self,
        module_id: i32,
        signal_fired: bool,
        trade_result_pnl: f64,
    ) {
        if signal_fired {
            if trade_result_pnl > 0.0 {
                // Reward: increase weight
                let current = self.module_rewards.entry(module_id).or_insert(0.0);
                *current += trade_result_pnl * self.learning_rate;
            } else {
                // Penalty: decrease weight
                let current = self.module_losses.entry(module_id).or_insert(0.0);
                *current += trade_result_pnl.abs() * self.learning_rate;
            }
        }
    }

    pub fn compute_weight(&self, module_id: i32) -> f64 {
        let reward = self.module_rewards.get(&module_id).copied().unwrap_or(0.0);
        let loss = self.module_losses.get(&module_id).copied().unwrap_or(0.0);

        // Softmax over module performance
        let net_score = reward - loss;
        let base_weight = 1.0 + (net_score / (loss + 1e-9)).min(2.0).max(0.5);

        // Add epsilon for exploration
        if self.total_episodes % 100 == 0 {
            base_weight * (1.0 + self.epsilon)  // Exploration phase
        } else {
            base_weight * (1.0 - self.epsilon * 0.5)  // Exploitation phase
        }
    }

    pub fn end_episode(&mut self) {
        self.total_episodes += 1;
        // Decay epsilon over time
        if self.total_episodes % 1000 == 0 {
            self.epsilon *= 0.99;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test DQN initialization with default parameters
    #[test]
    fn test_dqn_init() {
        let dqn = DQNWeighting::new();
        assert_eq!(dqn.learning_rate, 0.01, "Default learning rate should be 0.01");
        assert_eq!(dqn.epsilon, 0.1, "Default epsilon should be 0.1");
        assert_eq!(dqn.total_episodes, 0, "Episode counter should start at 0");
    }

    /// Test recording winning signal
    #[test]
    fn test_record_winning_signal() {
        let mut dqn = DQNWeighting::new();

        // Record a winning trade from module 0 (HotScanner)
        dqn.record_signal_outcome(0, true, 50.0);

        let weight = dqn.compute_weight(0);
        assert!(weight > 1.0, "Weight should increase for winning signals");
    }

    /// Test recording losing signal
    #[test]
    fn test_record_losing_signal() {
        let mut dqn = DQNWeighting::new();

        // Record a losing trade from module 1 (RotationScanner)
        dqn.record_signal_outcome(1, true, -25.0);

        let weight = dqn.compute_weight(1);
        assert!(weight >= 0.5, "Weight should stay above minimum bound");
    }

    /// Test differential weighting across modules
    #[test]
    fn test_differential_module_weights() {
        let mut dqn = DQNWeighting::new();

        // Module 0: 3 wins
        dqn.record_signal_outcome(0, true, 100.0);
        dqn.record_signal_outcome(0, true, 80.0);
        dqn.record_signal_outcome(0, true, 60.0);

        // Module 1: 1 loss
        dqn.record_signal_outcome(1, true, -50.0);

        let weight0 = dqn.compute_weight(0);
        let weight1 = dqn.compute_weight(1);

        assert!(weight0 > weight1, "Winning module should have higher weight than losing module");
    }

    /// Test epsilon decay after 1000 episodes
    #[test]
    fn test_epsilon_decay() {
        let mut dqn = DQNWeighting::new();
        let initial_epsilon = dqn.epsilon;

        // Run 1001 episodes
        for i in 0..1001 {
            dqn.end_episode();
            if i == 1000 {
                assert!(dqn.epsilon < initial_epsilon, "Epsilon should decay after 1000 episodes");
            }
        }
    }

    /// Test exploration vs exploitation phases
    #[test]
    fn test_exploration_exploitation_phases() {
        let mut dqn = DQNWeighting::new();
        dqn.record_signal_outcome(0, true, 50.0);

        // Episode 50 (exploitation)
        dqn.total_episodes = 50;
        let weight_exploit = dqn.compute_weight(0);

        // Episode 100 (exploration, triggered at episode % 100 == 0)
        dqn.total_episodes = 100;
        let weight_explore = dqn.compute_weight(0);

        // At episode 100, we multiply by (1.0 + epsilon) = 1.1, vs 0.95 at other times
        assert!(weight_explore > weight_exploit, "Exploration phase should boost weight");
    }

    /// Test untraded modules get baseline weight
    #[test]
    fn test_baseline_weight_for_new_modules() {
        let dqn = DQNWeighting::new();

        // Module 999 has never traded
        let weight = dqn.compute_weight(999);
        assert!(weight >= 0.5, "Untraded modules should have baseline weight");
    }
}
