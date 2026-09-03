package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.stage.FileChooser;
import javafx.util.Duration;

import java.nio.file.Path;
import java.time.LocalDate;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** Catalog-driven job forms. Python retains validation, scheduling and promotion authority. */
public final class JobWorkspace extends VBox {
    private final GatewayClient gateway;
    private final Executor commands;
    private final Executor reads;
    private final Consumer<Throwable> errors;
    private final Path root;
    private final ObjectMapper json = new ObjectMapper();
    private final ComboBox<String> action = new ComboBox<>(FXCollections.observableArrayList(
            "labels", "ml_train", "ml_loop", "transformer_dataset", "transformer_train",
            "transformer_loop", "transformer_batch", "backtest", "research", "report",
            "llm_analysis", "llm_cycle", "llm_macro", "llm_coach", "llm_council", "llm_advice", "llm_labels", "llm_train"));
    private final ComboBox<String> scope = new ComboBox<>(FXCollections.observableArrayList("fast_microstructure", "minute", "news_event", "exit"));
    private final ComboBox<String> source = new ComboBox<>(FXCollections.observableArrayList("raw", "decisions", "auto"));
    private final ComboBox<Artifact> artifact = new ComboBox<>();
    private final ListView<Artifact> batch = new ListView<>();
    private final TextField database = new TextField();
    private final TextField output = new TextField();
    private final TextField classical = new TextField();
    private final DatePicker start = new DatePicker(LocalDate.of(2021, 1, 1));
    private final DatePicker end = new DatePicker(LocalDate.of(2026, 1, 1));
    private final Spinner<Integer> samples = new Spinner<>(100, 2000000, 50000, 1000);
    private final Spinner<Integer> epochs = new Spinner<>(1, 100, 10);
    private final Spinner<Integer> lookback = new Spinner<>(1, 3650, 90);
    private final Spinner<Integer> interval = new Spinner<>(5, 1440, 60, 5);
    private final TextArea query = new TextArea("Review execution quality, completed trades and missed opportunities.");
    private final VBox fields = new VBox(10);
    private final TextArea logs = new TextArea();
    private final Label status = new Label("Choose a job");
    private final AtomicBoolean reading = new AtomicBoolean();
    private final Button run = new Button("START JOB");
    private JsonNode catalog;

    public JobWorkspace(GatewayClient gateway, Executor commands, Executor reads, Path root, Consumer<Throwable> errors) {
        super(12); this.gateway = gateway; this.commands = commands; this.reads = reads; this.root = root; this.errors = errors;
        getStyleClass().add("panel");
        scope.setValue("fast_microstructure"); source.setValue("raw"); action.setValue("labels");
        batch.getSelectionModel().setSelectionMode(SelectionMode.MULTIPLE); batch.setPrefHeight(130);
        artifact.setMaxWidth(Double.MAX_VALUE); query.setPrefRowCount(3);
        logs.setEditable(false); logs.setPrefRowCount(12); logs.setWrapText(false);
        run.getStyleClass().add("primary");
        Button cancel = new Button("REQUEST STOP"); cancel.getStyleClass().add("danger");
        Button refresh = new Button("REFRESH LOG"); refresh.setOnAction(event -> refresh());
        run.setOnAction(event -> submit());
        cancel.setOnAction(event -> {
            String selected = action.getValue();
            commands.execute(() -> {
                try {
                    JsonNode result = gateway.post("/api/v1/training/jobs/" + selected + "/cancel", Map.of(
                            "actor_id", "javafx-operator", "idempotency_key", UUID.randomUUID().toString()));
                    Platform.runLater(() -> status.setText(result.path("message").asText("Stop requested; awaiting process exit")));
                } catch (Exception exc) { errors.accept(exc); }
            });
        });
        action.setOnAction(event -> rebuild()); scope.setOnAction(event -> applyPreset());
        getChildren().addAll(field("Job", action), fields, new HBox(10, run, cancel, refresh), status, logs);
        Timeline timer = new Timeline(new KeyFrame(Duration.seconds(5), event -> refresh())); timer.setCycleCount(Timeline.INDEFINITE);
        sceneProperty().addListener((observable, before, after) -> { if (after == null) timer.stop(); else timer.play(); });
        reads.execute(() -> {
            try {
                JsonNode response = gateway.get("/api/transformer/catalog");
                Platform.runLater(() -> {
                    catalog = response;
                    response.path("artifacts").forEach(item -> {
                        Artifact entry = new Artifact(item.path("scope").asText(), item.path("path").asText(), item.path("sample_count").asInt());
                        artifact.getItems().add(entry); batch.getItems().add(entry);
                    });
                    if (!artifact.getItems().isEmpty()) artifact.getSelectionModel().selectFirst();
                    applyPreset(); rebuild();
                });
            } catch (Exception exc) { errors.accept(exc); }
        });
        rebuild();
    }

    private void applyPreset() {
        if (catalog == null) return;
        JsonNode preset = catalog.path("presets").path(scope.getValue());
        database.setText(preset.path("database").asText()); output.setText(preset.path("output").asText());
        source.setValue(preset.path("source").asText("raw")); samples.getValueFactory().setValue(preset.path("max_samples").asInt(50000));
    }

    private void rebuild() {
        fields.getChildren().clear(); String job = action.getValue();
        if (job.equals("transformer_dataset")) fields.getChildren().addAll(field("Scope", scope), field("Source", source),
                fileField("Database", database, "*.db"), field("Output artifact", output), field("Maximum samples", samples));
        if (job.equals("transformer_dataset") || job.equals("backtest")) fields.getChildren().add(new HBox(12, field("Start (inclusive)", start), field("End", end)));
        if (job.equals("transformer_train")) fields.getChildren().add(field("Discovered sequence archive", artifact));
        if (job.equals("transformer_loop") || job.equals("transformer_batch")) fields.getChildren().add(field("Sequence archives (multiple selection)", batch));
        if (job.startsWith("transformer_") && !job.equals("transformer_dataset")) fields.getChildren().add(field("Epochs", epochs));
        if (job.equals("ml_loop")) fields.getChildren().add(fileField("Classical training archive", classical, "*.joblib"));
        if (job.endsWith("loop")) fields.getChildren().add(field("Interval minutes", interval));
        if (job.equals("ml_train") || job.equals("llm_train")) fields.getChildren().add(field("Lookback days", lookback));
        if (job.equals("llm_analysis") || job.equals("llm_coach")) fields.getChildren().add(field("Research question", query));
        status.setText("Selected: " + job); logs.clear(); refresh();
    }

    private void submit() {
        final String selected = action.getValue();
        try {
            ObjectNode options = json.createObjectNode();
            if (selected.equals("transformer_dataset")) {
                if (catalog == null) throw new IllegalStateException("Wait for the artifact catalog");
                options.setAll((ObjectNode) catalog.path("presets").path(scope.getValue()).deepCopy());
                options.put("scope", scope.getValue()).put("source", source.getValue()).put("database", database.getText())
                        .put("output", output.getText()).put("max_samples", samples.getValue());
            }
            if (selected.equals("transformer_dataset") || selected.equals("backtest")) {
                if (start.getValue() == null || end.getValue() == null || !end.getValue().isAfter(start.getValue())) throw new IllegalArgumentException("End must be after start");
                options.put("start", start.getValue().toString()).put("end", end.getValue().toString());
            }
            if (selected.equals("transformer_train")) {
                if (artifact.getValue() == null) throw new IllegalArgumentException("Build or select a completed sequence archive first");
                options.put("artifact", artifact.getValue().path());
            }
            if (selected.equals("transformer_loop") || selected.equals("transformer_batch")) {
                if (batch.getSelectionModel().getSelectedItems().isEmpty()) throw new IllegalArgumentException("Select at least one archive");
                var values = options.putArray("artifacts");
                batch.getSelectionModel().getSelectedItems().forEach(item -> values.add(item.scope() + "=" + item.path()));
            }
            options.put("epochs", epochs.getValue()).put("interval_minutes", interval.getValue()).put("lookback_days", lookback.getValue());
            if (selected.equals("ml_loop")) options.put("artifact", classical.getText());
            options.put("query", query.getText());
            run.setDisable(true); status.setText("Submitting " + selected);
            commands.execute(() -> {
                try {
                    JsonNode result = gateway.startJob(selected, options);
                    Platform.runLater(() -> status.setText(result.path("message").asText(result.path("status").asText())));
                } catch (Exception exc) { errors.accept(exc); }
                finally { Platform.runLater(() -> run.setDisable(false)); }
            });
        } catch (Exception exc) { status.setText(exc.getMessage()); }
    }

    private void refresh() {
        String selected = action.getValue();
        if (!reading.compareAndSet(false, true)) return;
        reads.execute(() -> {
            try {
                JsonNode log = gateway.get("/api/logs/" + selected + "?lines=160");
                JsonNode processes = gateway.get("/api/v1/training/jobs");
                StringBuilder text = new StringBuilder(); log.path("lines").forEach(line -> text.append(line.asText()).append('\n'));
                Platform.runLater(() -> {
                    if (!selected.equals(action.getValue())) return;
                    logs.setText(text.toString());
                    for (JsonNode process : processes) if (selected.equals(process.path("name").asText())) status.setText(selected + " / " + process.path("state").asText() + " / PID " + process.path("pid").asText());
                });
            } catch (Exception exc) { errors.accept(exc); }
            finally { reading.set(false); }
        });
    }

    private VBox fileField(String title, TextField target, String filter) {
        Button browse = new Button("BROWSE");
        browse.setOnAction(event -> {
            FileChooser chooser = new FileChooser(); chooser.setInitialDirectory(root.toFile());
            chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("Artifacts", filter));
            var file = chooser.showOpenDialog(getScene().getWindow());
            if (file != null) {
                Path chosen = file.toPath().toAbsolutePath().normalize();
                if (!chosen.startsWith(root)) { status.setText("Choose a file inside the project"); return; }
                target.setText(root.relativize(chosen).toString());
            }
        });
        HBox row = new HBox(8, target, browse); HBox.setHgrow(target, Priority.ALWAYS);
        return field(title, row);
    }

    private static VBox field(String title, javafx.scene.Node node) { return new VBox(5, new Label(title), node); }
    private record Artifact(String scope, String path, int rows) {
        @Override public String toString() { return scope + " / " + Path.of(path).getFileName() + " / " + rows + " samples"; }
    }
}
