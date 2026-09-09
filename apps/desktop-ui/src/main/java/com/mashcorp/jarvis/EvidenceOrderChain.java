package com.mashcorp.jarvis;

import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.control.Label;
import javafx.scene.effect.DropShadow;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.shape.Circle;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.List;
import java.util.Locale;

/** Read-only, connected projection of evidence gates from market data to execution. */
final class EvidenceOrderChain extends StackPane {
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("HH:mm:ss")
            .withLocale(Locale.US).withZone(ZoneId.of("America/New_York"));
    private final VBox rows = new VBox(2);

    EvidenceOrderChain() {
        getStyleClass().add("evidence-chain");
        Region connector = new Region();
        connector.getStyleClass().add("evidence-chain-connector");
        connector.setMinWidth(2);
        connector.setPrefWidth(2);
        connector.setMaxWidth(2);
        connector.setMaxHeight(Double.MAX_VALUE);
        StackPane.setAlignment(connector, Pos.CENTER_LEFT);
        StackPane.setMargin(connector, new Insets(22, 0, 22, 17));

        rows.getStyleClass().add("evidence-chain-rows");
        rows.setFillWidth(true);
        getChildren().addAll(connector, rows);
    }

    void update(List<DecisionTelemetry.Evidence> evidence, String timestamp) {
        String observedAt = timestamp(timestamp);
        rows.getChildren().setAll(evidence.stream().map(item -> row(item, observedAt)).toList());
    }

    private static HBox row(DecisionTelemetry.Evidence item, String observedAt) {
        Color color = stateColor(item.state());
        Circle ring = new Circle(10, Color.web("#021014"));
        ring.setStroke(color);
        ring.setStrokeWidth(1.6);
        ring.setEffect(new DropShadow(12, color.deriveColor(0, 1, 1, 0.72)));
        Circle core = new Circle(5.2, color);
        StackPane node = new StackPane(ring, core);
        node.setMinWidth(36);
        node.setPrefWidth(36);
        node.setMaxWidth(36);
        node.getStyleClass().add("evidence-chain-node");

        Label title = new Label(displayTitle(item));
        title.getStyleClass().addAll("evidence-name", stateClass(item.state()));
        Label detail = new Label(item.detail());
        detail.getStyleClass().add("evidence-detail");
        detail.setWrapText(true);
        VBox copy = new VBox(1, title, detail);
        copy.setMinWidth(0);
        HBox.setHgrow(copy, Priority.ALWAYS);

        Label time = new Label(observedAt);
        time.getStyleClass().add("evidence-time");
        HBox content = new HBox(8, copy, time);
        content.setAlignment(Pos.CENTER_LEFT);
        content.setMinWidth(0);
        HBox.setHgrow(content, Priority.ALWAYS);

        HBox row = new HBox(6, node, content);
        row.setAlignment(Pos.CENTER_LEFT);
        row.setMaxWidth(Double.MAX_VALUE);
        row.getStyleClass().addAll("evidence-chain-row", stateClass(item.state()));
        return row;
    }

    static String displayTitle(DecisionTelemetry.Evidence item) {
        String name = item.name().toLowerCase(Locale.ROOT);
        return switch (name) {
            case "market data" -> item.detail().toLowerCase(Locale.ROOT).contains("closed")
                    ? "MARKET CLOSED" : statusTitle(item.state(), "DATA FRESH", "DATA CHECK", "DATA BLOCKED", "DATA UNAVAILABLE");
            case "liquidity" -> statusTitle(item.state(), "LIQUIDITY GOOD", "LIQUIDITY CAUTION", "LIQUIDITY BLOCKED", "LIQUIDITY UNAVAILABLE");
            case "price action" -> statusTitle(item.state(), "PRICE ACTION CONFIRMED", "PRICE ACTION WAITING", "PRICE ACTION BLOCKED", "PRICE ACTION UNAVAILABLE");
            case "classical ml" -> statusTitle(item.state(), "CLASSICAL ML READY", "CLASSICAL ML CAUTION", "CLASSICAL ML BLOCKED", "CLASSICAL ML UNAVAILABLE");
            case "transformer" -> statusTitle(item.state(), "TRANSFORMER READY", "TRANSFORMER CAUTION", "TRANSFORMER BLOCKED", "TRANSFORMER UNAVAILABLE");
            case "risk gate" -> statusTitle(item.state(), "RISK APPROVED", "RISK REVIEW", "RISK BLOCKED", "RISK UNAVAILABLE");
            case "execution" -> statusTitle(item.state(), "EXECUTION ACTIVE", "NO ACTIVE ORDER", "EXECUTION BLOCKED", "EXECUTION UNAVAILABLE");
            default -> item.name().toUpperCase(Locale.ROOT);
        };
    }

    private static String statusTitle(DecisionTelemetry.EvidenceState state, String pass, String warn,
                                      String blocked, String unavailable) {
        return switch (state) {
            case PASS -> pass;
            case WARN -> warn;
            case BLOCKED -> blocked;
            case UNAVAILABLE -> unavailable;
        };
    }

    static String stateClass(DecisionTelemetry.EvidenceState state) {
        return "chain-" + state.name().toLowerCase(Locale.ROOT);
    }

    private static Color stateColor(DecisionTelemetry.EvidenceState state) {
        return switch (state) {
            case PASS -> Color.web("#19f7a7");
            case WARN -> Color.web("#ffbf3f");
            case BLOCKED -> Color.web("#ff5470");
            case UNAVAILABLE -> Color.web("#607780");
        };
    }

    static String timestamp(String value) {
        if (value == null || value.isBlank()) return "--";
        try {
            return TIME.format(Instant.parse(value));
        } catch (DateTimeParseException ignored) {
            return "--";
        }
    }
}
