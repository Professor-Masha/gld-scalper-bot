package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.jupiter.api.Assertions.*;

class GatewayClientTest {
    private static final String TOKEN = "test-only-256-bit-session-token-no-real-secrets";

    @Test void rejectsRemoteGatewayAndWeakToken() {
        assertThrows(IllegalArgumentException.class, () -> new GatewayClient(URI.create("https://example.org"), TOKEN));
        assertThrows(IllegalArgumentException.class, () -> new GatewayClient(URI.create("http://127.0.0.1:8765"), "short"));
    }

    @Test void optionsMustBeObjects() throws Exception {
        var client = new GatewayClient(URI.create("http://127.0.0.1:8765"), TOKEN);
        assertTrue(client.parseObject("").isObject());
        assertEquals(8, client.parseObject("{\"epochs\":8}").path("epochs").asInt());
        assertThrows(IllegalArgumentException.class, () -> client.parseObject("[]"));
    }

    @Test void authenticatesTypedCommandsWithoutRealBrokerCalls() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        AtomicReference<String> body = new AtomicReference<>();
        AtomicReference<String> auth = new AtomicReference<>();
        server.createContext("/api/v1/bot/start", exchange -> {
            auth.set(exchange.getRequestHeaders().getFirst("X-Dashboard-Token"));
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] response = "{\"accepted\":true}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length); exchange.getResponseBody().write(response); exchange.close();
        });
        server.createContext("/api/error", exchange -> { exchange.sendResponseHeaders(409, -1); exchange.close(); });
        server.start();
        try {
            var client = new GatewayClient(URI.create("http://127.0.0.1:" + server.getAddress().getPort()), TOKEN);
            assertTrue(client.startPaper().path("accepted").asBoolean());
            assertEquals(TOKEN, auth.get()); assertFalse(body.get().contains(TOKEN));
            var command = new ObjectMapper().readTree(body.get());
            assertTrue(command.path("options").path("no_retraining").asBoolean());
            assertFalse(command.path("idempotency_key").asText().isBlank());
            assertThrows(IllegalStateException.class, () -> client.get("/api/error"));
            @SuppressWarnings("unchecked")
            var operations = (java.util.Map<String, Object>) client.performanceSnapshot().get("operations");
            assertTrue(operations.containsKey("POST /api/v1/bot/start"));
            assertTrue(operations.containsKey("GET /api/error"));
        } finally { server.stop(0); }
    }
}
