package com.mashcorp.jarvis;

import javafx.animation.AnimationTimer;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Samples JavaFX pulse intervals for opt-in visual performance tests. */
final class FrameTimeMonitor extends AnimationTimer {
    private final List<Double> milliseconds = new ArrayList<>();
    private long previous;

    @Override public void handle(long now) {
        if (previous > 0) {
            double value = (now - previous) / 1_000_000.0;
            if (value < 500 && milliseconds.size() < 20_000) milliseconds.add(value);
        }
        previous = now;
    }

    String json() {
        if (milliseconds.isEmpty()) return "{\"samples\":0}";
        List<Double> sorted = new ArrayList<>(milliseconds); Collections.sort(sorted);
        double p50 = percentile(sorted, 0.50), p95 = percentile(sorted, 0.95), p99 = percentile(sorted, 0.99);
        long slow = sorted.stream().filter(value -> value > 33.34).count();
        return String.format(java.util.Locale.ROOT,
                "{\"samples\":%d,\"p50_ms\":%.3f,\"p95_ms\":%.3f,\"p99_ms\":%.3f,\"max_ms\":%.3f,\"frames_over_33ms\":%d}",
                sorted.size(), p50, p95, p99, sorted.get(sorted.size() - 1), slow);
    }

    private static double percentile(List<Double> values, double fraction) {
        return values.get(Math.min(values.size() - 1, Math.max(0, (int)Math.round((values.size() - 1) * fraction))));
    }
}
