package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.control.*;
import javafx.scene.layout.*;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executor;
import java.util.function.Consumer;

/** Read-only explorer for model lineage, training evidence, outcomes, and live decisions. */
final class MemoryGraphWorkspace extends VBox {
    private final GatewayClient gateway;
    private final Executor worker;
    private final Consumer<Throwable> errors;
    private final DecisionCore3D graph = new DecisionCore3D();
    private final ComboBox<String> window = new ComboBox<>(FXCollections.observableArrayList("1d", "7d", "30d", "90d", "all"));
    private final CheckBox models = filter("Models", true);
    private final CheckBox trades = filter("Trades", true);
    private final CheckBox playbooks = filter("Playbooks", true);
    private final CheckBox datasets = filter("Datasets", true);
    private final CheckBox training = filter("Training", true);
    private final CheckBox llm = filter("LLM reviews", true);
    private final Label summary = new Label("Loading persistent memory...");
    private final Label nodeTitle = new Label("SELECT A MEMORY NODE");
    private final Label nodeType = new Label("Click a colored node to inspect its evidence.");
    private final TextArea inspector = new TextArea();
    private final ListView<TimelineItem> timeline = new ListView<>();

    MemoryGraphWorkspace(GatewayClient gateway, Executor worker, Consumer<Throwable> errors) {
        super(12);
        this.gateway = gateway; this.worker = worker; this.errors = errors;
        getStyleClass().add("memory-workspace");
        setMinWidth(0);
        window.getSelectionModel().select("30d");
        window.setTooltip(new Tooltip("Limits time-sensitive trades, datasets, and LLM reviews. Model lineage remains visible."));
        Button refresh = new Button("REFRESH MEMORY"); refresh.getStyleClass().add("secondary");
        refresh.setOnAction(event -> refresh()); window.setOnAction(event -> refresh());
        for (CheckBox filter : List.of(models, trades, playbooks, datasets, training, llm)) filter.setOnAction(event -> refresh());
        FlowPane filters = new FlowPane(12, 8, new Label("WINDOW"), window, models, trades, playbooks, datasets, training, llm, refresh);
        filters.setAlignment(Pos.CENTER_LEFT); filters.setPrefWrapLength(880); filters.setMinWidth(0);
        filters.getStyleClass().add("memory-filters");

        StackPane graphDeck = new StackPane(graph.node()); graphDeck.getStyleClass().add("memory-graph-deck");
        graphDeck.setMinWidth(0); graphDeck.setMinHeight(420); graphDeck.setPrefHeight(560);
        graph.bindSize(graphDeck.widthProperty(), graphDeck.heightProperty());
        graph.setSelectionListener(this::inspect);

        nodeTitle.getStyleClass().add("memory-node-title");
        nodeType.getStyleClass().add("memory-node-type"); nodeType.setWrapText(true);
        inspector.setEditable(false); inspector.setWrapText(true); inspector.setPrefRowCount(22);
        VBox details = new VBox(8, nodeTitle, nodeType, inspector); details.setPadding(new Insets(12));
        details.getStyleClass().add("memory-inspector"); details.setMinWidth(250); details.setPrefWidth(350);
        SplitPane split = new SplitPane(graphDeck, details); split.setMinWidth(0); split.setDividerPositions(0.68); split.setPrefHeight(590);

        timeline.setMinWidth(0); timeline.setPrefHeight(155);
        timeline.setCellFactory(view -> new ListCell<>() {
            @Override protected void updateItem(TimelineItem item, boolean empty) {
                super.updateItem(item, empty);
                setText(empty || item == null ? null : item.timestamp() + "  //  " + item.type().toUpperCase() + "  //  " + item.label());
            }
        });
        timeline.getSelectionModel().selectedItemProperty().addListener((observable, before, selected) -> {
            if (selected != null) graph.selectNode(selected.nodeId());
        });
        Label timelineTitle = new Label("TRAINING & MEMORY LINEAGE"); timelineTitle.getStyleClass().add("panel-title");
        summary.getStyleClass().add("memory-summary");
        VBox timelinePanel = new VBox(7, timelineTitle, timeline); timelinePanel.getStyleClass().add("panel");
        getChildren().addAll(filters, summary, split, timelinePanel);
        VBox.setVgrow(split, Priority.ALWAYS);
        refresh();
    }

    void refresh() {
        summary.setText("Synchronizing bounded read-only memory graph...");
        String selectedTypes = selectedTypes();
        String selectedWindow = window.getValue() == null ? "30d" : window.getValue();
        worker.execute(() -> {
            try {
                JsonNode payload = gateway.get("/api/v1/memory-graph?types=" + selectedTypes + "&window=" + selectedWindow);
                Platform.runLater(() -> apply(payload));
            } catch (Exception exception) { errors.accept(exception); }
        });
    }

    private void apply(JsonNode payload) {
        graph.setGraph(payload);
        JsonNode limits = payload.path("limits");
        JsonNode cache = payload.path("cache");
        summary.setText(String.format("%d NODES  //  %d CONNECTIONS  //  %d LINEAGE EVENTS  //  CACHE %s  //  RAW QUOTE SCAN: DISABLED",
                limits.path("nodes").asInt(), limits.path("edges").asInt(), limits.path("timeline").asInt(),
                cache.path("hit").asBoolean() ? "HIT" : "REFRESHED"));
        List<TimelineItem> items = new ArrayList<>();
        payload.path("timeline").forEach(item -> items.add(new TimelineItem(
                item.path("timestamp").asText("--"), item.path("type").asText("event"),
                item.path("label").asText("memory event"), item.path("node_id").asText())));
        timeline.setItems(FXCollections.observableArrayList(items));
        JsonNode current = null;
        for (JsonNode node : payload.path("nodes")) if ("decision:current".equals(node.path("id").asText())) { current = node; break; }
        if (current != null) { graph.selectNode("decision:current"); inspect(current); }
    }

    private void inspect(JsonNode node) {
        nodeTitle.setText(node.path("label").asText("MEMORY NODE"));
        nodeType.setText(node.path("type").asText("unknown").toUpperCase() + "  //  " + node.path("status").asText("available").toUpperCase());
        try { inspector.setText(node.path("details").toPrettyString()); }
        catch (Exception exception) { inspector.setText(node.toString()); }
        inspector.positionCaret(0);
    }

    private String selectedTypes() {
        List<String> values = new ArrayList<>(List.of("decision", "market", "risk"));
        if (models.isSelected()) values.add("model");
        if (trades.isSelected()) values.add("trade");
        if (playbooks.isSelected()) values.add("playbook");
        if (datasets.isSelected()) values.add("dataset");
        if (training.isSelected()) values.add("training");
        if (llm.isSelected()) values.add("llm");
        return String.join(",", values);
    }

    private static CheckBox filter(String label, boolean selected) {
        CheckBox checkBox = new CheckBox(label); checkBox.setSelected(selected); return checkBox;
    }

    private record TimelineItem(String timestamp, String type, String label, String nodeId) {}
}
