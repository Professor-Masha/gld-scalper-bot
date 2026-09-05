from __future__ import annotations

import numpy as np
import pytest

from gld_scalper.ml.calibration import chronological_partitions, fit_natural_frequency_calibrator
from gld_scalper.ml.decision_policy import expected_edge_decision
from gld_scalper.ml.feature_selection import select_stable_features
from gld_scalper.ml.holding_labels import triple_barrier_entry_label, triple_barrier_holding_label


def test_chronological_partitions_are_ordered_and_purged() -> None:
    timestamps = np.arange(400, dtype=float) * 60.0 + 1_700_000_000.0
    partitions = chronological_partitions(timestamps, purge_seconds=15 * 60, minimum_partition_size=20)
    values = [partitions.train, partitions.calibration, partitions.threshold, partitions.holdout]
    assert all(np.all(np.diff(part) > 0) for part in values)
    for previous, current in zip(values, values[1:]):
        assert timestamps[current[0]] > timestamps[previous[-1]] + 15 * 60


def test_calibrator_preserves_probability_shape_on_natural_prevalence() -> None:
    probabilities = np.tile([0.20, 0.25, 0.55], (120, 1))
    labels = ["no_trade"] * 80 + ["long_good"] * 25 + ["short_good"] * 15
    calibrator, metrics = fit_natural_frequency_calibrator(
        probabilities, labels, ["long_good", "short_good", "no_trade"]
    )
    calibrated = calibrator.predict_proba(probabilities)
    assert calibrated.shape == (120, 3)
    assert np.allclose(calibrated.sum(axis=1), 1.0)
    assert metrics["samples"] == 120


def test_expected_edge_blocks_high_probability_trade_when_cost_wins() -> None:
    decision = expected_edge_decision(
        probabilities={"long_good": 0.70, "short_good": 0.10, "no_trade": 0.20},
        expected_returns={"long_good": 0.0004, "short_good": 0.0003},
        expected_cost=0.0005,
        minimum_edge=0.00005,
        minimum_confidence=0.60,
        minimum_margin=0.10,
    )
    assert not decision.accepted
    assert decision.action == "NO_TRADE"
    assert decision.expected_net_edge < 0


def test_feature_selection_caps_and_removes_redundancy() -> None:
    records = []
    for index in range(200):
        records.append({"features": {"a": index / 10, "copy": index / 10, "stable": index % 7, "empty": None}})
    selected, diagnostics = select_stable_features(records, ["a", "copy", "stable", "empty"], maximum_features=2)
    assert len(selected) == 2
    assert not ({"a", "copy"} <= set(selected))
    assert diagnostics["selected_feature_count"] == 2


def test_triple_barrier_entry_uses_first_touch_not_terminal_only() -> None:
    label = triple_barrier_entry_label(
        current_price=100.0,
        future_prices=[100.2, 100.5, 99.7],
        seconds_per_observation=1,
        profit_barrier_pct=0.004,
        risk_barrier_pct=0.004,
        round_trip_cost_pct=0.001,
    )
    assert label.label == "long_good"
    assert label.barrier == "upper"


def test_holding_label_accounts_for_cost_and_path() -> None:
    label = triple_barrier_holding_label(
        direction="LONG",
        current_price=100.0,
        future_prices=[100.05, 100.12, 100.08],
        seconds_per_observation=1,
        profit_barrier_pct=0.003,
        risk_barrier_pct=0.004,
        round_trip_cost_pct=0.001,
    )
    assert label.action in {"hold", "reduce", "close"}
    assert label.remaining_net_return == pytest.approx(-0.0002)
