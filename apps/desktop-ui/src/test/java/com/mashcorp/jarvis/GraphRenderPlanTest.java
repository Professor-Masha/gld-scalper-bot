package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

final class GraphRenderPlanTest {
    @Test
    void preparesBoundedGraphAwayFromSceneMutation() throws Exception {
        StringBuilder nodes = new StringBuilder();
        StringBuilder edges = new StringBuilder();
        for (int i = 0; i < 80; i++) {
            if (i > 0) nodes.append(',');
            nodes.append("{\"id\":\"node:").append(i).append("\",\"label\":\"Node ").append(i).append("\",\"type\":\"model\",\"color\":\"#2de1ff\",\"size\":6}");
            if (i > 0) {
                if (edges.length() > 0) edges.append(',');
                edges.append("{\"id\":\"edge:").append(i).append("\",\"source\":\"node:").append(i - 1)
                        .append("\",\"target\":\"node:").append(i).append("\",\"weight\":1}");
            }
        }
        var json = new ObjectMapper().readTree("{\"topology_fingerprint\":\"test\",\"layout_fingerprint\":\"layout\",\"nodes\":[" + nodes + "],\"edges\":[" + edges + "]}");
        GraphRenderPlan plan = GraphRenderPlan.build(json);
        assertEquals(80, plan.nodes().size());
        assertEquals(79, plan.edges().size());
        assertEquals("test", plan.fingerprint());
        assertEquals("layout", plan.layoutFingerprint());
        assertTrue(plan.layoutNanos() / 1_000_000.0 < 500.0, "layout plan should remain below half a second in CI");
    }
}
