package com.mashcorp.jarvis;

import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.control.Label;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;

import java.util.ArrayDeque;
import java.util.Deque;

/** Bounded, presentation-only telemetry sparkline used by Market Pulse. */
final class PulseSparkline extends VBox {
    private final Canvas canvas = new Canvas();
    private final Label value = new Label("--");
    private final Deque<Double> samples = new ArrayDeque<>();
    private final Color color;

    PulseSparkline(String title, Color color) {
        this.color = color;
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

    void update(double next, String display) {
        value.setText(display);
        value.setTextFill(color);
        if (Double.isFinite(next)) {
            samples.addLast(next);
            while (samples.size() > 160) samples.removeFirst();
        }
        draw();
    }

    private void resizeAndDraw() {
        CanvasSurface.resize(canvas, getWidth(), Math.max(1, getHeight() - 28));
        draw();
    }

    private void draw() {
        double width = canvas.getWidth();
        double height = canvas.getHeight();
        if (width < 40 || height < 30) return;
        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.clearRect(0, 0, width, height);
        graphics.setFill(Color.web("#02090c"));
        graphics.fillRect(0, 0, width, height);
        graphics.setStroke(Color.web("#12323b"));
        graphics.setLineWidth(0.7);
        for (int index = 1; index < 4; index++) {
            graphics.strokeLine(0, height * index / 4.0, width, height * index / 4.0);
            graphics.strokeLine(width * index / 4.0, 0, width * index / 4.0, height);
        }
        if (samples.isEmpty()) return;
        double minimum = samples.stream().mapToDouble(Double::doubleValue).min().orElse(0);
        double maximum = samples.stream().mapToDouble(Double::doubleValue).max().orElse(0);
        double padding = Math.max((maximum - minimum) * 0.15, 0.000001);
        minimum -= padding;
        maximum += padding;
        graphics.setStroke(color);
        graphics.setLineWidth(1.7);
        graphics.beginPath();
        int index = 0;
        for (double sample : samples) {
            double x = samples.size() == 1 ? width - 3 : 3 + (width - 6) * index / (samples.size() - 1.0);
            double y = height - 3 - (sample - minimum) / (maximum - minimum) * (height - 6);
            if (index++ == 0) graphics.moveTo(x, y); else graphics.lineTo(x, y);
        }
        graphics.stroke();
    }
}
