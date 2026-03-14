"""
DQN Execution Agent Implementation
Learns optimal execution timing via deep reinforcement learning
"""

import logging
import numpy as np
from typing import Tuple, Dict, Optional
from dataclasses import dataclass
import json

logger = logging.getLogger("nzt48.dqn_agent")

@dataclass
class ExecutionState:
    """State representation for DQN decision making"""
    position_pnl_pct: float         # Current unrealized P&L as % of entry
    position_duration_seconds: int  # How long position held
    current_volatility: float       # Current ATR or IV
    baseline_volatility: float      # 20-day average volatility
    market_momentum: float          # -1.0 (bearish) to +1.0 (bullish)
    order_flow_imbalance: float     # OFI signal strength
    regime: str                     # 'TREND', 'MEAN_REVERSION', 'CHOPPY'
    time_to_market_close: float     # Minutes until session end
    recent_volatility_jump: bool    # Whether volatility just spiked
    chandelier_rung: int            # Current exit rung (0-5)
    
    def to_features(self) -> np.ndarray:
        """Convert state to neural network input features"""
        regime_encode = {"TREND": 0, "MEAN_REVERSION": 1, "CHOPPY": 2}
        features = np.array([
            self.position_pnl_pct,
            min(self.position_duration_seconds / 3600, 8),  # Cap at 8 hours
            self.current_volatility,
            self.baseline_volatility,
            self.market_momentum,
            self.order_flow_imbalance,
            regime_encode.get(self.regime, 1),
            self.time_to_market_close / 60,  # Normalize to hours
            float(self.recent_volatility_jump),
            self.chandelier_rung / 5.0
        ], dtype=np.float32)
        return features

class DQNExecutionAgent:
    """
    Deep Q-Network for execution decisions.
    Learns optimal scaling, exiting, and risk management policies.
    
    21 Action Space:
    - HOLD (0): Do nothing
    - SCALE_UP (1-3): Add 10%, 25%, 50% position
    - SCALE_DOWN (4-6): Remove 10%, 25%, 50% position
    - PARTIAL_EXIT (7-9): Trim 25%, 50%, 75% position
    - FULL_EXIT (10-11): Market or limit order full exit
    - TRAILING_STOP (12-13): Tighten or relax trailing stop
    - TAKE_PROFIT (14-15): Lock profits at breakeven or at +2%
    - ADVANCED (16-20): Add, hedge, reverse, flatten, hold passive
    """
    
    ACTIONS = {
        0: "HOLD",
        1: "SCALE_UP_10", 2: "SCALE_UP_25", 3: "SCALE_UP_50",
        4: "SCALE_DOWN_10", 5: "SCALE_DOWN_25", 6: "SCALE_DOWN_50",
        7: "PARTIAL_EXIT_25", 8: "PARTIAL_EXIT_50", 9: "PARTIAL_EXIT_75",
        10: "FULL_EXIT_MARKET", 11: "FULL_EXIT_LIMIT",
        12: "TRAILING_STOP_TIGHTEN", 13: "TRAILING_STOP_RELAX",
        14: "TAKE_PROFIT_LOCK_2PCT", 15: "LOCK_BREAKEVEN",
        16: "ADD_POSITION", 17: "HEDGE_50PCT", 18: "REVERSE_SHORT",
        19: "EMERGENCY_FLATTEN", 20: "HOLD_PASSIVE"
    }
    
    ACTION_SCALES = {
        1: 0.10, 2: 0.25, 3: 0.50,  # Scale up amounts
        4: 0.10, 5: 0.25, 6: 0.50,  # Scale down amounts
        7: 0.25, 8: 0.50, 9: 0.75,  # Exit percentages
    }
    
    def __init__(self, learning_rate: float = 0.001, gamma: float = 0.99, 
                 epsilon: float = 0.1, epsilon_decay: float = 0.995):
        self.lr = learning_rate
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = 0.01
        
        # Simple Q-table (sparse: will use neural network in production)
        self.q_table = {}
        self.visit_counts = {}
        self.learning_enabled = True
        
        logger.info(f"DQN Execution Agent initialized: "
                   f"lr={learning_rate}, gamma={gamma}, epsilon={epsilon}")
    
    def choose_action(self, state: ExecutionState, training: bool = False) -> Tuple[int, str]:
        """
        Choose next action based on current state.
        
        Args:
            state: Current ExecutionState
            training: If True, use epsilon-greedy exploration; else use greedy
            
        Returns:
            Tuple of (action_id, action_name)
        """
        # Epsilon-greedy exploration vs exploitation
        if training and np.random.random() < self.epsilon:
            # Explore: random action
            action = np.random.randint(0, 21)
            logger.debug(f"Exploration: random action {action}")
        else:
            # Exploit: best known action for this state
            action = self._get_best_action(state)
            logger.debug(f"Exploitation: best action {action} for state")
        
        return action, self.ACTIONS[action]
    
    def _get_best_action(self, state: ExecutionState) -> int:
        """
        Get best action for state using heuristics.
        In production, this will use a trained neural network.
        """
        # Heuristic-based policy (fallback until neural net trained)
        
        # Emergency exit if losses are severe
        if state.position_pnl_pct < -5.0:
            logger.info(f"Emergency exit: PnL={state.position_pnl_pct:.1f}%")
            return 19  # EMERGENCY_FLATTEN
        
        # Lock profits if > 5% gain and volatility rising
        if state.position_pnl_pct > 5.0 and state.current_volatility > state.baseline_volatility * 1.2:
            logger.info(f"Lock profits: PnL={state.position_pnl_pct:.1f}%, vol_spike")
            return 10  # FULL_EXIT_MARKET
        
        # Partial exit on good profit with regime shift
        if state.position_pnl_pct > 2.0 and state.regime == "CHOPPY":
            logger.info(f"Partial exit: PnL={state.position_pnl_pct:.1f}%, choppy regime")
            return 8  # PARTIAL_EXIT_50
        
        # Tighten trailing stop if momentum strong in position direction
        if state.position_pnl_pct > 1.0 and state.market_momentum > 0.5:
            logger.info(f"Tighten stop: strong momentum {state.market_momentum:.2f}")
            return 12  # TRAILING_STOP_TIGHTEN
        
        # Scale up on strong momentum and low volatility
        if state.position_pnl_pct > 0.5 and state.market_momentum > 0.7 and state.current_volatility < state.baseline_volatility:
            logger.info(f"Scale up: momentum={state.market_momentum:.2f}, vol_low")
            return 2  # SCALE_UP_25
        
        # Relax stop if volatility normalize
        if state.current_volatility < state.baseline_volatility * 0.9:
            logger.info(f"Relax stop: vol normalized {state.current_volatility:.2f}")
            return 13  # TRAILING_STOP_RELAX
        
        # Default: hold
        return 0  # HOLD
    
    def learn(self, state: ExecutionState, action: int, reward: float, 
              next_state: ExecutionState, done: bool = False):
        """
        Update Q-value for (state, action) pair using Q-learning.
        In production, this trains a neural network via backprop.
        
        Q(s,a) ← Q(s,a) + α[r + γ max_a' Q(s',a') - Q(s,a)]
        """
        if not self.learning_enabled:
            return
        
        state_key = self._state_to_key(state)
        action_key = (state_key, action)
        
        # Initialize Q-value if new
        if action_key not in self.q_table:
            self.q_table[action_key] = 0.0
        
        # Get best Q-value for next state
        if done:
            target = reward
        else:
            next_state_key = self._state_to_key(next_state)
            best_next_q = max(
                [self.q_table.get((next_state_key, a), 0.0) for a in range(21)],
                default=0.0
            )
            target = reward + self.gamma * best_next_q
        
        # Q-learning update
        old_q = self.q_table[action_key]
        self.q_table[action_key] = old_q + self.lr * (target - old_q)
        
        # Track visitation count
        self.visit_counts[action_key] = self.visit_counts.get(action_key, 0) + 1
        
        if reward > 1.0 or reward < -1.0:
            logger.info(f"Q-update: action={action}, reward={reward:.2f}, "
                       f"Q={self.q_table[action_key]:.4f}")
        
        # Decay exploration
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay
    
    def _state_to_key(self, state: ExecutionState) -> str:
        """Convert state to hashable key for Q-table lookup"""
        # Discretize continuous values
        pnl_bucket = int(state.position_pnl_pct / 0.5) * 0.5
        vol_bucket = int(state.current_volatility / 0.1) * 0.1
        momentum_bucket = int(state.market_momentum * 10) / 10
        
        return f"pnl={pnl_bucket:.1f}|vol={vol_bucket:.1f}|mom={momentum_bucket:.1f}|regime={state.regime}"
    
    def get_statistics(self) -> Dict:
        """Return training statistics"""
        if not self.q_table:
            return {"q_table_size": 0, "learning_enabled": self.learning_enabled}
        
        q_values = list(self.q_table.values())
        return {
            "q_table_size": len(self.q_table),
            "avg_q_value": np.mean(q_values),
            "max_q_value": np.max(q_values),
            "min_q_value": np.min(q_values),
            "epsilon": self.epsilon,
            "learning_enabled": self.learning_enabled,
            "most_visited_action": self._get_most_visited_action()
        }
    
    def _get_most_visited_action(self) -> str:
        """Find most visited state-action pair"""
        if not self.visit_counts:
            return "None"
        max_key = max(self.visit_counts, key=self.visit_counts.get)
        state_key, action = max_key
        return f"{self.ACTIONS[action]}({self.visit_counts[max_key]}x)"
    
    def save_policy(self, filepath: str):
        """Save Q-table to JSON"""
        data = {
            "q_table": {str(k): v for k, v in self.q_table.items()},
            "visit_counts": {str(k): v for k, v in self.visit_counts.items()},
            "epsilon": self.epsilon,
            "learning_rate": self.lr,
            "gamma": self.gamma
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Policy saved to {filepath}")
    
    def load_policy(self, filepath: str):
        """Load Q-table from JSON"""
        with open(filepath, 'r') as f:
            data = json.load(f)
        self.q_table = {eval(k): v for k, v in data.get("q_table", {}).items()}
        self.visit_counts = {eval(k): v for k, v in data.get("visit_counts", {}).items()}
        self.epsilon = data.get("epsilon", 0.1)
        logger.info(f"Policy loaded from {filepath} (Q-table size: {len(self.q_table)})")
