#pragma once

#include "event.hpp"
#include <functional>
#include <string>
#include <vector>
#include <optional>
#include <atomic>

namespace mdrap {

struct ConsumerConfig {
    std::string endpoint{"127.0.0.1:8901"};
    std::string shm_name{""};
    std::string api_key{""};
    uint32_t poll_timeout_ms{100};
    bool track_sequence_gaps{true};
};

struct ConsumerStats {
    uint64_t events_consumed{0};
    uint64_t gaps_detected{0};
    uint64_t missing_events{0};
    uint64_t last_sequence{0};
};

using EventCallback = std::function<void(const MarketEvent&)>;

class MdrapConsumer {
public:
    explicit MdrapConsumer(const ConsumerConfig& config);
    ~MdrapConsumer();

    // Move semantics (RAII), copy disabled
    MdrapConsumer(const MdrapConsumer&) = delete;
    MdrapConsumer& operator=(const MdrapConsumer&) = delete;
    MdrapConsumer(MdrapConsumer&& other) noexcept;
    MdrapConsumer& operator=(MdrapConsumer&& other) noexcept;

    bool connect();
    void disconnect();
    bool is_connected() const;

    std::optional<MarketEvent> poll();
    void subscribe(const std::string& symbol);
    ConsumerStats get_stats() const;

    // Direct buffer decoding for zero-copy IPC / stream parsing
    bool feed_sbe_frame(const SbeMarketEvent& frame, MarketEvent& out_event);

private:
    void audit_sequence(uint64_t seq);

    ConsumerConfig config_;
    std::atomic<bool> connected_{false};
    ConsumerStats stats_;
    uint64_t expected_seq_{0};
    bool has_expected_seq_{false};
    std::vector<std::string> subscriptions_;
};

} // namespace mdrap
