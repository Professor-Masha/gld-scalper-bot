package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class EvidenceOrderChainTest {
    @Test
    void titlesDescribeActualGateState() {
        assertEquals("DATA FRESH", EvidenceOrderChain.displayTitle(evidence("Market data", "Fresh", DecisionTelemetry.EvidenceState.PASS)));
        assertEquals("LIQUIDITY BLOCKED", EvidenceOrderChain.displayTitle(evidence("Liquidity", "Wide spread", DecisionTelemetry.EvidenceState.BLOCKED)));
        assertEquals("NO ACTIVE ORDER", EvidenceOrderChain.displayTitle(evidence("Execution", "No active execution episode", DecisionTelemetry.EvidenceState.WARN)));
    }

    @Test
    void timestampsFailClosed() {
        assertEquals("10:30:00", EvidenceOrderChain.timestamp("2026-09-09T14:30:00Z"));
        assertEquals("--", EvidenceOrderChain.timestamp(""));
        assertEquals("--", EvidenceOrderChain.timestamp("not-a-timestamp"));
    }

    private static DecisionTelemetry.Evidence evidence(String name, String detail,
                                                        DecisionTelemetry.EvidenceState state) {
        return new DecisionTelemetry.Evidence(name, detail, state);
    }
}
