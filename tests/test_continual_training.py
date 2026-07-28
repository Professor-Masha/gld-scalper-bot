from __future__ import annotations

from datetime import datetime, timedelta, timezone

import joblib
import pytest

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.ml.continual_training import ContinualTrainingRunner, TrainingLock, prepare_experiment_records
from gld_scalper.ml.evaluator import optimize_policy_thresholds


def _record(index: int, *, playbook: str = "proper_breakout", source: str = "historical_archive"):
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    outcomes = {
        1: {"label": "long_good", "net_return_long": 0.002, "net_return_short": -0.003},
        5: {"label": "short_good", "net_return_long": -0.004, "net_return_short": 0.003},
    }
    return {
        "timestamp": timestamp,
        "features": {"x": float(index), f"playbook__{playbook}": 1.0},
        "label": outcomes[5]["label"],
        "outcome": outcomes[5],
        "outcomes_by_horizon": outcomes,
        "horizon_minutes": 5,
        "playbook": playbook,
        "source": source,
    }


def test_prepare_experiment_records_selects_horizon_playbook_and_weights_paper():
    archive = [_record(1), _record(2, playbook="trend_continuation")]
    paper = [_record(3, source="paper")]
    records, paper_count = prepare_experiment_records(
        archive,
        paper,
        playbook="proper_breakout",
        horizon_minutes=1,
        paper_weight=3,
    )
    assert len(records) == 4
    assert paper_count == 1
    assert all(record["label"] == "long_good" for record in records)


def test_threshold_optimizer_uses_after_cost_outcomes():
    probabilities = [[0.75, 0.15, 0.10]] * 30 + [[0.42, 0.38, 0.20]] * 30
    outcomes = [
        {"net_return_long": 0.002, "net_return_short": -0.003}
        for _ in probabilities
    ]
    result = optimize_policy_thresholds(probabilities, outcomes, minimum_trades=10)
    assert result["trade_count"] >= 10
    assert result["net_return"] > 0


def test_training_lock_is_exclusive_and_releases(tmp_path):
    lock_path = tmp_path / "training.lock"
    with TrainingLock(lock_path):
        assert lock_path.exists()
        with pytest.raises(RuntimeError):
            with TrainingLock(lock_path):
                pass
    assert not lock_path.exists()


def test_continual_runner_remembers_completed_fingerprint(tmp_path, monkeypatch):
    artifact = tmp_path / "training_v3.joblib"
    records = [_record(index, playbook="proper_breakout") for index in range(40)]
    for index, record in enumerate(records):
        if index % 3 == 0:
            record["outcomes_by_horizon"][5] = {
                "label": "no_trade",
                "net_return_long": -0.001,
                "net_return_short": -0.001,
            }
        elif index % 3 == 1:
            record["outcomes_by_horizon"][5] = {
                "label": "long_good",
                "net_return_long": 0.002,
                "net_return_short": -0.003,
            }
        record["label"] = record["outcomes_by_horizon"][5]["label"]
        record["outcome"] = record["outcomes_by_horizon"][5]
    joblib.dump({"format_version": 3, "records": records}, artifact)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'paper.db'}",
        continual_training_horizons=[5],
        continual_training_playbooks=["all"],
        continual_training_min_playbook_samples=10,
    )
    database = Database(settings=settings)
    database.init_db()
    calls = []

    def fake_train(*args, **kwargs):
        calls.append(kwargs["experiment_context"]["experiment_key"])
        return {
            "model_version": "candidate-test",
            "path": str(tmp_path / "candidate-test.pkl"),
            "manifest_path": str(tmp_path / "candidate-test.json"),
            "promotion_metrics": {"profit_factor": 1.1, "net_return": 0.01},
        }

    monkeypatch.setattr("gld_scalper.ml.continual_training.train_candidate_model", fake_train)
    runner = ContinualTrainingRunner(
        database,
        settings,
        artifact=artifact,
        horizons=[5],
        playbooks=["all"],
        minimum_samples=10,
        state_root=tmp_path / "state",
    )
    first = runner.run_cycle()
    second = runner.run_cycle()
    assert first["counts"]["completed"] == 1
    assert second["counts"]["skipped_existing"] == 1
    assert len(calls) == 1
    assert database.count_rows("ml_training_experiments") == 1


def test_historical_search_round_changes_experiment_identity(tmp_path):
    artifact = tmp_path / "training_v3.joblib"
    joblib.dump({"format_version": 3, "records": [_record(index) for index in range(10)]}, artifact)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    runner = ContinualTrainingRunner(database, settings, artifact=artifact, state_root=tmp_path / "state")

    checkpoint = runner._paper_checkpoint()
    first_fingerprint = runner._dataset_fingerprint(checkpoint, search_round=1)
    second_fingerprint = runner._dataset_fingerprint(checkpoint, search_round=2)
    first = runner._experiment_queue(first_fingerprint, search_round=1)[0]
    second = runner._experiment_queue(second_fingerprint, search_round=2)[0]

    assert first_fingerprint != second_fingerprint
    assert first["experiment_key"] != second["experiment_key"]
    assert first["search_round"] == 1
    assert second["search_round"] == 2


def test_paper_checkpoint_ignores_unavailable_outcome_markers(tmp_path):
    artifact = tmp_path / "training_v3.joblib"
    joblib.dump({"format_version": 3, "records": [_record(index) for index in range(10)]}, artifact)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    usable_id = database.insert_outcome_label(
        {
            "timestamp": datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc),
            "symbol": "GLD",
            "decision": "LONG",
            "label": "long_good",
            "forward_return_1m": 0.001,
        }
    )
    database.insert_outcome_label(
        {
            "timestamp": datetime(2026, 7, 13, 15, 1, tzinfo=timezone.utc),
            "symbol": "GLD",
            "decision": "NO_TRADE",
            "label": None,
            "price_source": "unavailable_no_entry",
        }
    )
    runner = ContinualTrainingRunner(database, settings, artifact=artifact, state_root=tmp_path / "state")

    checkpoint = runner._paper_checkpoint()

    assert checkpoint == {"outcome_count": 1, "max_outcome_id": usable_id}


def test_continuous_historical_mode_runs_new_rounds_without_paper_labels(tmp_path, monkeypatch):
    artifact = tmp_path / "training_v3.joblib"
    joblib.dump({"format_version": 3, "records": [_record(index) for index in range(10)]}, artifact)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    runner = ContinualTrainingRunner(database, settings, artifact=artifact, state_root=tmp_path / "state")
    rounds = []

    def fake_cycle(**kwargs):
        search_round = kwargs["search_round"]
        rounds.append(search_round)
        return {"status": "completed", "search_round": search_round, "next_search_round": search_round + 1}

    monkeypatch.setattr(runner, "run_cycle", fake_cycle)
    monkeypatch.setattr(runner, "_wait", lambda _minutes: None)

    result = runner.run(continuous_historical=True, interval_minutes=1, max_cycles=3)

    assert result["status"] == "completed"
    assert result["cycles"] == 3
    assert rounds == [0, 1, 2]


def test_continuous_historical_mode_stops_after_patience(tmp_path, monkeypatch):
    artifact = tmp_path / "training_v3.joblib"
    joblib.dump({"format_version": 3, "records": [_record(index) for index in range(10)]}, artifact)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings=settings)
    database.init_db()
    runner = ContinualTrainingRunner(database, settings, artifact=artifact, state_root=tmp_path / "state")

    def fake_cycle(**kwargs):
        search_round = kwargs["search_round"]
        return {
            "status": "completed",
            "search_round": search_round,
            "next_search_round": search_round + 1,
            "no_improvement_rounds": search_round,
        }

    monkeypatch.setattr(runner, "run_cycle", fake_cycle)
    monkeypatch.setattr(runner, "_wait", lambda _minutes: None)

    result = runner.run(continuous_historical=True, no_improvement_patience=2)

    assert result["status"] == "converged"
    assert result["cycles"] == 3
    assert not runner.lock_path.exists()
