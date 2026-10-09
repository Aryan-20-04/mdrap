package com.mdrap.client;

import java.io.Closeable;
import java.nio.ByteBuffer;
import java.util.concurrent.atomic.AtomicBoolean;

public class MdrapConsumer implements AutoCloseable, Closeable {
    private final ConsumerConfig config;
    private final ConsumerStats stats = new ConsumerStats();
    private final AtomicBoolean connected = new AtomicBoolean(false);
    private Long expectedSequence = null;

    public MdrapConsumer(ConsumerConfig config) {
        this.config = config != null ? config : ConsumerConfig.defaultConfig();
    }

    public synchronized void connect() {
        connected.set(true);
    }

    public synchronized void disconnect() {
        connected.set(false);
    }

    public boolean isConnected() {
        return connected.get();
    }

    public synchronized MarketEvent processSbeBuffer(ByteBuffer buf) {
        if (!connected.get()) {
            throw new IllegalStateException("Consumer is not connected");
        }
        MarketEvent event = MarketEvent.fromSbeBuffer(buf);
        if (config.trackSequenceGaps()) {
            auditSequence(event.sequence());
        }
        stats.recordEvent(event.sequence());
        return event;
    }

    private void auditSequence(long seq) {
        if (expectedSequence != null) {
            if (seq > expectedSequence) {
                long missing = seq - expectedSequence;
                stats.recordGap(missing);
                expectedSequence = seq + 1;
            } else if (seq == expectedSequence) {
                expectedSequence = seq + 1;
            }
        } else {
            expectedSequence = seq + 1;
        }
    }

    public ConsumerStats getStats() {
        return stats;
    }

    @Override
    public void close() {
        disconnect();
    }
}
