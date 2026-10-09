//! Consumer implementation for MDRAP Rust SDK.

use crate::event::{MarketEvent, SbeMarketEvent};

#[derive(Debug, Clone)]
pub struct ConsumerConfig {
    pub endpoint: String,
    pub api_key: String,
    pub poll_timeout_ms: u32,
    pub track_sequence_gaps: bool,
}

impl Default for ConsumerConfig {
    fn default() -> Self {
        Self {
            endpoint: "127.0.0.1:8901".to_string(),
            api_key: String::new(),
            poll_timeout_ms: 100,
            track_sequence_gaps: true,
        }
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub struct ConsumerStats {
    pub events_consumed: u64,
    pub gaps_detected: u64,
    pub missing_events: u64,
    pub last_sequence: u64,
}

pub struct MdrapConsumer {
    config: ConsumerConfig,
    connected: bool,
    stats: ConsumerStats,
    expected_sequence: Option<u64>,
}

impl MdrapConsumer {
    pub fn new(config: ConsumerConfig) -> Self {
        Self {
            config,
            connected: false,
            stats: ConsumerStats::default(),
            expected_sequence: None,
        }
    }

    pub fn connect(&mut self) -> Result<(), &'static str> {
        self.connected = true;
        Ok(())
    }

    pub fn disconnect(&mut self) {
        self.connected = false;
    }

    pub fn is_connected(&self) -> bool {
        self.connected
    }

    pub fn feed_sbe_frame(&mut self, frame: &SbeMarketEvent) -> Result<MarketEvent, &'static str> {
        if !self.connected {
            return Err("Consumer is not connected");
        }

        if self.config.track_sequence_gaps {
            self.audit_sequence(frame.sequence);
        }

        self.stats.events_consumed += 1;
        self.stats.last_sequence = frame.sequence;

        Ok(MarketEvent::from_sbe(frame))
    }

    fn audit_sequence(&mut self, seq: u64) {
        if let Some(expected) = self.expected_sequence {
            if seq > expected {
                let missing = seq - expected;
                self.stats.gaps_detected += 1;
                self.stats.missing_events += missing;
                self.expected_sequence = Some(seq + 1);
            } else if seq == expected {
                self.expected_sequence = Some(seq + 1);
            }
        } else {
            self.expected_sequence = Some(seq + 1);
        }
    }

    pub fn stats(&self) -> ConsumerStats {
        self.stats
    }
}
