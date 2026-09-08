package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Immutable, human-facing projection of one read-only dashboard snapshot. */
public record DecisionTelemetry(
        double bid,
        double ask,
        double midpoint,
        double spreadPct,
        String quoteTimestamp,
        String decision,
        String marketState,
        String regime,
        String summary,
        double ruleStrength,
        double liquidityScore,
        double quoteAgeSeconds,
        double tradeAgeSeconds,
        double expectedReturn,
        double expectedCost,
        double expectedNetEdge,
        Prediction classical,
        Prediction transformer,
        String transformerRole,
        double transformerUncertainty,
        double transformerLatencyMs,
        Performance performance,
        Episode activeEpisode,
        Outcome latestOutcome,
        List<Evidence> evidence) {

    enum EvidenceState { PASS, WARN, BLOCKED, UNAVAILABLE }

    record Prediction(boolean available, double longProbability, double shortProbability,
                      double noTradeProbability, String version) {
        double highestDirectional() { return Math.max(longProbability, shortProbability); }
    }

    record Evidence(String name, String detail, EvidenceState state) { }

    record Performance(int trades, double netPnl, double grossPnl, double winRate,
                       double profitFactor, double spreadCost, double slippageCost,
                       double estimatedFees, double estimatedLiveCost, double averageHoldSeconds,
                       double drawdownPct) { }

    record Episode(String id, String direction, String playbook, String strategyPath,
                   String status, double filledQuantity, double remainingQuantity,
                   double entryPrice, double realizedPnl, String openedAt, JsonNode details) { }

    record Outcome(String id, String direction, String playbook, String strategyPath,
                   double entryPrice, double exitPrice, double quantity, double grossPnl,
                   double netPnl, double holdingSeconds, String exitReason, String result,
                   double spreadCost, double slippageCost, double estimatedFees,
                   double estimatedLiveCost, double profitGivenBack, double mfe, double mae,
                   String entryTime, String exitTime) { }

    static DecisionTelemetry from(JsonNode snapshot) {
        JsonNode quote = path(snapshot, "quote");
        double bid = number(quote, "bid_price");
        double ask = number(quote, "ask_price");
        double midpoint = bid > 0 && ask > 0 ? (bid + ask) / 2.0 : number(quote, "price");
        double spreadPct = number(quote, "spread_pct");
        if (spreadPct == 0 && midpoint > 0 && ask >= bid) spreadPct = (ask - bid) / midpoint;
        if (spreadPct < 0 || spreadPct > 0.005) spreadPct = Double.NaN;

        JsonNode signal = path(snapshot, "signal");
        JsonNode features = path(signal, "feature_snapshot_json");
        JsonNode explanation = path(signal, "explanation");
        Prediction classical = prediction(path(snapshot, "model_prediction"));
        JsonNode transformerNode = path(snapshot, "transformer");
        Prediction transformer = prediction(transformerNode);

        double expectedReturn = firstNumber(features, "ml_expected_return", "expected_return");
        if (expectedReturn == 0) expectedReturn = number(path(snapshot, "model_prediction"), "expected_return");
        if (expectedReturn == 0) expectedReturn = number(transformerNode, "expected_return_5m");
        double expectedCost = firstNumber(features, "ml_expected_cost", "expected_cost");
        if (expectedCost == 0) expectedCost = number(transformerNode, "expected_cost");
        double expectedNetEdge = firstNumber(features, "ml_expected_net_edge", "expected_net_edge");
        if (expectedNetEdge == 0 && (expectedReturn != 0 || expectedCost != 0)) expectedNetEdge = expectedReturn - expectedCost;
        expectedReturn = plausible(expectedReturn, -0.05, 0.05);
        expectedCost = plausible(expectedCost, 0.0, 0.005);
        expectedNetEdge = plausible(expectedNetEdge, -0.05, 0.05);

        JsonNode performanceNode = path(snapshot, "performance");
        JsonNode account = path(snapshot, "account");
        Performance performance = new Performance(
                integer(performanceNode, "trades"), number(performanceNode, "net_pnl"),
                number(performanceNode, "gross_pnl"), number(performanceNode, "win_rate"),
                nullableNumber(performanceNode, "profit_factor"), number(performanceNode, "spread_cost"),
                number(performanceNode, "slippage_cost"), number(performanceNode, "estimated_fees"),
                number(performanceNode, "estimated_live_cost"), number(performanceNode, "avg_holding_seconds"),
                number(account, "drawdown_pct"));

        Episode episode = null;
        JsonNode active = path(snapshot, "active_episodes");
        if (active.isArray() && !active.isEmpty()) episode = episode(active.get(0));
        Outcome outcome = outcome(path(snapshot, "latest_outcome"));

        String marketState = text(features, "market_state", text(signal, "market_state", text(signal, "regime", "UNKNOWN")));
        String summary = text(explanation, "summary", text(signal, "reason", "Waiting for decision evidence."));
        double liquidity = firstNumber(features, "liquidity_score", "micro_liquidity_score");
        double quoteAge = firstNumber(features, "quote_age_seconds", "stream_quote_age_seconds");
        double tradeAge = firstNumber(features, "trade_age_seconds", "stream_trade_age_seconds");
        String pattern = text(features, "pattern_classification", text(features, "pattern", "No confirmed pattern"));
        String riskBlock = text(features, "risk_block_reason", "");
        boolean streamConnected = signal.path("stream_connected").asBoolean(false);
        boolean marketClosed = marketState.toUpperCase(Locale.ROOT).contains("CLOSED");

        List<Evidence> evidence = new ArrayList<>();
        evidence.add(new Evidence("Market data", marketClosed ? "Market is closed" : freshnessDetail(streamConnected, quoteAge, tradeAge),
                marketClosed ? EvidenceState.WARN : streamConnected && quoteAge <= 30 && tradeAge <= 30 ? EvidenceState.PASS : EvidenceState.BLOCKED));
        String liquidityDetail = liquidity <= 0 ? "Liquidity score unavailable"
                : Double.isFinite(spreadPct)
                ? String.format(Locale.US, "Score %.0f%%; spread %.3f%%", liquidity * 100, spreadPct * 100)
                : String.format(Locale.US, "Score %.0f%%; spread invalid", liquidity * 100);
        evidence.add(new Evidence("Liquidity", liquidityDetail,
                liquidity <= 0 ? EvidenceState.UNAVAILABLE
                        : liquidity >= 0.55 && Double.isFinite(spreadPct) && spreadPct <= 0.0015 ? EvidenceState.PASS : EvidenceState.BLOCKED));
        evidence.add(new Evidence("Price action", human(pattern), pattern.toLowerCase(Locale.ROOT).contains("no confirmed") ? EvidenceState.WARN : EvidenceState.PASS));
        evidence.add(new Evidence("Classical ML", predictionDetail(classical), classical.available() ? EvidenceState.PASS : EvidenceState.UNAVAILABLE));
        evidence.add(new Evidence("Transformer", predictionDetail(transformer), transformer.available() ? EvidenceState.PASS : EvidenceState.UNAVAILABLE));
        evidence.add(new Evidence("Risk gate", riskBlock.isBlank() ? "No explicit risk block in this frame" : human(riskBlock),
                riskBlock.isBlank() ? EvidenceState.PASS : EvidenceState.BLOCKED));
        evidence.add(new Evidence("Execution", episode == null ? "No active execution episode" : human(episode.status()) + " / " + human(episode.direction()),
                episode == null ? EvidenceState.WARN : EvidenceState.PASS));

        return new DecisionTelemetry(
                bid, ask, midpoint, spreadPct, text(quote, "timestamp", ""),
                human(text(signal, "decision", "NO DATA")), human(marketState),
                human(text(signal, "regime", "UNKNOWN")), summary,
                number(signal, "directional_rule_strength"), liquidity, quoteAge, tradeAge,
                expectedReturn, expectedCost, expectedNetEdge, classical, transformer,
                human(text(transformerNode, "model_role", "Unavailable")),
                number(transformerNode, "uncertainty"), number(transformerNode, "inference_latency_ms"),
                performance, episode, outcome, List.copyOf(evidence));
    }

    private static Prediction prediction(JsonNode node) {
        if (node.isMissingNode() || node.isNull() || node.isEmpty()) return new Prediction(false, 0, 0, 0, "");
        boolean probabilities = node.hasNonNull("probability_long") || node.hasNonNull("probability_short") || node.hasNonNull("probability_no_trade");
        return new Prediction(probabilities, number(node, "probability_long"), number(node, "probability_short"),
                number(node, "probability_no_trade"), text(node, "model_version", ""));
    }

    private static Episode episode(JsonNode node) {
        return new Episode(text(node, "episode_id", ""), human(text(node, "direction", "Unknown")),
                human(text(node, "playbook", "Unassigned")), human(text(node, "strategy_path", "Unknown")),
                text(node, "status", "unknown"), number(node, "filled_qty"), number(node, "remaining_qty"),
                number(node, "entry_avg_price"), number(node, "realized_pnl"), text(node, "opened_at", ""),
                path(node, "details_json"));
    }

    private static Outcome outcome(JsonNode node) {
        if (node.isMissingNode() || node.isNull() || node.isEmpty()) return null;
        return new Outcome(text(node, "trade_id", ""), human(text(node, "direction", "Unknown")),
                human(text(node, "playbook", "Unassigned")), human(text(node, "strategy_path", "Unknown")),
                number(node, "entry_price"), number(node, "exit_price"), number(node, "qty"),
                number(node, "gross_pnl"), number(node, "net_pnl_after_costs"), number(node, "holding_seconds"),
                human(text(node, "exit_reason", "Unknown")), human(text(node, "win_loss", "Unknown")),
                number(node, "spread_cost"), number(node, "slippage_cost"), number(node, "estimated_fees"),
                number(node, "estimated_live_cost"), number(node, "profit_given_back"),
                number(node, "max_favorable_excursion"), number(node, "max_adverse_excursion"),
                text(node, "entry_time", ""), text(node, "exit_time", ""));
    }

    private static String predictionDetail(Prediction prediction) {
        if (!prediction.available()) return "No usable probability frame";
        return String.format(Locale.US, "Long %.0f%% / Short %.0f%% / Abstain %.0f%%",
                prediction.longProbability() * 100, prediction.shortProbability() * 100,
                prediction.noTradeProbability() * 100);
    }

    private static String freshnessDetail(boolean connected, double quoteAge, double tradeAge) {
        if (!connected) return "Live stream disconnected";
        return String.format(Locale.US, "Quote %.1fs / trade %.1fs old", quoteAge, tradeAge);
    }

    private static JsonNode path(JsonNode node, String field) {
        return node == null || node.isMissingNode() || node.isNull() ? com.fasterxml.jackson.databind.node.MissingNode.getInstance() : node.path(field);
    }

    private static double firstNumber(JsonNode node, String... fields) {
        for (String field : fields) if (node.hasNonNull(field)) return number(node, field);
        return 0;
    }

    private static double nullableNumber(JsonNode node, String field) {
        return node.hasNonNull(field) ? number(node, field) : Double.NaN;
    }

    private static double plausible(double value, double minimum, double maximum) {
        return Double.isFinite(value) && value >= minimum && value <= maximum ? value : Double.NaN;
    }

    private static double number(JsonNode node, String field) {
        JsonNode value = path(node, field);
        if (value.isNumber()) return value.asDouble();
        try { return Double.parseDouble(value.asText("0")); } catch (NumberFormatException ignored) { return 0; }
    }

    private static int integer(JsonNode node, String field) { return (int) Math.round(number(node, field)); }

    private static String text(JsonNode node, String field, String fallback) {
        JsonNode value = path(node, field);
        String text = value.isValueNode() ? value.asText("") : "";
        return text.isBlank() ? fallback : text;
    }

    static String human(String value) {
        if (value == null || value.isBlank()) return "Unavailable";
        String text = value.replace('_', ' ').trim().toLowerCase(Locale.ROOT);
        return Character.toUpperCase(text.charAt(0)) + text.substring(1);
    }
}
