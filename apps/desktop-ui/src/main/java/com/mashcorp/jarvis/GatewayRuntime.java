package com.mashcorp.jarvis;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Base64;

/** Starts the Python gateway as an isolated child and never receives broker credentials. */
public final class GatewayRuntime implements AutoCloseable {
    private final Path projectRoot;
    private final String token;
    private final int port;
    private Process process;
    private Path gatewayLogPath;
    private java.nio.channels.FileChannel lockChannel;
    private java.nio.channels.FileLock instanceLock;

    public GatewayRuntime() {
        projectRoot = locateProjectRoot();
        token = createToken();
        port = findAvailablePort(8765, 8795);
    }

    public void start() throws IOException, InterruptedException {
        String override = System.getenv("JARVIS_PYTHON");
        Path python = override == null ? projectRoot.resolve(".venv/Scripts/python.exe") : Path.of(override);
        if (!Files.isRegularFile(python)) {
            throw new IOException("Python environment not found: " + python);
        }
        Path logRoot = projectRoot.resolve("logs/dashboard");
        Files.createDirectories(logRoot);
        lockChannel = java.nio.channels.FileChannel.open(logRoot.resolve("javafx_instance.lock"),
                java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.WRITE);
        instanceLock = lockChannel.tryLock();
        if (instanceLock == null) { lockChannel.close(); throw new IOException("The JavaFX dashboard is already open for this project"); }
        Path gatewayLog = prepareGatewayLog(logRoot);
        gatewayLogPath = gatewayLog;
        ProcessBuilder builder = new ProcessBuilder(
                python.toString(), "-m", "gld_scalper.main", "dashboard",
                "--no-browser", "--port", Integer.toString(port));
        builder.directory(projectRoot.toFile());
        builder.environment().put("DASHBOARD_SESSION_TOKEN", token);
        builder.redirectOutput(ProcessBuilder.Redirect.to(gatewayLog.toFile()));
        builder.redirectErrorStream(true);
        try {
        process = builder.start();
        String startedAt = process.info().startInstant().orElse(Instant.now()).toString();
        Files.writeString(logRoot.resolve("javafx_gateway.pid"),
                "{\"pid\":" + process.pid() + ",\"started_at\":\"" + startedAt
                        + "\",\"log\":\"" + gatewayLog.getFileName() + "\"}", StandardCharsets.US_ASCII);
        awaitHealth();
        } catch (IOException | InterruptedException exc) { close(); throw exc; }
    }

    public URI baseUri() {
        return URI.create("http://127.0.0.1:" + port);
    }

    public String token() {
        return token;
    }

    public Path projectRoot() {
        return projectRoot;
    }

    private void awaitHealth() throws IOException, InterruptedException {
        HttpClient client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(1)).build();
        URI health = baseUri().resolve("/api/v1/system/health");
        long deadline = System.nanoTime() + Duration.ofSeconds(60).toNanos();
        String lastFailure = "local socket has not accepted a connection";
        while (System.nanoTime() < deadline) {
            if (process != null && !process.isAlive()) {
                throw startupFailure("Python dashboard gateway exited during startup with code " + process.exitValue());
            }
            try {
                HttpResponse<String> response = client.send(
                        HttpRequest.newBuilder(health).timeout(Duration.ofSeconds(1)).GET().build(),
                        HttpResponse.BodyHandlers.ofString());
                if (response.statusCode() == 200) return;
                lastFailure = "health endpoint returned HTTP " + response.statusCode();
            } catch (IOException exc) {
                lastFailure = exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage());
            }
            Thread.sleep(200);
        }
        throw startupFailure("Python dashboard gateway did not become healthy within 60 seconds (" + lastFailure + ")");
    }

    private IOException startupFailure(String message) {
        Path log = gatewayLogPath == null
                ? projectRoot.resolve("logs/dashboard/javafx_gateway.log")
                : gatewayLogPath;
        try {
            var lines = Files.readAllLines(log, StandardCharsets.UTF_8);
            int first = Math.max(0, lines.size() - 8);
            String tail = String.join(" | ", lines.subList(first, lines.size()));
            if (!tail.isBlank()) return new IOException(message + ". Gateway log: " + tail);
        } catch (IOException ignored) {
            // Preserve the primary startup failure when the log cannot be read.
        }
        return new IOException(message + ". Review " + log);
    }

    /**
     * Rotates the conventional log when Windows permits it. A stale process may
     * still hold that file open, so log housekeeping must never block startup.
     */
    static Path prepareGatewayLog(Path logRoot) throws IOException {
        Files.createDirectories(logRoot);
        Path current = logRoot.resolve("javafx_gateway.log");
        if (!Files.exists(current)) return current;
        try {
            Files.move(current, logRoot.resolve("javafx_gateway.previous.log"),
                    StandardCopyOption.REPLACE_EXISTING);
            return current;
        } catch (IOException lockedOrUnavailable) {
            String stamp = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSS")
                    .withZone(ZoneOffset.UTC).format(Instant.now());
            return logRoot.resolve("javafx_gateway." + stamp + "-" + ProcessHandle.current().pid() + ".log");
        }
    }

    private static Path locateProjectRoot() {
        String configured = System.getProperty("jarvis.projectRoot", System.getenv("JARVIS_PROJECT_ROOT"));
        Path current = configured == null || configured.isBlank()
                ? Path.of("").toAbsolutePath().normalize()
                : Path.of(configured).toAbsolutePath().normalize();
        for (Path candidate = current; candidate != null; candidate = candidate.getParent()) {
            if (Files.isRegularFile(candidate.resolve("pyproject.toml"))
                    && Files.isDirectory(candidate.resolve("src/gld_scalper"))) return candidate;
        }
        throw new IllegalStateException("Could not locate the GLD bot project root from " + current);
    }

    private static int findAvailablePort(int first, int last) {
        for (int candidate = first; candidate <= last; candidate++) {
            try (ServerSocket socket = new ServerSocket()) {
                socket.bind(new InetSocketAddress("127.0.0.1", candidate));
                return candidate;
            } catch (IOException ignored) {
                // Try the next local-only port.
            }
        }
        throw new IllegalStateException("No local dashboard port is available between " + first + " and " + last);
    }

    private static String createToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    @Override
    public void close() {
        if (process != null && process.isAlive()) {
        process.destroy();
        try {
            if (!process.waitFor(5, java.util.concurrent.TimeUnit.SECONDS)) process.destroyForcibly();
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            process.destroyForcibly();
        }
        }
        try {
            if (process != null) Files.deleteIfExists(projectRoot.resolve("logs/dashboard/javafx_gateway.pid"));
            if (instanceLock != null && instanceLock.isValid()) instanceLock.release();
            if (lockChannel != null && lockChannel.isOpen()) lockChannel.close();
        }
        catch (IOException ignored) { }
    }
}
