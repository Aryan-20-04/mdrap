package com.mdrap.client;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

public record MarketEvent(
    String instrument,
    long sequence,
    long exchangeTimestampNs,
    double price,
    double quantity,
    int eventType,
    int qualityStatus
) {
    public static final int SBE_FRAME_SIZE = 64;

    /**
     * Decode a 64-byte aligned SBE binary buffer into a MarketEvent.
     */
    public static MarketEvent fromSbeBuffer(ByteBuffer buf) {
        if (buf.remaining() < SBE_FRAME_SIZE) {
            throw new IllegalArgumentException("Buffer underflow: requires at least 64 bytes");
        }
        buf.order(ByteOrder.LITTLE_ENDIAN);

        byte[] instBytes = new byte[16];
        buf.get(instBytes);
        int len = 0;
        while (len < 16 && instBytes[len] != 0) {
            len++;
        }
        String inst = new String(instBytes, 0, len, StandardCharsets.US_ASCII);

        long seq = buf.getLong();
        long exTs = buf.getLong();
        double px = buf.getDouble();
        double qty = buf.getDouble();
        int evType = buf.getInt();
        int qFlag = buf.getInt();
        buf.getLong(); // skip 8-byte padding

        return new MarketEvent(inst, seq, exTs, px, qty, evType, qFlag);
    }
}
