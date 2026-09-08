package com.mashcorp.jarvis;

import javafx.animation.FadeTransition;
import javafx.application.Platform;
import javafx.geometry.Pos;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.util.Duration;

/** Real startup progress: every percentage is advanced by a completed readiness phase. */
final class StartupOverlay extends StackPane {
    private final ProgressBar progress = new ProgressBar(0);
    private final Label percentage = new Label("0%");
    private final Label phase = new Label("Preparing local command center");

    StartupOverlay() {
        Label monogram = new Label("MC");
        monogram.getStyleClass().add("startup-monogram");
        Label title = new Label("GLD COMMAND CENTER");
        title.getStyleClass().add("startup-title");
        Label contract = new Label("READ-ONLY INTERFACE BOOTSTRAP // PAPER NETWORK");
        contract.getStyleClass().add("startup-contract");
        percentage.getStyleClass().add("startup-percentage");
        phase.getStyleClass().add("startup-phase");
        phase.setWrapText(true);
        phase.setMaxWidth(760);
        phase.setAlignment(Pos.CENTER);
        progress.getStyleClass().add("startup-progress");
        progress.setPrefWidth(520);
        VBox content = new VBox(13, monogram, title, contract, progress, percentage, phase);
        content.setAlignment(Pos.CENTER);
        content.setMaxWidth(620);
        getChildren().add(content);
        setAlignment(Pos.CENTER);
        getStyleClass().add("startup-overlay");
    }

    void update(int percent, String detail) {
        Runnable change = () -> {
            int bounded = Math.max(0, Math.min(100, percent));
            progress.setProgress(bounded / 100.0);
            percentage.setText(bounded + "%");
            phase.setText(detail);
        };
        if (Platform.isFxApplicationThread()) change.run(); else Platform.runLater(change);
    }

    void complete(boolean reducedMotion, Runnable after) {
        update(100, "Core interface ready");
        if (reducedMotion) {
            setVisible(false);
            setManaged(false);
            after.run();
            return;
        }
        FadeTransition fade = new FadeTransition(Duration.millis(320), this);
        fade.setFromValue(1);
        fade.setToValue(0);
        fade.setDelay(Duration.millis(160));
        fade.setOnFinished(event -> {
            setVisible(false);
            setManaged(false);
            after.run();
        });
        fade.play();
    }

    void fail(String detail) {
        Runnable change = () -> {
            percentage.setText("BLOCKED");
            phase.setText("Startup blocked: " + detail);
        };
        if (Platform.isFxApplicationThread()) change.run(); else Platform.runLater(change);
        getStyleClass().add("startup-failed");
    }
}
