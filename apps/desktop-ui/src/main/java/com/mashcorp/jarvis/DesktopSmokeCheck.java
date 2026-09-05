package com.mashcorp.jarvis;

import javafx.animation.PauseTransition;
import javafx.application.Platform;
import javafx.scene.image.WritableImage;
import javafx.stage.Stage;
import javafx.util.Duration;
import java.awt.image.BufferedImage;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.function.BooleanSupplier;
import javax.imageio.ImageIO;

/** Explicit opt-in read-only desktop QA; it never fires operational controls. */
final class DesktopSmokeCheck {
    private final Stage stage;
    private final Path directory;
    private final BooleanSupplier telemetryReady;
    private final List<Runnable> views;
    private int index;
    private int readinessChecks;
    private final FrameTimeMonitor frameTimes = new FrameTimeMonitor();

    private DesktopSmokeCheck(Stage stage, Path directory, BooleanSupplier telemetryReady, List<Runnable> views) {
        this.stage = stage; this.directory = directory; this.telemetryReady = telemetryReady; this.views = views;
    }

    static void run(Stage stage, Path directory, BooleanSupplier telemetryReady, List<Runnable> views) {
        stage.setMaximized(false); stage.setWidth(1366); stage.setHeight(900);
        DesktopSmokeCheck check = new DesktopSmokeCheck(stage, directory, telemetryReady, views);
        check.frameTimes.start(); check.awaitTelemetry();
    }

    private void awaitTelemetry() {
        if (telemetryReady.getAsBoolean() || readinessChecks++ >= 60) {
            next();
            return;
        }
        PauseTransition wait = new PauseTransition(Duration.seconds(1));
        wait.setOnFinished(event -> awaitTelemetry());
        wait.play();
    }

    private void next() {
        if (index == views.size()) {
            frameTimes.stop();
            try {
                Files.writeString(directory.resolve("completed.txt"), "All read-only view snapshots completed");
                Files.writeString(directory.resolve("frame-times.json"), frameTimes.json());
            }
            catch (Exception exc) { exc.printStackTrace(); }
            Platform.exit(); return;
        }
        if (index == views.size() - 1) { stage.setWidth(900); stage.setHeight(700); }
        views.get(index).run();
        PauseTransition wait = new PauseTransition(Duration.seconds(3));
        wait.setOnFinished(event -> {
            try {
                Files.createDirectories(directory);
                WritableImage image = stage.getScene().snapshot(null);
                BufferedImage output = new BufferedImage((int)image.getWidth(), (int)image.getHeight(), BufferedImage.TYPE_INT_ARGB);
                for (int y = 0; y < output.getHeight(); y++) for (int x = 0; x < output.getWidth(); x++) output.setRGB(x, y, image.getPixelReader().getArgb(x, y));
                ImageIO.write(output, "png", directory.resolve("view-" + index + ".png").toFile());
                index++; next();
            } catch (Exception exc) { exc.printStackTrace(); Platform.exit(); }
        });
        wait.play();
    }
}
