package com.mashcorp.jarvis;

import javafx.geometry.Pos;
import javafx.scene.control.Label;
import javafx.scene.layout.HBox;
import javafx.scene.layout.VBox;

/** Compact, persistent separation of UI, trading, market, and research readiness. */
final class ReadinessStrip {
    private final Label interfaceValue = value("STARTING");
    private final Label tradingValue = value("LOCKED");
    private final Label marketValue = value("CHECKING");
    private final Label llmValue = value("CHECKING");
    private final HBox root = new HBox(1,
            item("INTERFACE", interfaceValue),
            item("TRADING", tradingValue),
            item("MARKET", marketValue),
            item("RESEARCH AI", llmValue));

    ReadinessStrip() {
        root.getStyleClass().add("readiness-strip");
        root.setAlignment(Pos.CENTER);
    }

    HBox node() {
        return root;
    }

    void apply(ReadinessSnapshot snapshot) {
        set(interfaceValue, snapshot.interfaceState());
        set(tradingValue, snapshot.tradingState());
        set(marketValue, snapshot.marketState());
        set(llmValue, snapshot.llmState());
    }

    void setTrading(String state) {
        set(tradingValue, state);
    }

    void setMarket(String state) {
        set(marketValue, state);
    }

    void degraded() {
        set(interfaceValue, "DEGRADED");
        set(tradingValue, "BLOCKED");
    }

    private static VBox item(String name, Label value) {
        Label caption = new Label(name);
        caption.getStyleClass().add("readiness-caption");
        VBox item = new VBox(1, caption, value);
        item.getStyleClass().add("readiness-item");
        return item;
    }

    private static Label value(String text) {
        Label value = new Label(text);
        value.getStyleClass().add("readiness-value");
        set(value, text);
        return value;
    }

    private static void set(Label label, String text) {
        String normalized = text == null || text.isBlank() ? "UNKNOWN" : text.toUpperCase();
        label.setText(normalized);
        label.getStyleClass().removeIf(style -> style.startsWith("readiness-state-"));
        String state = switch (normalized) {
            case "READY", "AVAILABLE", "ACTIVE", "OPEN", "CONNECTED" -> "good";
            case "STARTING", "CHECKING", "LOCKED", "CLOSED", "IDLE", "DISABLED" -> "idle";
            case "BLOCKED", "DEGRADED", "OFFLINE", "UNKNOWN" -> "warning";
            default -> normalized.endsWith("CONFIGURED") ? "configured" : "idle";
        };
        label.getStyleClass().add("readiness-state-" + state);
    }
}
