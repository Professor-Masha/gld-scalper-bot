package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.animation.FadeTransition;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.ToggleGroup;
import javafx.scene.layout.ColumnConstraints;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.shape.Circle;
import javafx.util.Duration;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Read-only live decision, evidence, and trade-management workspace. */
public final class LiveDecisionWorkspace extends VBox {
    private final StackPane viewHost = new StackPane();
    private final LiveView live = new LiveView();
    private final EvidenceView evidence = new EvidenceView();
    private final TradeView trade = new TradeView();
    private final ToggleButton liveMode;
    private final ToggleButton evidenceMode;
    private final ToggleButton tradeMode;
    private Node activeView = live;
    private boolean reducedMotion;

    public LiveDecisionWorkspace() {
        getStyleClass().add("decision-workspace");
        setSpacing(10);
        ToggleGroup group = new ToggleGroup();
        liveMode = modeButton("LIVE DECISION", live, group, true);
        evidenceMode = modeButton("EVIDENCE RADAR", evidence, group, false);
        tradeMode = modeButton("TRADE ANATOMY", trade, group, false);
        HBox modes = new HBox(4, liveMode, evidenceMode, tradeMode);
        modes.getStyleClass().add("decision-modes");
        viewHost.getChildren().add(live);
        viewHost.setMinSize(0, 0);
        VBox.setVgrow(viewHost, Priority.ALWAYS);
        getChildren().addAll(modes, viewHost);
        widthProperty().addListener((observable, before, width) -> {
            getStyleClass().removeAll("decision-compact", "decision-narrow");
            if (width.doubleValue() < 930) getStyleClass().add("decision-compact");
            if (width.doubleValue() < 720) getStyleClass().add("decision-narrow");
        });
    }

    public void update(JsonNode snapshot) {
        DecisionTelemetry frame = DecisionTelemetry.from(snapshot);
        live.update(frame);
        evidence.update(frame);
        trade.update(frame);
    }

    public void setReducedMotion(boolean value) {
        reducedMotion = value;
    }

    void showLiveDecision() { select(liveMode, live); }
    void showEvidenceRadar() { select(evidenceMode, evidence); }
    void showTradeAnatomy() { select(tradeMode, trade); }

    private void select(ToggleButton button, Node view) {
        button.setSelected(true);
        show(view);
    }

    private ToggleButton modeButton(String title, Node view, ToggleGroup group, boolean selected) {
        ToggleButton button = new ToggleButton(title);
        button.setToggleGroup(group);
        button.setSelected(selected);
        button.getStyleClass().add("decision-mode-button");
        button.setOnAction(event -> {
            button.setSelected(true);
            show(view);
        });
        return button;
    }

    private void show(Node next) {
        if (next == activeView) return;
        viewHost.getChildren().setAll(next);
        activeView = next;
        if (reducedMotion) return;
        next.setOpacity(0);
        FadeTransition fade = new FadeTransition(Duration.millis(170), next);
        fade.setToValue(1);
        fade.play();
    }

    private static final class LiveView extends VBox {
        private final DecisionPricePlot plot = new DecisionPricePlot();
        private final Label quote = value("--");
        private final Label quoteDetail = muted("Waiting for GLD quote");
        private final Label currentState = new Label("NO DATA");
        private final Label marketContext = muted("Waiting for decision evidence");
        private final ProbabilityRow longProbability = new ProbabilityRow("LONG", "positive");
        private final ProbabilityRow shortProbability = new ProbabilityRow("SHORT", "negative");
        private final ProbabilityRow abstainProbability = new ProbabilityRow("NO TRADE", "neutral");
        private final Label ruleStrength = value("--");
        private final Label expectedReturn = value("--");
        private final Label expectedCost = value("--");
        private final Label netEdge = value("--");
        private final Label uncertainty = value("--");
        private final EvidenceOrderChain evidenceChain = new EvidenceOrderChain();
        private final List<Label> statistics = new ArrayList<>();

        LiveView() {
            getStyleClass().add("decision-view");
            setSpacing(10);
            VBox priceCard = card("GLD LIVE PRICE", quote, quoteDetail, plot);
            plot.setMinHeight(150);
            VBox decisionCard = card("DECISION ENGINE", currentState, marketContext,
                    section("OUTCOME PROBABILITIES"),
                    longProbability, shortProbability, abstainProbability,
                    metricRow("RULE STRENGTH", ruleStrength),
                    metricRow("EXPECTED RETURN", expectedReturn),
                    metricRow("ESTIMATED COST", expectedCost),
                    metricRow("NET EXPECTED EDGE", netEdge),
                    metricRow("UNCERTAINTY", uncertainty));
            currentState.getStyleClass().add("flight-state");
            currentState.setWrapText(true);
            marketContext.setWrapText(true);
            VBox evidenceCard = card("EVIDENCE  ->  ORDER CHAIN", evidenceChain);
            GridPane main = grid(38, 29, 33);
            main.addRow(0, priceCard, decisionCard, evidenceCard);
            main.getStyleClass().add("decision-flow");
            GridPane strip = grid(16.67, 16.67, 16.67, 16.67, 16.67, 16.65);
            strip.getStyleClass().add("performance-strip");
            String[] names = {"NET P/L", "WIN RATE", "PROFIT FACTOR", "DRAWDOWN", "EPISODES", "AVG HOLD"};
            for (String name : names) {
                Label number = value("--");
                statistics.add(number);
                strip.add(stat(name, number), statistics.size() - 1, 0);
            }
            VBox.setVgrow(main, Priority.ALWAYS);
            getChildren().addAll(main, strip);
        }

        void update(DecisionTelemetry frame) {
            plot.update(frame);
            quote.setText(frame.midpoint() > 0 ? money(frame.midpoint()) : "--");
            quoteDetail.setText(frame.bid() > 0 && Double.isFinite(frame.spreadPct())
                    ? String.format(Locale.US, "Bid %.2f / Ask %.2f / Spread %.3f%%",
                    frame.bid(), frame.ask(), frame.spreadPct() * 100)
                    : frame.bid() > 0 ? "Quote spread is outside plausible GLD bounds" : "Live quote unavailable");
            currentState.setText(frame.decision());
            currentState.setStyle("-fx-text-fill:" + decisionColor(frame.decision()) + ";");
            marketContext.setText(frame.marketState() + " / " + frame.regime());
            DecisionTelemetry.Prediction model = frame.classical();
            longProbability.update(model.available() ? model.longProbability() : -1);
            shortProbability.update(model.available() ? model.shortProbability() : -1);
            abstainProbability.update(model.available() ? model.noTradeProbability() : -1);
            ruleStrength.setText(percent(frame.ruleStrength()));
            expectedReturn.setText(signedPercent(frame.expectedReturn()));
            expectedCost.setText(!Double.isFinite(frame.expectedCost()) || frame.expectedCost() == 0
                    ? "--" : "-" + percent(Math.abs(frame.expectedCost())));
            netEdge.setText(signedPercent(frame.expectedNetEdge()));
            netEdge.setStyle("-fx-text-fill:" + signedColor(frame.expectedNetEdge()) + ";");
            uncertainty.setText(frame.transformer().available() ? percent(frame.transformerUncertainty()) : "Unavailable");
            evidenceChain.update(frame.evidence(), frame.quoteTimestamp());
            DecisionTelemetry.Performance p = frame.performance();
            statistics.get(0).setText(money(p.netPnl()));
            statistics.get(0).setStyle("-fx-text-fill:" + signedColor(p.netPnl()) + ";");
            statistics.get(1).setText(p.trades() == 0 ? "--" : percent(p.winRate()));
            statistics.get(2).setText(Double.isNaN(p.profitFactor()) ? "--" : String.format(Locale.US, "%.2f", p.profitFactor()));
            statistics.get(3).setText(percent(p.drawdownPct()));
            statistics.get(4).setText(Integer.toString(p.trades()));
            statistics.get(5).setText(duration(p.averageHoldSeconds()));
        }
    }

    private static final class EvidenceView extends VBox {
        private final MarketPulseWorkspace pulse = new MarketPulseWorkspace();

        EvidenceView() {
            getStyleClass().add("decision-view");
            getChildren().add(pulse);
            VBox.setVgrow(pulse, Priority.ALWAYS);
        }

        void update(DecisionTelemetry frame) {
            pulse.update(frame);
        }
    }

    private static final class TradeView extends VBox {
        private final DecisionPricePlot plot = new DecisionPricePlot();
        private final Label identity = value("NO ACTIVE OR COMPLETED EPISODE");
        private final Label context = muted("Trade lifecycle appears when an execution episode exists.");
        private final HBox lifecycle = new HBox(8);
        private final VBox rationale = new VBox(7);
        private final List<Label> metrics = new ArrayList<>();

        TradeView() {
            getStyleClass().add("decision-view");
            setSpacing(10);
            lifecycle.getStyleClass().add("trade-lifecycle");
            VBox timeline = card("TRADE LIFECYCLE", identity, context, lifecycle);
            VBox price = card("PRICE ACTION AND ECONOMIC OUTCOME", plot);
            plot.setMinHeight(180);
            VBox why = card("WHY THIS TRADE", rationale);
            GridPane body = grid(64, 36);
            body.addRow(0, price, why);
            GridPane strip = grid(16.67, 16.67, 16.67, 16.67, 16.67, 16.65);
            strip.getStyleClass().add("performance-strip");
            String[] names = {"GROSS P/L", "NET P/L", "SPREAD COST", "SLIPPAGE", "HOLD TIME", "PROFIT GIVEN BACK"};
            for (String name : names) {
                Label number = value("--");
                metrics.add(number);
                strip.add(stat(name, number), metrics.size() - 1, 0);
            }
            VBox.setVgrow(body, Priority.ALWAYS);
            getChildren().addAll(timeline, body, strip);
        }

        void update(DecisionTelemetry frame) {
            plot.update(frame);
            DecisionTelemetry.Episode episode = frame.activeEpisode();
            DecisionTelemetry.Outcome outcome = frame.latestOutcome();
            boolean active = episode != null;
            if (active) {
                identity.setText(episode.direction() + " / " + episode.playbook() + " / " + episode.status().toUpperCase(Locale.ROOT));
                context.setText("Episode " + compact(episode.id(), 34) + " / " + episode.strategyPath()
                        + " / opened " + time(episode.openedAt()));
            } else if (outcome != null) {
                identity.setText("LATEST CLOSED / " + outcome.direction() + " / " + outcome.result());
                context.setText("Episode " + compact(outcome.id(), 34) + " / exited " + time(outcome.exitTime())
                        + " / " + outcome.exitReason());
            } else {
                identity.setText("NO ACTIVE OR COMPLETED EPISODE");
                context.setText("Trade lifecycle appears when an execution episode exists.");
            }
            lifecycle.getChildren().setAll(lifecycleStages(active ? episode.status() : outcome == null ? "" : "closed", active, outcome != null));
            rationale.getChildren().setAll(
                    tradeEvidenceRow(frame.evidence().get(0)),
                    tradeEvidenceRow(frame.evidence().get(1)),
                    tradeEvidenceRow(frame.evidence().get(2)),
                    tradeEvidenceRow(frame.evidence().get(3)),
                    tradeEvidenceRow(frame.evidence().get(4)),
                    tradeEvidenceRow(frame.evidence().get(5)));
            if (active) {
                double currentPnl = directionalPnl(episode.direction(), episode.entryPrice(), frame.midpoint(), episode.remainingQuantity());
                metrics.get(0).setText(money(currentPnl));
                metrics.get(1).setText("Open");
                metrics.get(2).setText("--");
                metrics.get(3).setText("--");
                metrics.get(4).setText(elapsed(episode.openedAt()));
                metrics.get(5).setText("--");
            } else if (outcome != null) {
                metrics.get(0).setText(money(outcome.grossPnl()));
                metrics.get(1).setText(money(outcome.netPnl()));
                metrics.get(1).setStyle("-fx-text-fill:" + signedColor(outcome.netPnl()) + ";");
                metrics.get(2).setText(money(outcome.spreadCost()));
                metrics.get(3).setText(money(outcome.slippageCost()));
                metrics.get(4).setText(duration(outcome.holdingSeconds()));
                metrics.get(5).setText(money(outcome.profitGivenBack()));
            } else {
                metrics.forEach(label -> label.setText("--"));
            }
        }
    }

    private static List<Node> lifecycleStages(String status, boolean active, boolean hasOutcome) {
        String normalized = status == null ? "" : status.toLowerCase(Locale.ROOT);
        int current = hasOutcome && !active ? 5
                : normalized.contains("protect") || normalized.contains("active") ? 4
                : normalized.contains("fill") ? 3
                : normalized.contains("submit") || normalized.contains("new") ? 2
                : active ? 1 : 0;
        String[] names = {"OBSERVED", "CONFIRMED", "SUBMITTED", "FILLED", "MANAGING", "EXIT"};
        List<Node> nodes = new ArrayList<>();
        for (int index = 0; index < names.length; index++) {
            VBox stage = new VBox(3);
            stage.getStyleClass().add("lifecycle-stage");
            if (index < current || index == current && active || index == 5 && hasOutcome) {
                stage.getStyleClass().add(index == current && active ? "stage-current" : "stage-complete");
            }
            Label sequence = new Label(String.format(Locale.US, "%02d", index + 1));
            sequence.getStyleClass().add("lifecycle-sequence");
            Label title = new Label(names[index]);
            title.getStyleClass().add("lifecycle-title");
            stage.getChildren().addAll(sequence, title);
            HBox.setHgrow(stage, Priority.ALWAYS);
            stage.setMaxWidth(Double.MAX_VALUE);
            nodes.add(stage);
        }
        return nodes;
    }

    private static VBox tradeEvidenceRow(DecisionTelemetry.Evidence item) {
        Circle dot = new Circle(5, switch (item.state()) {
            case PASS -> Color.web("#5cf2b5");
            case WARN -> Color.web("#ffbf69");
            case BLOCKED -> Color.web("#ff5470");
            case UNAVAILABLE -> Color.web("#607780");
        });
        Label title = new Label(item.name().toUpperCase(Locale.ROOT));
        title.getStyleClass().add("evidence-name");
        Label detail = new Label(item.detail());
        detail.getStyleClass().add("evidence-detail");
        detail.setWrapText(true);
        VBox text = new VBox(1, title, detail);
        HBox row = new HBox(9, dot, text);
        row.setAlignment(Pos.TOP_LEFT);
        row.getStyleClass().add("trade-evidence-row");
        HBox.setHgrow(text, Priority.ALWAYS);
        return new VBox(row);
    }

    private static VBox card(String title, Node... content) {
        Label heading = new Label(title);
        heading.getStyleClass().add("decision-card-title");
        Region rule = new Region();
        rule.getStyleClass().add("panel-rule");
        VBox card = new VBox(8, heading, rule);
        card.getChildren().addAll(content);
        card.getStyleClass().add("decision-card");
        card.setMinSize(0, 0);
        card.setMaxSize(Double.MAX_VALUE, Double.MAX_VALUE);
        return card;
    }

    private static GridPane grid(double... widths) {
        GridPane grid = new GridPane();
        grid.setHgap(10);
        grid.setVgap(10);
        grid.setMaxSize(Double.MAX_VALUE, Double.MAX_VALUE);
        for (double width : widths) {
            ColumnConstraints column = new ColumnConstraints();
            column.setPercentWidth(width);
            column.setMinWidth(0);
            column.setHgrow(Priority.ALWAYS);
            column.setFillWidth(true);
            grid.getColumnConstraints().add(column);
        }
        return grid;
    }

    private static Label section(String text) {
        Label label = new Label(text);
        label.getStyleClass().add("decision-section");
        return label;
    }

    private static HBox metricRow(String name, Label value) {
        Label label = muted(name);
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox row = new HBox(8, label, spacer, value);
        row.setAlignment(Pos.CENTER_LEFT);
        row.getStyleClass().add("decision-metric-row");
        return row;
    }

    private static VBox stat(String title, Label value) {
        Label caption = muted(title);
        VBox box = new VBox(3, caption, value);
        box.getStyleClass().add("performance-stat");
        box.setMinWidth(0);
        box.setMaxWidth(Double.MAX_VALUE);
        return box;
    }

    private static Label value(String text) {
        Label label = new Label(text);
        label.getStyleClass().add("decision-value");
        return label;
    }

    private static Label muted(String text) {
        Label label = new Label(text);
        label.getStyleClass().add("decision-muted");
        return label;
    }

    private static String decisionColor(String decision) {
        String value = decision.toUpperCase(Locale.ROOT);
        if (value.contains("LONG") || value.contains("BUY")) return "#5cf2b5";
        if (value.contains("SHORT") || value.contains("SELL")) return "#ff5470";
        return "#ffbf69";
    }

    private static String signedColor(double value) {
        return value < 0 ? "#ff5470" : value > 0 ? "#5cf2b5" : "#d9edf2";
    }

    private static String percent(double value) {
        return Double.isFinite(value) ? String.format(Locale.US, "%.1f%%", value * 100) : "--";
    }

    private static String signedPercent(double value) {
        return !Double.isFinite(value) || value == 0 ? "--" : String.format(Locale.US, "%+.3f%%", value * 100);
    }

    private static String money(double value) {
        return String.format(Locale.US, "$%,.2f", value);
    }

    private static String duration(double seconds) {
        if (seconds <= 0) return "--";
        if (seconds < 60) return String.format(Locale.US, "%.0fs", seconds);
        return String.format(Locale.US, "%.1fm", seconds / 60);
    }

    private static String elapsed(String timestamp) {
        try {
            return duration(Math.max(0, java.time.Duration.between(java.time.Instant.parse(timestamp), java.time.Instant.now()).toSeconds()));
        } catch (Exception ignored) {
            return "--";
        }
    }

    private static String time(String timestamp) {
        if (timestamp == null || timestamp.isBlank()) return "unknown time";
        try {
            return java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss")
                    .withZone(java.time.ZoneId.systemDefault()).format(java.time.Instant.parse(timestamp));
        } catch (Exception ignored) {
            return timestamp;
        }
    }

    private static String compact(String value, int limit) {
        if (value == null || value.length() <= limit) return value == null ? "" : value;
        return value.substring(0, Math.max(1, limit - 3)) + "...";
    }

    private static double directionalPnl(String direction, double entry, double current, double quantity) {
        if (entry <= 0 || current <= 0 || quantity <= 0) return 0;
        double sign = direction.toLowerCase(Locale.ROOT).contains("short") ? -1 : 1;
        return (current - entry) * quantity * sign;
    }

    private static final class ProbabilityRow extends HBox {
        private final ProgressBar bar = new ProgressBar();
        private final Label result = value("--");

        ProbabilityRow(String title, String style) {
            Label label = muted(title);
            label.setMinWidth(68);
            bar.setMaxWidth(Double.MAX_VALUE);
            bar.getStyleClass().addAll("decision-probability", style);
            HBox.setHgrow(bar, Priority.ALWAYS);
            result.setMinWidth(52);
            setAlignment(Pos.CENTER_LEFT);
            setSpacing(8);
            getChildren().addAll(label, bar, result);
        }

        void update(double probability) {
            boolean available = probability >= 0;
            bar.setProgress(available ? Math.max(0, Math.min(1, probability)) : 0);
            result.setText(available ? percent(probability) : "--");
        }
    }
}
