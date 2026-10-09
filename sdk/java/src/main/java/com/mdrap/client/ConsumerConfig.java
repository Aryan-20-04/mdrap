package com.mdrap.client;

public record ConsumerConfig(
    String endpoint,
    String apiKey,
    int pollTimeoutMs,
    boolean trackSequenceGaps
) {
    public static ConsumerConfig defaultConfig() {
        return new ConsumerConfig("127.0.0.1:8901", "", 100, true);
    }
}
