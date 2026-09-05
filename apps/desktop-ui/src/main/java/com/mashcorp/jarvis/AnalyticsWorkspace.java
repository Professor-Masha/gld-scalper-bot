package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.application.Platform;
import javafx.scene.chart.*;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import java.util.concurrent.Executor;
import java.util.function.Consumer;

/** Separate observed paper outcomes from the latest simulated backtest result. */
final class AnalyticsWorkspace extends VBox {
    private final ComboBox<String> mode = new ComboBox<>();
    private final FlowPane metrics = new FlowPane(12, 12);
    private final FlowPane plots = new FlowPane(12, 12);
    private final TextArea evidence = new TextArea();
    private final GatewayClient gateway;
    private final Executor worker;
    private final Consumer<Throwable> errors;

    AnalyticsWorkspace(GatewayClient gateway, Executor worker, Consumer<Throwable> errors) {
        super(14); this.gateway = gateway; this.worker = worker; this.errors = errors;
        mode.getItems().addAll("Paper outcomes (latest 5,000)", "Latest backtest", "Model validation"); mode.getSelectionModel().selectFirst();
        Button refresh = new Button("REFRESH"); refresh.setOnAction(event -> refresh()); mode.setOnAction(event -> refresh());
        evidence.setEditable(false); evidence.setPrefRowCount(8);
        TitledPane details = new TitledPane("Structured evidence", evidence); details.setExpanded(false);
        getChildren().addAll(new HBox(10, mode, refresh), metrics, plots, details); refresh();
    }

    private void refresh() {
        int selectedMode = mode.getSelectionModel().getSelectedIndex();
        boolean backtest = selectedMode == 1;
        boolean validation = selectedMode == 2;
        worker.execute(() -> {
            try {
                JsonNode data = gateway.get(backtest ? "/api/results/backtest" : validation ? "/api/v1/models/validation" : "/api/analytics");
                JsonNode equity = backtest || validation ? null : gateway.get("/api/equity");
                Platform.runLater(() -> {
                    if (selectedMode != mode.getSelectionModel().getSelectedIndex()) return;
                    metrics.getChildren().clear(); plots.getChildren().clear(); evidence.setText(data.toPrettyString());
                    if (backtest) renderBacktest(data.path("result")); else if (validation) renderValidation(data); else renderPaper(data, equity);
                });
            } catch (Exception exc) { errors.accept(exc); }
        });
    }

    private void renderPaper(JsonNode data, JsonNode equity) {
        JsonNode summary = data.path("summary");
        metric("Root outcomes", summary.path("trades").asText("--"));
        metric("Net P/L (USD)", decimal(summary, "net_pnl")); metric("Estimated costs (USD)", decimal(summary, "estimated_costs"));
        metric("Win rate", String.format("%.1f%%", summary.path("win_rate").asDouble() * 100));
        metric("Profit factor", decimal(summary, "profit_factor"));
        plots.getChildren().add(line("Cumulative after-cost P/L (USD)", data.path("pnl_series"), "cumulative"));
        plots.getChildren().add(line("Account equity (USD)", equity, "equity"));
        PieChart pie = new PieChart(); pie.setTitle("Closed outcomes"); pie.setAnimated(false);
        pie.getData().addAll(new PieChart.Data("Wins", summary.path("wins").asDouble()), new PieChart.Data("Non-wins", summary.path("losses").asDouble()));
        size(pie); plots.getChildren().add(pie);
        plots.getChildren().add(bars("Playbook net P/L (USD)", data.path("breakdowns").path("playbook"), "label", "net_pnl"));
    }

    private void renderBacktest(JsonNode data) {
        if (!data.isObject()) { metric("Backtest", "No completed result"); return; }
        metric("Simulated trades", data.path("number_of_trades").asText("--"));
        metric("Net P/L (USD)", decimal(data, "net_pnl"));
        metric("Total return", percent(data, "total_return")); metric("Max drawdown", percent(data, "max_drawdown"));
        metric("Win rate", percent(data, "win_rate")); metric("Profit factor", decimal(data, "profit_factor"));
        var nodes = new com.fasterxml.jackson.databind.ObjectMapper().createArrayNode();
        nodes.addObject().put("label", "Long").put("value", data.path("long_net_pnl").asDouble());
        nodes.addObject().put("label", "Short").put("value", data.path("short_net_pnl").asDouble());
        plots.getChildren().add(bars("Simulated direction P/L (USD)", nodes, "label", "value"));
    }

    private void renderValidation(JsonNode data) {
        JsonNode summary = data.path("summary");
        metric("Registered models", summary.path("registered").asText("0"));
        metric("Approved champions", summary.path("champions").asText("0"));
        metric("Independent scopes", summary.path("scopes").asText("0"));
        metric("With calibration evidence", summary.path("calibrated_models").asText("0"));
        JsonNode models = data.path("models");
        plots.getChildren().add(bars("Holdout after-cost net return", models, "model_scope", "holdout_net_return"));
        plots.getChildren().add(bars("Holdout selective accuracy", models, "model_scope", "holdout_selective_accuracy"));
        plots.getChildren().add(bars("Holdout calibration error", models, "model_scope", "holdout_ece"));
        plots.getChildren().add(bars("Walk-forward after-cost net return", models, "model_scope", "walk_forward_net_return"));
    }

    private void metric(String title, String value) {
        Label caption = new Label(title); caption.getStyleClass().add("metric-caption");
        Label number = new Label(value); number.getStyleClass().add("metric-value");
        VBox box = new VBox(6, caption, number); box.getStyleClass().add("metric-card"); metrics.getChildren().add(box);
    }

    private static LineChart<Number, Number> line(String title, JsonNode values, String field) {
        NumberAxis y = new NumberAxis(); y.setForceZeroInRange(false);
        var chart = new LineChart<Number, Number>(new NumberAxis(), y); chart.setTitle(title);
        chart.setAnimated(false); chart.setCreateSymbols(false); chart.setLegendVisible(false);
        var series = new XYChart.Series<Number, Number>(); int index = 0;
        if (values != null) for (JsonNode value : values) series.getData().add(new XYChart.Data<>(index++, value.path(field).asDouble()));
        chart.getData().add(series); size(chart); return chart;
    }

    private static BarChart<String, Number> bars(String title, JsonNode values, String label, String field) {
        var chart = new BarChart<String, Number>(new CategoryAxis(), new NumberAxis()); chart.setTitle(title);
        chart.setAnimated(false); chart.setLegendVisible(false);
        var series = new XYChart.Series<String, Number>();
        for (JsonNode value : values) series.getData().add(new XYChart.Data<>(value.path(label).asText(), value.path(field).asDouble()));
        chart.getData().add(series); size(chart); return chart;
    }

    private static void size(Chart chart) { chart.setPrefSize(420, 300); chart.setMinSize(300, 240); }
    private static String decimal(JsonNode node, String field) { return node.hasNonNull(field) ? String.format("%,.2f", node.path(field).asDouble()) : "--"; }
    private static String percent(JsonNode node, String field) { return node.hasNonNull(field) ? String.format("%.2f%%", node.path(field).asDouble() * 100) : "--"; }
}
