package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class LiveDecisionWorkspaceTest {
    @Test
    void uncertaintyIsRenderedAsAnOperatorVerdict() {
        assertEquals("UNAVAILABLE", LiveDecisionWorkspace.uncertaintyVerdict(false, 0.1));
        assertEquals("LOW", LiveDecisionWorkspace.uncertaintyVerdict(true, 0.2));
        assertEquals("MEDIUM", LiveDecisionWorkspace.uncertaintyVerdict(true, 0.5));
        assertEquals("HIGH", LiveDecisionWorkspace.uncertaintyVerdict(true, 0.9));
    }

    @Test
    void setupSummaryIsBoundedAndLabeled() {
        String text = LiveDecisionWorkspace.setupText(
                "bullish momentum near resistance; awaiting volume confirmation");
        assertTrue(text.startsWith("SETUP: "));
        assertTrue(text.contains("\n"));
        assertTrue(text.length() <= 126);
    }

    @Test
    void currentStateContextDoesNotRepeatEquivalentLabels() {
        assertEquals("POOR LIQUIDITY",
                LiveDecisionWorkspace.stateContext("Poor liquidity", "poor liquidity"));
        assertEquals("OPEN - TRENDING",
                LiveDecisionWorkspace.stateContext("Open", "Trending"));
    }

    @Test
    void marketTimestampUsesTheExchangeTimezone() {
        assertTrue(LiveDecisionWorkspace.marketTimestamp(
                "2026-09-09T14:30:00Z").endsWith("10:30:00 ET"));
        assertEquals("TIMESTAMP UNAVAILABLE",
                LiveDecisionWorkspace.marketTimestamp("invalid"));
    }

    @Test
    void layoutDensityAccountsForWidthAndHeight() {
        assertEquals(LiveDecisionWorkspace.LayoutDensity.NORMAL,
                LiveDecisionWorkspace.layoutDensity(1500, 900));
        assertEquals(LiveDecisionWorkspace.LayoutDensity.COMPACT,
                LiveDecisionWorkspace.layoutDensity(1200, 700));
        assertEquals(LiveDecisionWorkspace.LayoutDensity.COMPACT,
                LiveDecisionWorkspace.layoutDensity(850, 800));
        assertEquals(LiveDecisionWorkspace.LayoutDensity.NARROW,
                LiveDecisionWorkspace.layoutDensity(850, 500));
    }
}
