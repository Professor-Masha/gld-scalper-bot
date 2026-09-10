package com.mashcorp.jarvis;

import javafx.geometry.VPos;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.control.Label;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.text.Font;
import javafx.scene.text.TextAlignment;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

/** Bounded, presentation-only telemetry chart used by Market Pulse. */
final class PulseSparkline extends VBox {
    private static final int MAXIMUM_SAMPLES = 160;
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("HH:mm", Locale.US)
            .withZone(ZoneId.of("America/New_York"));

    enum ValueFormat { PERCENT, BPS, RELATIVE, MONEY }
    record Sample(String timestamp, double value) { }

    private final Canvas canvas = new Canvas();
    private final Label value = new Label("--");
    private final Deque<Sample> samples = new ArrayDeque<>();
    private final Color color;
    private final ValueFormat format;
    private double observedMinimum = Double.NaN;
    private double observedMaximum = Double.NaN;
    private String historySession = "";

    PulseSparkline(String title, Color color, ValueFormat format) {
        this.color = color;
        this.format = format;
        getStyleClass().add("pulse-chart");
        Label heading = new Label(title);
        heading.getStyleClass().add("pulse-chart-title");
        value.getStyleClass().add("pulse-chart-value");
        Region gap = new Region();
        HBox.setHgrow(gap, Priority.ALWAYS);
        HBox header = new HBox(8, heading, gap, value);
        getChildren().addAll(header, canvas);
        VBox.setVgrow(canvas, Priority.ALWAYS);
        widthProperty().addListener((ignored, before, after) -> resizeAndDraw());
        heightProperty().addListener((ignored, before, after) -> resizeAndDraw());
    }

    void replaceSamples(List<Sample> history) {
        samples.clear();
        observedMinimum = Double.NaN;
        observedMaximum = Double.NaN;
        historySession = "";
        int first = Math.max(0, history.size() - MAXIMUM_SAMPLES);
        history.subList(first, history.size()).stream()
                .filter(sample -> Double.isFinite(sample.value()))
                .forEach(this::append);
        draw();
    }

    void update(double next, String display, String timestamp) {
        value.setText(display);
        value.setTextFill(color);
        if (Double.isFinite(next)) {
            Sample sample = new Sample(timestamp == null ? "" : timestamp, next);
            String session = sessionDate(sample.timestamp());
            if (!historySession.isBlank() && !session.isBlank() && !historySession.equals(session)) {
                draw();
                return;
            }
            if (!samples.isEmpty() && !sample.timestamp().isBlank()
                    && sample.timestamp().equals(samples.getLast().timestamp())) {
                samples.removeLast();
            }
            append(sample);
        }
        draw();
    }

    int sampleCount() {
        return samples.size();
    }

    private void append(Sample sample) {
        samples.addLast(sample);
        String session = sessionDate(sample.timestamp());
        if (!session.isBlank()) historySession = session;
        while (samples.size() > MAXIMUM_SAMPLES) samples.removeFirst();
        observedMinimum = Double.isFinite(observedMinimum)
                ? Math.min(observedMinimum, sample.value()) : sample.value();
        observedMaximum = Double.isFinite(observedMaximum)
                ? Math.max(observedMaximum, sample.value()) : sample.value();
    }

    private void resizeAndDraw() {
        CanvasSurface.resize(canvas, Math.max(1, getWidth() - 16), Math.max(1, getHeight() - 28));
        draw();
    }

    private void draw() {
        double width = canvas.getWidth();
        double height = canvas.getHeight();
        if (width < 70 || height < 38) return;
        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.clearRect(0, 0, width, height);
        graphics.setFill(Color.web("#02090c"));
        graphics.fillRect(0, 0, width, height);

        double left = 2;
        double right = width - 34;
        double top = 3;
        double bottom = height - 14;
        graphics.setStroke(Color.web("#12323b"));
        graphics.setLineWidth(0.7);
        for (int index = 0; index <= 3; index++) {
            double y = top + (bottom - top) * index / 3.0;
            graphics.strokeLine(left, y, right, y);
        }
        for (int index = 0; index <= 4; index++) {
            double x = left + (right - left) * index / 4.0;
            graphics.strokeLine(x, top, x, bottom);
        }

        if (samples.isEmpty()) {
            graphics.setFill(Color.web("#52747c"));
            graphics.setFont(Font.font("Consolas", 8));
            graphics.setTextAlign(TextAlignment.CENTER);
            graphics.setTextBaseline(VPos.CENTER);
            graphics.fillText("AWAITING SESSION HISTORY", (left + right) / 2, (top + bottom) / 2);
            return;
        }

        double magnitude = Math.max(Math.abs(observedMinimum), Math.abs(observedMaximum));
        double padding = Math.max((observedMaximum - observedMinimum) * 0.12,
                Math.max(magnitude * 0.025, minimumPadding()));
        double minimum = observedMinimum - padding;
        double maximum = observedMaximum + padding;
        List<Sample> points = List.copyOf(samples);

        graphics.setFill(new Color(color.getRed(), color.getGreen(), color.getBlue(), 0.13));
        graphics.beginPath();
        for (int index = 0; index < points.size(); index++) {
            double x = pointX(index, points.size(), left, right);
            double y = pointY(points.get(index).value(), minimum, maximum, top, bottom);
            if (index == 0) graphics.moveTo(x, y); else graphics.lineTo(x, y);
        }
        graphics.lineTo(right, bottom);
        graphics.lineTo(left, bottom);
        graphics.closePath();
        graphics.fill();

        graphics.setStroke(color);
        graphics.setLineWidth(1.6);
        graphics.beginPath();
        for (int index = 0; index < points.size(); index++) {
            double x = pointX(index, points.size(), left, right);
            double y = pointY(points.get(index).value(), minimum, maximum, top, bottom);
            if (index == 0) graphics.moveTo(x, y); else graphics.lineTo(x, y);
        }
        graphics.stroke();

        Sample latest = points.get(points.size() - 1);
        graphics.setFill(color);
        graphics.fillOval(pointX(points.size() - 1, points.size(), left, right) - 2.5,
                pointY(latest.value(), minimum, maximum, top, bottom) - 2.5, 5, 5);
        drawLabels(graphics, points, minimum, maximum, left, right, top, bottom, width, height);
    }

    private void drawLabels(GraphicsContext graphics, List<Sample> points, double minimum, double maximum,
                            double left, double right, double top, double bottom, double width, double height) {
        graphics.setFont(Font.font("Consolas", 8));
        graphics.setFill(Color.web("#6d9aa4"));
        graphics.setTextAlign(TextAlignment.LEFT);
        graphics.setTextBaseline(VPos.CENTER);
        graphics.fillText(axis(maximum), right + 4, top);
        graphics.fillText(axis((minimum + maximum) / 2), right + 4, (top + bottom) / 2);
        graphics.fillText(axis(minimum), right + 4, bottom);

        graphics.setTextBaseline(VPos.BOTTOM);
        graphics.setTextAlign(TextAlignment.LEFT);
        graphics.fillText(time(points.get(0).timestamp()), left, height);
        graphics.setTextAlign(TextAlignment.CENTER);
        graphics.fillText(time(points.get(points.size() / 2).timestamp()), (left + right) / 2, height);
        graphics.setTextAlign(TextAlignment.RIGHT);
        graphics.fillText(time(points.get(points.size() - 1).timestamp()), right, height);
    }

    private double minimumPadding() {
        return switch (format) {
            case PERCENT -> 0.00005;
            case BPS -> 0.05;
            case RELATIVE -> 0.01;
            case MONEY -> 0.5;
        };
    }

    private String axis(double number) {
        return switch (format) {
            case PERCENT -> String.format(Locale.US, "%+.2f%%", number * 100);
            case BPS -> String.format(Locale.US, "%.1f", number);
            case RELATIVE -> String.format(Locale.US, "%.2f", number);
            case MONEY -> String.format(Locale.US, "$%.0f", number);
        };
    }

    static String time(String timestamp) {
        if (timestamp == null || timestamp.isBlank()) return "--:--";
        try {
            return TIME.format(Instant.parse(timestamp));
        } catch (RuntimeException ignored) {
            return timestamp.length() >= 16 ? timestamp.substring(11, 16) : "--:--";
        }
    }

    private static double pointX(int index, int count, double left, double right) {
        return count == 1 ? right : left + (right - left) * index / (count - 1.0);
    }

    private static double pointY(double value, double minimum, double maximum, double top, double bottom) {
        return bottom - (value - minimum) / (maximum - minimum) * (bottom - top);
    }

    static String sessionDate(String timestamp) {
        return timestamp != null && timestamp.length() >= 10 ? timestamp.substring(0, 10) : "";
    }
}
