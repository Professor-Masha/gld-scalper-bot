package com.mashcorp.jarvis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;

final class DecisionPricePlotTest {
    @Test
    void liveScaleNeverContractsWithinASession() {
        assertArrayEquals(new double[] {99, 101}, DecisionPricePlot.nextScale(99, 101, 99.5, 100.5));
        assertArrayEquals(new double[] {98, 102}, DecisionPricePlot.nextScale(99, 101, 98, 102));
    }

    @Test
    void initialScaleRequiresAFinitePositiveRange() {
        assertArrayEquals(new double[] {99, 101}, DecisionPricePlot.nextScale(Double.NaN, Double.NaN, 99, 101));
        assertArrayEquals(new double[] {99, 101}, DecisionPricePlot.nextScale(99, 101, Double.NaN, 100));
    }
}
