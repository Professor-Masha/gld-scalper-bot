package com.mashcorp.jarvis;

import javafx.scene.canvas.Canvas;

/** Keeps JavaFX canvas backing textures within safe, finite GPU dimensions. */
final class CanvasSurface {
    static final double MAX_DIMENSION = 2048;

    private CanvasSurface() { }

    static void resize(Canvas canvas, double requestedWidth, double requestedHeight) {
        double width = boundedDimension(requestedWidth);
        double height = boundedDimension(requestedHeight);
        if (Math.abs(canvas.getWidth() - width) > 0.5) canvas.setWidth(width);
        if (Math.abs(canvas.getHeight() - height) > 0.5) canvas.setHeight(height);
    }

    static double boundedDimension(double value) {
        if (!Double.isFinite(value) || value <= 0) return 1;
        return Math.min(MAX_DIMENSION, value);
    }
}
