package com.mdrap.client.examples;

import com.mdrap.client.ConsumerConfig;
import com.mdrap.client.ConsumerStats;
import com.mdrap.client.MarketEvent;
import com.mdrap.client.MdrapConsumer;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

public class ConsumerExample {
    public static void main(String[] args) {
        System.out.println("[MDRAP Java SDK] Initializing Java consumer example...");

        ConsumerConfig config = ConsumerConfig.defaultConfig();
        try (MdrapConsumer consumer = new MdrapConsumer(config)) {
            consumer.connect();
            if (!consumer.isConnected()) {
                throw new AssertionError("Consumer should be connected");
            }

            // Construct 64-byte SBE buffer: AAPL trade, seq 200
            ByteBuffer buf1 = ByteBuffer.allocate(64).order(ByteOrder.LITTLE_ENDIAN);
            byte[] sym = "AAPL".getBytes(StandardCharsets.US_ASCII);
            buf1.put(sym);
            buf1.position(16);
            buf1.putLong(200L); // sequence
            buf1.putLong(1728475200000000000L); // exTs
            buf1.putDouble(150.25); // price
            buf1.putDouble(10.0); // quantity
            buf1.putInt(1); // eventType (TRADE)
            buf1.putInt(0); // quality (VALID)
            buf1.putLong(0L); // padding
            buf1.flip();

            MarketEvent evt1 = consumer.processSbeBuffer(buf1);
            if (!"AAPL".equals(evt1.instrument()) || evt1.sequence() != 200L || evt1.price() != 150.25) {
                throw new AssertionError("Mismatch in parsed event 1 fields");
            }

            // Construct buffer with gap: seq 205 (expected 201 -> gap of 4: 201, 202, 203, 204)
            ByteBuffer buf2 = ByteBuffer.allocate(64).order(ByteOrder.LITTLE_ENDIAN);
            buf2.put(sym);
            buf2.position(16);
            buf2.putLong(205L); // sequence
            buf2.putLong(1728475200001000000L); // exTs
            buf2.putDouble(150.50);
            buf2.putDouble(50.0);
            buf2.putInt(1);
            buf2.putInt(0);
            buf2.putLong(0L);
            buf2.flip();

            MarketEvent evt2 = consumer.processSbeBuffer(buf2);
            if (evt2.sequence() != 205L) {
                throw new AssertionError("Mismatch in parsed event 2 sequence");
            }

            ConsumerStats stats = consumer.getStats();
            if (stats.getEventsConsumed() != 2 || stats.getGapsDetected() != 1 || stats.getMissingEvents() != 4) {
                throw new AssertionError("Mismatch in consumer stats accounting");
            }

            System.out.println("[MDRAP Java SDK] All assertions passed successfully! Consumed: "
                    + stats.getEventsConsumed() + ", Gaps: " + stats.getGapsDetected()
                    + ", Missing: " + stats.getMissingEvents());
        }
    }
}
