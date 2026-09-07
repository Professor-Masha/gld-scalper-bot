package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.animation.FadeTransition;
import javafx.animation.ParallelTransition;
import javafx.animation.TranslateTransition;
import javafx.application.Application;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.Scene;
import javafx.scene.chart.LineChart;
import javafx.scene.chart.NumberAxis;
import javafx.scene.chart.XYChart;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.scene.paint.Color;
import javafx.scene.shape.Circle;
import javafx.stage.Stage;
import javafx.util.Duration;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

public final class JarvisApplication extends Application {
    private final ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(2, threadFactory("jarvis-scheduler"));
    private final java.util.concurrent.ExecutorService startupWorker = Executors.newSingleThreadExecutor(threadFactory("jarvis-startup"));
    private final java.util.concurrent.ExecutorService telemetryWorker = Executors.newSingleThreadExecutor(threadFactory("jarvis-telemetry"));
    private final java.util.concurrent.ExecutorService readWorkers = Executors.newFixedThreadPool(3, threadFactory("jarvis-read"));
    private final java.util.concurrent.ExecutorService graphWorker = Executors.newSingleThreadExecutor(threadFactory("jarvis-graph"));
    private final AtomicBoolean telemetryPollQueued = new AtomicBoolean();
    private final StackPane workspace = new StackPane();
    private final Label systemState = new Label("STARTING");
    private final Label clock = new Label();
    private final Label notification = new Label("Initializing secure local gateway...");
    private final Label quoteValue = metricValue("--");
    private final Label equityValue = metricValue("--");
    private final Label pnlValue = metricValue("--");
    private final Label winValue = metricValue("--");
    private final Label botValue = metricValue("OFFLINE");
    private final Label decisionValue = new Label("NO DATA");
    private final Label decisionReason = new Label("Waiting for the first backend decision.");
    private final Label dataLinkValue = statusValue("CONNECTING");
    private final Label streamValue = statusValue("WAITING");
    private final Label modelValue = statusValue("DISCOVERING");
    private final Label sessionValue = statusValue("PAPER");
    private final ReadinessStrip readinessStrip = new ReadinessStrip();
    private final StartupOverlay startupOverlay = new StartupOverlay();
    private final TextArea terminal = new TextArea();
    private final DecisionCore3D core3D = new DecisionCore3D();
    private final NodeInspector overviewInspector = new NodeInspector();
    private final LineChart<Number, Number> marketChart = lineChart("Minute", "GLD");
    private GatewayRuntime runtime;
    private GatewayClient gateway;
    private JsonNode latestSnapshot;
    private java.net.http.WebSocket socket;
    private volatile long lastEventNanos;
    private volatile long socketOpenedNanos;
    private final java.util.concurrent.ExecutorService commands = Executors.newFixedThreadPool(2, threadFactory("jarvis-operator-command"));
    private final java.util.concurrent.ExecutorService research = Executors.newSingleThreadExecutor(threadFactory("jarvis-research"));
    private Button paperStart;
    private Button paperStop;
    private final ToggleGroup navigationGroup = new ToggleGroup();
    private int navigationIndex;
    private Runnable currentRefresh = () -> {};
    private long eventSequence;
    private Node overviewPage;
    private MemoryGraphWorkspace memoryWorkspace;
    private Node memoryPage;
    private final java.util.prefs.Preferences preferences = java.util.prefs.Preferences.userNodeForPackage(JarvisApplication.class);
    private boolean reducedMotion = preferences.getBoolean("reducedMotion", false);
    private volatile String overviewGraphFingerprint = "";
    private volatile GraphRenderPlan overviewGraphPlan;
    private volatile boolean startupComplete;
    private volatile boolean tradingAllowed;
    private volatile ReadinessSnapshot readinessSnapshot = new ReadinessSnapshot(
            "STARTING", "LOCKED", "CHECKING", "CHECKING", false);

    public static void launchApplication(String[] args) {
        launch(args);
    }

    @Override
    public void init() {
        runtime = new GatewayRuntime();
    }

    @Override
    public void start(Stage stage) {
        StackPane root = new StackPane(new HudBackdrop(), startupOverlay);
        root.getStyleClass().add("root-deck");
        Scene scene = new Scene(root, 1500, 920, Color.web("#010507"));
        scene.getStylesheets().add(getClass().getResource("/com/mashcorp/jarvis/jarvis.css").toExternalForm());
        stage.setTitle("Mashcorp GLD Command Center");
        stage.setMinWidth(850);
        stage.setMinHeight(600);
        stage.setScene(scene);
        stage.widthProperty().addListener((observable, before, width) -> {
            boolean wide = width.doubleValue() >= 1100; clock.setVisible(wide); clock.setManaged(wide);
            boolean readinessVisible = width.doubleValue() >= 1260;
            readinessStrip.node().setVisible(readinessVisible);
            readinessStrip.node().setManaged(readinessVisible);
        });
        stage.setMaximized(true);
        stage.setOnCloseRequest(event -> {
            if (latestSnapshot != null && latestSnapshot.path("control_plane").path("paper_process").path("state").asText().matches("running|stopping")) {
                Alert confirmation = new Alert(Alert.AlertType.CONFIRMATION,
                        "Paper trading is still active. Closing the dashboard does not stop the bot. Close the dashboard only?", ButtonType.YES, ButtonType.NO);
                if (confirmation.showAndWait().orElse(ButtonType.NO) != ButtonType.YES) event.consume();
            }
        });
        stage.show();
        startupOverlay.update(10, "JavaFX interface initialized");
        startupWorker.execute(() -> bootstrap(stage, root));
    }

    private void bootstrap(Stage stage, StackPane root) {
        long startupStarted = System.nanoTime();
        try {
            startupOverlay.update(15, "Starting secure local Python gateway");
            runtime.start();
            gateway = new GatewayClient(runtime.baseUri(), runtime.token());
            startupOverlay.update(25, "Local gateway healthy");

            JsonNode readiness = gateway.get("/api/v1/system/readiness");
            startupOverlay.update(40, "Database, configuration, and audit ledger checked");
            JsonNode snapshot = gateway.get("/api/snapshot");
            startupOverlay.update(55, "Account and bot status loaded");
            JsonNode providers = providerCatalogOrFallback(readiness);
            ReadinessSnapshot resolvedReadiness = ReadinessSnapshot.from(readiness, providers);
            startupOverlay.update(70, "ML registry and research configuration discovered");
            GraphRenderPlan graphPlan = loadInitialGraphPlan();
            startupOverlay.update(88, graphPlan == null
                    ? "Decision memory deferred; interface remains available"
                    : "Decision memory summary cached");

            runOnFxAndWait(() -> initializeShell(stage, root, snapshot, resolvedReadiness, graphPlan));
            startupOverlay.update(96, "Telemetry listener initialized");
            startupComplete = true;
            gateway.recordOperation("startup_to_usable", (System.nanoTime() - startupStarted) / 1_000_000.0);
            Platform.runLater(() -> startupOverlay.complete(reducedMotion, () -> beginSmokeCheck(stage)));
        } catch (Exception exc) {
            Platform.runLater(() -> {
                readinessStrip.degraded();
                startupOverlay.fail(exc.getMessage());
                notification.setText("STARTUP BLOCKED\n" + exc.getMessage());
            });
        }
    }

    private void initializeShell(
            Stage stage,
            StackPane root,
            JsonNode snapshot,
            ReadinessSnapshot resolvedReadiness,
            GraphRenderPlan graphPlan) {
        writeClientPid();
        BorderPane shell = new BorderPane();
        shell.getStyleClass().add("app-shell");
        shell.setTop(buildTopBar());
        shell.setLeft(buildNavigation());
        shell.setCenter(workspace);
        root.getChildren().add(1, shell);
        showOverview();
        if (graphPlan != null) {
            core3D.applyPlan(graphPlan);
            overviewGraphFingerprint = graphPlan.fingerprint();
            overviewGraphPlan = graphPlan;
        }
        applyReadiness(resolvedReadiness);
        applySnapshot(snapshot);
        startTelemetry();
        scheduleInterfaceTasks();
    }

    private JsonNode providerCatalogOrFallback(JsonNode readiness) {
        try {
            return gateway.get("/api/llm/providers");
        } catch (Exception ignored) {
            var fallback = com.fasterxml.jackson.databind.node.JsonNodeFactory.instance.objectNode();
            fallback.put("active_provider", readiness.path("llm").path("provider").asText("none"));
            return fallback;
        }
    }

    private GraphRenderPlan loadInitialGraphPlan() {
        try {
            JsonNode graph = gateway.get("/api/v1/memory-graph/summary?types=decision,market,model,playbook,risk&window=1d");
            return GraphRenderPlan.build(graph, null);
        } catch (Exception ignored) {
            return null;
        }
    }

    private void scheduleInterfaceTasks() {
        scheduler.scheduleWithFixedDelay(() -> Platform.runLater(() -> currentRefresh.run()), 10, 10, TimeUnit.SECONDS);
        scheduler.scheduleWithFixedDelay(() -> readWorkers.execute(this::refreshReadiness), 30, 30, TimeUnit.SECONDS);
        scheduler.scheduleAtFixedRate(() -> Platform.runLater(() ->
                clock.setText(DateTimeFormatter.ofPattern("EEE, dd MMM yyyy  HH:mm:ss")
                        .withZone(ZoneId.systemDefault()).format(Instant.now()))), 0, 1, TimeUnit.SECONDS);
    }

    private void beginSmokeCheck(Stage stage) {
        String smokeDirectory = System.getenv("JARVIS_SMOKE_DIR");
        if (smokeDirectory != null && !smokeDirectory.isBlank()) {
            DesktopSmokeCheck.run(stage, java.nio.file.Path.of(smokeDirectory), () -> latestSnapshot != null, List.of(
                    this::showOverview, this::showOverview, this::showMarket, this::showPerformance,
                    this::showTrading, this::showIntelligence, this::showMemoryGraph, this::showTraining, this::showAiLab,
                    this::showWhitePaper, this::showControlPlane, this::showSettings, this::showOverview));
        }
    }

    private Node buildTopBar() {
        Label title = new Label("MASHCORP // GLD COMMAND CENTER");
        title.getStyleClass().add("brand-title");
        Label kicker = new Label("AUTONOMOUS RESEARCH & EXECUTION SYSTEM");
        kicker.getStyleClass().add("brand-kicker");
        VBox brand = new VBox(2, title, kicker);
        systemState.getStyleClass().add("state-pill");
        systemState.getStyleClass().add("state-starting");
        clock.getStyleClass().add("clock");
        Circle pulse = new Circle(4, Color.web("#5cf2b5"));
        pulse.getStyleClass().add("link-pulse");
        Label environment = new Label("PAPER NETWORK // GLD");
        environment.getStyleClass().add("environment-badge");
        HBox network = new HBox(7, pulse, environment);
        network.setAlignment(Pos.CENTER);
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        paperStop = actionButton("STOP BOT", "danger", () -> execute("Requesting safety shutdown", gateway::stopPaper));
        paperStart = actionButton("START PAPER", "primary", () -> execute("Starting paper bot", gateway::startPaper));
        paperStart.setDisable(true); paperStop.setDisable(true);
        paperStart.setMinWidth(Region.USE_PREF_SIZE); paperStop.setMinWidth(Region.USE_PREF_SIZE);
        systemState.setMinWidth(Region.USE_PREF_SIZE);
        brand.setMinWidth(240); brand.setMaxWidth(410);
        title.setMinWidth(120); title.setMaxWidth(410); title.setWrapText(true);
        clock.setMinWidth(0); clock.setMaxWidth(210); clock.setWrapText(true);
        HBox bar = new HBox(12, brand, systemState, readinessStrip.node(), spacer, network, clock, paperStop, paperStart);
        bar.setAlignment(Pos.CENTER_LEFT);
        bar.getStyleClass().add("top-bar");
        return bar;
    }

    private Node buildNavigation() {
        VBox nav = new VBox(8);
        nav.getStyleClass().add("navigation");
        Label monogram = new Label("MC");
        monogram.getStyleClass().add("monogram");
        Label caption = new Label("PAPER OPERATIONS");
        caption.getStyleClass().add("eyebrow");
        Label deck = new Label("COMMAND DECK / 01");
        deck.getStyleClass().add("nav-deck-label");
        nav.getChildren().addAll(monogram, caption, deck, new Separator());
        addNav(nav, "OVERVIEW", this::showOverview);
        addNav(nav, "MARKET", this::showMarket);
        addNav(nav, "PERFORMANCE", this::showPerformance);
        addNav(nav, "TRADING", this::showTrading);
        addNav(nav, "INTELLIGENCE", this::showIntelligence);
        addNav(nav, "MEMORY GRAPH", this::showMemoryGraph);
        addNav(nav, "TRAINING", this::showTraining);
        addNav(nav, "AI LAB", this::showAiLab);
        addNav(nav, "WHITE PAPER", this::showWhitePaper);
        addNav(nav, "CONTROL PLANE", this::showControlPlane);
        addNav(nav, "SETTINGS", this::showSettings);
        Region spacer = new Region(); VBox.setVgrow(spacer, Priority.ALWAYS);
        notification.setWrapText(true);
        notification.getStyleClass().add("nav-status");
        nav.getChildren().addAll(spacer, new Separator(), notification);
        ScrollPane scroll = new ScrollPane(nav); scroll.setFitToWidth(true); scroll.setPrefWidth(196);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        nav.setMinWidth(0); nav.setPrefWidth(180);
        return scroll;
    }

    private void showOverview() {
        if (overviewPage != null) {
            setWorkspace(overviewPage); currentRefresh = this::refreshOverviewGraph;
            if (startupComplete) refreshOverviewGraph();
            return;
        }
        FlowPane metrics = new FlowPane(10, 10,
                metric("GLD MID", quoteValue), metric("EQUITY", equityValue), metric("NET P/L TODAY", pnlValue),
                metric("WIN RATE", winValue), metric("BOT STATE", botValue));
        metrics.getStyleClass().add("metrics");
        HBox telemetryBand = new HBox(1,
                statusItem("DATA LINK", dataLinkValue), statusItem("LIVE STREAM", streamValue),
                statusItem("DECISION MODEL", modelValue), statusItem("ENVIRONMENT", sessionValue));
        telemetryBand.getStyleClass().add("telemetry-band");
        telemetryBand.setMaxWidth(Double.MAX_VALUE);
        telemetryBand.getChildren().forEach(child -> HBox.setHgrow(child, Priority.ALWAYS));
        VBox corePanel = panel("DECISION CORE // NATIVE 3D", core3D.node());
        corePanel.setMinHeight(360);
        corePanel.setPrefHeight(360); corePanel.setMaxHeight(360); corePanel.setMinWidth(180);
        core3D.bindSize(corePanel.widthProperty().subtract(28), corePanel.heightProperty().subtract(54));
        VBox decision = panel("LATEST DECISION", decisionValue, decisionReason, overviewInspector);
        decisionValue.getStyleClass().add("decision");
        decisionReason.setWrapText(true);
        HBox center = new HBox(12, corePanel, decision);
        HBox.setHgrow(corePanel, Priority.ALWAYS);
        corePanel.setMaxWidth(Double.MAX_VALUE);
        decision.setPrefWidth(380);
        decision.setMinWidth(180);
        terminal.setEditable(false); terminal.setWrapText(false); terminal.setPrefRowCount(10);
        overviewPage = page("SYSTEM OVERVIEW", telemetryBand, metrics, center, panel("LIVE OPERATIONS LOG", terminal));
        core3D.setSelectionListener(this::inspectOverviewNode);
        setWorkspace(overviewPage);
        currentRefresh = this::refreshOverviewGraph;
        if (startupComplete) refreshOverviewGraph();
    }

    private void showMarket() {
        marketChart.setAnimated(false);
        setWorkspace(page("MARKET TELEMETRY", panel("GLD ONE-MINUTE PRICE", marketChart),
                panel("MARKET CONTRACT", new Label("Read-only bars and quote telemetry supplied by the Python gateway. JavaFX has no broker connection."))));
        refreshMarket();
        currentRefresh = this::refreshMarket;
    }

    private void showPerformance() {
        setWorkspace(page("PERFORMANCE & BACKTEST", new AnalyticsWorkspace(gateway, readWorkers, this::showError)));
    }

    private void showTrading() {
        TableView<RowData> episodes = table("Direction", "direction", "Playbook", "playbook", "Status", "status", "Quantity", "remaining_qty", "P/L", "realized_pnl");
        TableView<RowData> orders = table("Side", "side", "Type", "order_type", "Status", "status", "Qty", "qty", "Filled", "filled_qty");
        setWorkspace(page("TRADING & EXECUTION", panel("OPEN EXECUTION EPISODES", episodes), panel("RECENT BROKER ORDERS", orders)));
        currentRefresh = () -> readWorkers.execute(() -> {
            try {
                JsonNode snapshot = gateway.get("/api/snapshot");
                JsonNode recent = gateway.get("/api/v1/orders?limit=100");
                Platform.runLater(() -> {
                    episodes.setItems(rows(snapshot.path("active_episodes")));
                    orders.setItems(rows(recent));
                });
            } catch (Exception exc) { showError(exc); }
        });
        currentRefresh.run();
    }

    private void showIntelligence() {
        TableView<RowData> decisions = table("Time", "timestamp", "Decision", "decision", "Confidence", "confidence", "Regime", "regime", "Model", "model_version");
        TableView<RowData> models = table("Version", "model_version", "Type", "model_type", "Scope", "model_scope", "Status", "status", "Created", "created_at");
        setWorkspace(page("DECISION INTELLIGENCE", panel("RECENT SIGNALS", decisions), panel("MODEL REGISTRY", models)));
        currentRefresh = () -> readWorkers.execute(() -> {
            try {
                JsonNode signalRows = gateway.get("/api/decisions?limit=100");
                JsonNode modelRows = gateway.get("/api/v1/models");
                Platform.runLater(() -> { decisions.setItems(rows(signalRows)); models.setItems(rows(modelRows)); });
            } catch (Exception exc) { showError(exc); }
        });
        currentRefresh.run();
    }

    private void showMemoryGraph() {
        if (memoryWorkspace == null) {
            memoryWorkspace = new MemoryGraphWorkspace(gateway, graphWorker, this::showError);
            memoryPage = page("DECISION CORE MEMORY GRAPH", memoryWorkspace);
        }
        setWorkspace(memoryPage);
        currentRefresh = memoryWorkspace::refresh;
    }

    private void refreshOverviewGraph() {
        graphWorker.execute(() -> {
            try {
                JsonNode graph = gateway.get("/api/v1/memory-graph/summary?types=decision,market,model,playbook,risk&window=1d");
                String fingerprint = graph.path("topology_fingerprint").asText();
                GraphRenderPlan plan = fingerprint.equals(overviewGraphFingerprint) ? null : GraphRenderPlan.build(graph, overviewGraphPlan);
                Platform.runLater(() -> {
                    if (plan != null) { core3D.applyPlan(plan); overviewGraphFingerprint = plan.fingerprint(); overviewGraphPlan = plan; }
                    core3D.selectNode("decision:current");
                });
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void inspectOverviewNode(String nodeId) {
        overviewInspector.loading(nodeId.replace(':', ' '));
        graphWorker.execute(() -> {
            try {
                String encoded = java.net.URLEncoder.encode(nodeId, StandardCharsets.UTF_8).replace("+", "%20");
                JsonNode detail = gateway.get("/api/v1/memory-graph/nodes/" + encoded);
                Platform.runLater(() -> overviewInspector.show(detail));
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void showTraining() {
        setWorkspace(page("TRAINING & RESEARCH JOBS", new JobWorkspace(gateway, commands, research, runtime.projectRoot(), this::showError)));
    }

    private void showWhitePaper() {
        TextArea document = outputArea(); document.setWrapText(true); document.setPrefHeight(620);
        setWorkspace(page("BOT WHITE PAPER", document));
        readWorkers.execute(() -> {
            try {
                JsonNode response = gateway.get("/api/whitepaper");
                Platform.runLater(() -> document.setText(response.path("markdown").asText("Document unavailable")));
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void showAiLab() {
        ComboBox<String> provider = new ComboBox<>(FXCollections.observableArrayList("ollama", "kimi"));
        provider.getSelectionModel().select("ollama");
        TextField model = new TextField("llama3.2:1b");
        TextField baseUrl = new TextField("http://127.0.0.1:11434");
        PasswordField apiKey = new PasswordField();
        HumanReadableView result = new HumanReadableView();
        result.setPrefHeight(360);
        provider.setOnAction(event -> {
            boolean kimi = "kimi".equals(provider.getValue());
            model.setText(kimi ? "kimi-k2.6" : "llama3.2:1b");
            baseUrl.setText(kimi ? "https://api.moonshot.ai/v1" : "http://127.0.0.1:11434");
        });
        Button activate = actionButton("ACTIVATE", "primary", () -> {
            var request = Map.of("provider", provider.getValue(), "model", model.getText(), "base_url", baseUrl.getText(), "api_key", apiKey.getText());
            execute("Activating research provider", () -> gateway.post("/api/llm/providers/activate", request)); apiKey.clear();
        });
        Button test = actionButton("TEST CONNECTION", "secondary", () -> {
            var request = Map.of("provider", provider.getValue());
            result.show(new com.fasterxml.jackson.databind.ObjectMapper().valueToTree(Map.of(
                    "status", "testing", "message", "Sending a small generation request to " + provider.getValue())));
            research.execute(() -> {
            try {
                JsonNode response = gateway.post("/api/llm/providers/test", request);
                Platform.runLater(() -> result.show(response));
            } catch (Exception exc) { showError(exc); }
            });
        });
        TextArea prompt = new TextArea("Analyze completed trades, missed opportunities, execution quality, and model improvements.");
        Button analyze = actionButton("RUN OFFLINE REVIEW", "primary", () -> {
            JsonNode options = new com.fasterxml.jackson.databind.ObjectMapper().valueToTree(Map.of("query", prompt.getText()));
            execute("Starting offline review", () -> gateway.startJob("llm_analysis", options));
        });
        Button latest = actionButton("LATEST REVIEW", "secondary", () -> readWorkers.execute(() -> {
            try { JsonNode response = gateway.get("/api/results/llm_analysis"); Platform.runLater(() -> result.show(response)); }
            catch (Exception exc) { showError(exc); }
        }));
        HBox controls = new HBox(10, activate, test);
        setWorkspace(page("AI RESEARCH LAB",
                panel("REASONING PROVIDER", labeled("Provider", provider), labeled("Model", model), labeled("Base URL", baseUrl), labeled("API key (never displayed)", apiKey), controls),
                panel("RESEARCH TASK", prompt, new HBox(10, analyze, latest)), panel("AI RESULT", result)));
        readWorkers.execute(() -> {
            try {
                JsonNode config = gateway.get("/api/settings");
                JsonNode catalog = gateway.get("/api/llm/providers");
                Platform.runLater(() -> {
                    provider.setValue(config.path("llm_provider").asText("ollama"));
                    model.setText(config.path("llm_model").asText()); baseUrl.setText(config.path("llm_base_url").asText());
                    result.show(catalog);
                });
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void showControlPlane() {
        HumanReadableView health = new HumanReadableView();
        health.setPrefHeight(360);
        TableView<RowData> audit = table("Time", "timestamp", "Event", "event_type", "Action", "action", "Status", "status", "Actor", "actor_id");
        Button refresh = actionButton("VERIFY CONTROL PLANE", "primary", () -> readWorkers.execute(() -> {
            try {
                JsonNode state = gateway.get("/api/v1/system/status");
                JsonNode readiness = gateway.get("/api/v1/system/readiness");
                JsonNode integrity = gateway.get("/api/v1/audit/verify");
                JsonNode events = gateway.get("/api/v1/audit/events?limit=100");
                Platform.runLater(() -> {
                    var combined = new com.fasterxml.jackson.databind.ObjectMapper().createObjectNode();
                    combined.set("system_status", state);
                    combined.set("readiness", readiness);
                    combined.set("audit_integrity", integrity);
                    health.show(combined);
                    audit.setItems(rows(events));
                });
            } catch (Exception exc) { showError(exc); }
        }));
        setWorkspace(page("CONTROL PLANE", refresh, panel("HEALTH & READINESS", health), panel("TAMPER-EVIDENT AUDIT EVENTS", audit)));
        refresh.fire();
    }

    private void showSettings() {
        PasswordField alpacaKey = new PasswordField();
        PasswordField alpacaSecret = new PasswordField();
        ComboBox<String> feed = new ComboBox<>(FXCollections.observableArrayList("iex", "sip"));
        feed.getSelectionModel().select("iex");
        Label warning = new Label("Blank secret fields preserve existing values. The gateway enforces Alpaca paper mode.");
        warning.setWrapText(true);
        CheckBox reduceMotion = new CheckBox("Reduce interface motion"); reduceMotion.setSelected(reducedMotion);
        reduceMotion.setOnAction(event -> {
            reducedMotion = reduceMotion.isSelected(); preferences.putBoolean("reducedMotion", reducedMotion); core3D.setReducedMotion(reducedMotion);
        });
        Button save = actionButton("SAVE SECURE SETTINGS", "primary", () -> {
            var request = Map.of("alpaca_api_key", alpacaKey.getText(), "alpaca_secret_key", alpacaSecret.getText(), "alpaca_data_feed", feed.getValue());
            execute("Saving local settings", () -> gateway.post("/api/settings", request));
            alpacaKey.clear(); alpacaSecret.clear();
        });
        save.setDisable(true);
        setWorkspace(page("SECURE SETTINGS", panel("INTERFACE ACCESSIBILITY", reduceMotion), panel("ALPACA PAPER CREDENTIALS", labeled("API key", alpacaKey), labeled("Secret key", alpacaSecret), labeled("Data feed", feed), warning, save)));
        readWorkers.execute(() -> {
            try {
                JsonNode config = gateway.get("/api/settings");
                Platform.runLater(() -> { feed.setValue(config.path("alpaca_data_feed").asText("iex")); save.setDisable(false); });
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void startTelemetry() {
        scheduler.scheduleAtFixedRate(() -> {
            if (!telemetryPollQueued.compareAndSet(false, true)) return;
            telemetryWorker.execute(() -> {
                try {
                    pollTelemetry();
                } finally {
                    telemetryPollQueued.set(false);
                }
            });
        }, 2, 5, TimeUnit.SECONDS);
    }

    private void pollTelemetry() {
        try {
            if (socket == null || socket.isInputClosed()) {
                eventSequence = 0;
                socket = gateway.openEvents(event -> {
                    if (event.path("version").asInt() != 1) return;
                    synchronized (this) {
                        long sequence = event.path("sequence").asLong();
                        if (sequence <= eventSequence) return;
                        eventSequence = sequence;
                    }
                    lastEventNanos = System.nanoTime();
                    Platform.runLater(() -> applySnapshot(event.path("payload")));
                }, error -> { lastEventNanos = 0; showTelemetryError(error); });
                socketOpenedNanos = System.nanoTime();
                return;
            }
            long now = System.nanoTime();
            boolean initialGraceExpired = lastEventNanos == 0
                    && socketOpenedNanos > 0
                    && now - socketOpenedNanos > TimeUnit.SECONDS.toNanos(8);
            boolean establishedStreamStale = lastEventNanos > 0
                    && now - lastEventNanos > TimeUnit.SECONDS.toNanos(8);
            if (initialGraceExpired || establishedStreamStale) {
                JsonNode snapshot = gateway.get("/api/snapshot");
                JsonNode logs = gateway.get("/api/logs/bot?lines=80");
                ((com.fasterxml.jackson.databind.node.ObjectNode) snapshot).set("log_tail", logs.path("lines"));
                Platform.runLater(() -> applySnapshot(snapshot));
            }
        } catch (Exception exc) {
            showTelemetryError(exc);
        }
    }

    private void refreshReadiness() {
        try {
            JsonNode readiness = gateway.get("/api/v1/system/readiness");
            JsonNode providers = gateway.get("/api/llm/providers");
            ReadinessSnapshot resolved = ReadinessSnapshot.from(readiness, providers);
            Platform.runLater(() -> applyReadiness(resolved));
        } catch (Exception exc) {
            Platform.runLater(() -> {
                tradingAllowed = false;
                readinessStrip.degraded();
                if (paperStart != null) paperStart.setDisable(true);
            });
        }
    }

    private void applyReadiness(ReadinessSnapshot resolved) {
        readinessSnapshot = resolved;
        tradingAllowed = resolved.tradingAllowed();
        readinessStrip.apply(resolved);
        if (paperStart == null) return;
        String bot = latestSnapshot == null
                ? "OFFLINE"
                : latestSnapshot.path("control_plane").path("paper_process").path("state").asText("offline").toUpperCase();
        paperStart.setDisable(!tradingAllowed || bot.matches("RUNNING|STOPPING|UNKNOWN"));
        if (bot.matches("RUNNING|STOPPING")) {
            readinessStrip.setTrading("RUNNING".equals(bot) ? "ACTIVE" : "STOPPING");
        }
    }

    private void applySnapshot(JsonNode snapshot) {
        if (snapshot == null || snapshot.isMissingNode()) return;
        latestSnapshot = snapshot;
        JsonNode quote = snapshot.path("quote");
        double bid = number(quote, "bid_price");
        double ask = number(quote, "ask_price");
        double mid = bid > 0 && ask > 0 ? (bid + ask) / 2 : number(quote, "price");
        quoteValue.setText(mid > 0 ? money(mid) : "--");
        JsonNode account = snapshot.path("account");
        equityValue.setText(account.hasNonNull("equity") ? money(number(account, "equity")) : "--");
        JsonNode performance = snapshot.path("performance");
        double pnl = number(performance, "net_pnl");
        pnlValue.setText(performance.hasNonNull("net_pnl") ? money(pnl) : "--");
        pnlValue.setStyle("-fx-text-fill:" + (pnl < 0 ? "#ff4d6d" : "#19f7a7") + ";");
        winValue.setText(number(performance, "trades") > 0 ? String.format("%.1f%%", number(performance, "win_rate") * 100) : "--");
        JsonNode paper = snapshot.path("control_plane").path("paper_process");
        String bot = paper.path("state").asText("offline").toUpperCase();
        botValue.setText(bot.matches("RUNNING|STOPPING") ? bot : "OFFLINE");
        paperStart.setDisable(!tradingAllowed || bot.matches("RUNNING|STOPPING|UNKNOWN"));
        paperStop.setDisable(!bot.matches("RUNNING"));
        readinessStrip.setTrading(bot.matches("RUNNING|STOPPING")
                ? ("RUNNING".equals(bot) ? "ACTIVE" : "STOPPING")
                : readinessSnapshot.tradingState());
        String state = snapshot.path("control_plane").path("state").asText("UNKNOWN");
        updateSystemState(state);
        core3D.setState(state);
        JsonNode signal = snapshot.path("signal");
        decisionValue.setText(signal.path("decision").asText("NO DATA"));
        JsonNode explanation = signal.path("explanation");
        decisionReason.setText(explanation.path("summary").asText(signal.path("reason").asText("Waiting for the first backend decision.")));
        dataLinkValue.setText(snapshot.path("database_available").asBoolean() ? "SYNCHRONIZED" : "UNAVAILABLE");
        streamValue.setText(signal.path("stream_connected").asBoolean() ? "CONNECTED" : "OFFLINE");
        String model = signal.path("model_version").asText("");
        modelValue.setText(model.isBlank() ? "RULES + SHADOW" : compact(model, 22));
        sessionValue.setText(snapshot.path("control_plane").path("environment").asText("paper").toUpperCase());
        if (snapshot.has("log_tail")) {
            List<String> lines = new ArrayList<>(); snapshot.path("log_tail").forEach(line -> lines.add(line.asText()));
            terminal.setText(String.join("\n", lines)); terminal.positionCaret(terminal.getLength());
        }
        notification.setText("TELEMETRY LINKED\n" + snapshot.path("server_time").asText("local gateway"));
    }

    private void refreshMarket() {
        readWorkers.execute(() -> {
            try {
                JsonNode bars = gateway.get("/api/v1/market/GLD/bars?limit=390").path("bars");
                XYChart.Series<Number, Number> series = new XYChart.Series<>();
                int index = 0;
                for (JsonNode bar : bars) series.getData().add(new XYChart.Data<>(index++, number(bar, "close")));
                Platform.runLater(() -> marketChart.getData().setAll(List.of(series)));
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void execute(String activity, ThrowingSupplier action) {
        notification.setText(activity + "...");
        commands.execute(() -> {
            try {
                JsonNode result = action.get();
                Platform.runLater(() -> notification.setText(result.path("accepted").asBoolean(true)
                        ? "COMMAND RECEIVED\n" + HumanReadableFormatter.value("correlation_id", result.path("correlation_id"))
                        : HumanReadableFormatter.plainText(result)));
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void showError(Throwable error) {
        Platform.runLater(() -> {
            notification.setText("REQUEST FAILED\n" + error.getMessage());
        });
    }

    private void showTelemetryError(Throwable error) {
        Platform.runLater(() -> {
            notification.setText("TELEMETRY DEGRADED\n" + error.getMessage());
            updateSystemState("DEGRADED");
            core3D.setState("DEGRADED");
            paperStart.setDisable(true);
            readinessStrip.setTrading("BLOCKED");
            readinessStrip.setMarket("DEGRADED");
        });
    }

    private static void runOnFxAndWait(Runnable action) throws Exception {
        if (Platform.isFxApplicationThread()) {
            action.run();
            return;
        }
        CountDownLatch completed = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.runLater(() -> {
            try {
                action.run();
            } catch (Throwable exc) {
                failure.set(exc);
            } finally {
                completed.countDown();
            }
        });
        completed.await();
        if (failure.get() != null) throw new IllegalStateException("JavaFX startup composition failed", failure.get());
    }

    private void setWorkspace(Node node) {
        currentRefresh = () -> {};
        if (workspace.getChildren().size() == 1 && workspace.getChildren().get(0) == node) return;
        Node previous = workspace.getChildren().isEmpty() ? null : workspace.getChildren().get(workspace.getChildren().size() - 1);
        if (node.getParent() == workspace) workspace.getChildren().remove(node);
        workspace.getChildren().add(node);
        if (reducedMotion || previous == null) {
            node.setOpacity(1); node.setTranslateY(0);
            if (previous != null) workspace.getChildren().remove(previous);
            return;
        }
        node.setOpacity(0);
        node.setTranslateY(5);
        FadeTransition fade = new FadeTransition(Duration.millis(180), node);
        fade.setFromValue(0); fade.setToValue(1);
        TranslateTransition lift = new TranslateTransition(Duration.millis(180), node);
        lift.setFromY(5); lift.setToY(0);
        FadeTransition fadePrevious = new FadeTransition(Duration.millis(140), previous);
        fadePrevious.setFromValue(previous.getOpacity()); fadePrevious.setToValue(0);
        ParallelTransition transition = new ParallelTransition(fade, lift, fadePrevious);
        transition.setOnFinished(event -> workspace.getChildren().remove(previous));
        transition.play();
    }

    private static VBox page(String title, Node... nodes) {
        Label heading = new Label(title); heading.getStyleClass().add("page-title");
        Label context = new Label("MASHCORP / OPERATIONS WORKSPACE / LOCAL CONTROL");
        context.getStyleClass().add("page-context");
        Region rule = new Region(); rule.getStyleClass().add("page-rule"); HBox.setHgrow(rule, Priority.ALWAYS);
        VBox titles = new VBox(2, context, heading);
        HBox header = new HBox(16, titles, rule); header.setAlignment(Pos.CENTER_LEFT);
        VBox content = new VBox(14, header); content.getChildren().addAll(nodes);
        content.getStyleClass().add("page");
        ScrollPane scroll = new ScrollPane(content); scroll.setFitToWidth(true); scroll.getStyleClass().add("page-scroll");
        VBox wrapper = new VBox(scroll); VBox.setVgrow(scroll, Priority.ALWAYS); return wrapper;
    }

    private static VBox panel(String title, Node... nodes) {
        Label heading = new Label(title); heading.getStyleClass().add("panel-title");
        Region rule = new Region(); rule.getStyleClass().add("panel-rule"); HBox.setHgrow(rule, Priority.ALWAYS);
        HBox header = new HBox(10, heading, rule); header.setAlignment(Pos.CENTER_LEFT);
        VBox box = new VBox(10, header); box.getChildren().addAll(nodes); box.getStyleClass().add("panel");
        return box;
    }

    private static VBox metric(String label, Label value) {
        Label caption = new Label(label); caption.getStyleClass().add("metric-caption");
        VBox box = new VBox(7, caption, value); box.getStyleClass().addAll("metric-card", metricAccent(label)); box.setPrefWidth(180); return box;
    }

    private static Label metricValue(String text) { Label label = new Label(text); label.getStyleClass().add("metric-value"); return label; }

    private static VBox labeled(String label, Node control) {
        Label caption = new Label(label); caption.getStyleClass().add("field-label");
        VBox box = new VBox(5, caption, control); VBox.setVgrow(control, Priority.NEVER); return box;
    }

    private static Button actionButton(String text, String style, Runnable action) {
        Button button = new Button(text); button.getStyleClass().add(style); button.setOnAction(event -> action.run()); return button;
    }

    private void addNav(VBox nav, String text, Runnable action) {
        ToggleButton button = new ToggleButton(String.format("%02d  %s", ++navigationIndex, text));
        button.setToggleGroup(navigationGroup); button.setMaxWidth(Double.MAX_VALUE); button.getStyleClass().add("nav-button");
        button.setOnAction(event -> { button.setSelected(true); action.run(); });
        if (navigationGroup.getSelectedToggle() == null) button.setSelected(true);
        nav.getChildren().add(button);
    }

    private void updateSystemState(String state) {
        String normalized = state == null || state.isBlank() ? "UNKNOWN" : state.toUpperCase();
        systemState.setText(normalized);
        systemState.getStyleClass().removeIf(style -> style.startsWith("state-") && !style.equals("state-pill"));
        systemState.getStyleClass().add("state-" + normalized.toLowerCase());
    }

    private static VBox statusItem(String label, Label value) {
        Label caption = new Label(label); caption.getStyleClass().add("status-caption");
        VBox box = new VBox(3, caption, value); box.getStyleClass().add("status-cell");
        box.setPadding(new Insets(9, 14, 9, 14)); box.setMaxWidth(Double.MAX_VALUE);
        return box;
    }

    private static Label statusValue(String text) {
        Label value = new Label(text); value.getStyleClass().add("status-value"); return value;
    }

    private static String metricAccent(String label) {
        return switch (label) {
            case "EQUITY" -> "accent-cyan";
            case "NET P/L TODAY" -> "accent-emerald";
            case "WIN RATE" -> "accent-violet";
            case "BOT STATE" -> "accent-amber";
            default -> "accent-blue";
        };
    }

    private static String compact(String value, int limit) {
        if (value == null || value.length() <= limit) return value;
        return value.substring(0, Math.max(1, limit - 3)) + "...";
    }

    private static LineChart<Number, Number> lineChart(String xLabel, String yLabel) {
        NumberAxis x = new NumberAxis(); NumberAxis y = new NumberAxis();
        x.setLabel(xLabel); y.setLabel(yLabel); y.setForceZeroInRange(false);
        LineChart<Number, Number> chart = new LineChart<>(x, y); chart.setCreateSymbols(false); chart.setLegendVisible(false); chart.setPrefHeight(360); return chart;
    }

    private static TextArea outputArea() { TextArea area = new TextArea(); area.setEditable(false); area.setWrapText(false); area.setPrefRowCount(12); return area; }

    @SuppressWarnings("unchecked")
    private static TableView<RowData> table(String... columns) {
        TableView<RowData> table = new TableView<>(); table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN); table.setPrefHeight(250);
        for (int index = 0; index < columns.length; index += 2) {
            String title = columns[index], key = columns[index + 1];
            TableColumn<RowData, String> column = new TableColumn<>(title);
            column.setCellValueFactory(cell -> new javafx.beans.property.SimpleStringProperty(cell.getValue().value(key)));
            table.getColumns().add(column);
        }
        return table;
    }

    private static javafx.collections.ObservableList<RowData> rows(JsonNode values) {
        List<RowData> rows = new ArrayList<>(); if (values != null && values.isArray()) values.forEach(value -> rows.add(new RowData(value)));
        return FXCollections.observableArrayList(rows);
    }

    private static double number(JsonNode node, String key) {
        if (node == null || node.isMissingNode() || node.isNull()) return 0;
        JsonNode value = node.path(key); if (value.isNumber()) return value.asDouble();
        try { return Double.parseDouble(value.asText("0")); } catch (NumberFormatException ignored) { return 0; }
    }

    private static String money(double value) { return String.format("$%,.2f", value); }

    @Override
    public void stop() {
        scheduler.shutdownNow();
        startupWorker.shutdownNow();
        telemetryWorker.shutdownNow();
        readWorkers.shutdownNow();
        graphWorker.shutdownNow();
        commands.shutdownNow();
        research.shutdownNow();
        writeClientPerformance();
        if (socket != null) socket.abort();
        if (runtime != null) runtime.close();
        try { Files.deleteIfExists(runtime.projectRoot().resolve("logs/dashboard/javafx_client.pid")); }
        catch (Exception ignored) { }
    }

    private void writeClientPid() {
        try {
            java.nio.file.Path path = runtime.projectRoot().resolve("logs/dashboard/javafx_client.pid");
            Files.createDirectories(path.getParent());
            Files.writeString(path,
                    "{\"pid\":" + ProcessHandle.current().pid() + ",\"started_at\":\""
                            + ProcessHandle.current().info().startInstant().orElse(Instant.now()) + "\"}",
                    StandardCharsets.US_ASCII);
        } catch (Exception exc) {
            showError(exc);
        }
    }

    private record RowData(JsonNode node) {
        String value(String key) { return HumanReadableFormatter.value(key, node.path(key)); }
    }

    private void writeClientPerformance() {
        if (gateway == null || runtime == null) return;
        try {
            java.nio.file.Path destination = runtime.projectRoot().resolve("logs/dashboard/javafx_client_latency.json");
            Files.createDirectories(destination.getParent());
            new com.fasterxml.jackson.databind.ObjectMapper()
                    .writerWithDefaultPrettyPrinter()
                    .writeValue(destination.toFile(), gateway.performanceSnapshot());
        } catch (Exception ignored) {
            // Shutdown diagnostics must never prevent the dashboard from closing.
        }
    }

    private static java.util.concurrent.ThreadFactory threadFactory(String prefix) {
        java.util.concurrent.atomic.AtomicInteger sequence = new java.util.concurrent.atomic.AtomicInteger();
        return runnable -> {
            Thread thread = new Thread(runnable, prefix + "-" + sequence.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        };
    }

    @FunctionalInterface private interface ThrowingSupplier { JsonNode get() throws Exception; }
}
