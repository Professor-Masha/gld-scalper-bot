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
import java.util.concurrent.atomic.AtomicLong;
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
    private final NodeInspector inspector = new NodeInspector();
    private final ListView<TimelineItem> timeline = new ListView<>();
    private volatile String lastFingerprint = "";
    private volatile GraphRenderPlan lastPlan;
    private final AtomicLong inspectionGeneration = new AtomicLong();

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

        javafx.scene.shape.Arc boundary = new javafx.scene.shape.Arc();
        boundary.setStartAngle(125);
        boundary.setLength(290);
        boundary.setType(javafx.scene.shape.ArcType.OPEN);
        boundary.setFill(javafx.scene.paint.Color.TRANSPARENT);
        boundary.setMouseTransparent(true);
        boundary.setManaged(false);
        boundary.getStyleClass().add("memory-open-ring");
        StackPane graphDeck = new StackPane(graph.node(), boundary); graphDeck.getStyleClass().add("memory-graph-deck");
        graphDeck.setMinWidth(0); graphDeck.setMinHeight(470); graphDeck.setPrefHeight(650);
        boundary.centerXProperty().bind(graphDeck.widthProperty().divide(2));
        boundary.centerYProperty().bind(graphDeck.heightProperty().divide(2));
        boundary.radiusXProperty().bind(graphDeck.widthProperty().multiply(0.42));
        boundary.radiusYProperty().bind(graphDeck.heightProperty().multiply(0.38));
        graph.bindSize(graphDeck.widthProperty(), graphDeck.heightProperty());
        graph.setSelectionListener(this::inspect);
        SplitPane split = new SplitPane(graphDeck, inspector); split.setMinWidth(0); split.setDividerPositions(0.79); split.setPrefHeight(680);

        timeline.setMinWidth(0); timeline.setPrefHeight(125);
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
                JsonNode payload = gateway.get("/api/v1/memory-graph/summary?types=" + selectedTypes + "&window=" + selectedWindow);
                String nextFingerprint = payload.path("topology_fingerprint").asText();
                GraphRenderPlan plan = nextFingerprint.equals(lastFingerprint) ? null : GraphRenderPlan.build(payload, lastPlan);
                Platform.runLater(() -> apply(payload, plan));
            } catch (Exception exception) { errors.accept(exception); }
        });
    }

    private void apply(JsonNode payload, GraphRenderPlan plan) {
        if (plan != null) { graph.applyPlan(plan); lastFingerprint = plan.fingerprint(); lastPlan = plan; }
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
        boolean hasCurrent = false;
        for (JsonNode node : payload.path("nodes")) if ("decision:current".equals(node.path("id").asText())) { hasCurrent = true; break; }
        if (hasCurrent) graph.selectNode("decision:current");
    }

    private void inspect(String nodeId) {
        long generation = inspectionGeneration.incrementAndGet();
        GraphRenderPlan.NodePlan preview = lastPlan == null ? null : lastPlan.nodes().get(nodeId);
        if (preview == null) inspector.loading(nodeId.replace(':', ' '));
        else inspector.preview(preview);
        worker.execute(() -> {
            try {
                String encoded = java.net.URLEncoder.encode(nodeId, java.nio.charset.StandardCharsets.UTF_8).replace("+", "%20");
                JsonNode detail = gateway.get("/api/v1/memory-graph/nodes/" + encoded);
                Platform.runLater(() -> {
                    if (inspectionGeneration.get() == generation) inspector.show(detail);
                });
            } catch (Exception exception) { errors.accept(exception); }
        });
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
