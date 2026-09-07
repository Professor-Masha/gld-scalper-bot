package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.geometry.Point3D;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Immutable, deterministic open-ring geometry prepared away from the JavaFX application thread. */
record GraphRenderPlan(String fingerprint, String layoutFingerprint, Map<String, NodePlan> nodes, Map<String, EdgePlan> edges, long layoutNanos) {
    private static final String LAYOUT_VERSION = "open-ring-v1";
    private static final Map<String, Double> TYPE_ANGLES = Map.of(
            "model", -15.0,
            "training", 35.0,
            "dataset", 85.0,
            "llm", 135.0,
            "trade", 185.0,
            "playbook", 225.0
    );

    record NodePlan(
            String id,
            String label,
            String type,
            String status,
            String subtitle,
            String preview,
            String color,
            double radius,
            Point3D position) {}
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
        String sourceLayoutFingerprint = graph == null ? "" : graph.path("layout_fingerprint").asText();
        String layoutFingerprint = LAYOUT_VERSION + ":" + sourceLayoutFingerprint;
        Point3D[] positions;
        if (previous != null && layoutFingerprint.equals(previous.layoutFingerprint()) && previous.nodes().keySet().containsAll(ids)) {
            positions = new Point3D[ids.size()];
            for (int index = 0; index < ids.size(); index++) positions[index] = previous.nodes().get(ids.get(index)).position();
        } else {
            positions = stableOpenRingLayout(ids, sourceNodes);
        }
        Map<String, NodePlan> nodes = new LinkedHashMap<>();
        for (int index = 0; index < ids.size(); index++) {
            JsonNode item = sourceNodes.get(ids.get(index));
            nodes.put(ids.get(index), new NodePlan(ids.get(index), item.path("label").asText(ids.get(index)),
                    item.path("type").asText("memory"), item.path("status").asText("available"),
                    item.path("subtitle").asText(""), item.path("preview").asText(""),
                    item.path("color").asText("#84939a"),
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

    private static Point3D[] stableOpenRingLayout(List<String> ids, Map<String, JsonNode> sourceNodes) {
        Map<String, List<String>> grouped = new LinkedHashMap<>();
        ids.stream().sorted(Comparator.naturalOrder()).forEach(id -> {
            String type = sourceNodes.get(id).path("type").asText("memory");
            grouped.computeIfAbsent(type, ignored -> new ArrayList<>()).add(id);
        });
        Map<String, Point3D> positioned = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> group : grouped.entrySet()) {
            String type = group.getKey();
            List<String> members = group.getValue();
            if ("decision".equals(type)) {
                for (int index = 0; index < members.size(); index++) {
                    positioned.put(members.get(index), index == 0 ? Point3D.ZERO : polar(index * 52.0, 48, 32, 0));
                }
                continue;
            }
            if ("market".equals(type) || "risk".equals(type)) {
                double anchor = "market".equals(type) ? 205.0 : -25.0;
                for (int index = 0; index < members.size(); index++) {
                    positioned.put(members.get(index), polar(anchor + spread(index, members.size(), 20), 78, 48, depth(index, type)));
                }
                continue;
            }
            double anchor = TYPE_ANGLES.getOrDefault(type, 110.0);
            int lanes = Math.min(3, Math.max(1, (int) Math.ceil(members.size() / 8.0)));
            double groupWidth = Math.min(80.0, 28.0 + members.size() * 1.7);
            for (int index = 0; index < members.size(); index++) {
                int lane = index % lanes;
                int slot = index / lanes;
                int slots = (int) Math.ceil((members.size() - lane) / (double) lanes);
                double angle = anchor + spread(slot, slots, groupWidth);
                double radiusX = 100 + lane * 26;
                double radiusY = 60 + lane * 15;
                positioned.put(members.get(index), polar(angle, radiusX, radiusY, depth(index, type)));
            }
        }
        Point3D[] result = new Point3D[ids.size()];
        for (int index = 0; index < ids.size(); index++) result[index] = positioned.getOrDefault(ids.get(index), Point3D.ZERO);
        return result;
    }

    private static double spread(int index, int count, double widthDegrees) {
        if (count <= 1) return 0.0;
        return -widthDegrees / 2.0 + widthDegrees * index / (count - 1.0);
    }

    private static Point3D polar(double degrees, double radiusX, double radiusY, double z) {
        double radians = Math.toRadians(degrees);
        return new Point3D(Math.cos(radians) * radiusX, Math.sin(radians) * radiusY, z);
    }

    private static double depth(int index, String type) {
        int seed = 31 * type.hashCode() + index * 17;
        return ((Math.floorMod(seed, 9) - 4) * 8.0);
    }
}
