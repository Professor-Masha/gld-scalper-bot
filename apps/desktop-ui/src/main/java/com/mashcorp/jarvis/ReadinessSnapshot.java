package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;

/** Human-facing readiness states derived from the gateway contract. */
record ReadinessSnapshot(
        String interfaceState,
        String tradingState,
        String marketState,
        String llmState,
        boolean tradingAllowed) {

    static ReadinessSnapshot from(JsonNode readiness, JsonNode providers) {
        boolean tradingAllowed = readiness.path("ready").asBoolean(false);
        String trading = readiness.path("trading").path("state")
                .asText(tradingAllowed ? "AVAILABLE" : "BLOCKED").toUpperCase();
        String market = readiness.path("market").path("session").asText("unknown").toUpperCase();
        if ("REGULAR".equals(market)) market = "OPEN";

        String activeProvider = providers.path("active_provider").asText("none");
        String llm = "none".equalsIgnoreCase(activeProvider)
                ? "DISABLED"
                : activeProvider.toUpperCase() + " CONFIGURED";
        return new ReadinessSnapshot("READY", trading, market, llm, tradingAllowed);
    }
}
