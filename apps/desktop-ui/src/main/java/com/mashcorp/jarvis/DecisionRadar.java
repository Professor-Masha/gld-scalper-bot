package com.mashcorp.jarvis;

import javafx.geometry.VPos;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.effect.DropShadow;
import javafx.scene.layout.StackPane;
import javafx.scene.paint.Color;
import javafx.scene.shape.Rectangle;
import javafx.scene.text.Font;
import javafx.scene.text.FontWeight;
import javafx.scene.text.TextAlignment;

import java.util.Locale;

/** Six-axis explainability radar derived only from measured values and explicit gate states. */
final class DecisionRadar extends StackPane {
    private static final double DESIGN_WIDTH = 497;
    private static final double DESIGN_HEIGHT = 463;
    private static final String[] LABELS = {
            "TREND", "MOMENTUM", "PRICE ACTION", "MICROSTRUCTURE", "ML", "RISK"
    };
    private static final Color[] AXIS_COLORS = {
            Color.web("#19f7a7"), Color.web("#62ef6f"), Color.web("#ffbf3f"),
            Color.web("#ff5470"), Color.web("#3198ff"), Color.web("#ff5470")
    };

    private final Canvas canvas = new Canvas();
    private DecisionTelemetry frame;

    DecisionRadar() {
        getStyleClass().add("decision-radar");
        canvas.setManaged(false);
        canvas.setMouseTransparent(true);
        getChildren().add(canvas);
        setMaxHeight(720);
        Rectangle clip = new Rectangle();
        clip.widthProperty().bind(widthProperty());
        clip.heightProperty().bind(heightProperty());
        setClip(clip);
        setOnScroll(event -> event.consume());
        setOnZoom(event -> event.consume());
        widthProperty().addListener((ignored, before, after) -> resizeAndDraw());
        heightProperty().addListener((ignored, before, after) -> resizeAndDraw());
    }

    private void resizeAndDraw() {
        canvas.relocate(0, 0);
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
        if (width < 180 || height < 180) return;

        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.clearRect(0, 0, width, height);
        graphics.setFill(Color.web("#02090c"));
        graphics.fillRect(0, 0, width, height);
        graphics.setTextAlign(TextAlignment.CENTER);
        graphics.setTextBaseline(VPos.CENTER);

        double scale = Math.max(0.62, Math.min(1.20,
                Math.min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT)));
        double centerX = width * 0.5;
        double centerY = height * 0.505;
        double radius = Math.min(width * 0.365, height * 0.38);

        drawGrid(graphics, centerX, centerY, radius, scale);
        if (frame == null) return;

        double[] values = values(frame);
        drawEvidenceArea(graphics, centerX, centerY, radius, values, scale);
        drawScale(graphics, centerX, centerY, radius, scale);
        drawCallouts(graphics, centerX, centerY, radius, width, height, values, scale);
        drawDecision(graphics, centerX, centerY, radius, scale);
        graphics.setEffect(null);
    }

    private static void drawGrid(GraphicsContext graphics, double centerX, double centerY,
                                 double radius, double scale) {
        graphics.setFill(Color.web("#06171d"));
        graphics.fillOval(centerX - radius, centerY - radius, radius * 2, radius * 2);

        for (int ring = 5; ring >= 1; ring--) {
            double ringRadius = radius * ring / 5.0;
            if (ring % 2 == 1) {
                graphics.setFill(Color.web("#0b3440", 0.13));
                graphics.fillOval(centerX - ringRadius, centerY - ringRadius,
                        ringRadius * 2, ringRadius * 2);
            }
            graphics.setStroke(Color.web("#176079", ring == 5 ? 0.82 : 0.72));
            graphics.setLineWidth(Math.max(0.75, scale));
            graphics.strokeOval(centerX - ringRadius, centerY - ringRadius,
                    ringRadius * 2, ringRadius * 2);
        }

        for (int axis = 0; axis < LABELS.length; axis++) {
            double outerX = centerX + Math.cos(angle(axis)) * radius;
            double outerY = centerY + Math.sin(angle(axis)) * radius;
            graphics.setStroke(Color.web("#5ba9bd", 0.72));
            graphics.setLineWidth(Math.max(0.7, scale * 0.9));
            graphics.strokeLine(centerX, centerY, outerX, outerY);
            graphics.setFill(Color.web("#b9edfa"));
            graphics.fillOval(outerX - 1.7 * scale, outerY - 1.7 * scale,
                    3.4 * scale, 3.4 * scale);
        }

        graphics.setEffect(new DropShadow(14 * scale, Color.web("#2de1ff", 0.72)));
        graphics.setStroke(Color.web("#2de1ff", 0.16));
        graphics.setLineWidth(14 * scale);
        graphics.strokeOval(centerX - radius, centerY - radius, radius * 2, radius * 2);
        graphics.setEffect(null);
        graphics.setStroke(Color.web("#22dff5"));
        graphics.setLineWidth(2.5 * scale);
        graphics.strokeOval(centerX - radius, centerY - radius, radius * 2, radius * 2);
        graphics.setStroke(Color.web("#2de1ff", 0.34));
        graphics.setLineWidth(Math.max(0.8, scale));
        graphics.strokeOval(centerX - radius * 0.94, centerY - radius * 0.94,
                radius * 1.88, radius * 1.88);
    }

    private static void drawEvidenceArea(GraphicsContext graphics, double centerX, double centerY,
                                         double radius, double[] values, double scale) {
        double[] xs = new double[values.length];
        double[] ys = new double[values.length];
        for (int axis = 0; axis < values.length; axis++) {
            xs[axis] = centerX + Math.cos(angle(axis)) * radius * values[axis];
            ys[axis] = centerY + Math.sin(angle(axis)) * radius * values[axis];
        }

        for (int axis = 0; axis < values.length; axis++) {
            int next = (axis + 1) % values.length;
            graphics.setFill(AXIS_COLORS[axis].deriveColor(0, 1, 1, 0.12));
            graphics.fillPolygon(new double[] {centerX, xs[axis], xs[next]},
                    new double[] {centerY, ys[axis], ys[next]}, 3);
        }

        for (int axis = 0; axis < values.length; axis++) {
            int next = (axis + 1) % values.length;
            graphics.setStroke(AXIS_COLORS[next]);
            graphics.setLineWidth(2.2 * scale);
            graphics.strokeLine(xs[axis], ys[axis], xs[next], ys[next]);
        }

        for (int axis = 0; axis < values.length; axis++) {
            graphics.setEffect(new DropShadow(8 * scale, AXIS_COLORS[axis].deriveColor(0, 1, 1, 0.72)));
            graphics.setFill(AXIS_COLORS[axis]);
            double marker = 10 * scale;
            graphics.fillOval(xs[axis] - marker / 2, ys[axis] - marker / 2, marker, marker);
        }
        graphics.setEffect(null);
    }

    private static void drawScale(GraphicsContext graphics, double centerX, double centerY,
                                  double radius, double scale) {
        graphics.setFont(font(10 * scale, FontWeight.NORMAL));
        graphics.setStroke(Color.web("#c5edf5", 0.86));
        graphics.setFill(Color.web("#c5edf5"));
        graphics.setLineWidth(Math.max(0.8, scale));
        graphics.setTextAlign(TextAlignment.LEFT);
        for (int ring = 1; ring <= 5; ring++) {
            double y = centerY - radius * ring / 5.0;
            graphics.strokeLine(centerX - 3 * scale, y, centerX + 3 * scale, y);
            double labelY = ring == 5 ? y + 12 * scale : y;
            graphics.fillText(Integer.toString(ring * 20), centerX + 7 * scale, labelY);
        }
        graphics.setTextAlign(TextAlignment.CENTER);
    }

    private void drawCallouts(GraphicsContext graphics, double centerX, double centerY,
                              double radius, double width, double height,
                              double[] values, double scale) {
        double boxWidth = Math.max(74, Math.min(116, width * 0.215));
        double boxHeight = Math.max(34, Math.min(50, height * 0.105));
        double inset = Math.max(3, 7 * scale);
        double upperY = centerY - radius * 0.58;
        double lowerY = centerY + radius * 0.58;
        double topY = 18 * scale;
        double bottomY = height - 27 * scale - boxHeight;
        double[][] boxes = {
                {centerX - boxWidth / 2, topY},
                {width - boxWidth - inset, upperY - boxHeight / 2},
                {width - boxWidth - inset, lowerY - boxHeight / 2},
                {centerX - boxWidth / 2, bottomY},
                {inset, lowerY - boxHeight / 2},
                {inset, upperY - boxHeight / 2}
        };

        for (int axis = 0; axis < LABELS.length; axis++) {
            double boxCenterX = boxes[axis][0] + boxWidth / 2;
            double boxCenterY = boxes[axis][1] + boxHeight / 2;
            double outerX = centerX + Math.cos(angle(axis)) * radius;
            double outerY = centerY + Math.sin(angle(axis)) * radius;
            graphics.setStroke(Color.web("#95d9e7", 0.82));
            graphics.setLineWidth(Math.max(0.75, scale));
            graphics.strokeLine(outerX, outerY, boxCenterX, boxCenterY);
        }

        for (int axis = 0; axis < LABELS.length; axis++) {
            drawCallout(graphics, axis, boxes[axis][0], boxes[axis][1],
                    boxWidth, boxHeight, reading(frame, axis), values[axis], scale);
        }
    }

    private static void drawCallout(GraphicsContext graphics, int axis,
                                    double x, double y, double width, double height,
                                    AxisReading reading, double value, double scale) {
        Color color = AXIS_COLORS[axis];
        graphics.setFill(Color.web("#020a0d", 0.97));
        graphics.fillRoundRect(x, y, width, height, 7 * scale, 7 * scale);
        graphics.setEffect(new DropShadow(8 * scale, color.deriveColor(0, 1, 1, 0.28)));
        graphics.setStroke(color);
        graphics.setLineWidth(1.5 * scale);
        graphics.strokeRoundRect(x, y, width, height, 7 * scale, 7 * scale);
        graphics.setEffect(null);

        double titleY = axis == 3 ? y + height + 14 * scale : y - 10 * scale;
        graphics.setFont(font(12 * scale, FontWeight.BOLD));
        graphics.setFill(Color.web("#65d8eb"));
        graphics.fillText(LABELS[axis], x + width / 2, titleY);

        graphics.setFill(color);
        graphics.setFont(font(10.5 * scale, FontWeight.NORMAL));
        boolean secondLine = !reading.secondLine().isBlank();
        if (reading.showScore()) {
            graphics.fillText(reading.firstLine(), x + width / 2,
                    y + height * (secondLine ? 0.22 : 0.30));
            if (secondLine) {
                graphics.fillText(reading.secondLine(), x + width / 2, y + height * 0.47);
            }
            graphics.setFont(font(14 * scale, FontWeight.BOLD));
            graphics.fillText(Integer.toString((int) Math.round(value * 100)),
                    x + width / 2, y + height * 0.76);
        } else {
            graphics.fillText(reading.firstLine(), x + width / 2,
                    y + height * (secondLine ? 0.36 : 0.50));
            if (secondLine) {
                graphics.fillText(reading.secondLine(), x + width / 2, y + height * 0.68);
            }
        }
    }

    private void drawDecision(GraphicsContext graphics, double centerX, double centerY,
                              double radius, double scale) {
        double badgeRadius = Math.max(31 * scale, radius * 0.27);
        graphics.setEffect(new DropShadow(16 * scale, Color.web("#2de1ff", 0.28)));
        graphics.setFill(Color.web("#020a0d", 0.97));
        graphics.fillOval(centerX - badgeRadius, centerY - badgeRadius,
                badgeRadius * 2, badgeRadius * 2);
        graphics.setEffect(null);
        graphics.setStroke(Color.web("#2b6675"));
        graphics.setLineWidth(1.4 * scale);
        graphics.strokeOval(centerX - badgeRadius, centerY - badgeRadius,
                badgeRadius * 2, badgeRadius * 2);

        graphics.setFont(font(13 * scale, FontWeight.BOLD));
        graphics.setFill(Color.web(decisionColor(frame.decision())));
        graphics.fillText(frame.decision().toUpperCase(Locale.ROOT), centerX, centerY - 7 * scale);
        graphics.setFont(font(10.5 * scale, FontWeight.NORMAL));
        graphics.setFill(Color.web("#d6eff4"));
        graphics.fillText(centerReason(frame), centerX, centerY + 10 * scale);
    }

    private static double[] values(DecisionTelemetry frame) {
        return new double[] {
                clamp(frame.ruleStrength()),
                momentumValue(frame),
                gateValue(frame.evidence().get(2).state()),
                clamp(frame.liquidityScore()),
                frame.classical().available() ? clamp(frame.classical().highestDirectional()) : 0.05,
                Math.min(gateValue(frame.evidence().get(0).state()),
                        gateValue(frame.evidence().get(5).state()))
        };
    }

    private static AxisReading reading(DecisionTelemetry frame, int axis) {
        return switch (axis) {
            case 0 -> trendReading(frame);
            case 1 -> momentumReading(frame);
            case 2 -> stateReading("Price action", frame.evidence().get(2).state());
            case 3 -> new AxisReading(frame.liquidityScore() >= 0.55 ? "Liquidity good" : "Liquidity weak", "", true);
            case 4 -> modelReading(frame.classical());
            default -> riskReading(frame);
        };
    }

    private static AxisReading trendReading(DecisionTelemetry frame) {
        String regime = frame.regime().toLowerCase(Locale.ROOT);
        if (regime.contains("bear")) return new AxisReading("Trend bearish", "", true);
        if (regime.contains("bull") || regime.contains("trend")) {
            return new AxisReading("Trend bullish", "", true);
        }
        return new AxisReading("Rule strength", "", true);
    }

    private static AxisReading momentumReading(DecisionTelemetry frame) {
        if (!Double.isFinite(frame.expectedReturn()) || frame.expectedReturn() == 0) {
            return new AxisReading("Unavailable", "", false);
        }
        if (momentumValue(frame) >= 0.60) return new AxisReading("Momentum", "constructive", true);
        if (momentumValue(frame) >= 0.40) return new AxisReading("Momentum", "neutral", true);
        return new AxisReading("Momentum weak", "", true);
    }

    private static AxisReading stateReading(String subject, DecisionTelemetry.EvidenceState state) {
        String detail = switch (state) {
            case PASS -> "confirmed";
            case WARN -> "neutral";
            case BLOCKED -> "blocked";
            case UNAVAILABLE -> "unavailable";
        };
        return new AxisReading(subject, detail, state != DecisionTelemetry.EvidenceState.UNAVAILABLE);
    }

    private static AxisReading modelReading(DecisionTelemetry.Prediction model) {
        if (!model.available()) return new AxisReading("Unavailable", "", false);
        if (model.longProbability() >= model.shortProbability()) {
            return new AxisReading("ML long", "", true);
        }
        return new AxisReading("ML short", "", true);
    }

    private static AxisReading riskReading(DecisionTelemetry frame) {
        DecisionTelemetry.EvidenceState market = frame.evidence().get(0).state();
        DecisionTelemetry.EvidenceState risk = frame.evidence().get(5).state();
        if (market == DecisionTelemetry.EvidenceState.BLOCKED || risk == DecisionTelemetry.EvidenceState.BLOCKED) {
            return new AxisReading("Risk blocked", "", true);
        }
        if (market == DecisionTelemetry.EvidenceState.UNAVAILABLE || risk == DecisionTelemetry.EvidenceState.UNAVAILABLE) {
            return new AxisReading("Unavailable", "", false);
        }
        if (market == DecisionTelemetry.EvidenceState.PASS && risk == DecisionTelemetry.EvidenceState.PASS) {
            return new AxisReading("Risk approved", "", true);
        }
        return new AxisReading("Risk review", "", true);
    }

    private static String centerReason(DecisionTelemetry frame) {
        String market = frame.marketState();
        if (market == null || market.isBlank()) return "AWAITING EVIDENCE";
        String upper = market.toUpperCase(Locale.ROOT).replace('_', ' ');
        return upper.length() <= 22 ? upper : upper.substring(0, 22);
    }

    private static double momentumValue(DecisionTelemetry frame) {
        if (!Double.isFinite(frame.expectedReturn()) || frame.expectedReturn() == 0) return 0.05;
        return clamp(0.5 + frame.expectedReturn() * 50);
    }

    private static double angle(int axis) {
        return -Math.PI / 2 + axis * Math.PI * 2 / LABELS.length;
    }

    private static double clamp(double value) {
        return Double.isFinite(value) ? Math.max(0.05, Math.min(1, value)) : 0.05;
    }

    static double gateValue(DecisionTelemetry.EvidenceState state) {
        return switch (state) {
            case PASS -> 0.80;
            case WARN -> 0.48;
            case BLOCKED -> 0.20;
            case UNAVAILABLE -> 0.05;
        };
    }

    private static Font font(double size, FontWeight weight) {
        return Font.font("Consolas", weight, Math.max(7, size));
    }

    private static String decisionColor(String decision) {
        String value = decision.toUpperCase(Locale.ROOT);
        if (value.contains("LONG") || value.contains("BUY")) return "#5cf2b5";
        if (value.contains("SHORT") || value.contains("SELL")) return "#ff5470";
        return "#ff657f";
    }

    private record AxisReading(String firstLine, String secondLine, boolean showScore) { }
}
