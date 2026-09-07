package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

final class HumanReadableFormatterTest {
    private final ObjectMapper json = new ObjectMapper();

    @Test
    void formatsOperatorLabelsAndTypedValues() throws Exception {
        var payload = json.readTree("""
                {
                  "net_pnl": 12.5,
                  "win_rate": 0.625,
                  "stream_connected": true,
                  "created_at": "2026-09-07T10:30:00Z",
                  "model_status": "shadow_mode"
                }
                """);

        assertEquals("Net P/L", HumanReadableFormatter.label("net_pnl"));
        assertEquals("$12.5000", HumanReadableFormatter.value("net_pnl", payload.path("net_pnl")));
        assertEquals("62.50%", HumanReadableFormatter.value("win_rate", payload.path("win_rate")));
        assertEquals("Yes", HumanReadableFormatter.value("stream_connected", payload.path("stream_connected")));
        assertTrue(HumanReadableFormatter.value("created_at", payload.path("created_at")).contains("2026"));
        assertEquals("Shadow Mode", HumanReadableFormatter.value("model_status", payload.path("model_status")));
        assertEquals("Command Completed", HumanReadableFormatter.value("event_type", json.readTree("\"command.completed\"")));
    }

    @Test
    void structuresNestedPayloadsAndNeverDisplaysSecrets() throws Exception {
        var payload = json.readTree("""
                {
                  "status":"ready",
                  "api_key":"do-not-display",
                  "readiness":{"trading_allowed":true,"market_state":"open"},
                  "models":[{"model_version":"candidate-1","confidence":0.81}]
                }
                """);

        String text = HumanReadableFormatter.plainText(payload);
        assertTrue(text.contains("Status: Ready"));
        assertTrue(text.contains("Trading Allowed: Yes"));
        assertTrue(text.contains("Model Version: candidate-1"));
        assertFalse(text.contains("do-not-display"));
    }
}
