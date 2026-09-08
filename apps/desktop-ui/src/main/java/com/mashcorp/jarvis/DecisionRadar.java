package com.mashcorp.jarvis;

import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.layout.StackPane;
import javafx.scene.paint.Color;

import java.util.Locale;

/** Six-axis explainability radar derived only from measured values and explicit gate states. */
final class DecisionRadar extends StackPane {
    private static final String[] LABELS = {
            "RULE STRENGTH", "PRICE ACTION", "LIQUIDITY", "CLASSICAL ML", "TRANSFORMER", "RISK GATE"
    };
    private final Canvas canvas = new Canvas();
    private DecisionTelemetry frame;

    DecisionRadar() {
        getStyleClass().add("decision-radar");
        getChildren().add(canvas);
        canvas.widthProperty().bind(widthProperty());
        canvas.heightProperty().bind(heightProperty());
        widthProperty().addListener((ignored, before, after) -> draw());
        heightProperty().addListener((ignored, before, after) -> draw());
    }

    void update(DecisionTelemetry value) {
        frame = value;
        draw();
    }

    private void draw() {
        double width = canvas.getWidth();
        double height = canvas.getHeight();
        if (width < 80 || height < 80) return;
        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.clearRect(0, 0, width, height);
        double centerX = width * 0.5;
        double centerY = height * 0.52;
        double radius = Math.min(width, height) * 0.34;
        graphics.setStroke(Color.web("#17414d"));
        graphics.setLineWidth(1);
        for (int ring = 1; ring <= 5; ring++) {
            polygon(graphics, centerX, centerY, radius * ring / 5.0, null, false);
        }
        for (int axis = 0; axis < LABELS.length; axis++) {
            double angle = angle(axis);
            double x = centerX + Math.cos(angle) * radius;
            double y = centerY + Math.sin(angle) * radius;
            graphics.strokeLine(centerX, centerY, x, y);
            graphics.setFill(Color.web("#73c9d8"));
            double labelX = centerX + Math.cos(angle) * (radius + 24);
            double labelY = centerY + Math.sin(angle) * (radius + 18);
            graphics.fillText(LABELS[axis], labelX - LABELS[axis].length() * 3.2, labelY + 4);
        }
        if (frame == null) return;
        double[] values = {
                clamp(frame.ruleStrength()),
                gateValue(frame.evidence().get(2).state()),
                clamp(frame.liquidityScore()),
                frame.classical().available() ? clamp(frame.classical().highestDirectional()) : 0.05,
                frame.transformer().available() ? clamp(frame.transformer().highestDirectional()) : 0.05,
                gateValue(frame.evidence().get(5).state())
        };
        graphics.setFill(Color.web("#5cf2b5", 0.16));
        graphics.setStroke(Color.web("#5cf2b5"));
        graphics.setLineWidth(2.2);
        polygon(graphics, centerX, centerY, radius, values, true);
        graphics.setFill(Color.web("#020a0d", 0.94));
        graphics.fillOval(centerX - 58, centerY - 31, 116, 62);
        graphics.setStroke(Color.web("#285a64"));
        graphics.strokeOval(centerX - 58, centerY - 31, 116, 62);
        graphics.setFill(Color.web(decisionColor(frame.decision())));
        graphics.fillText(frame.decision().toUpperCase(Locale.ROOT), centerX - frame.decision().length() * 3.6, centerY - 2);
        graphics.setFill(Color.web("#78959c"));
        String state = frame.marketState().toUpperCase(Locale.ROOT);
        graphics.fillText(state, centerX - state.length() * 3.2, centerY + 16);
    }

    private static void polygon(GraphicsContext graphics, double centerX, double centerY, double radius,
                                double[] values, boolean fill) {
        graphics.beginPath();
        for (int axis = 0; axis < LABELS.length; axis++) {
            double factor = values == null ? 1 : values[axis];
            double x = centerX + Math.cos(angle(axis)) * radius * factor;
            double y = centerY + Math.sin(angle(axis)) * radius * factor;
            if (axis == 0) graphics.moveTo(x, y); else graphics.lineTo(x, y);
        }
        graphics.closePath();
        if (fill) graphics.fill();
        graphics.stroke();
    }

    private static double angle(int axis) { return -Math.PI / 2 + axis * Math.PI * 2 / LABELS.length; }
    private static double clamp(double value) {
        return Double.isFinite(value) ? Math.max(0.05, Math.min(1, value)) : 0.05;
    }

    private static double gateValue(DecisionTelemetry.EvidenceState state) {
        return switch (state) {
            case PASS -> 1.0;
            case WARN -> 0.5;
            case BLOCKED -> 0.05;
            case UNAVAILABLE -> 0.05;
        };
    }

    private static String decisionColor(String decision) {
        String value = decision.toUpperCase(Locale.ROOT);
        if (value.contains("LONG") || value.contains("BUY")) return "#5cf2b5";
        if (value.contains("SHORT") || value.contains("SELL")) return "#ff5470";
        return "#ffbf69";
    }
}
