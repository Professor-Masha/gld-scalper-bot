package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Pos;
import javafx.scene.control.*;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.util.Duration;

import java.util.Map;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** Offline-only Ollama/Kimi and FinGPT research console with managed-job feedback. */
final class AiLabWorkspace extends VBox {
    private final GatewayClient gateway;
    private final Executor commands;
    private final Executor research;
    private final Consumer<Throwable> errors;
    private final ObjectMapper json = new ObjectMapper();
    private final ComboBox<String> provider = new ComboBox<>(FXCollections.observableArrayList("ollama", "kimi"));
    private final TextField model = new TextField("llama3.2:1b");
    private final TextField baseUrl = new TextField("http://127.0.0.1:11434");
    private final PasswordField apiKey = new PasswordField();
    private final Label providerState = stateLabel("CONFIGURED, NOT TESTED");
    private final Label reviewState = stateLabel("IDLE");
    private final Label finGptSourceState = stateLabel("CHECKING SOURCE");
    private final Label finGptCycleState = stateLabel("IDLE");
    private final Label reviewMessage = new Label("No completed offline review is available yet.");
    private final Label finGptMessage = new Label("FinGPT supplies financial workflows; the selected provider performs inference.");
    private final Label finGptModules = new Label("Discovering local FinGPT modules.");
    private final TextArea prompt = new TextArea("Analyze completed trades, missed opportunities, execution quality, and model improvements.");
    private final TextArea activity = new TextArea();
    private final HumanReadableView result = new HumanReadableView();
    private final Button hourlyCycle = button("RUN HOURLY PIPELINE", "secondary", () -> startFinGptCycle("hourly"));
    private final Button dailyCycle = button("RUN DAILY PIPELINE", "primary", () -> startFinGptCycle("daily"));
    private final AtomicBoolean refreshQueued = new AtomicBoolean();
    private final Timeline poller = new Timeline(new KeyFrame(Duration.seconds(3), event -> refresh()));
    private JsonNode catalog;
    private String renderedResult = "";

    AiLabWorkspace(GatewayClient gateway, Executor commands, Executor research, Consumer<Throwable> errors) {
        super(14);
        this.gateway = gateway;
        this.commands = commands;
        this.research = research;
        this.errors = errors;
        provider.setValue("ollama");
        provider.setOnAction(event -> applyProviderDefaults());
        prompt.setPrefRowCount(4);
        activity.setEditable(false);
        activity.setWrapText(true);
        activity.setPrefRowCount(5);
        result.setPrefHeight(330);
        reviewMessage.setWrapText(true);
        reviewMessage.getStyleClass().add("ai-status-message");
        finGptMessage.setWrapText(true);
        finGptMessage.getStyleClass().add("ai-status-message");
        finGptModules.setWrapText(true);
        finGptModules.getStyleClass().add("ai-pipeline-detail");

        Button activate = button("ACTIVATE", "primary", this::activate);
        Button test = button("TEST GENERATION", "secondary", this::testProvider);
        Button analyze = button("RUN OFFLINE REVIEW", "primary", this::startReview);
        Button latest = button("REFRESH RESULT", "secondary", this::refresh);
        HBox providerHeader = new HBox(12, new Label("Provider health"), providerState);
        providerHeader.setAlignment(Pos.CENTER_LEFT);
        HBox reviewHeader = new HBox(12, new Label("Review status"), reviewState);
        reviewHeader.setAlignment(Pos.CENTER_LEFT);
        HBox finGptHeader = new HBox(12, new Label("FinGPT source"), finGptSourceState,
                new Label("Cycle"), finGptCycleState);
        finGptHeader.setAlignment(Pos.CENTER_LEFT);

        getChildren().addAll(
                section("REASONING PROVIDER", providerHeader, field("Provider", provider), field("Model", model),
                        field("Base URL", baseUrl), field("API key (never displayed)", apiKey), new HBox(10, activate, test)),
                section("FINGPT FINANCIAL RESEARCH PIPELINE", finGptHeader, finGptMessage, finGptModules,
                        new HBox(10, hourlyCycle, dailyCycle)),
                section("RESEARCH TASK", reviewHeader, reviewMessage, prompt, new HBox(10, analyze, latest)),
                section("RECENT ACTIVITY", activity),
                section("AI RESULT", result));

        poller.setCycleCount(Timeline.INDEFINITE);
        sceneProperty().addListener((observable, before, after) -> {
            if (after == null) poller.stop();
            else { poller.play(); refresh(); }
        });
        loadConfiguration();
    }

    void refresh() {
        if (!refreshQueued.compareAndSet(false, true)) return;
        research.execute(() -> {
            try {
                JsonNode status = gateway.get("/api/v1/llm/status");
                Platform.runLater(() -> applyStatus(status));
            } catch (Exception exc) {
                errors.accept(exc);
            } finally {
                refreshQueued.set(false);
            }
        });
    }

    private void loadConfiguration() {
        research.execute(() -> {
            try {
                JsonNode settings = gateway.get("/api/settings");
                JsonNode providers = gateway.get("/api/llm/providers");
                Platform.runLater(() -> {
                    catalog = providers;
                    provider.setValue(settings.path("llm_provider").asText("ollama"));
                    model.setText(settings.path("llm_model").asText("llama3.2:1b"));
                    baseUrl.setText(settings.path("llm_base_url").asText("http://127.0.0.1:11434"));
                    applyCatalogState();
                    refresh();
                });
            } catch (Exception exc) { errors.accept(exc); }
        });
    }

    private void activate() {
        providerState.setText("SAVING SETTINGS");
        Map<String, String> request = Map.of(
                "provider", provider.getValue(), "model", model.getText(), "base_url", baseUrl.getText(), "api_key", apiKey.getText());
        commands.execute(() -> {
            try {
                JsonNode response = gateway.post("/api/llm/providers/activate", request);
                Platform.runLater(() -> {
                    catalog = response;
                    apiKey.clear();
                    applyCatalogState();
                    refresh();
                });
            } catch (Exception exc) { Platform.runLater(() -> showFailure("Provider activation failed", exc)); }
        });
    }

    private void testProvider() {
        providerState.setText("TESTING GENERATION");
        result.show(json.valueToTree(Map.of("status", "testing", "message", "Waiting for a real model response.")));
        research.execute(() -> {
            try {
                JsonNode response = gateway.post("/api/llm/providers/test", Map.of("provider", provider.getValue()));
                Platform.runLater(() -> { result.show(response); refresh(); });
            } catch (Exception exc) {
                Platform.runLater(() -> { showFailure("Generation test failed", exc); refresh(); });
            }
        });
    }

    private void startReview() {
        reviewState.setText("SUBMITTING");
        reviewMessage.setText("Submitting the offline review to the managed research process.");
        JsonNode options = json.valueToTree(Map.of("query", prompt.getText()));
        commands.execute(() -> {
            try {
                JsonNode response = gateway.startJob("llm_analysis", options);
                Platform.runLater(() -> { result.show(response); refresh(); });
            } catch (Exception exc) { Platform.runLater(() -> showReviewFailure("Offline review could not start", exc)); }
        });
    }

    private void startFinGptCycle(String cadence) {
        finGptCycleState.setText("SUBMITTING");
        styleState(finGptCycleState, "submitting");
        finGptMessage.setText("Submitting the " + cadence + " FinGPT workflow to the managed offline research process.");
        JsonNode options = json.valueToTree(Map.of("cadence", cadence));
        commands.execute(() -> {
            try {
                JsonNode response = gateway.startJob("llm_cycle", options);
                Platform.runLater(() -> { result.show(response); refresh(); });
            } catch (Exception exc) {
                Platform.runLater(() -> showPipelineFailure("FinGPT cycle could not start", exc));
            }
        });
    }

    private void applyStatus(JsonNode status) {
        JsonNode providerPayload = status.path("provider");
        for (JsonNode candidate : status.path("providers")) {
            if (provider.getValue().equals(candidate.path("id").asText())) {
                providerPayload = candidate;
                break;
            }
        }
        String runtime = providerPayload.path("runtime").path("state").asText(
                providerPayload.path("configured").asBoolean() ? "not_tested" : "not_configured");
        providerState.setText(HumanReadableFormatter.label(runtime).toUpperCase());
        styleState(providerState, runtime);

        JsonNode review = status.path("review");
        String state = review.path("state").asText("idle");
        reviewState.setText(HumanReadableFormatter.label(state).toUpperCase());
        styleState(reviewState, state);
        reviewMessage.setText(review.path("message").asText("No review status is available."));
        StringBuilder lines = new StringBuilder();
        review.path("recent_activity").forEach(line -> lines.append(line.asText()).append('\n'));
        JsonNode pipeline = status.path("fingpt_pipeline");
        JsonNode source = pipeline.path("source");
        String sourceState = source.path("status").asText("source_missing");
        finGptSourceState.setText(HumanReadableFormatter.label(sourceState).toUpperCase());
        styleState(finGptSourceState, "ready".equals(sourceState) ? "healthy" : "failed");
        JsonNode cycle = pipeline.path("cycle");
        String cycleState = cycle.path("state").asText("idle");
        finGptCycleState.setText(HumanReadableFormatter.label(cycleState).toUpperCase());
        styleState(finGptCycleState, cycleState);
        boolean canRun = pipeline.path("can_run").asBoolean(false);
        hourlyCycle.setDisable(!canRun || cycleState.matches("running|stopping"));
        dailyCycle.setDisable(!canRun || cycleState.matches("running|stopping"));
        String cycleMessage = cycle.path("message").asText("No FinGPT cycle status is available.");
        if (!source.path("available").asBoolean(false)) {
            cycleMessage = "Local FinGPT workflow files were not found. Configure FINGPT_SOURCE_DIR before running a cycle.";
        } else if (!canRun) {
            cycleMessage = "Select and activate Ollama or Kimi before running the FinGPT workflow.";
        }
        finGptMessage.setText(cycleMessage);
        String modules = joinText(source.path("modules"));
        String workflows = joinText(pipeline.path("workflows"));
        finGptModules.setText("Modules: " + (modules.isBlank() ? "not found" : modules)
                + "\nReasoning engine: " + HumanReadableFormatter.label(source.path("reasoning_engine").asText("none"))
                + "\nWorkflow: " + (workflows.isBlank() ? "not available" : workflows)
                + "\nBroker authority: none");
        cycle.path("recent_activity").forEach(line -> lines.append("[FinGPT] ").append(line.asText()).append('\n'));
        activity.setText(lines.isEmpty() ? "No recent AI activity." : lines.toString().trim());
        activity.positionCaret(activity.getLength());

        JsonNode completed = review.path("result");
        if ((completed.isMissingNode() || completed.isNull()) && cycle.path("result_available").asBoolean()) {
            completed = cycle.path("result");
        }
        if (!completed.isMissingNode() && !completed.isNull()) {
            String fingerprint = Integer.toHexString(completed.toString().hashCode());
            if (!fingerprint.equals(renderedResult)) {
                renderedResult = fingerprint;
                result.show(completed);
            }
        }
    }

    private void applyProviderDefaults() {
        if (catalog == null) {
            boolean kimi = "kimi".equals(provider.getValue());
            model.setText(kimi ? "kimi-k2.6" : "llama3.2:1b");
            baseUrl.setText(kimi ? "https://api.moonshot.ai/v1" : "http://127.0.0.1:11434");
            return;
        }
        for (JsonNode item : catalog.path("providers")) {
            if (provider.getValue().equals(item.path("id").asText())) {
                model.setText(item.path("current_model").asText(item.path("model").asText()));
                baseUrl.setText(item.path("current_base_url").asText(item.path("base_url").asText()));
                JsonNode runtime = item.path("runtime");
                providerState.setText(HumanReadableFormatter.label(runtime.path("state").asText("not_tested")).toUpperCase());
                break;
            }
        }
    }

    private void applyCatalogState() {
        if (catalog == null) return;
        applyProviderDefaults();
    }

    private void showFailure(String title, Throwable failure) {
        providerState.setText("FAILED");
        styleState(providerState, "failed");
        result.show(json.valueToTree(Map.of("status", "failed", "message", title, "details", String.valueOf(failure.getMessage()))));
        errors.accept(failure);
    }

    private void showReviewFailure(String title, Throwable failure) {
        reviewState.setText("FAILED");
        styleState(reviewState, "failed");
        reviewMessage.setText(title + ": " + String.valueOf(failure.getMessage()));
        result.show(json.valueToTree(Map.of("status", "failed", "message", title, "details", String.valueOf(failure.getMessage()))));
        errors.accept(failure);
    }

    private void showPipelineFailure(String title, Throwable failure) {
        finGptCycleState.setText("FAILED");
        styleState(finGptCycleState, "failed");
        finGptMessage.setText(title + ": " + String.valueOf(failure.getMessage()));
        result.show(json.valueToTree(Map.of("status", "failed", "message", title, "details", String.valueOf(failure.getMessage()))));
        errors.accept(failure);
    }

    private static String joinText(JsonNode values) {
        StringBuilder text = new StringBuilder();
        if (values != null && values.isArray()) {
            values.forEach(value -> {
                if (!text.isEmpty()) text.append(", ");
                text.append(HumanReadableFormatter.label(value.asText()));
            });
        }
        return text.toString();
    }

    private static void styleState(Label label, String state) {
        label.getStyleClass().removeAll("ai-state-good", "ai-state-working", "ai-state-failed", "ai-state-idle");
        label.getStyleClass().add(state.matches("healthy|completed") ? "ai-state-good"
                : state.matches("testing|running|submitting|stopping") ? "ai-state-working"
                : state.matches("failed|not_configured") ? "ai-state-failed" : "ai-state-idle");
    }

    private static Label stateLabel(String text) {
        Label label = new Label(text);
        label.getStyleClass().addAll("state-pill", "ai-state-idle");
        return label;
    }

    private static Button button(String text, String style, Runnable action) {
        Button button = new Button(text);
        button.getStyleClass().add(style);
        button.setOnAction(event -> action.run());
        return button;
    }

    private static VBox field(String title, javafx.scene.Node control) {
        Label caption = new Label(title);
        caption.getStyleClass().add("field-label");
        VBox field = new VBox(5, caption, control);
        VBox.setVgrow(control, Priority.NEVER);
        return field;
    }

    private static VBox section(String title, javafx.scene.Node... nodes) {
        Label heading = new Label(title);
        heading.getStyleClass().add("panel-title");
        VBox box = new VBox(10, heading);
        box.getChildren().addAll(nodes);
        box.getStyleClass().add("panel");
        return box;
    }
}
