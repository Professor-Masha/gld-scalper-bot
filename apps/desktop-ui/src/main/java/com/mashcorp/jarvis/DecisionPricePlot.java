package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.geometry.VPos;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.layout.StackPane;
import javafx.scene.paint.Color;
import javafx.scene.shape.Rectangle;
import javafx.scene.text.TextAlignment;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

/** Fixed-viewport live GLD chart; rendering is read-only and bounded in memory. */
final class DecisionPricePlot extends StackPane {
    private static final int MAX_SAMPLES = 160;
    private static final ZoneId MARKET_ZONE = ZoneId.of("America/New_York");
    private static final DateTimeFormatter CLOCK = DateTimeFormatter.ofPattern("HH:mm")
            .withLocale(Locale.US).withZone(MARKET_ZONE);
    private final Canvas canvas = new Canvas();
    private final Deque<Sample> samples = new ArrayDeque<>();
    private String latestTimestamp = "";
    private String sessionKey = "";
    private double scaleMinimum = Double.NaN;
    private double scaleMaximum = Double.NaN;
    private DecisionTelemetry frame;

    DecisionPricePlot() {
        getStyleClass().add("decision-price-plot");
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

    void update(DecisionTelemetry next) {
        frame = next;
        String nextSession = session(next.quoteTimestamp());
        if (!sessionKey.isBlank() && !nextSession.isBlank() && !sessionKey.equals(nextSession)) {
            samples.clear();
            latestTimestamp = "";
            scaleMinimum = Double.NaN;
            scaleMaximum = Double.NaN;
        }
        if (!nextSession.isBlank()) sessionKey = nextSession;
        if (next.midpoint() > 0 && !next.quoteTimestamp().equals(latestTimestamp)) {
            samples.addLast(new Sample(next.midpoint(), next.quoteTimestamp()));
            while (samples.size() > MAX_SAMPLES) samples.removeFirst();
            latestTimestamp = next.quoteTimestamp();
        }
        draw();
    }

    double sessionReturn() {
        if (frame == null || samples.isEmpty() || samples.getFirst().price() <= 0) return Double.NaN;
        return frame.midpoint() / samples.getFirst().price() - 1.0;
    }

    double sessionChange() {
        if (frame == null || samples.isEmpty()) return Double.NaN;
        return frame.midpoint() - samples.getFirst().price();
    }

    private void draw() {
        double width = canvas.getWidth();
        double height = canvas.getHeight();
        if (width < 120 || height < 100) return;
        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.clearRect(0, 0, width, height);
        graphics.setFill(Color.web("#02090c"));
        graphics.fillRect(0, 0, width, height);

        double left = 54;
        double right = width - 12;
        double top = 14;
        double bottom = height - 30;
        List<Sample> values = new ArrayList<>(samples);
        updateScale(values);
        drawGrid(graphics, left, right, top, bottom);
        if (frame == null || values.isEmpty() || !Double.isFinite(scaleMinimum) || !Double.isFinite(scaleMaximum)) {
            graphics.setFill(Color.web("#57727a"));
            graphics.setTextAlign(TextAlignment.CENTER);
            graphics.fillText("WAITING FOR LIVE GLD SNAPSHOTS", width / 2, height / 2);
            return;
        }

        drawAxisLabels(graphics, values, left, right, top, bottom);
        drawRangeBand(graphics, values, left, right, top, bottom);
        drawLine(graphics, values, left, right, top, bottom);

        double target = episodeLevel("take_profit_price", "target_price", "expected_take_profit_price");
        double stop = episodeLevel("current_stop_price", "stop_price", "stop_loss_price", "initial_stop_price", "expected_stop_price");
        if (target > 0) horizontal(graphics, target, left, right, top, bottom,
                "TARGET  " + price(target), Color.web("#19f7a7"));
        if (frame.activeEpisode() != null && frame.activeEpisode().entryPrice() > 0) {
            double entry = frame.activeEpisode().entryPrice();
            horizontal(graphics, entry, left, right, top, bottom,
                    "ENTRY  " + price(entry), Color.web("#ffbf3f"));
        } else if (frame.latestOutcome() != null && frame.latestOutcome().entryPrice() > 0) {
            horizontal(graphics, frame.latestOutcome().entryPrice(), left, right, top, bottom,
                    "LAST ENTRY", Color.web("#3f7cff"));
        }
        if (stop > 0) horizontal(graphics, stop, left, right, top, bottom,
                "STOP  " + price(stop), Color.web("#ff5470"));

        double currentY = y(frame.midpoint(), top, bottom);
        graphics.setStroke(Color.web("#2de1ff"));
        graphics.setLineDashes(4, 4);
        graphics.strokeLine(left, currentY, right, currentY);
        graphics.setLineDashes();
        drawTag(graphics, 4, currentY, price(frame.midpoint()), Color.web("#2de1ff"), 48);
        graphics.setFill(Color.web("#2de1ff"));
        graphics.fillOval(right - 6, currentY - 6, 12, 12);

        if (Double.isFinite(frame.spreadPct())) {
            String spread = String.format(Locale.US, "SPREAD %.2f%%", frame.spreadPct() * 100);
            graphics.setFill(Color.web("#7aaabc"));
            graphics.setTextAlign(TextAlignment.RIGHT);
            graphics.fillText(spread, right, bottom - 8);
        }
        graphics.setTextAlign(TextAlignment.LEFT);
        graphics.setFill(Color.web("#63838b"));
        graphics.fillText("LIVE HISTORY  " + values.size() + " / " + MAX_SAMPLES, left, height - 7);
    }

    private void updateScale(List<Sample> values) {
        if (frame == null || values.isEmpty()) return;
        double minimum = values.stream().mapToDouble(Sample::price).min().orElse(frame.midpoint());
        double maximum = values.stream().mapToDouble(Sample::price).max().orElse(frame.midpoint());
        if (frame.activeEpisode() != null && frame.activeEpisode().entryPrice() > 0) {
            minimum = Math.min(minimum, frame.activeEpisode().entryPrice());
            maximum = Math.max(maximum, frame.activeEpisode().entryPrice());
        }
        for (double level : new double[] {
                episodeLevel("take_profit_price", "target_price", "expected_take_profit_price"),
                episodeLevel("current_stop_price", "stop_price", "stop_loss_price", "initial_stop_price", "expected_stop_price")}) {
            if (level > 0) {
                minimum = Math.min(minimum, level);
                maximum = Math.max(maximum, level);
            }
        }
        double minimumSpan = Math.max(frame.midpoint() * 0.003, 0.20);
        double center = (minimum + maximum) / 2.0;
        double span = Math.max(maximum - minimum, minimumSpan);
        double desiredMinimum = center - span * 0.62;
        double desiredMaximum = center + span * 0.62;
        double[] scale = nextScale(scaleMinimum, scaleMaximum, desiredMinimum, desiredMaximum);
        scaleMinimum = scale[0];
        scaleMaximum = scale[1];
    }

    static double[] nextScale(double currentMinimum, double currentMaximum,
                              double desiredMinimum, double desiredMaximum) {
        if (!Double.isFinite(desiredMinimum) || !Double.isFinite(desiredMaximum) || desiredMaximum <= desiredMinimum) {
            return new double[] {currentMinimum, currentMaximum};
        }
        if (!Double.isFinite(currentMinimum) || !Double.isFinite(currentMaximum) || currentMaximum <= currentMinimum) {
            return new double[] {desiredMinimum, desiredMaximum};
        }
        return new double[] {Math.min(currentMinimum, desiredMinimum), Math.max(currentMaximum, desiredMaximum)};
    }

    private void drawGrid(GraphicsContext graphics, double left, double right, double top, double bottom) {
        graphics.setStroke(Color.web("#123a45"));
        graphics.setLineWidth(0.8);
        for (int index = 0; index <= 4; index++) {
            double x = left + (right - left) * index / 4.0;
            double y = top + (bottom - top) * index / 4.0;
            graphics.strokeLine(x, top, x, bottom);
            graphics.strokeLine(left, y, right, y);
        }
    }

    private void drawAxisLabels(GraphicsContext graphics, List<Sample> values,
                                double left, double right, double top, double bottom) {
        graphics.setFill(Color.web("#79b7c6"));
        graphics.setTextBaseline(VPos.CENTER);
        graphics.setTextAlign(TextAlignment.RIGHT);
        for (int index = 0; index <= 4; index++) {
            double value = scaleMaximum - (scaleMaximum - scaleMinimum) * index / 4.0;
            double labelY = top + (bottom - top) * index / 4.0;
            graphics.fillText(price(value), left - 7, labelY);
        }
        graphics.setTextBaseline(VPos.BASELINE);
        graphics.setTextAlign(TextAlignment.CENTER);
        int labels = Math.min(4, values.size());
        for (int index = 0; index < labels; index++) {
            int sampleIndex = labels == 1 ? 0 : (int) Math.round((values.size() - 1.0) * index / (labels - 1.0));
            double x = labels == 1 ? right : left + (right - left) * sampleIndex / Math.max(1, values.size() - 1.0);
            graphics.fillText(clock(values.get(sampleIndex).timestamp()), x, bottom + 17);
        }
    }

    private void drawRangeBand(GraphicsContext graphics, List<Sample> values,
                               double left, double right, double top, double bottom) {
        if (values.size() < 5) return;
        double mean = values.stream().mapToDouble(Sample::price).average().orElse(0);
        double variance = values.stream().mapToDouble(sample -> Math.pow(sample.price() - mean, 2)).average().orElse(0);
        double deviation = Math.sqrt(Math.max(0, variance));
        double upper = y(mean + deviation * 2, top, bottom);
        double lower = y(mean - deviation * 2, top, bottom);
        graphics.setFill(Color.web("#2de1ff", 0.06));
        graphics.fillRect(left, Math.min(upper, lower), right - left, Math.abs(lower - upper));
        graphics.setStroke(Color.web("#2de1ff", 0.24));
        graphics.strokeLine(left, upper, right, upper);
        graphics.strokeLine(left, lower, right, lower);
    }

    private void drawLine(GraphicsContext graphics, List<Sample> values,
                          double left, double right, double top, double bottom) {
        graphics.setStroke(Color.web("#2de1ff"));
        graphics.setLineWidth(2.0);
        graphics.beginPath();
        for (int index = 0; index < values.size(); index++) {
            double x = values.size() == 1 ? right : left + (right - left) * index / (values.size() - 1.0);
            double sampleY = y(values.get(index).price(), top, bottom);
            if (index == 0) graphics.moveTo(x, sampleY); else graphics.lineTo(x, sampleY);
        }
        graphics.stroke();
    }

    private void horizontal(GraphicsContext graphics, double value, double left, double right,
                            double top, double bottom, String label, Color color) {
        double lineY = y(value, top, bottom);
        graphics.setStroke(color);
        graphics.setLineDashes(5, 5);
        graphics.strokeLine(left, lineY, right, lineY);
        graphics.setLineDashes();
        drawTag(graphics, 4, lineY, label, color, 91);
    }

    private static void drawTag(GraphicsContext graphics, double x, double centerY,
                                String label, Color color, double width) {
        double tagY = centerY - 9;
        graphics.setFill(Color.web("#061116", 0.97));
        graphics.fillRoundRect(x, tagY, width, 18, 3, 3);
        graphics.setStroke(color);
        graphics.strokeRoundRect(x, tagY, width, 18, 3, 3);
        graphics.setFill(color);
        graphics.setTextAlign(TextAlignment.LEFT);
        graphics.setTextBaseline(VPos.CENTER);
        graphics.fillText(label, x + 5, centerY);
        graphics.setTextBaseline(VPos.BASELINE);
    }

    private double y(double value, double top, double bottom) {
        return bottom - ((value - scaleMinimum) / Math.max(0.000001, scaleMaximum - scaleMinimum)) * (bottom - top);
    }

    private double episodeLevel(String... names) {
        if (frame == null || frame.activeEpisode() == null) return 0;
        JsonNode details = frame.activeEpisode().details();
        if (details == null || details.isMissingNode() || details.isNull()) return 0;
        for (String name : names) {
            JsonNode value = details.findValue(name);
            if (value != null && value.isNumber() && value.asDouble() > 0 && Double.isFinite(value.asDouble())) {
                return value.asDouble();
            }
        }
        return 0;
    }

    private static String session(String timestamp) {
        if (timestamp == null || timestamp.isBlank()) return "";
        try {
            return Instant.parse(timestamp).atZone(MARKET_ZONE).toLocalDate().toString();
        } catch (Exception ignored) {
            return "";
        }
    }

    private static String clock(String timestamp) {
        if (timestamp == null || timestamp.isBlank()) return "--:--";
        try {
            return CLOCK.format(Instant.parse(timestamp));
        } catch (Exception ignored) {
            return "--:--";
        }
    }

    private static String price(double value) {
        return String.format(Locale.US, "%.2f", value);
    }

    private record Sample(double price, String timestamp) { }
}
