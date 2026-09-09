package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class DecisionRadarTest {
    @Test
    void categoricalGateScoresMatchTheRadarLegend() {
        assertEquals(0.80, DecisionRadar.gateValue(DecisionTelemetry.EvidenceState.PASS));
        assertEquals(0.48, DecisionRadar.gateValue(DecisionTelemetry.EvidenceState.WARN));
        assertEquals(0.20, DecisionRadar.gateValue(DecisionTelemetry.EvidenceState.BLOCKED));
        assertEquals(0.05, DecisionRadar.gateValue(DecisionTelemetry.EvidenceState.UNAVAILABLE));
    }
}
