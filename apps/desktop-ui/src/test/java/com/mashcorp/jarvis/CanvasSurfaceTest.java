package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class CanvasSurfaceTest {
    @Test
    void boundsInvalidAndOversizedTextureDimensions() {
        assertEquals(1, CanvasSurface.boundedDimension(Double.POSITIVE_INFINITY));
        assertEquals(1, CanvasSurface.boundedDimension(-20));
        assertEquals(900, CanvasSurface.boundedDimension(900));
        assertEquals(CanvasSurface.MAX_DIMENSION, CanvasSurface.boundedDimension(50_000));
    }
}
