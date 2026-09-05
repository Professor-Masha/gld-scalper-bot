package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.geometry.Point3D;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;

/** Immutable graph geometry prepared away from the JavaFX application thread. */
record GraphRenderPlan(String fingerprint, String layoutFingerprint, Map<String, NodePlan> nodes, Map<String, EdgePlan> edges, long layoutNanos) {
    record NodePlan(String id, String label, String type, String color, double radius, Point3D position) {}
    record EdgePlan(String id, String source, String target, boolean active, double weight) {}

    static GraphRenderPlan build(JsonNode graph) {
        return build(graph, null);
    }

    static GraphRenderPlan build(JsonNode graph, GraphRenderPlan previous) {
        long started = System.nanoTime();
        Map<String, JsonNode> sourceNodes = new LinkedHashMap<>();
        if (graph != null && graph.path("nodes").isArray()) {
            graph.path("nodes").forEach(item -> {
                if (sourceNodes.size() < 180) sourceNodes.put(item.path("id").asText(), item);
            });
        }
        List<JsonNode> sourceEdges = new ArrayList<>();
        if (graph != null) graph.path("edges").forEach(item -> {
            if (sourceNodes.containsKey(item.path("source").asText()) && sourceNodes.containsKey(item.path("target").asText())) sourceEdges.add(item);
        });
        List<String> ids = new ArrayList<>(sourceNodes.keySet());
        Map<String, Integer> indexes = new LinkedHashMap<>();
        for (int index = 0; index < ids.size(); index++) indexes.put(ids.get(index), index);
        String layoutFingerprint = graph == null ? "" : graph.path("layout_fingerprint").asText();
        Point3D[] positions;
        if (previous != null && layoutFingerprint.equals(previous.layoutFingerprint()) && previous.nodes().keySet().containsAll(ids)) {
            positions = new Point3D[ids.size()];
            for (int index = 0; index < ids.size(); index++) positions[index] = previous.nodes().get(ids.get(index)).position();
        } else {
            positions = forceLayout(ids, sourceEdges, indexes);
        }
        Map<String, NodePlan> nodes = new LinkedHashMap<>();
        for (int index = 0; index < ids.size(); index++) {
            JsonNode item = sourceNodes.get(ids.get(index));
            nodes.put(ids.get(index), new NodePlan(ids.get(index), item.path("label").asText(ids.get(index)),
                    item.path("type").asText("memory"), item.path("color").asText("#84939a"),
                    Math.max(3.0, Math.min(12.0, item.path("size").asDouble(6.0))), positions[index]));
        }
        Map<String, EdgePlan> edges = new LinkedHashMap<>();
        for (JsonNode item : sourceEdges) {
            String id = item.path("id").asText();
            edges.put(id, new EdgePlan(id, item.path("source").asText(), item.path("target").asText(),
                    item.path("active").asBoolean(false), item.path("weight").asDouble(1.0)));
        }
        return new GraphRenderPlan(graph == null ? "" : graph.path("topology_fingerprint").asText(), layoutFingerprint,
                Map.copyOf(nodes), Map.copyOf(edges), System.nanoTime() - started);
    }

    private static Point3D[] forceLayout(List<String> ids, List<JsonNode> edges, Map<String, Integer> indexes) {
        int count = ids.size();
        Point3D[] positions = new Point3D[count];
        Random random = new Random(57L);
        int center = -1;
        for (int index = 0; index < count; index++) {
            if ("decision:current".equals(ids.get(index))) center = index;
            double angle = Math.PI * 2 * index / Math.max(count, 1);
            double radius = 75 + (index % 5) * 28;
            positions[index] = new Point3D(Math.cos(angle) * radius, Math.sin(angle) * radius * 0.58, -65 + random.nextDouble() * 130);
        }
        if (center >= 0) positions[center] = Point3D.ZERO;
        for (int iteration = 0; iteration < 55; iteration++) {
            Point3D[] forces = new Point3D[count];
            for (int index = 0; index < count; index++) forces[index] = Point3D.ZERO;
            for (int left = 0; left < count; left++) for (int right = left + 1; right < count; right++) {
                Point3D delta = positions[left].subtract(positions[right]);
                double distance = Math.max(delta.magnitude(), 8.0);
                Point3D force = delta.normalize().multiply(1050.0 / (distance * distance));
                forces[left] = forces[left].add(force); forces[right] = forces[right].subtract(force);
            }
            for (JsonNode edge : edges) {
                Integer left = indexes.get(edge.path("source").asText()), right = indexes.get(edge.path("target").asText());
                if (left == null || right == null) continue;
                Point3D delta = positions[right].subtract(positions[left]);
                double distance = Math.max(delta.magnitude(), 1.0);
                double target = 58.0 + 8.0 / Math.max(edge.path("weight").asDouble(1.0), 0.2);
                Point3D force = delta.normalize().multiply((distance - target) * 0.012);
                forces[left] = forces[left].add(force); forces[right] = forces[right].subtract(force);
            }
            for (int index = 0; index < count; index++) {
                if (index == center) continue;
                Point3D next = positions[index].add(forces[index].add(positions[index].multiply(-0.0025)).multiply(0.72));
                positions[index] = new Point3D(clamp(next.getX(), -235, 235), clamp(next.getY(), -135, 135), clamp(next.getZ(), -105, 105));
            }
        }
        return positions;
    }

    private static double clamp(double value, double minimum, double maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }
}
