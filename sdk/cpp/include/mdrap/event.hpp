#pragma once

#include <cstdint>
#include <string>
#include <cstring>

namespace mdrap {

enum class EventType : uint32_t {
    UNKNOWN = 0,
    TRADE = 1,
    QUOTE = 2,
    BBO = 3,
    BOOK = 4,
    HEARTBEAT = 5
};

enum class QualityStatus : uint32_t {
    VALID = 0,
    SUSPICIOUS = 1,
    INVALID = 2
};

#pragma pack(push, 1)
struct SbeMarketEvent {
    char instrument[16];
    uint64_t sequence;
    int64_t exchange_ts_ns;
    double price;
    double quantity;
    uint32_t event_type;
    uint32_t quality_flag;
    uint8_t padding[8];
};
#pragma pack(pop)

static_assert(sizeof(SbeMarketEvent) == 64, "SbeMarketEvent must be exactly 64 bytes aligned");

struct MarketEvent {
    std::string instrument;
    uint64_t sequence{0};
    int64_t exchange_ts_ns{0};
    double price{0.0};
    double quantity{0.0};
    EventType event_type{EventType::UNKNOWN};
    QualityStatus quality_status{QualityStatus::VALID};

    static MarketEvent from_sbe(const SbeMarketEvent& sbe) {
        MarketEvent evt;
        char buf[17] = {0};
        std::memcpy(buf, sbe.instrument, 16);
        evt.instrument = std::string(buf);
        evt.sequence = sbe.sequence;
        evt.exchange_ts_ns = sbe.exchange_ts_ns;
        evt.price = sbe.price;
        evt.quantity = sbe.quantity;
        evt.event_type = static_cast<EventType>(sbe.event_type);
        evt.quality_status = static_cast<QualityStatus>(sbe.quality_flag);
        return evt;
    }
};

} // namespace mdrap
