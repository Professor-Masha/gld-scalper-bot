package com.mashcorp.jarvis;

import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.layout.StackPane;
import javafx.scene.paint.Color;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

/** Lightweight live quote plot; rendering is presentation-only and bounded in memory. */
final class DecisionPricePlot extends StackPane {
    private final Canvas canvas = new Canvas();
    private final Deque<Double> prices = new ArrayDeque<>();
    private String latestTimestamp = "";
    private DecisionTelemetry frame;

    DecisionPricePlot() {
        getStyleClass().add("decision-price-plot");
        getChildren().add(canvas);
        setMaxHeight(720);
        widthProperty().addListener((ignored, before, after) -> resizeAndDraw());
        heightProperty().addListener((ignored, before, after) -> resizeAndDraw());
    }

    private void resizeAndDraw() {
        CanvasSurface.resize(canvas, getWidth(), getHeight());
        draw();
    }

    void update(DecisionTelemetry next) {
        frame = next;
        if (next.midpoint() > 0 && !next.quoteTimestamp().equals(latestTimestamp)) {
            prices.addLast(next.midpoint());
            while (prices.size() > 160) prices.removeFirst();
            latestTimestamp = next.quoteTimestamp();
        }
        draw();
    }

    private void draw() {
        double width = canvas.getWidth();
        double height = canvas.getHeight();
        if (width < 40 || height < 40) return;
        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.clearRect(0, 0, width, height);
        graphics.setFill(Color.web("#02090c"));
        graphics.fillRect(0, 0, width, height);
        graphics.setStroke(Color.web("#12323b"));
        graphics.setLineWidth(0.8);
        for (int index = 1; index < 5; index++) {
            double x = width * index / 5.0;
            double y = height * index / 5.0;
            graphics.strokeLine(x, 0, x, height);
            graphics.strokeLine(0, y, width, y);
        }
        if (frame == null || prices.isEmpty()) {
            graphics.setFill(Color.web("#57727a"));
            graphics.fillText("Waiting for live GLD snapshots", 18, height / 2);
            return;
        }
        List<Double> values = new ArrayList<>(prices);
        double minimum = values.stream().mapToDouble(Double::doubleValue).min().orElse(frame.midpoint());
        double maximum = values.stream().mapToDouble(Double::doubleValue).max().orElse(frame.midpoint());
        if (frame.activeEpisode() != null && frame.activeEpisode().entryPrice() > 0) {
            minimum = Math.min(minimum, frame.activeEpisode().entryPrice());
            maximum = Math.max(maximum, frame.activeEpisode().entryPrice());
        }
        double padding = Math.max((maximum - minimum) * 0.16, Math.max(frame.midpoint() * 0.00025, 0.02));
        minimum -= padding;
        maximum += padding;
        double left = 12;
        double right = width - 68;
        double top = 14;
        double bottom = height - 22;
        if (frame.bid() > 0 && frame.ask() > 0) {
            double askY = y(frame.ask(), minimum, maximum, top, bottom);
            double bidY = y(frame.bid(), minimum, maximum, top, bottom);
            graphics.setFill(Color.web("#2de1ff", 0.08));
            graphics.fillRect(left, Math.min(askY, bidY), right - left, Math.max(1, Math.abs(askY - bidY)));
        }
        graphics.setStroke(Color.web("#2de1ff"));
        graphics.setLineWidth(2.0);
        graphics.beginPath();
        for (int index = 0; index < values.size(); index++) {
            double x = values.size() == 1 ? right : left + (right - left) * index / (values.size() - 1.0);
            double y = y(values.get(index), minimum, maximum, top, bottom);
            if (index == 0) graphics.moveTo(x, y); else graphics.lineTo(x, y);
        }
        graphics.stroke();
        if (frame.activeEpisode() != null && frame.activeEpisode().entryPrice() > 0) {
            horizontal(graphics, frame.activeEpisode().entryPrice(), minimum, maximum, top, bottom,
                    width, "ENTRY " + String.format(Locale.US, "%.2f", frame.activeEpisode().entryPrice()), Color.web("#ffbf69"));
        } else if (frame.latestOutcome() != null && frame.latestOutcome().entryPrice() > 0) {
            horizontal(graphics, frame.latestOutcome().entryPrice(), minimum, maximum, top, bottom,
                    width, "LAST ENTRY", Color.web("#3f7cff"));
        }
        double currentY = y(frame.midpoint(), minimum, maximum, top, bottom);
        graphics.setFill(Color.web("#5cf2b5"));
        graphics.fillOval(right - 4, currentY - 4, 8, 8);
        graphics.fillText(String.format(Locale.US, "%.2f", frame.midpoint()), right + 8, currentY + 4);
        graphics.setFill(Color.web("#63838b"));
        graphics.fillText("HISTORY " + values.size() + " OF 160 SAMPLES", left, height - 6);
    }

    private static void horizontal(GraphicsContext graphics, double value, double minimum, double maximum,
                                   double top, double bottom, double width, String label, Color color) {
        double y = y(value, minimum, maximum, top, bottom);
        graphics.setStroke(color);
        graphics.setLineDashes(5, 5);
        graphics.strokeLine(12, y, width - 68, y);
        graphics.setLineDashes();
        graphics.setFill(color);
        graphics.fillText(label, 16, y - 5);
    }

    private static double y(double value, double minimum, double maximum, double top, double bottom) {
        return bottom - ((value - minimum) / Math.max(0.000001, maximum - minimum)) * (bottom - top);
    }
}
