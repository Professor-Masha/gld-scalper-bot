package com.mashcorp.jarvis;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Bounded, thread-safe end-to-end timings for JavaFX gateway operations. */
public final class ClientLatencyMonitor {
    private final int samplesPerOperation;
    private final long startedNanos = System.nanoTime();
    private final Map<String, ArrayDeque<Double>> samples = new LinkedHashMap<>();
    private final Map<String, Map<Integer, Integer>> statuses = new LinkedHashMap<>();

    public ClientLatencyMonitor() {
        this(256);
    }

    ClientLatencyMonitor(int samplesPerOperation) {
        this.samplesPerOperation = Math.max(32, Math.min(samplesPerOperation, 2_000));
    }

    public synchronized void observe(String method, String path, double elapsedMs, int statusCode) {
        String operation = method.toUpperCase() + " " + normalize(path);
        ArrayDeque<Double> values = samples.computeIfAbsent(operation, ignored -> new ArrayDeque<>());
        while (values.size() >= samplesPerOperation) values.removeFirst();
        values.addLast(Math.max(0.0, elapsedMs));
        statuses.computeIfAbsent(operation, ignored -> new LinkedHashMap<>())
                .merge(statusCode, 1, Integer::sum);
    }

    public synchronized void observeOperation(String name, double elapsedMs) {
        observe("CLIENT", name, elapsedMs, 0);
    }

    public synchronized Map<String, Object> snapshot() {
        List<Double> all = new ArrayList<>();
        Map<String, Object> operations = new LinkedHashMap<>();
        for (Map.Entry<String, ArrayDeque<Double>> entry : samples.entrySet()) {
            List<Double> values = new ArrayList<>(entry.getValue());
            all.addAll(values);
            Map<String, Object> summary = summarize(values);
            summary.put("status_counts", new LinkedHashMap<>(statuses.getOrDefault(entry.getKey(), Map.of())));
            operations.put(entry.getKey(), summary);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("uptime_seconds", round((System.nanoTime() - startedNanos) / 1_000_000_000.0));
        result.put("sample_count", all.size());
        result.put("overall", summarize(all));
        result.put("operations", operations);
        result.put("bounded_samples_per_operation", samplesPerOperation);
        return result;
    }

    private static Map<String, Object> summarize(List<Double> values) {
        List<Double> ordered = values.stream().sorted().toList();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("count", ordered.size());
        result.put("p50_ms", percentile(ordered, 0.50));
        result.put("p95_ms", percentile(ordered, 0.95));
        result.put("p99_ms", percentile(ordered, 0.99));
        result.put("max_ms", ordered.isEmpty() ? 0.0 : round(ordered.getLast()));
        return result;
    }

    private static double percentile(List<Double> values, double fraction) {
        if (values.isEmpty()) return 0.0;
        int index = (int) Math.round((values.size() - 1) * fraction);
        return round(values.get(Math.max(0, Math.min(values.size() - 1, index))));
    }

    private static String normalize(String path) {
        String value = path == null || path.isBlank() ? "/" : path.split("\\?", 2)[0];
        if (value.startsWith("/api/v1/memory-graph/nodes/")) return "/api/v1/memory-graph/nodes/{id}";
        if (value.startsWith("/api/results/")) return "/api/results/{name}";
        if (value.startsWith("/api/logs/")) return "/api/logs/{name}";
        return value;
    }

    private static double round(double value) {
        return Math.round(value * 1_000.0) / 1_000.0;
    }
}
