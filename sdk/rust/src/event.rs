//! Canonical Event definitions for MDRAP Rust SDK.

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventType {
    Unknown = 0,
    Trade = 1,
    Quote = 2,
    Bbo = 3,
    Book = 4,
    Heartbeat = 5,
}

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QualityStatus {
    Valid = 0,
    Suspicious = 1,
    Invalid = 2,
}

#[repr(C, packed)]
#[derive(Debug, Clone, Copy)]
pub struct SbeMarketEvent {
    pub instrument: [u8; 16],
    pub sequence: u64,
    pub exchange_ts_ns: i64,
    pub price: f64,
    pub quantity: f64,
    pub event_type: u32,
    pub quality_flag: u32,
    pub _padding: [u8; 8],
}

impl SbeMarketEvent {
    pub const SIZE: usize = 64;
}

#[derive(Debug, Clone, PartialEq)]
pub struct MarketEvent {
    pub instrument: String,
    pub sequence: u64,
    pub exchange_ts_ns: i64,
    pub price: f64,
    pub quantity: f64,
    pub event_type: EventType,
    pub quality_status: QualityStatus,
}

impl MarketEvent {
    pub fn from_sbe(raw: &SbeMarketEvent) -> Self {
        let end = raw.instrument.iter().position(|&c| c == 0).unwrap_or(16);
        let inst = String::from_utf8_lossy(&raw.instrument[..end]).to_string();

        let ev_type = match raw.event_type {
            1 => EventType::Trade,
            2 => EventType::Quote,
            3 => EventType::Bbo,
            4 => EventType::Book,
            5 => EventType::Heartbeat,
            _ => EventType::Unknown,
        };

        let q_status = match raw.quality_flag {
            0 => QualityStatus::Valid,
            1 => QualityStatus::Suspicious,
            _ => QualityStatus::Invalid,
        };

        MarketEvent {
            instrument: inst,
            sequence: raw.sequence,
            exchange_ts_ns: raw.exchange_ts_ns,
            price: raw.price,
            quantity: raw.quantity,
            event_type: ev_type,
            quality_status: q_status,
        }
    }
}
