package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ClientLatencyMonitorTest {
    @Test void boundsSamplesAndNormalizesDynamicRoutes() {
        ClientLatencyMonitor monitor = new ClientLatencyMonitor(32);
        for (int index = 0; index < 40; index++) {
            monitor.observe("get", "/api/v1/memory-graph/nodes/trade:" + index, index + 1, 200);
        }

        Map<String, Object> snapshot = monitor.snapshot();
        @SuppressWarnings("unchecked")
        Map<String, Object> operations = (Map<String, Object>) snapshot.get("operations");
        @SuppressWarnings("unchecked")
        Map<String, Object> route = (Map<String, Object>) operations.get("GET /api/v1/memory-graph/nodes/{id}");

        assertEquals(32, route.get("count"));
        assertTrue((double) route.get("p95_ms") >= 38.0);
        assertEquals(32, snapshot.get("sample_count"));
    }
}
