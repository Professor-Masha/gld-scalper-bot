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
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;

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

        VBox identity = new VBox(1, title("MARKET PULSE + DECISION RADAR"), text("GLD  SPDR Gold Shares  |  Live Decision-Making Dashboard"));
        Region headerGap = new Region();
        HBox.setHgrow(headerGap, Priority.ALWAYS);
        VBox center = new VBox(2, clock, liveState);
        center.setAlignment(Pos.CENTER_LEFT);
        GridPane service = tableGrid(56, 44);
        addRow(service, 0, "Uptime", uptime);
        addRow(service, 1, "Update rate", updateRate);
        addRow(service, 2, "Decision latency", latency);
        HBox header = new HBox(18, identity, headerGap, center, service);
        header.setAlignment(Pos.CENTER_LEFT);
        header.getStyleClass().add("pulse-header");

        HBox quoteLine = new HBox(12, price, change);
        quoteLine.setAlignment(Pos.BASELINE_LEFT);
        GridPane quoteGrid = tableGrid(36, 64);
        addRow(quoteGrid, 0, "Bid", bid);
        addRow(quoteGrid, 1, "Ask", ask);
        addRow(quoteGrid, 2, "Spread", spread);
        addRow(quoteGrid, 3, "Volume", volume);
        VBox quoteCard = card("GLD", quoteLine, quoteGrid);

        GridPane context = tableGrid(47, 53);
        addRow(context, 0, "Session", session);
        addRow(context, 1, "Regime", regime);
        addRow(context, 2, "Volatility", volatility);
        addRow(context, 3, "Liquidity", liquidity);
        addRow(context, 4, "News", news);
        VBox contextCard = card("MARKET CONTEXT", context);

        decision.getStyleClass().add("pulse-decision-value");
        decisionReason.setWrapText(true);
        VBox decisionCard = card("DECISION STATUS", decision, decisionReason, section("REASON BREAKDOWN"), reasonGrid);
        VBox leftRail = new VBox(8, quoteCard, contextCard, decisionCard);
        VBox.setVgrow(decisionCard, Priority.ALWAYS);

        radar.setMinSize(240, 240);
        VBox radarCard = card("DECISION RADAR", radar);
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
        clock.setText(frame.quoteTimestamp().isBlank() ? "Timestamp unavailable" : localTime(frame.quoteTimestamp()));
        liveState.setText(fresh ? "DATA -> EVIDENCE -> DECISION   LIVE" : "DATA LINK DEGRADED");
        liveState.setStyle("-fx-text-fill:" + (fresh ? "#5cf2b5" : "#ffbf69") + ";");
        long seconds = Math.max(0, Duration.between(openedAt, Instant.now()).toSeconds());
        uptime.setText(String.format(Locale.US, "%02d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60));
        updateRate.setText(String.format(Locale.US, "%.0f / min", frameCount * 60.0 / Math.max(1, seconds)));
        latency.setText(frame.transformerLatencyMs() > 0
                ? String.format(Locale.US, "%.1f ms", frame.transformerLatencyMs()) : "Unavailable");

        if (openingMidpoint <= 0 && frame.midpoint() > 0) openingMidpoint = frame.midpoint();
        double returnPct = openingMidpoint > 0 && frame.midpoint() > 0 ? frame.midpoint() / openingMidpoint - 1 : Double.NaN;
        price.setText(frame.midpoint() > 0 ? money(frame.midpoint()) : "--");
        change.setText(Double.isFinite(returnPct) ? String.format(Locale.US, "%+.2f%%", returnPct * 100) : "--");
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
        decisionReason.setText(frame.summary());
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
        addRow(reasonGrid, 0, "Trend", percent(frame.ruleStrength()));
        addRow(reasonGrid, 1, "Momentum", signedPercent(frame.expectedReturn()));
        addRow(reasonGrid, 2, "Price Action", frame.evidence().get(2).detail());
        addRow(reasonGrid, 3, "Microstructure", frame.evidence().get(1).detail());
        addRow(reasonGrid, 4, "ML (Classical)", probability(frame.classical()));
        addRow(reasonGrid, 5, "Risk", frame.evidence().get(5).detail());
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
            eventCell(eventTable, 0, row, localTime(event.timestamp()), "event-time");
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
        grid.addRow(row, label, value);
    }

    private static void addRow(GridPane grid, int row, String name, String value) {
        addRow(grid, row, name, text(value));
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

    private static String localTime(String timestamp) {
        try {
            return DateTimeFormatter.ofPattern("HH:mm:ss.SSS").withZone(ZoneId.systemDefault()).format(Instant.parse(timestamp));
        } catch (Exception ignored) {
            return timestamp == null || timestamp.isBlank() ? "--" : timestamp;
        }
    }

    private static String probability(DecisionTelemetry.Prediction model) {
        if (!model.available()) return "Unavailable";
        return String.format(Locale.US, "Long %.0f%% / Short %.0f%% / Abstain %.0f%%",
                model.longProbability() * 100, model.shortProbability() * 100, model.noTradeProbability() * 100);
    }

    private static String volatilityDescription(DecisionTelemetry frame) {
        String regime = frame.regime().toLowerCase(Locale.ROOT);
        if (regime.contains("volatility")) return frame.regime();
        return "Not separately reported";
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
        return "#ffbf69";
    }

    private record PulseEvent(String timestamp, String type, String detail, String latency, String style) { }
}
