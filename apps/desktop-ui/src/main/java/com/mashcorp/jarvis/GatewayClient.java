package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.function.Consumer;

/** Typed-enough localhost client for the versioned gateway and approved legacy read routes. */
public final class GatewayClient {
    private final URI baseUri;
    private final String token;
    private final HttpClient http;
    private final ObjectMapper json = new ObjectMapper();

    public GatewayClient(URI baseUri, String token) {
        if (!"http".equals(baseUri.getScheme()) || !java.util.Set.of("127.0.0.1", "localhost", "[::1]").contains(baseUri.getHost()) || baseUri.getUserInfo() != null) {
            throw new IllegalArgumentException("The desktop gateway must use a loopback HTTP endpoint");
        }
        if (token == null || token.length() < 32) throw new IllegalArgumentException("A strong session token is required");
        this.baseUri = baseUri;
        this.token = token;
        this.http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(3)).build();
    }

    public JsonNode get(String path) throws Exception {
        HttpResponse<String> response = http.send(
                HttpRequest.newBuilder(baseUri.resolve(path)).timeout(Duration.ofSeconds(12)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        return parse(response, path);
    }

    public JsonNode post(String path, Object body) throws Exception {
        String payload = json.writeValueAsString(body);
        HttpResponse<String> response = http.send(
                HttpRequest.newBuilder(baseUri.resolve(path))
                        .timeout(Duration.ofMinutes(5))
                        .header("Content-Type", "application/json")
                        .header("X-Dashboard-Token", token)
                        .header("X-Correlation-ID", "javafx-" + UUID.randomUUID())
                        .POST(HttpRequest.BodyPublishers.ofString(payload))
                        .build(), HttpResponse.BodyHandlers.ofString());
        return parse(response, path);
    }

    public JsonNode startPaper() throws Exception {
        return post("/api/v1/bot/start", Map.of(
                "actor_id", "javafx-operator",
                "idempotency_key", "paper-start-" + UUID.randomUUID(),
                "options", Map.of("no_retraining", true)));
    }

    public JsonNode stopPaper() throws Exception {
        return post("/api/v1/bot/stop", Map.of(
                "actor_id", "javafx-operator",
                "idempotency_key", "paper-stop-" + UUID.randomUUID(),
                "options", Map.of()));
    }

    public JsonNode startJob(String action, JsonNode options) throws Exception {
        ObjectNode request = json.createObjectNode();
        request.put("action", action);
        request.put("actor_id", "javafx-operator");
        request.put("idempotency_key", "job-" + UUID.randomUUID());
        request.set("options", options);
        return post("/api/v1/training/jobs", request);
    }

    public JsonNode parseObject(String value) throws Exception {
        if (value == null || value.isBlank()) return json.createObjectNode();
        JsonNode node = json.readTree(value);
        if (!node.isObject()) throw new IllegalArgumentException("Options must be a JSON object");
        return node;
    }

    public WebSocket openEvents(Consumer<JsonNode> onEvent, Consumer<Throwable> onError) {
        return http.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(5))
                .buildAsync(webSocketUri(), new WebSocket.Listener() {
                    private final StringBuilder buffer = new StringBuilder();

                    @Override
                    public void onOpen(WebSocket webSocket) {
                        webSocket.sendText(token, true);
                        webSocket.request(1);
                    }

                    @Override
                    public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
                        buffer.append(data);
                        if (last) {
                            try { onEvent.accept(json.readTree(buffer.toString())); }
                            catch (Exception exc) { onError.accept(exc); }
                            buffer.setLength(0);
                        }
                        webSocket.request(1);
                        return CompletableFuture.completedFuture(null);
                    }

                    @Override
                    public void onError(WebSocket webSocket, Throwable error) {
                        onError.accept(error);
                    }
                }).join();
    }

    private URI webSocketUri() {
        return URI.create("ws://" + baseUri.getHost() + ":" + baseUri.getPort() + "/api/v1/events");
    }

    private JsonNode parse(HttpResponse<String> response, String path) throws Exception {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException(path + " returned HTTP " + response.statusCode() + ": " + response.body());
        }
        return response.body().isBlank() ? json.createObjectNode() : json.readTree(response.body());
    }
}
