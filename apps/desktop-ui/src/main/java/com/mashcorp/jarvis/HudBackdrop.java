package com.mashcorp.jarvis;

import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.layout.Region;
import javafx.scene.paint.Color;

/** Low-cost command-grid backdrop. It is visual only and owns no application state. */
final class HudBackdrop extends Region {
    private final Canvas canvas = new Canvas();

    HudBackdrop() {
        getChildren().add(canvas);
        canvas.widthProperty().bind(widthProperty());
        canvas.heightProperty().bind(heightProperty());
        widthProperty().addListener((observable, before, after) -> draw());
        heightProperty().addListener((observable, before, after) -> draw());
        setMouseTransparent(true);
    }

    private void draw() {
        double width = getWidth();
        double height = getHeight();
        if (width <= 0 || height <= 0) return;
        GraphicsContext graphics = canvas.getGraphicsContext2D();
        graphics.setFill(Color.web("#010507"));
        graphics.fillRect(0, 0, width, height);

        graphics.setLineWidth(1);
        for (int x = 0; x < width; x += 32) {
            graphics.setStroke(Color.web(x % 160 == 0 ? "#103543" : "#071b22", x % 160 == 0 ? 0.28 : 0.18));
            graphics.strokeLine(x + 0.5, 0, x + 0.5, height);
        }
        for (int y = 0; y < height; y += 32) {
            graphics.setStroke(Color.web(y % 160 == 0 ? "#103543" : "#071b22", y % 160 == 0 ? 0.28 : 0.18));
            graphics.strokeLine(0, y + 0.5, width, y + 0.5);
        }

        graphics.setStroke(Color.web("#2de1ff", 0.26));
        graphics.setLineWidth(1.25);
        double inset = 14;
        double length = 42;
        graphics.strokeLine(inset, inset, inset + length, inset);
        graphics.strokeLine(inset, inset, inset, inset + length);
        graphics.strokeLine(width - inset, inset, width - inset - length, inset);
        graphics.strokeLine(width - inset, inset, width - inset, inset + length);
        graphics.strokeLine(inset, height - inset, inset + length, height - inset);
        graphics.strokeLine(inset, height - inset, inset, height - inset - length);
        graphics.strokeLine(width - inset, height - inset, width - inset - length, height - inset);
        graphics.strokeLine(width - inset, height - inset, width - inset, height - inset - length);
    }
}
