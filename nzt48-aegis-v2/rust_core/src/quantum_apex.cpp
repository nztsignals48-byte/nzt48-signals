//! Quantum Apex C++ engine — compiled as static lib
#include <cstdint>
#include <cmath>
#include <unordered_map>
#include <deque>
#include <algorithm>

struct TickData {
    uint32_t ticker_id;
    double price;
    uint32_t volume;
    uint64_t timestamp_ns;
};

static std::unordered_map<uint32_t, std::deque<TickData>> tick_buffer;
static std::unordered_map<int, double> signal_weights;

extern "C" {
    const char* qa_init() {
        // Initialize signal weights to baseline (equal weighting)
        signal_weights[0] = 1.0;   // HotScanner
        signal_weights[1] = 1.0;   // RotationScanner
        signal_weights[2] = 1.0;   // VanguardSniper
        signal_weights[3] = 1.0;   // MeanReversion
        signal_weights[4] = 1.0;   // Correlation
        return "Quantum Apex initialized";
    }

    double qa_process_tick(
        uint32_t ticker_id,
        double price,
        uint32_t volume,
        uint32_t timestamp_ns
    ) {
        TickData tick{ticker_id, price, volume, static_cast<uint64_t>(timestamp_ns)};
        auto& buffer = tick_buffer[ticker_id];
        buffer.push_back(tick);

        // Keep only last 60 ticks (1 minute of data)
        if (buffer.size() > 60) {
            buffer.pop_front();
        }

        // DQN: Compute signal strength based on recent ticks
        if (buffer.size() < 10) return 0.0;  // Need minimum history

        // Calculate volatility (measure of opportunity)
        double sum_sq_returns = 0.0;
        for (size_t i = 1; i < buffer.size(); i++) {
            double ret = std::log(buffer[i].price / buffer[i - 1].price);
            sum_sq_returns += ret * ret;
        }
        double volatility = std::sqrt(sum_sq_returns / buffer.size());

        // Calculate volume trend
        double avg_volume = 0.0;
        for (const auto& t : buffer) {
            avg_volume += t.volume;
        }
        avg_volume /= buffer.size();
        double volume_ratio = volume / (avg_volume + 1e-9);

        // DQN signal: volatility × volume_ratio × momentum
        double momentum = (buffer.back().price - buffer.front().price) / buffer.front().price;
        double dqn_signal = volatility * volume_ratio * std::abs(momentum);

        return dqn_signal;  // 0.0 = no signal, > 0.1 = strong signal
    }

    double qa_get_signal_weight(int module_id) {
        auto it = signal_weights.find(module_id);
        if (it != signal_weights.end()) {
            return it->second;
        }
        return 1.0;  // Default if module not found
    }

    int qa_shutdown() {
        tick_buffer.clear();
        signal_weights.clear();
        return 0;  // Success
    }

    void qa_free(char* ptr) {
        // C++ manages memory internally
        (void)ptr;  // Suppress unused warning
    }
}
