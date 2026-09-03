package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.application.Application;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.Scene;
import javafx.scene.chart.LineChart;
import javafx.scene.chart.NumberAxis;
import javafx.scene.chart.XYChart;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.scene.paint.Color;
import javafx.stage.Stage;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

public final class JarvisApplication extends Application {
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "jarvis-gateway-client");
        thread.setDaemon(true);
        return thread;
    });
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
    private final TextArea terminal = new TextArea();
    private final DecisionCore3D core3D = new DecisionCore3D();
    private final LineChart<Number, Number> marketChart = lineChart("Minute", "GLD");
    private GatewayRuntime runtime;
    private GatewayClient gateway;
    private JsonNode latestSnapshot;
    private java.net.http.WebSocket socket;
    private volatile long lastEventNanos;
    private final java.util.concurrent.ExecutorService commands = Executors.newFixedThreadPool(2, r -> {
        Thread thread = new Thread(r, "jarvis-operator-command"); thread.setDaemon(true); return thread;
    });
    private final java.util.concurrent.ExecutorService research = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "jarvis-research"); thread.setDaemon(true); return thread;
    });
    private Button paperStart;
    private Button paperStop;
    private Runnable currentRefresh = () -> {};
    private long eventSequence;

    public static void launchApplication(String[] args) {
        launch(args);
    }

    @Override
    public void init() throws Exception {
        runtime = new GatewayRuntime();
        runtime.start();
        gateway = new GatewayClient(runtime.baseUri(), runtime.token());
    }

    @Override
    public void start(Stage stage) {
        writeClientPid();
        BorderPane shell = new BorderPane();
        shell.getStyleClass().add("app-shell");
        shell.setTop(buildTopBar());
        shell.setLeft(buildNavigation());
        shell.setCenter(workspace);
        showOverview();

        Scene scene = new Scene(shell, 1500, 920, Color.web("#020608"));
        scene.getStylesheets().add(getClass().getResource("/com/mashcorp/jarvis/jarvis.css").toExternalForm());
        stage.setTitle("Mashcorp GLD Command Center");
        stage.setMinWidth(850);
        stage.setMinHeight(600);
        stage.setScene(scene);
        stage.widthProperty().addListener((observable, before, width) -> {
            boolean wide = width.doubleValue() >= 1100; clock.setVisible(wide); clock.setManaged(wide);
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
        String smokeDirectory = System.getenv("JARVIS_SMOKE_DIR");
        if (smokeDirectory != null && !smokeDirectory.isBlank()) {
            DesktopSmokeCheck.run(stage, java.nio.file.Path.of(smokeDirectory), List.of(
                    this::showOverview, this::showOverview, this::showMarket, this::showPerformance,
                    this::showTrading, this::showIntelligence, this::showTraining, this::showAiLab,
                    this::showWhitePaper, this::showControlPlane, this::showSettings, this::showOverview));
        }

        startTelemetry();
        worker.scheduleWithFixedDelay(() -> Platform.runLater(() -> currentRefresh.run()), 10, 10, TimeUnit.SECONDS);
        worker.scheduleAtFixedRate(() -> Platform.runLater(() ->
                clock.setText(DateTimeFormatter.ofPattern("EEE, dd MMM yyyy  HH:mm:ss")
                        .withZone(ZoneId.systemDefault()).format(Instant.now()))), 0, 1, TimeUnit.SECONDS);
    }

    private Node buildTopBar() {
        Label title = new Label("MASHCORP // GLD COMMAND CENTER");
        title.getStyleClass().add("brand-title");
        systemState.getStyleClass().add("state-pill");
        clock.getStyleClass().add("clock");
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        paperStop = actionButton("STOP BOT", "danger", () -> execute("Requesting safety shutdown", gateway::stopPaper));
        paperStart = actionButton("START PAPER", "primary", () -> execute("Starting paper bot", gateway::startPaper));
        paperStart.setDisable(true); paperStop.setDisable(true);
        paperStart.setMinWidth(Region.USE_PREF_SIZE); paperStop.setMinWidth(Region.USE_PREF_SIZE);
        systemState.setMinWidth(Region.USE_PREF_SIZE);
        title.setMinWidth(120); title.setMaxWidth(360); title.setWrapText(true);
        clock.setMinWidth(0); clock.setMaxWidth(210); clock.setWrapText(true);
        HBox bar = new HBox(10, title, systemState, spacer, clock, paperStop, paperStart);
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
        nav.getChildren().addAll(monogram, caption, new Separator());
        addNav(nav, "OVERVIEW", this::showOverview);
        addNav(nav, "MARKET", this::showMarket);
        addNav(nav, "PERFORMANCE", this::showPerformance);
        addNav(nav, "TRADING", this::showTrading);
        addNav(nav, "INTELLIGENCE", this::showIntelligence);
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
        FlowPane metrics = new FlowPane(10, 10,
                metric("GLD MID", quoteValue), metric("EQUITY", equityValue), metric("NET P/L TODAY", pnlValue),
                metric("WIN RATE", winValue), metric("BOT STATE", botValue));
        metrics.getStyleClass().add("metrics");
        VBox corePanel = panel("DECISION CORE // NATIVE 3D", core3D.node());
        corePanel.setMinHeight(360);
        corePanel.setPrefHeight(360); corePanel.setMaxHeight(360); corePanel.setMinWidth(180);
        core3D.bindSize(corePanel.widthProperty().subtract(28), corePanel.heightProperty().subtract(54));
        VBox decision = panel("LATEST DECISION", decisionValue, decisionReason);
        decisionValue.getStyleClass().add("decision");
        decisionReason.setWrapText(true);
        HBox center = new HBox(12, corePanel, decision);
        HBox.setHgrow(corePanel, Priority.ALWAYS);
        corePanel.setMaxWidth(Double.MAX_VALUE);
        decision.setPrefWidth(380);
        decision.setMinWidth(180);
        terminal.setEditable(false); terminal.setWrapText(false); terminal.setPrefRowCount(10);
        setWorkspace(page("SYSTEM OVERVIEW", metrics, center, panel("LIVE OPERATIONS LOG", terminal)));
    }

    private void showMarket() {
        marketChart.setAnimated(false);
        setWorkspace(page("MARKET TELEMETRY", panel("GLD ONE-MINUTE PRICE", marketChart),
                panel("MARKET CONTRACT", new Label("Read-only bars and quote telemetry supplied by the Python gateway. JavaFX has no broker connection."))));
        refreshMarket();
        currentRefresh = this::refreshMarket;
    }

    private void showPerformance() {
        setWorkspace(page("PERFORMANCE & BACKTEST", new AnalyticsWorkspace(gateway, worker, this::showError)));
    }

    private void showTrading() {
        TableView<RowData> episodes = table("Direction", "direction", "Playbook", "playbook", "Status", "status", "Quantity", "remaining_qty", "P/L", "realized_pnl");
        TableView<RowData> orders = table("Side", "side", "Type", "order_type", "Status", "status", "Qty", "qty", "Filled", "filled_qty");
        setWorkspace(page("TRADING & EXECUTION", panel("OPEN EXECUTION EPISODES", episodes), panel("RECENT BROKER ORDERS", orders)));
        currentRefresh = () -> worker.execute(() -> {
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
        currentRefresh = () -> worker.execute(() -> {
            try {
                JsonNode signalRows = gateway.get("/api/decisions?limit=100");
                JsonNode modelRows = gateway.get("/api/v1/models");
                Platform.runLater(() -> { decisions.setItems(rows(signalRows)); models.setItems(rows(modelRows)); });
            } catch (Exception exc) { showError(exc); }
        });
        currentRefresh.run();
    }

    private void showTraining() {
        setWorkspace(page("TRAINING & RESEARCH JOBS", new JobWorkspace(gateway, commands, research, runtime.projectRoot(), this::showError)));
    }

    private void showWhitePaper() {
        TextArea document = outputArea(); document.setWrapText(true); document.setPrefHeight(620);
        setWorkspace(page("BOT WHITE PAPER", document));
        worker.execute(() -> {
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
        TextArea result = outputArea();
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
            var request = Map.of("provider", provider.getValue()); result.setText("Testing generation...");
            research.execute(() -> {
            try {
                JsonNode response = gateway.post("/api/llm/providers/test", request);
                Platform.runLater(() -> result.setText(response.toPrettyString()));
            } catch (Exception exc) { showError(exc); }
            });
        });
        TextArea prompt = new TextArea("Analyze completed trades, missed opportunities, execution quality, and model improvements.");
        Button analyze = actionButton("RUN OFFLINE REVIEW", "primary", () -> {
            JsonNode options = new com.fasterxml.jackson.databind.ObjectMapper().valueToTree(Map.of("query", prompt.getText()));
            execute("Starting offline review", () -> gateway.startJob("llm_analysis", options));
        });
        Button latest = actionButton("LATEST REVIEW", "secondary", () -> worker.execute(() -> {
            try { JsonNode response = gateway.get("/api/results/llm_analysis"); Platform.runLater(() -> result.setText(response.toPrettyString())); }
            catch (Exception exc) { showError(exc); }
        }));
        HBox controls = new HBox(10, activate, test);
        setWorkspace(page("AI RESEARCH LAB",
                panel("REASONING PROVIDER", labeled("Provider", provider), labeled("Model", model), labeled("Base URL", baseUrl), labeled("API key (never displayed)", apiKey), controls),
                panel("RESEARCH TASK", prompt, new HBox(10, analyze, latest)), panel("AI RESULT", result)));
        worker.execute(() -> {
            try {
                JsonNode config = gateway.get("/api/settings");
                JsonNode catalog = gateway.get("/api/llm/providers");
                Platform.runLater(() -> {
                    provider.setValue(config.path("llm_provider").asText("ollama"));
                    model.setText(config.path("llm_model").asText()); baseUrl.setText(config.path("llm_base_url").asText());
                    result.setText(catalog.toPrettyString());
                });
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void showControlPlane() {
        TextArea health = outputArea();
        TableView<RowData> audit = table("Time", "timestamp", "Event", "event_type", "Action", "action", "Status", "status", "Actor", "actor_id");
        Button refresh = actionButton("VERIFY CONTROL PLANE", "primary", () -> worker.execute(() -> {
            try {
                JsonNode state = gateway.get("/api/v1/system/status");
                JsonNode readiness = gateway.get("/api/v1/system/readiness");
                JsonNode integrity = gateway.get("/api/v1/audit/verify");
                JsonNode events = gateway.get("/api/v1/audit/events?limit=100");
                Platform.runLater(() -> {
                    health.setText(state.toPrettyString() + "\n" + readiness.toPrettyString() + "\n" + integrity.toPrettyString());
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
        Button save = actionButton("SAVE SECURE SETTINGS", "primary", () -> {
            var request = Map.of("alpaca_api_key", alpacaKey.getText(), "alpaca_secret_key", alpacaSecret.getText(), "alpaca_data_feed", feed.getValue());
            execute("Saving local settings", () -> gateway.post("/api/settings", request));
            alpacaKey.clear(); alpacaSecret.clear();
        });
        save.setDisable(true);
        setWorkspace(page("SECURE SETTINGS", panel("ALPACA PAPER CREDENTIALS", labeled("API key", alpacaKey), labeled("Secret key", alpacaSecret), labeled("Data feed", feed), warning, save)));
        worker.execute(() -> {
            try {
                JsonNode config = gateway.get("/api/settings");
                Platform.runLater(() -> { feed.setValue(config.path("alpaca_data_feed").asText("iex")); save.setDisable(false); });
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void startTelemetry() {
        worker.scheduleAtFixedRate(() -> {
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
                    }, error -> { lastEventNanos = 0; showError(error); });
                }
                if (lastEventNanos == 0 || System.nanoTime() - lastEventNanos > TimeUnit.SECONDS.toNanos(8)) {
                    JsonNode snapshot = gateway.get("/api/snapshot");
                    JsonNode logs = gateway.get("/api/logs/bot?lines=80");
                    ((com.fasterxml.jackson.databind.node.ObjectNode) snapshot).set("log_tail", logs.path("lines"));
                    Platform.runLater(() -> applySnapshot(snapshot));
                }
            }
            catch (Exception exc) { showError(exc); }
        }, 2, 5, TimeUnit.SECONDS);
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
        botValue.setText(bot);
        paperStart.setDisable(bot.matches("RUNNING|STOPPING|UNKNOWN"));
        paperStop.setDisable(!bot.matches("RUNNING"));
        String state = snapshot.path("control_plane").path("state").asText("UNKNOWN");
        systemState.setText(state);
        core3D.setState(state);
        JsonNode signal = snapshot.path("signal");
        decisionValue.setText(signal.path("decision").asText("NO DATA"));
        decisionReason.setText(signal.path("reason").asText("Waiting for the first backend decision."));
        if (snapshot.has("log_tail")) {
            List<String> lines = new ArrayList<>(); snapshot.path("log_tail").forEach(line -> lines.add(line.asText()));
            terminal.setText(String.join("\n", lines)); terminal.positionCaret(terminal.getLength());
        }
        notification.setText("TELEMETRY LINKED\n" + snapshot.path("server_time").asText("local gateway"));
    }

    private void refreshMarket() {
        worker.execute(() -> {
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
                Platform.runLater(() -> notification.setText(result.path("accepted").asBoolean(true) ? "COMMAND RECEIVED\n" + result.path("correlation_id").asText("See result/status") : result.toPrettyString()));
            } catch (Exception exc) { showError(exc); }
        });
    }

    private void showError(Throwable error) {
        Platform.runLater(() -> {
            notification.setText("DEGRADED\n" + error.getMessage());
            systemState.setText("DEGRADED"); core3D.setState("DEGRADED");
            paperStart.setDisable(true);
        });
    }

    private void setWorkspace(Node node) { currentRefresh = () -> {}; workspace.getChildren().setAll(node); }

    private static VBox page(String title, Node... nodes) {
        Label heading = new Label(title); heading.getStyleClass().add("page-title");
        VBox content = new VBox(12, heading); content.getChildren().addAll(nodes);
        content.getStyleClass().add("page");
        ScrollPane scroll = new ScrollPane(content); scroll.setFitToWidth(true); scroll.getStyleClass().add("page-scroll");
        VBox wrapper = new VBox(scroll); VBox.setVgrow(scroll, Priority.ALWAYS); return wrapper;
    }

    private static VBox panel(String title, Node... nodes) {
        Label heading = new Label(title); heading.getStyleClass().add("panel-title");
        VBox box = new VBox(10, heading); box.getChildren().addAll(nodes); box.getStyleClass().add("panel");
        return box;
    }

    private static VBox metric(String label, Label value) {
        Label caption = new Label(label); caption.getStyleClass().add("metric-caption");
        VBox box = new VBox(7, caption, value); box.getStyleClass().add("metric-card"); box.setPrefWidth(180); return box;
    }

    private static Label metricValue(String text) { Label label = new Label(text); label.getStyleClass().add("metric-value"); return label; }

    private static VBox labeled(String label, Node control) {
        Label caption = new Label(label); caption.getStyleClass().add("field-label");
        VBox box = new VBox(5, caption, control); VBox.setVgrow(control, Priority.NEVER); return box;
    }

    private static Button actionButton(String text, String style, Runnable action) {
        Button button = new Button(text); button.getStyleClass().add(style); button.setOnAction(event -> action.run()); return button;
    }

    private static void addNav(VBox nav, String text, Runnable action) {
        Button button = new Button(text); button.setMaxWidth(Double.MAX_VALUE); button.getStyleClass().add("nav-button");
        button.setOnAction(event -> action.run()); nav.getChildren().add(button);
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
        worker.shutdownNow();
        commands.shutdownNow(); research.shutdownNow();
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
        String value(String key) { JsonNode value = node.path(key); return value.isMissingNode() || value.isNull() ? "" : value.asText(); }
    }

    @FunctionalInterface private interface ThrowingSupplier { JsonNode get() throws Exception; }
}
