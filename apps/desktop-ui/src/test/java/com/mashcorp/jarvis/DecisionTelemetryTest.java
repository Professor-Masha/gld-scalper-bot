package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

final class DecisionTelemetryTest {
    @Test
    void projectsLiveDecisionModelsCostsAndEpisodeWithoutInventingValues() throws Exception {
        var snapshot = new ObjectMapper().readTree("""
                {
                  "quote":{"bid_price":218.40,"ask_price":218.42,"spread_pct":0.0000916,"timestamp":"2026-09-08T14:27:41Z"},
                  "signal":{"decision":"LONG","regime":"bullish_trend","stream_connected":true,"directional_rule_strength":0.72,
                    "reason":"Clean proper break","feature_snapshot_json":{"market_state":"market_open","liquidity_score":0.81,
                    "quote_age_seconds":0.2,"trade_age_seconds":0.4,"pattern_classification":"proper_break",
                    "ml_expected_return":0.0012,"ml_expected_cost":0.0003,"ml_expected_net_edge":0.0009}},
                  "model_prediction":{"model_version":"rf-7","probability_long":0.68,"probability_short":0.12,"probability_no_trade":0.20},
                  "transformer":{"model_version":"tx-3","model_role":"paper_shadow","probability_long":0.62,
                    "probability_short":0.15,"probability_no_trade":0.23,"uncertainty":0.18,"inference_latency_ms":3.4},
                  "performance":{"trades":12,"net_pnl":18.42,"gross_pnl":24.2,"win_rate":0.58,"profit_factor":1.31,
                    "spread_cost":2.1,"slippage_cost":1.2,"avg_holding_seconds":93},
                  "account":{"drawdown_pct":0.004},
                  "active_episodes":[{"episode_id":"episode-1","direction":"long","playbook":"proper_breakout",
                    "strategy_path":"fast","status":"active","filled_qty":100,"remaining_qty":100,
                    "entry_avg_price":218.10,"opened_at":"2026-09-08T14:26:00Z","details_json":{"stop_price":217.90}}]
                }
                """);

        DecisionTelemetry frame = DecisionTelemetry.from(snapshot);

        assertEquals(218.41, frame.midpoint(), 0.0001);
        assertEquals("Long", frame.decision());
        assertEquals(0.0009, frame.expectedNetEdge(), 0.000001);
        assertEquals(0.68, frame.classical().longProbability(), 0.0001);
        assertEquals(0.62, frame.transformer().longProbability(), 0.0001);
        assertEquals("Paper shadow", frame.transformerRole());
        assertEquals(1.31, frame.performance().profitFactor(), 0.0001);
        assertNotNull(frame.activeEpisode());
        assertEquals("Proper breakout", frame.activeEpisode().playbook());
        assertEquals(DecisionTelemetry.EvidenceState.PASS, frame.evidence().get(0).state());
        assertEquals(DecisionTelemetry.EvidenceState.PASS, frame.evidence().get(1).state());
    }

    @Test
    void keepsUnavailableModelsAndClosedMarketExplicit() throws Exception {
        var snapshot = new ObjectMapper().readTree("""
                {"signal":{"decision":"NO_TRADE","stream_connected":false,"feature_snapshot_json":{"market_state":"market_closed"}},
                 "performance":{},"account":{},"active_episodes":[]}
                """);
        DecisionTelemetry frame = DecisionTelemetry.from(snapshot);
        assertFalse(frame.classical().available());
        assertFalse(frame.transformer().available());
        assertEquals("Market closed", frame.marketState());
        assertEquals(DecisionTelemetry.EvidenceState.WARN, frame.evidence().get(0).state());
    }
}
