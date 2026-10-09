#include "mdrap/consumer.hpp"
#include <iostream>

namespace mdrap {

MdrapConsumer::MdrapConsumer(const ConsumerConfig& config)
    : config_(config), connected_(false) {}

MdrapConsumer::~MdrapConsumer() {
    disconnect();
}

MdrapConsumer::MdrapConsumer(MdrapConsumer&& other) noexcept
    : config_(std::move(other.config_)),
      connected_(other.connected_.load()),
      stats_(other.stats_),
      expected_seq_(other.expected_seq_),
      has_expected_seq_(other.has_expected_seq_),
      subscriptions_(std::move(other.subscriptions_)) {
    other.connected_ = false;
}

MdrapConsumer& MdrapConsumer::operator=(MdrapConsumer&& other) noexcept {
    if (this != &other) {
        disconnect();
        config_ = std::move(other.config_);
        connected_ = other.connected_.load();
        stats_ = other.stats_;
        expected_seq_ = other.expected_seq_;
        has_expected_seq_ = other.has_expected_seq_;
        subscriptions_ = std::move(other.subscriptions_);
        other.connected_ = false;
    }
    return *this;
}

bool MdrapConsumer::connect() {
    if (connected_) return true;
    // In production, attaches to SHM or opens TCP socket
    connected_ = true;
    return true;
}

void MdrapConsumer::disconnect() {
    if (!connected_) return;
    connected_ = false;
}

bool MdrapConsumer::is_connected() const {
    return connected_;
}

void MdrapConsumer::subscribe(const std::string& symbol) {
    subscriptions_.push_back(symbol);
}

ConsumerStats MdrapConsumer::get_stats() const {
    return stats_;
}

void MdrapConsumer::audit_sequence(uint64_t seq) {
    if (has_expected_seq_) {
        if (seq > expected_seq_) {
            stats_.gaps_detected++;
            stats_.missing_events += (seq - expected_seq_);
            expected_seq_ = seq + 1;
        } else if (seq == expected_seq_) {
            expected_seq_ = seq + 1;
        }
    } else {
        expected_seq_ = seq + 1;
        has_expected_seq_ = true;
    }
    stats_.last_sequence = seq;
}

bool MdrapConsumer::feed_sbe_frame(const SbeMarketEvent& frame, MarketEvent& out_event) {
    if (!connected_) return false;

    if (config_.track_sequence_gaps) {
        audit_sequence(frame.sequence);
    }

    out_event = MarketEvent::from_sbe(frame);
    stats_.events_consumed++;
    return true;
}

std::optional<MarketEvent> MdrapConsumer::poll() {
    if (!connected_) return std::nullopt;
    // Standalone poll interface for stream
    return std::nullopt;
}

} // namespace mdrap
