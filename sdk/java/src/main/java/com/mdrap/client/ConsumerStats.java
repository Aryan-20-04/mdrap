package com.mdrap.client;

public class ConsumerStats {
    private long eventsConsumed = 0;
    private long gapsDetected = 0;
    private long missingEvents = 0;
    private long lastSequence = 0;

    public synchronized void recordEvent(long sequence) {
        eventsConsumed++;
        lastSequence = sequence;
    }

    public synchronized void recordGap(long missingCount) {
        gapsDetected++;
        missingEvents += missingCount;
    }

    public synchronized long getEventsConsumed() { return eventsConsumed; }
    public synchronized long getGapsDetected() { return gapsDetected; }
    public synchronized long getMissingEvents() { return missingEvents; }
    public synchronized long getLastSequence() { return lastSequence; }
}
