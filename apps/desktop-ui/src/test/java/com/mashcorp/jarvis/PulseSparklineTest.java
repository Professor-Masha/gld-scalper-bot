package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class PulseSparklineTest {
    @Test
    void formatsEasternTimeAndSeparatesTradingSessions() {
        assertEquals("09:30", PulseSparkline.time("2026-09-10T13:30:00Z"));
        assertEquals("2026-09-10", PulseSparkline.sessionDate("2026-09-10T13:30:00Z"));
        assertEquals("", PulseSparkline.sessionDate(""));
    }
}
