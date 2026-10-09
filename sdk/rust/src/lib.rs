//! MDRAP Rust Consumer SDK.
//!
//! Provides institutional market data stream consumption with SBE decoding,
//! sequence gap detection, and zero-allocation frame processing.

pub mod event;
pub mod consumer;

pub use event::{EventType, MarketEvent, QualityStatus, SbeMarketEvent};
pub use consumer::{ConsumerConfig, ConsumerStats, MdrapConsumer};
