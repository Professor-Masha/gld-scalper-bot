package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ReadinessSnapshotTest {
    private final ObjectMapper json = new ObjectMapper();

    @Test void separatesInterfaceTradingMarketAndLlmReadiness() throws Exception {
        var readiness = json.readTree("""
                {"ready":true,"trading":{"state":"available"},"market":{"session":"regular"}}
                """);
        var providers = json.readTree("{" + "\"active_provider\":\"ollama\"}");
        ReadinessSnapshot snapshot = ReadinessSnapshot.from(readiness, providers);
        assertEquals("READY", snapshot.interfaceState());
        assertEquals("AVAILABLE", snapshot.tradingState());
        assertEquals("OPEN", snapshot.marketState());
        assertEquals("OLLAMA CONFIGURED", snapshot.llmState());
        assertTrue(snapshot.tradingAllowed());
    }

    @Test void anUnavailableTradingCoreDoesNotMakeTheInterfaceUnusable() throws Exception {
        var readiness = json.readTree("""
                {"ready":false,"trading":{"state":"blocked"},"market":{"session":"closed"}}
                """);
        var providers = json.readTree("{" + "\"active_provider\":\"none\"}");
        ReadinessSnapshot snapshot = ReadinessSnapshot.from(readiness, providers);
        assertEquals("READY", snapshot.interfaceState());
        assertEquals("BLOCKED", snapshot.tradingState());
        assertEquals("CLOSED", snapshot.marketState());
        assertEquals("DISABLED", snapshot.llmState());
        assertFalse(snapshot.tradingAllowed());
    }
}
