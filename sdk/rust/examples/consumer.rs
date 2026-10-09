use mdrap_consumer::{ConsumerConfig, EventType, MdrapConsumer, QualityStatus, SbeMarketEvent};

fn main() {
    println!("[MDRAP Rust SDK] Initializing Rust consumer example...");

    let mut consumer = MdrapConsumer::new(ConsumerConfig::default());
    assert!(consumer.connect().is_ok());
    assert!(consumer.is_connected());

    let mut inst = [0u8; 16];
    inst[..4].copy_from_slice(b"AAPL");

    // Frame 1: Sequence 300
    let frame1 = SbeMarketEvent {
        instrument: inst,
        sequence: 300,
        exchange_ts_ns: 1728475200000000000,
        price: 150.25,
        quantity: 10.0,
        event_type: 1, // Trade
        quality_flag: 0, // Valid
        _padding: [0u8; 8],
    };

    let evt1 = consumer.feed_sbe_frame(&frame1).expect("Failed to feed frame 1");
    assert_eq!(evt1.instrument, "AAPL");
    assert_eq!(evt1.sequence, 300);
    assert_eq!(evt1.price, 150.25);
    assert_eq!(evt1.event_type, EventType::Trade);
    assert_eq!(evt1.quality_status, QualityStatus::Valid);

    // Frame 2: Gap detected (seq 305, expected 301 -> gap of 4)
    let frame2 = SbeMarketEvent {
        instrument: inst,
        sequence: 305,
        exchange_ts_ns: 1728475200001000000,
        price: 150.50,
        quantity: 50.0,
        event_type: 1,
        quality_flag: 0,
        _padding: [0u8; 8],
    };

    let evt2 = consumer.feed_sbe_frame(&frame2).expect("Failed to feed frame 2");
    assert_eq!(evt2.sequence, 305);

    let stats = consumer.stats();
    assert_eq!(stats.events_consumed, 2);
    assert_eq!(stats.gaps_detected, 1);
    assert_eq!(stats.missing_events, 4);

    consumer.disconnect();
    assert!(!consumer.is_connected());

    println!(
        "[MDRAP Rust SDK] All assertions passed successfully! Consumed: {}, Gaps: {}, Missing: {}",
        stats.events_consumed, stats.gaps_detected, stats.missing_events
    );
}
