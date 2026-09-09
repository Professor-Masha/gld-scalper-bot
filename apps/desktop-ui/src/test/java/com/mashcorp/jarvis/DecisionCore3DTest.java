package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class DecisionCore3DTest {
    @Test
    void zoomOffsetIsFiniteAndBounded() {
        assertEquals(140, DecisionCore3D.clampZoomOffset(Double.MAX_VALUE));
        assertEquals(-250, DecisionCore3D.clampZoomOffset(-Double.MAX_VALUE));
        assertEquals(0, DecisionCore3D.clampZoomOffset(Double.NaN));
    }

    @Test
    void cameraDistanceCannotEscapeRenderingEnvelope() {
        for (double width : new double[] {1, 320, 1920, Double.NaN}) {
            for (double height : new double[] {1, 240, 1080, Double.POSITIVE_INFINITY}) {
                for (double offset : new double[] {-10_000, 0, 10_000, Double.NaN}) {
                    double distance = DecisionCore3D.cameraDistance(width, height, offset);
                    assertTrue(distance >= DecisionCore3D.MIN_CAMERA_Z);
                    assertTrue(distance <= DecisionCore3D.MAX_CAMERA_Z);
                }
            }
        }
    }
}
