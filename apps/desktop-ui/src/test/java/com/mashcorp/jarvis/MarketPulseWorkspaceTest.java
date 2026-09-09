package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static com.mashcorp.jarvis.DecisionTelemetry.EvidenceState.BLOCKED;
import static com.mashcorp.jarvis.DecisionTelemetry.EvidenceState.PASS;
import static org.junit.jupiter.api.Assertions.assertEquals;

final class MarketPulseWorkspaceTest {
    @Test
    void headerTimestampUsesEasternMarketTime() {
        assertEquals("2026-09-09  10:30:00 (ET)",
                MarketPulseWorkspace.marketDateTime("2026-09-09T14:30:00Z"));
        assertEquals("TIMESTAMP UNAVAILABLE", MarketPulseWorkspace.marketDateTime(""));
    }

    @Test
    void overallRiskIncludesTheMarketDataGate() {
        assertEquals("Blocked", MarketPulseWorkspace.riskSummary(BLOCKED, PASS));
        assertEquals("Approved", MarketPulseWorkspace.riskSummary(PASS, PASS));
    }

    @Test
    void decisionIconTracksTheActionAndDirection() {
        assertEquals(MarketPulseWorkspace.DecisionIconKind.NO_TRADE,
                MarketPulseWorkspace.decisionIconKind("NO_TRADE"));
        assertEquals(MarketPulseWorkspace.DecisionIconKind.LONG,
                MarketPulseWorkspace.decisionIconKind("buy"));
        assertEquals(MarketPulseWorkspace.DecisionIconKind.SHORT,
                MarketPulseWorkspace.decisionIconKind("SHORT"));
        assertEquals(MarketPulseWorkspace.DecisionIconKind.TRADE,
                MarketPulseWorkspace.decisionIconKind("trade"));
        assertEquals(MarketPulseWorkspace.DecisionIconKind.NONE,
                MarketPulseWorkspace.decisionIconKind("NO DATA"));
    }
}
