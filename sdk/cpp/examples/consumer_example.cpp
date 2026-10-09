#include "mdrap/consumer.hpp"
#include <iostream>
#include <cassert>
#include <cstring>

int main() {
    std::cout << "[MDRAP C++ SDK] Initializing consumer example..." << std::endl;

    mdrap::ConsumerConfig cfg;
    cfg.endpoint = "127.0.0.1:8901";
    cfg.track_sequence_gaps = true;

    mdrap::MdrapConsumer consumer(cfg);
    assert(consumer.connect() == true);
    assert(consumer.is_connected() == true);

    // Create synthetic SBE frames
    mdrap::SbeMarketEvent frame1{};
    std::strncpy(frame1.instrument, "AAPL", sizeof(frame1.instrument) - 1);
    frame1.sequence = 100;
    frame1.exchange_ts_ns = 1728475200000000000LL;
    frame1.price = 150.25;
    frame1.quantity = 10.0;
    frame1.event_type = static_cast<uint32_t>(mdrap::EventType::TRADE);
    frame1.quality_flag = static_cast<uint32_t>(mdrap::QualityStatus::VALID);

    mdrap::MarketEvent evt1;
    assert(consumer.feed_sbe_frame(frame1, evt1) == true);
    assert(evt1.instrument == "AAPL");
    assert(evt1.sequence == 100);
    assert(evt1.price == 150.25);
    assert(evt1.event_type == mdrap::EventType::TRADE);

    // Frame with gap: seq = 104 (expected 101 -> gap of 3: 101, 102, 103)
    mdrap::SbeMarketEvent frame2{};
    std::strncpy(frame2.instrument, "AAPL", sizeof(frame2.instrument) - 1);
    frame2.sequence = 104;
    frame2.exchange_ts_ns = 1728475200001000000LL;
    frame2.price = 150.50;
    frame2.quantity = 25.0;
    frame2.event_type = static_cast<uint32_t>(mdrap::EventType::TRADE);
    frame2.quality_flag = static_cast<uint32_t>(mdrap::QualityStatus::VALID);

    mdrap::MarketEvent evt2;
    assert(consumer.feed_sbe_frame(frame2, evt2) == true);
    assert(evt2.sequence == 104);

    mdrap::ConsumerStats stats = consumer.get_stats();
    assert(stats.events_consumed == 2);
    assert(stats.gaps_detected == 1);
    assert(stats.missing_events == 3);
    assert(stats.last_sequence == 104);

    consumer.disconnect();
    assert(consumer.is_connected() == false);

    std::cout << "[MDRAP C++ SDK] All assertions passed successfully! Consumed: "
              << stats.events_consumed << ", Gaps: " << stats.gaps_detected
              << ", Missing: " << stats.missing_events << std::endl;
    return 0;
}
