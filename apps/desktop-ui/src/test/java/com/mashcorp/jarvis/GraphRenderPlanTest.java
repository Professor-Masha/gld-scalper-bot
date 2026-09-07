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
        assertEquals("open-ring-v1:layout", plan.layoutFingerprint());
        assertTrue(plan.layoutNanos() / 1_000_000.0 < 500.0, "layout plan should remain below half a second in CI");
    }

    @Test
    void buildsStableOpenRingWithImmediateNodeSummaries() throws Exception {
        var json = new ObjectMapper().readTree("""
                {
                  "topology_fingerprint":"topology-a",
                  "layout_fingerprint":"layout-a",
                  "nodes":[
                    {"id":"decision:current","label":"NO TRADE","type":"decision","status":"current","subtitle":"Poor liquidity","preview":"Spread is wide","color":"#2de1ff","size":10},
                    {"id":"model:a","label":"Model A","type":"model","status":"candidate","subtitle":"Fast microstructure","preview":"Holdout pending","color":"#8b7cff","size":7},
                    {"id":"training:1","label":"Training 1","type":"training","status":"completed","color":"#51a8ff","size":7},
                    {"id":"dataset:1","label":"Dataset 1","type":"dataset","status":"available","color":"#3f7cff","size":7},
                    {"id":"llm:1","label":"Review 1","type":"llm","status":"advisory","color":"#b56dff","size":7},
                    {"id":"trade:1","label":"Long breakout","type":"trade","status":"win","color":"#5cf2b5","size":7},
                    {"id":"playbook:1","label":"Breakout","type":"playbook","status":"observed","color":"#ffbf69","size":7}
                  ],
                  "edges":[]
                }
                """);

        GraphRenderPlan first = GraphRenderPlan.build(json);
        GraphRenderPlan second = GraphRenderPlan.build(json);

        assertEquals(javafx.geometry.Point3D.ZERO, first.nodes().get("decision:current").position());
        assertEquals(first.nodes().get("model:a").position(), second.nodes().get("model:a").position());
        assertEquals("Fast microstructure", first.nodes().get("model:a").subtitle());
        assertEquals("Holdout pending", first.nodes().get("model:a").preview());
        assertTrue(first.nodes().values().stream()
                .filter(node -> !node.id().equals("decision:current"))
                .noneMatch(node -> inTopGap(node.position())), "outer memory nodes must preserve the open top gap");
    }

    private static boolean inTopGap(javafx.geometry.Point3D point) {
        double normalizedAngle = (Math.toDegrees(Math.atan2(point.getY() / 142.0, point.getX() / 242.0)) + 360.0) % 360.0;
        return normalizedAngle > 238.0 && normalizedAngle < 302.0;
    }
}
