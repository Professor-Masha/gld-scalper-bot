package com.mashcorp.jarvis;

import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.layout.StackPane;
import javafx.scene.paint.Color;
import javafx.scene.text.TextAlignment;

import java.util.Locale;

/** Six-axis explainability radar derived only from measured values and explicit gate states. */
final class DecisionRadar extends StackPane {
    private static final String[] LABELS = {
            "TREND", "MOMENTUM", "PRICE ACTION", "MICROSTRUCTURE", "ML", "RISK"
    };
    private static final Color[] AXIS_COLORS = {
            Color.web("#5cf2b5"), Color.web("#62ef6f"), Color.web("#ffbf3f"),
            Color.web("#ff5470"), Color.web("#3f9cff"), Color.web("#ff5470")
    };
    private final Canvas canvas = new Canvas();
    private DecisionTelemetry frame;

    DecisionRadar() {
        getStyleClass().add("decision-radar");
        getChildren().add(canvas);
        setMaxHeight(720);
        widthProperty().addListener((ignored, before, after) -> resizeAndDraw());
        heightProperty().addListener((ignored, before, after) -> resizeAndDraw());
    }

    private void resizeAndDraw() {
        CanvasSurface.resize(canvas, getWidth(), getHeight());
        draw();
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
        double centerY = height * 0.51;
        double radius = Math.min(width, height) * 0.31;
        graphics.setTextAlign(TextAlignment.CENTER);
        graphics.setStroke(Color.web("#17414d"));
        graphics.setLineWidth(1);
        for (int ring = 1; ring <= 5; ring++) {
            double ringRadius = radius * ring / 5.0;
            graphics.strokeOval(centerX - ringRadius, centerY - ringRadius, ringRadius * 2, ringRadius * 2);
        }
        graphics.setStroke(Color.web("#2de1ff", 0.15));
        graphics.setLineWidth(12);
        graphics.strokeOval(centerX - radius, centerY - radius, radius * 2, radius * 2);
        graphics.setStroke(Color.web("#2de1ff"));
        graphics.setLineWidth(2.2);
        graphics.strokeOval(centerX - radius, centerY - radius, radius * 2, radius * 2);
        for (int axis = 0; axis < LABELS.length; axis++) {
            double angle = angle(axis);
            double x = centerX + Math.cos(angle) * radius;
            double y = centerY + Math.sin(angle) * radius;
            graphics.setStroke(Color.web("#286678"));
            graphics.setLineWidth(1);
            graphics.strokeLine(centerX, centerY, x, y);
        }
        if (frame == null) return;
        double[] values = {
                clamp(frame.ruleStrength()),
                momentumValue(frame),
                gateValue(frame.evidence().get(2).state()),
                clamp(frame.liquidityScore()),
                frame.classical().available() ? clamp(frame.classical().highestDirectional()) : 0.05,
                Math.min(gateValue(frame.evidence().get(0).state()), gateValue(frame.evidence().get(5).state()))
        };
        graphics.setFill(Color.web("#5cf2b5", 0.16));
        graphics.setStroke(Color.web("#5cf2b5"));
        graphics.setLineWidth(2.2);
        polygon(graphics, centerX, centerY, radius, values, true);
        for (int axis = 0; axis < LABELS.length; axis++) {
            double pointX = centerX + Math.cos(angle(axis)) * radius * values[axis];
            double pointY = centerY + Math.sin(angle(axis)) * radius * values[axis];
            graphics.setFill(AXIS_COLORS[axis]);
            graphics.fillOval(pointX - 5, pointY - 5, 10, 10);
            callout(graphics, centerX, centerY, radius, axis, values[axis], axisDetail(frame, axis));
        }
        graphics.setFill(Color.web("#020a0d", 0.94));
        graphics.fillOval(centerX - 58, centerY - 31, 116, 62);
        graphics.setStroke(Color.web("#285a64"));
        graphics.strokeOval(centerX - 58, centerY - 31, 116, 62);
        graphics.setFill(Color.web(decisionColor(frame.decision())));
        graphics.fillText(frame.decision().toUpperCase(Locale.ROOT), centerX, centerY - 2);
        graphics.setFill(Color.web("#78959c"));
        String state = frame.marketState().toUpperCase(Locale.ROOT);
        graphics.fillText(state, centerX, centerY + 16);
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

    private static void callout(GraphicsContext graphics, double centerX, double centerY,
                                double radius, int axis, double value, String detail) {
        double calloutX = centerX + Math.cos(angle(axis)) * radius * 1.38;
        double calloutY = centerY + Math.sin(angle(axis)) * radius * 1.38;
        double boxWidth = Math.max(84, Math.min(104, radius * 0.76));
        double boxHeight = 39;
        double x = calloutX - boxWidth / 2;
        double y = calloutY - boxHeight / 2;
        Color color = AXIS_COLORS[axis];
        graphics.setFill(Color.web("#020a0d", 0.96));
        graphics.fillRoundRect(x, y, boxWidth, boxHeight, 8, 8);
        graphics.setStroke(color);
        graphics.setLineWidth(1.3);
        graphics.strokeRoundRect(x, y, boxWidth, boxHeight, 8, 8);
        graphics.setFill(Color.web("#8bcbd5"));
        graphics.fillText(LABELS[axis], calloutX, y + 13);
        graphics.setFill(color);
        String reading = detail.equals("Unavailable") ? detail : detail + "  " + Math.round(value * 100);
        graphics.fillText(reading, calloutX, y + 30);
    }

    private static String axisDetail(DecisionTelemetry frame, int axis) {
        return switch (axis) {
            case 0 -> frame.decision().toLowerCase(Locale.ROOT).contains("short") ? "Bearish" : "Rule strength";
            case 1 -> !Double.isFinite(frame.expectedReturn()) || frame.expectedReturn() == 0
                    ? "Unavailable" : momentumValue(frame) >= 0.6 ? "Constructive" : "Neutral";
            case 2 -> "Evidence";
            case 3 -> frame.liquidityScore() >= 0.55 ? "Liquid" : "Weak";
            case 4 -> frame.classical().available() ? "Model" : "Unavailable";
            default -> frame.evidence().get(0).state() == DecisionTelemetry.EvidenceState.PASS
                    && frame.evidence().get(5).state() == DecisionTelemetry.EvidenceState.PASS
                    ? "Approved" : "Blocked";
        };
    }

    private static double momentumValue(DecisionTelemetry frame) {
        if (!Double.isFinite(frame.expectedReturn()) || frame.expectedReturn() == 0) {
            return 0.05;
        }
        return clamp(0.5 + frame.expectedReturn() * 50);
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
