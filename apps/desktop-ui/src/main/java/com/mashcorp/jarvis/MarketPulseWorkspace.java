package com.mashcorp.jarvis;

import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Label;
import javafx.scene.control.OverrunStyle;
import javafx.scene.layout.ColumnConstraints;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.RowConstraints;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.shape.Circle;
import javafx.scene.shape.Line;
import javafx.scene.shape.Rectangle;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Locale;

/** Full read-only Market Pulse composition backed only by live gateway frames. */
final class MarketPulseWorkspace extends VBox {
    private final Instant openedAt = Instant.now();
    private final DecisionRadar radar = new DecisionRadar();
    private final Label clock = text("Waiting for telemetry");
    private final Label liveState = text("OFFLINE");
    private final Label pipelineState = text("DATA -> EVIDENCE -> DECISION");
    private final Circle liveDot = new Circle(5, Color.web("#607780"));
    private final Circle pipelineDot = new Circle(4, Color.web("#607780"));
    private final Label uptime = text("00:00:00");
    private final Label updateRate = text("-- / min");
    private final Label latency = text("--");
    private final Label price = value("--");
    private final Label change = value("--");
    private final Label bid = text("--");
    private final Label ask = text("--");
    private final Label spread = text("--");
    private final Label volume = text("Unavailable");
    private final Label session = text("Unavailable");
    private final Label regime = text("Unavailable");
    private final Label volatility = text("Unavailable");
    private final Label liquidity = text("Unavailable");
    private final Label news = text("Unavailable");
    private final Label decision = value("NO DATA");
    private final Label decisionReason = text("Waiting for live evidence");
    private final Node noTradeIcon = noTradeIcon();
    private final GridPane reasonGrid = tableGrid(47, 53);
    private final GridPane eventTable = tableGrid(24, 18, 48, 10);
    private final Deque<PulseEvent> events = new ArrayDeque<>();
    private final PulseSparkline returnChart = new PulseSparkline("GLD RETURN (SESSION)", Color.web("#5cf2b5"));
    private final PulseSparkline spreadChart = new PulseSparkline("SPREAD (BPS)", Color.web("#3f9cff"));
    private final PulseSparkline liquidityChart = new PulseSparkline("LIQUIDITY (RELATIVE)", Color.web("#ffbf3f"));
    private final PulseSparkline pnlChart = new PulseSparkline("CUMULATIVE NET P/L", Color.web("#ff5470"));
    private double openingMidpoint;
    private long frameCount;
    private String lastQuoteTimestamp = "";
    private String lastDecisionSignature = "";

    MarketPulseWorkspace() {
        getStyleClass().add("market-pulse-workspace");
        setSpacing(8);

        Label heading = title("MARKET PULSE + DECISION RADAR");
        heading.getStyleClass().add("pulse-main-title");
        Region titleDivider = new Region();
        titleDivider.getStyleClass().add("pulse-title-divider");
        HBox titleLine = new HBox(12, heading, titleDivider);
        titleLine.setAlignment(Pos.CENTER_LEFT);
        Label subtitle = text("GLD  SPDR Gold Shares   |   Live Decision-Making Dashboard");
        subtitle.getStyleClass().add("pulse-subtitle");
        VBox identity = new VBox(1, titleLine, subtitle);
        identity.setMinWidth(0);
        liveState.getStyleClass().add("pulse-live-state");
        pipelineState.getStyleClass().add("pulse-pipeline-state");
        HBox statusLine = new HBox(9, clock, liveDot, liveState);
        statusLine.setAlignment(Pos.CENTER_LEFT);
        HBox pipelineLine = new HBox(7, pipelineDot, pipelineState);
        pipelineLine.setAlignment(Pos.CENTER_LEFT);
        VBox center = new VBox(2, statusLine, pipelineLine);
        center.setAlignment(Pos.CENTER_LEFT);
        center.getStyleClass().add("pulse-header-status");
        GridPane service = tableGrid(56, 44);
        service.getStyleClass().add("pulse-service");
        addRow(service, 0, "Uptime", uptime);
        addRow(service, 1, "Update rate", updateRate);
        addRow(service, 2, "Decision latency", latency);
        GridPane header = tableGrid(50, 32, 18);
        header.setHgap(12);
        header.addRow(0, identity, center, service);
        header.getStyleClass().add("pulse-header");

        HBox quoteLine = new HBox(12, price, change);
        quoteLine.setAlignment(Pos.BASELINE_LEFT);
        quoteLine.getStyleClass().add("market-quote-line");
        change.getStyleClass().add("market-quote-change");
        GridPane quoteGrid = tableGrid(36, 64);
        addRow(quoteGrid, 0, "Bid", bid);
        addRow(quoteGrid, 1, "Ask", ask);
        addRow(quoteGrid, 2, "Spread", spread);
        addRow(quoteGrid, 3, "Volume", volume);
        VBox quoteCard = card("GLD", quoteLine, quoteGrid);
        quoteCard.getStyleClass().add("market-quote-card");

        GridPane context = tableGrid(47, 53);
        addRow(context, 0, "Session", session);
        addRow(context, 1, "Regime", regime);
        addRow(context, 2, "Volatility", volatility);
        addRow(context, 3, "Liquidity", liquidity);
        addRow(context, 4, "News", news);
        VBox contextCard = card("MARKET CONTEXT", context);
        contextCard.getStyleClass().add("market-context-card");

        decision.getStyleClass().add("pulse-decision-value");
        decisionReason.setWrapText(false);
        decisionReason.setTextOverrun(OverrunStyle.ELLIPSIS);
        decisionReason.getStyleClass().add("pulse-decision-reason");
        VBox decisionCopy = new VBox(1, decision, decisionReason);
        HBox decisionStatus = new HBox(10, noTradeIcon, decisionCopy);
        decisionStatus.setAlignment(Pos.CENTER_LEFT);
        decisionStatus.getStyleClass().add("pulse-decision-status");
        VBox decisionCard = card("DECISION STATUS", decisionStatus, section("REASON BREAKDOWN"), reasonGrid);
        decisionCard.getStyleClass().add("market-decision-card");

        GridPane leftRail = new GridPane();
        leftRail.setVgap(8);
        leftRail.getStyleClass().add("pulse-left-rail");
        ColumnConstraints railColumn = new ColumnConstraints();
        railColumn.setPercentWidth(100);
        railColumn.setHgrow(Priority.ALWAYS);
        leftRail.getColumnConstraints().add(railColumn);
        leftRail.getRowConstraints().addAll(row(31), row(25), row(44));
        leftRail.add(quoteCard, 0, 0);
        leftRail.add(contextCard, 0, 1);
        leftRail.add(decisionCard, 0, 2);
        for (Node card : new Node[] {quoteCard, contextCard, decisionCard}) {
            GridPane.setHgrow(card, Priority.ALWAYS);
            GridPane.setVgrow(card, Priority.ALWAYS);
        }

        radar.setMinSize(240, 240);
        VBox radarCard = new VBox(radar);
        radarCard.getStyleClass().addAll("pulse-card", "radar-card");
        radarCard.setMinSize(0, 0);
        radarCard.setMaxSize(Double.MAX_VALUE, Double.MAX_VALUE);
        VBox.setVgrow(radar, Priority.ALWAYS);

        renderEventTable();
        VBox feedCard = card("LIVE EVENT FEED", eventTable);

        GridPane main = grid(21, 41, 38);
        main.addRow(0, leftRail, radarCard, feedCard);
        VBox.setVgrow(main, Priority.ALWAYS);

        GridPane charts = grid(25, 25, 25, 25);
        charts.getStyleClass().add("pulse-charts");
        charts.setMinHeight(96);
        charts.setPrefHeight(118);
        charts.setMaxHeight(132);
        charts.addRow(0, returnChart, spreadChart, liquidityChart, pnlChart);
        for (PulseSparkline chart : new PulseSparkline[] {returnChart, spreadChart, liquidityChart, pnlChart}) {
            chart.setMinHeight(96);
            chart.setMaxHeight(Double.MAX_VALUE);
            GridPane.setVgrow(chart, Priority.ALWAYS);
        }

        Label legend = text("SIGNAL LEGEND     GREEN  RULE EVIDENCE     BLUE  CLASSICAL ML     PURPLE  TRANSFORMER");
        Label governance = text("GLD  |  READ-ONLY VISUALIZATION  |  NO EXECUTION AUTHORITY");
        Region footerGap = new Region();
        HBox.setHgrow(footerGap, Priority.ALWAYS);
        HBox footer = new HBox(10, legend, footerGap, governance);
        footer.getStyleClass().add("pulse-footer");

        getChildren().addAll(header, main, charts, footer);
    }

    void update(DecisionTelemetry frame) {
        frameCount++;
        boolean fresh = frame.evidence().get(0).state() == DecisionTelemetry.EvidenceState.PASS;
        clock.setText(frame.quoteTimestamp().isBlank() ? "TIMESTAMP UNAVAILABLE" : marketDateTime(frame.quoteTimestamp()));
        liveState.setText(fresh ? "LIVE" : "DEGRADED");
        liveState.setStyle("-fx-text-fill:" + (fresh ? "#5cf2b5" : "#ffbf69") + ";");
        liveDot.setFill(Color.web(fresh ? "#19f7a7" : "#ffbf3f"));
        pipelineDot.setFill(Color.web(fresh ? "#19c87f" : "#8b650f"));
        pipelineState.setStyle("-fx-text-fill:" + (fresh ? "#5facc0" : "#ffbf3f") + ";");
        long seconds = Math.max(0, Duration.between(openedAt, Instant.now()).toSeconds());
        uptime.setText(String.format(Locale.US, "%02d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60));
        updateRate.setText(String.format(Locale.US, "%.0f / min", frameCount * 60.0 / Math.max(1, seconds)));
        latency.setText(frame.transformerLatencyMs() > 0
                ? String.format(Locale.US, "%.1f ms", frame.transformerLatencyMs()) : "Unavailable");

        if (openingMidpoint <= 0 && frame.midpoint() > 0) openingMidpoint = frame.midpoint();
        double returnPct = openingMidpoint > 0 && frame.midpoint() > 0 ? frame.midpoint() / openingMidpoint - 1 : Double.NaN;
        double absoluteChange = openingMidpoint > 0 && frame.midpoint() > 0
                ? frame.midpoint() - openingMidpoint : Double.NaN;
        price.setText(frame.midpoint() > 0 ? String.format(Locale.US, "%.2f", frame.midpoint()) : "--");
        change.setText(Double.isFinite(returnPct) && Double.isFinite(absoluteChange)
                ? String.format(Locale.US, "%+.2f (%+.2f%%)", absoluteChange, returnPct * 100) : "--");
        change.setStyle("-fx-text-fill:" + signedColor(returnPct) + ";");
        bid.setText(frame.bid() > 0 ? String.format(Locale.US, "%.2f", frame.bid()) : "Unavailable");
        ask.setText(frame.ask() > 0 ? String.format(Locale.US, "%.2f", frame.ask()) : "Unavailable");
        double spreadBps = Double.isFinite(frame.spreadPct()) ? frame.spreadPct() * 10_000 : Double.NaN;
        spread.setText(Double.isFinite(spreadBps) ? String.format(Locale.US, "%.2f bps", spreadBps) : "Invalid");
        session.setText(frame.marketState());
        regime.setText(frame.regime());
        volatility.setText(volatilityDescription(frame));
        liquidity.setText(frame.liquidityScore() > 0 ? String.format(Locale.US, "%.0f%%", frame.liquidityScore() * 100) : "Unavailable");

        decision.setText(frame.decision().toUpperCase(Locale.ROOT));
        decision.setStyle("-fx-text-fill:" + decisionColor(frame.decision()) + ";");
        decisionReason.setText(statusReason(frame));
        boolean noTrade = frame.decision().toUpperCase(Locale.ROOT).contains("NO TRADE");
        noTradeIcon.setVisible(noTrade);
        noTradeIcon.setManaged(noTrade);
        renderReasons(frame);
        radar.update(frame);
        recordEvents(frame);
        renderEventTable();

        returnChart.update(returnPct, Double.isFinite(returnPct) ? String.format(Locale.US, "%+.2f%%", returnPct * 100) : "--");
        spreadChart.update(spreadBps, Double.isFinite(spreadBps) ? String.format(Locale.US, "%.2f", spreadBps) : "--");
        liquidityChart.update(frame.liquidityScore(), frame.liquidityScore() > 0
                ? String.format(Locale.US, "%.2f", frame.liquidityScore()) : "--");
        pnlChart.update(frame.performance().netPnl(), money(frame.performance().netPnl()));
    }

    private void renderReasons(DecisionTelemetry frame) {
        reasonGrid.getChildren().clear();
        String trend = frame.regime().toLowerCase(Locale.ROOT).contains("bear") ? "Bearish"
                : frame.regime().toLowerCase(Locale.ROOT).contains("bull") ? "Bullish" : "Rule";
        addRow(reasonGrid, 0, "Trend", String.format(Locale.US, "%s (%.0f)", trend, frame.ruleStrength() * 100), "#8ecbd7");
        double momentum = momentumScore(frame.expectedReturn());
        addRow(reasonGrid, 1, "Momentum", Double.isFinite(momentum)
                ? String.format(Locale.US, "%s (%.0f)", momentum >= 60 ? "Constructive" : momentum >= 40 ? "Neutral" : "Weak", momentum)
                : "Unavailable", "#8ecbd7");
        addRow(reasonGrid, 2, "Price Action", gateSummary(frame.evidence().get(2)), "#8ecbd7");
        addRow(reasonGrid, 3, "Microstructure", String.format(Locale.US, "%s (%.0f)",
                frame.liquidityScore() >= 0.55 ? "Good" : "Weak", frame.liquidityScore() * 100), "#ffbf3f");
        addRow(reasonGrid, 4, "ML (Classical)", modelSummary(frame.classical()), "#e2d269");
        addRow(reasonGrid, 5, "Risk", riskSummary(
                frame.evidence().get(0).state(), frame.evidence().get(5).state()), "#ff657f");
    }

    private void recordEvents(DecisionTelemetry frame) {
        String now = frame.quoteTimestamp().isBlank() ? Instant.now().toString() : frame.quoteTimestamp();
        if (!frame.quoteTimestamp().equals(lastQuoteTimestamp)) {
            addEvent(new PulseEvent(now, "QUOTE", String.format(Locale.US, "%.2f x %.2f", frame.bid(), frame.ask()), "--", "quote"));
            lastQuoteTimestamp = frame.quoteTimestamp();
        }
        String signature = frame.decision() + frame.marketState() + frame.classical().version() + frame.transformer().version();
        if (!signature.equals(lastDecisionSignature)) {
            addEvent(new PulseEvent(now, "DECISION", frame.decision() + " / " + frame.marketState(), "--", "risk"));
            addEvent(new PulseEvent(now, "CLASSICAL", probability(frame.classical()), "--", "model"));
            addEvent(new PulseEvent(now, "TRANSFORMER", probability(frame.transformer()),
                    frame.transformerLatencyMs() > 0 ? String.format(Locale.US, "%.1f ms", frame.transformerLatencyMs()) : "--", "transformer"));
            addEvent(new PulseEvent(now, "RISK", frame.evidence().get(5).detail(), "--", "risk"));
            lastDecisionSignature = signature;
        }
    }

    private void addEvent(PulseEvent event) {
        events.addFirst(event);
        while (events.size() > 10) events.removeLast();
    }

    private void renderEventTable() {
        eventTable.getChildren().clear();
        eventCell(eventTable, 0, 0, "TIME", "event-heading");
        eventCell(eventTable, 1, 0, "TYPE", "event-heading");
        eventCell(eventTable, 2, 0, "DETAIL", "event-heading");
        eventCell(eventTable, 3, 0, "LATENCY", "event-heading");
        int row = 1;
        for (PulseEvent event : events) {
            eventCell(eventTable, 0, row, marketTime(event.timestamp()), "event-time");
            eventCell(eventTable, 1, row, event.type(), "event-type-" + event.style());
            eventCell(eventTable, 2, row, event.detail(), "event-detail");
            eventCell(eventTable, 3, row, event.latency(), "event-time");
            row++;
        }
        if (events.isEmpty()) eventCell(eventTable, 0, 1, "Waiting for live events", "event-detail", 4);
    }

    private static void eventCell(GridPane grid, int column, int row, String text, String style) {
        eventCell(grid, column, row, text, style, 1);
    }

    private static void eventCell(GridPane grid, int column, int row, String text, String style, int span) {
        Label label = new Label(text);
        label.getStyleClass().addAll("event-cell", style);
        label.setTextOverrun(OverrunStyle.ELLIPSIS);
        label.setMaxWidth(Double.MAX_VALUE);
        grid.add(label, column, row, span, 1);
    }

    private static VBox card(String title, Node... content) {
        Label heading = title(title);
        Region rule = new Region();
        rule.getStyleClass().add("panel-rule");
        VBox box = new VBox(6, heading, rule);
        box.getChildren().addAll(content);
        box.getStyleClass().add("pulse-card");
        box.setMinSize(0, 0);
        box.setMaxSize(Double.MAX_VALUE, Double.MAX_VALUE);
        Rectangle clip = new Rectangle();
        clip.widthProperty().bind(box.widthProperty());
        clip.heightProperty().bind(box.heightProperty());
        box.setClip(clip);
        return box;
    }

    private static GridPane grid(double... widths) {
        GridPane grid = tableGrid(widths);
        grid.setHgap(8);
        grid.setVgap(8);
        grid.setMaxSize(Double.MAX_VALUE, Double.MAX_VALUE);
        return grid;
    }

    private static GridPane tableGrid(double... widths) {
        GridPane grid = new GridPane();
        for (double width : widths) {
            ColumnConstraints column = new ColumnConstraints();
            column.setPercentWidth(width);
            column.setMinWidth(0);
            column.setHgrow(Priority.ALWAYS);
            grid.getColumnConstraints().add(column);
        }
        return grid;
    }

    private static void addRow(GridPane grid, int row, String name, Label value) {
        Label label = text(name);
        label.getStyleClass().add("pulse-field-name");
        value.getStyleClass().add("pulse-field-value");
        label.setTextOverrun(OverrunStyle.ELLIPSIS);
        value.setTextOverrun(OverrunStyle.ELLIPSIS);
        label.setMaxWidth(Double.MAX_VALUE);
        value.setMaxWidth(Double.MAX_VALUE);
        grid.addRow(row, label, value);
    }

    private static void addRow(GridPane grid, int row, String name, String value) {
        addRow(grid, row, name, text(value));
    }

    private static void addRow(GridPane grid, int row, String name, String value, String color) {
        Label label = text(value);
        label.setStyle("-fx-text-fill:" + color + ";");
        addRow(grid, row, name, label);
    }

    private static RowConstraints row(double height) {
        RowConstraints row = new RowConstraints();
        row.setPercentHeight(height);
        row.setMinHeight(0);
        row.setVgrow(Priority.ALWAYS);
        return row;
    }

    private static Node noTradeIcon() {
        Circle ring = new Circle(17, Color.TRANSPARENT);
        ring.setStroke(Color.web("#ff5a67"));
        ring.setStrokeWidth(3.5);
        Line slash = new Line(-12, -12, 12, 12);
        slash.setStroke(Color.web("#ff5a67"));
        slash.setStrokeWidth(3.5);
        slash.setStrokeLineCap(javafx.scene.shape.StrokeLineCap.ROUND);
        javafx.scene.Group mark = new javafx.scene.Group(ring, slash);
        StackPane icon = new StackPane(mark);
        icon.setMinSize(42, 42);
        icon.setPrefSize(42, 42);
        icon.setMaxSize(42, 42);
        icon.getStyleClass().add("no-trade-icon");
        return icon;
    }

    private static Label title(String value) {
        Label label = new Label(value);
        label.getStyleClass().add("pulse-title");
        return label;
    }

    private static Label section(String value) {
        Label label = new Label(value);
        label.getStyleClass().add("pulse-section");
        return label;
    }

    private static Label value(String value) {
        Label label = new Label(value);
        label.getStyleClass().add("pulse-value");
        return label;
    }

    private static Label text(String value) {
        Label label = new Label(value);
        label.getStyleClass().add("pulse-text");
        return label;
    }

    static String marketTime(String timestamp) {
        try {
            return DateTimeFormatter.ofPattern("HH:mm:ss.SSS")
                    .withZone(ZoneId.of("America/New_York")).format(Instant.parse(timestamp));
        } catch (Exception ignored) {
            return timestamp == null || timestamp.isBlank() ? "--" : timestamp;
        }
    }

    static String marketDateTime(String timestamp) {
        try {
            return DateTimeFormatter.ofPattern("yyyy-MM-dd  HH:mm:ss '(ET)'", Locale.US)
                    .withZone(ZoneId.of("America/New_York")).format(Instant.parse(timestamp));
        } catch (Exception ignored) {
            return timestamp == null || timestamp.isBlank() ? "TIMESTAMP UNAVAILABLE" : timestamp;
        }
    }

    private static double momentumScore(double expectedReturn) {
        if (!Double.isFinite(expectedReturn) || expectedReturn == 0) return Double.NaN;
        return Math.max(5, Math.min(100, 50 + expectedReturn * 5_000));
    }

    private static String gateSummary(DecisionTelemetry.Evidence evidence) {
        String label = switch (evidence.state()) {
            case PASS -> evidence.name().equalsIgnoreCase("Risk gate") ? "Approved" : "Confirmed";
            case WARN -> "Neutral";
            case BLOCKED -> "Blocked";
            case UNAVAILABLE -> "Unavailable";
        };
        if (evidence.name().equalsIgnoreCase("Risk gate")
                || evidence.state() == DecisionTelemetry.EvidenceState.UNAVAILABLE) return label;
        return String.format(Locale.US, "%s (%.0f)", label, DecisionRadar.gateValue(evidence.state()) * 100);
    }

    private static String modelSummary(DecisionTelemetry.Prediction model) {
        if (!model.available()) return "Unavailable";
        boolean longSide = model.longProbability() >= model.shortProbability();
        return String.format(Locale.US, "%s (%.0f)", longSide ? "Long" : "Short",
                Math.max(model.longProbability(), model.shortProbability()) * 100);
    }

    static String riskSummary(DecisionTelemetry.EvidenceState market,
                              DecisionTelemetry.EvidenceState risk) {
        if (market == DecisionTelemetry.EvidenceState.BLOCKED
                || risk == DecisionTelemetry.EvidenceState.BLOCKED) return "Blocked";
        if (market == DecisionTelemetry.EvidenceState.UNAVAILABLE
                || risk == DecisionTelemetry.EvidenceState.UNAVAILABLE) return "Unavailable";
        return market == DecisionTelemetry.EvidenceState.PASS
                && risk == DecisionTelemetry.EvidenceState.PASS ? "Approved" : "Review";
    }

    private static String probability(DecisionTelemetry.Prediction model) {
        if (!model.available()) return "Unavailable";
        return String.format(Locale.US, "Long %.0f%% / Short %.0f%% / Abstain %.0f%%",
                model.longProbability() * 100, model.shortProbability() * 100, model.noTradeProbability() * 100);
    }

    private static String volatilityDescription(DecisionTelemetry frame) {
        String regime = frame.regime().toLowerCase(Locale.ROOT);
        if (regime.contains("volatility")) return frame.regime();
        return "Unavailable";
    }

    private static String statusReason(DecisionTelemetry frame) {
        String market = frame.marketState();
        return market == null || market.isBlank() || market.equalsIgnoreCase("Unavailable")
                ? frame.summary() : market;
    }

    private static String percent(double value) {
        return Double.isFinite(value) ? String.format(Locale.US, "%.0f%%", value * 100) : "--";
    }

    private static String signedPercent(double value) {
        return Double.isFinite(value) && value != 0 ? String.format(Locale.US, "%+.3f%%", value * 100) : "Unavailable";
    }

    private static String money(double value) { return String.format(Locale.US, "$%,.2f", value); }

    private static String signedColor(double value) { return value < 0 ? "#ff5470" : value > 0 ? "#5cf2b5" : "#d9edf2"; }

    private static String decisionColor(String value) {
        String decision = value.toUpperCase(Locale.ROOT);
        if (decision.contains("LONG") || decision.contains("BUY")) return "#5cf2b5";
        if (decision.contains("SHORT") || decision.contains("SELL")) return "#ff5470";
        if (decision.contains("NO TRADE")) return "#ff5a67";
        return "#ffbf69";
    }

    private record PulseEvent(String timestamp, String type, String detail, String latency, String style) { }
}
