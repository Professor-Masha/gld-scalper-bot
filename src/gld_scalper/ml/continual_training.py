from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import market_session, utc_iso, utc_now
from .archive_dataset import load_archive_training_artifact
from .dataset_builder import build_training_records, label_quality
from .trainer import train_candidate_model


TERMINAL_STATUSES = {"completed", "skipped"}
HISTORICAL_EVALUATION_POLICY = "actual_candidate_walk_forward_v2"


class TrainingLock(AbstractContextManager):
    def __init__(self, path: Path, *, stale_hours: int = 24) -> None:
        self.path = path
        self.stale_after = timedelta(hours=max(1, stale_hours))
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clear_stale_lock()
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"Another offline training loop holds {self.path}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
            json.dump({"pid": os.getpid(), "started_at": utc_now().isoformat()}, file_handle)
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self.acquired = False
        return False

    def _clear_stale_lock(self) -> None:
        if not self.path.exists():
            return
        modified = datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
        if utc_now() - modified > self.stale_after:
            self.path.unlink(missing_ok=True)


class ContinualTrainingRunner:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        artifact: str | Path,
        horizons: list[int] | None = None,
        playbooks: list[str] | None = None,
        minimum_samples: int | None = None,
        paper_weight: int | None = None,
        state_root: str | Path | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.artifact = Path(artifact).resolve()
        if not self.artifact.exists():
            raise RuntimeError(f"Training artifact not found: {self.artifact}")
        self.horizons = sorted(set(horizons or settings.continual_training_horizons))
        self.playbooks = list(dict.fromkeys(playbooks or settings.continual_training_playbooks))
        self.minimum_samples = minimum_samples or settings.continual_training_min_playbook_samples
        self.paper_weight = max(1, paper_weight or settings.continual_training_paper_weight)
        self.root = Path(state_root) if state_root is not None else PROJECT_ROOT / "data" / settings.data_mode / "ml_training" / "continual"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / "training.lock"
        self.stop_path = self.root / "STOP_TRAINING"
        self._artifact_hash: str | None = None

    def run(
        self,
        *,
        watch: bool = False,
        continuous_historical: bool = False,
        interval_minutes: int | None = None,
        max_cycles: int = 0,
        retry_failed: bool = False,
        no_improvement_patience: int = 3,
        minimum_improvement: float = 0.001,
    ) -> dict[str, Any]:
        interval = max(
            1,
            interval_minutes
            or (1 if continuous_historical else self.settings.continual_training_interval_minutes),
        )
        watch = watch or continuous_historical
        cycles = 0
        summaries: list[dict[str, Any]] = []
        historical_round: int | None = None
        patience = max(1, int(no_improvement_patience))
        meaningful_improvement = max(0.0, float(minimum_improvement))
        with TrainingLock(self.lock_path, stale_hours=self.settings.continual_training_lock_stale_hours):
            while True:
                if self.stop_path.exists():
                    return {"status": "stopped", "reason": str(self.stop_path), "cycles": cycles, "summaries": summaries}
                state = self._load_state()
                if continuous_historical and historical_round is None:
                    historical_round = self._next_historical_round(state)
                checkpoint = self._paper_checkpoint()
                new_labels = checkpoint["outcome_count"] - int(state.get("paper_outcome_count", 0) or 0)
                first_cycle = not state.get("last_dataset_fingerprint")
                regular_session = market_session(utc_now(), extended_hours=False) == "regular"
                market_blocked = (
                    watch
                    and not continuous_historical
                    and self.settings.continual_training_only_outside_regular_hours
                    and regular_session
                )
                should_train = continuous_historical or first_cycle or new_labels >= self.settings.continual_training_min_new_labels or not watch
                if not market_blocked and should_train:
                    summary = self.run_cycle(
                        retry_failed=retry_failed,
                        search_round=historical_round or 0,
                        continuous_historical=continuous_historical,
                        minimum_improvement=meaningful_improvement,
                    )
                    summaries.append(summary)
                    cycles += 1
                    if continuous_historical and summary.get("status") == "completed":
                        historical_round = int(summary["next_search_round"])
                        if int(summary.get("no_improvement_rounds", 0)) >= patience:
                            print(
                                "historical search converged: "
                                f"no meaningful walk-forward improvement for {summary['no_improvement_rounds']} round(s); "
                                f"minimum_improvement={meaningful_improvement}",
                                flush=True,
                            )
                            return {
                                "status": "converged",
                                "reason": "no meaningful walk-forward improvement",
                                "cycles": cycles,
                                "patience_rounds": patience,
                                "minimum_improvement": meaningful_improvement,
                                "summaries": summaries,
                            }
                else:
                    summary = {
                        "status": "idle",
                        "new_labels": new_labels,
                        "required_new_labels": self.settings.continual_training_min_new_labels,
                        "market_hours_blocked": market_blocked,
                    }
                    self._write_state({**state, "last_idle_at": utc_now().isoformat(), "last_idle": summary})
                if not watch or (max_cycles > 0 and cycles >= max_cycles):
                    return {"status": "completed", "cycles": cycles, "summaries": summaries or [summary]}
                if continuous_historical:
                    print(
                        f"historical search sleeping {interval} minute(s); "
                        f"next_search_round={historical_round}",
                        flush=True,
                    )
                self._wait(interval)

    def run_cycle(
        self,
        *,
        retry_failed: bool = False,
        search_round: int = 0,
        continuous_historical: bool = False,
        minimum_improvement: float = 0.001,
    ) -> dict[str, Any]:
        archive_records = load_archive_training_artifact(self.artifact)
        paper_records = build_training_records(
            self.database,
            self.settings.continual_training_paper_lookback_days,
        )
        checkpoint = self._paper_checkpoint()
        dataset_fingerprint = self._dataset_fingerprint(checkpoint, search_round=search_round)
        queue = self._experiment_queue(dataset_fingerprint, search_round=search_round)
        print(
            f"training cycle fingerprint={dataset_fingerprint[:12]} archive_rows={len(archive_records)} "
            f"paper_rows={len(paper_records)} experiments={len(queue)} search_round={search_round}",
            flush=True,
        )
        counts = {"completed": 0, "skipped_existing": 0, "skipped_samples": 0, "failed": 0}
        results: list[dict[str, Any]] = []
        for experiment in queue:
            if self.stop_path.exists():
                break
            existing = self._existing_experiment(experiment["experiment_key"])
            if existing and (existing["status"] in TERMINAL_STATUSES or existing["status"] == "failed" and not retry_failed):
                counts["skipped_existing"] += 1
                print(f"skip completed experiment playbook={experiment['playbook']} horizon={experiment['horizon_minutes']}m", flush=True)
                continue
            records, paper_count = prepare_experiment_records(
                archive_records,
                paper_records,
                playbook=experiment["playbook"],
                horizon_minutes=experiment["horizon_minutes"],
                paper_weight=self.paper_weight,
            )
            labels_ok, label_reason = label_quality([record["label"] for record in records])
            if len(records) < self.minimum_samples or not labels_ok:
                reason = f"samples={len(records)} below {self.minimum_samples}" if len(records) < self.minimum_samples else label_reason
                self._save_experiment(experiment, status="skipped", sample_count=len(records), paper_count=paper_count, error=reason)
                counts["skipped_samples"] += 1
                print(f"skip insufficient experiment playbook={experiment['playbook']} horizon={experiment['horizon_minutes']}m {reason}", flush=True)
                continue
            previous_best = self._previous_best(experiment["playbook"], experiment["horizon_minutes"])
            context = {
                **experiment,
                "artifact": str(self.artifact),
                "paper_checkpoint": checkpoint,
                "previous_best": previous_best,
                "paper_weight": self.paper_weight,
                "shadow_only": True,
                "continuous_historical": continuous_historical,
            }
            self._save_experiment(experiment, status="running", sample_count=len(records), paper_count=paper_count)
            print(
                f"train experiment playbook={experiment['playbook']} horizon={experiment['horizon_minutes']}m "
                f"samples={len(records)} paper_samples={paper_count}",
                flush=True,
            )
            try:
                result = train_candidate_model(
                    self.database,
                    self.settings,
                    records=records,
                    allow_promotion=False,
                    experiment_context=context,
                )
                self._save_experiment(
                    experiment,
                    status="completed",
                    sample_count=len(records),
                    paper_count=paper_count,
                    result=result,
                )
                results.append(
                    {
                        "experiment_key": experiment["experiment_key"],
                        "playbook": experiment["playbook"],
                        "horizon_minutes": experiment["horizon_minutes"],
                        "candidate_version": result["model_version"],
                        "model_type": result.get("model_type"),
                        "feature_profile": result.get("feature_profile"),
                        "holdout_profit_factor": result.get("holdout_metrics", {}).get("profit_factor"),
                        "holdout_net_return": result.get("holdout_metrics", {}).get("net_return"),
                        "walk_forward_profit_factor": result.get("walk_forward_metrics", {}).get("profit_factor"),
                        "walk_forward_net_return": result.get("walk_forward_metrics", {}).get("net_return"),
                        "promoted": False,
                    }
                )
                counts["completed"] += 1
                print(
                    f"completed candidate={result['model_version']} model={result.get('model_type')} "
                    f"holdout_profit_factor={result.get('holdout_metrics', {}).get('profit_factor')} "
                    f"holdout_net_return={result.get('holdout_metrics', {}).get('net_return')} "
                    f"walk_forward_profit_factor={result.get('walk_forward_metrics', {}).get('profit_factor')} "
                    f"walk_forward_net_return={result.get('walk_forward_metrics', {}).get('net_return')}",
                    flush=True,
                )
            except Exception as exc:
                self._save_experiment(
                    experiment,
                    status="failed",
                    sample_count=len(records),
                    paper_count=paper_count,
                    error=str(exc),
                )
                counts["failed"] += 1
                print(f"failed experiment playbook={experiment['playbook']} horizon={experiment['horizon_minutes']}m error={exc}", flush=True)
        interrupted = self.stop_path.exists()
        next_search_round = search_round if interrupted else search_round + 1
        prior_state = self._load_state()
        same_policy = prior_state.get("evaluation_policy") == HISTORICAL_EVALUATION_POLICY
        prior_best_score = prior_state.get("best_historical_walk_forward_net_return") if same_policy else None
        prior_no_improvement = int(prior_state.get("no_improvement_rounds", 0) or 0) if same_policy else 0
        round_best = self._round_best(dataset_fingerprint)
        round_score = round_best.get("score") if round_best else None
        improvement = None if prior_best_score is None or round_score is None else float(round_score) - float(prior_best_score)
        improved = round_score is not None and (
            prior_best_score is None or float(round_score) > float(prior_best_score) + max(0.0, minimum_improvement)
        )
        if interrupted:
            best_score = prior_best_score
            no_improvement_rounds = prior_no_improvement
        elif improved:
            best_score = float(round_score)
            no_improvement_rounds = 0
        else:
            best_score = prior_best_score
            no_improvement_rounds = prior_no_improvement + 1
        state = {
            "updated_at": utc_now().isoformat(),
            "artifact": str(self.artifact),
            "last_dataset_fingerprint": dataset_fingerprint,
            "paper_outcome_count": checkpoint["outcome_count"],
            "paper_max_outcome_id": checkpoint["max_outcome_id"],
            "queue_size": len(queue),
            "training_mode": "continuous_historical" if continuous_historical else "label_driven",
            "search_round": search_round,
            "next_search_round": next_search_round,
            "evaluation_policy": HISTORICAL_EVALUATION_POLICY,
            "minimum_meaningful_improvement": max(0.0, minimum_improvement),
            "round_best": round_best,
            "round_improvement": improvement,
            "meaningful_improvement": improved,
            "best_historical_walk_forward_net_return": best_score,
            "no_improvement_rounds": no_improvement_rounds,
            "counts": counts,
            "best_completed": self._best_completed(),
        }
        self._write_state(state)
        return {
            "status": "stopped" if interrupted else "completed",
            "dataset_fingerprint": dataset_fingerprint,
            "search_round": search_round,
            "next_search_round": next_search_round,
            "round_best": round_best,
            "round_improvement": improvement,
            "meaningful_improvement": improved,
            "best_historical_walk_forward_net_return": best_score,
            "no_improvement_rounds": no_improvement_rounds,
            "counts": counts,
            "results": results,
        }

    def _experiment_queue(self, dataset_fingerprint: str, *, search_round: int = 0) -> list[dict[str, Any]]:
        queue = []
        for playbook in self.playbooks:
            for horizon in self.horizons:
                definition = {
                    "dataset_fingerprint": dataset_fingerprint,
                    "playbook": playbook,
                    "horizon_minutes": horizon,
                    "policy": "playbook_multi_horizon_v1",
                    "search_round": search_round,
                }
                key = hashlib.sha256(json.dumps(definition, sort_keys=True).encode("utf-8")).hexdigest()
                queue.append({**definition, "experiment_key": key})
        prior = {(row["playbook"], int(row["horizon_minutes"])): index for index, row in enumerate(self._ranked_prior_experiments())}
        return sorted(queue, key=lambda item: prior.get((item["playbook"], item["horizon_minutes"]), 1_000_000))

    def _dataset_fingerprint(self, checkpoint: dict[str, int], *, search_round: int = 0) -> str:
        definition = {
            "artifact_sha256": self._artifact_sha256(),
            "paper_checkpoint": checkpoint,
            "code_sha256": _code_fingerprint(),
            "paper_weight": self.paper_weight,
            "horizons": self.horizons,
            "playbooks": self.playbooks,
            "minimum_samples": self.minimum_samples,
            "search_round": search_round,
            "evaluation_policy": HISTORICAL_EVALUATION_POLICY,
        }
        return hashlib.sha256(json.dumps(definition, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _next_historical_round(state: dict[str, Any]) -> int:
        if "next_search_round" in state:
            return max(0, int(state.get("next_search_round") or 0))
        # Existing installations already completed the original round-zero matrix.
        return 1 if state.get("last_dataset_fingerprint") else 0

    def _artifact_sha256(self) -> str:
        if self._artifact_hash is None:
            self._artifact_hash = _file_sha256(self.artifact)
        return self._artifact_hash

    def _paper_checkpoint(self) -> dict[str, int]:
        row = self.database.conn.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id
            FROM outcome_labels
            WHERE label IN ('long_good', 'short_good', 'no_trade')
              AND (
                  forward_return_1m IS NOT NULL OR forward_return_3m IS NOT NULL
                  OR forward_return_5m IS NOT NULL OR forward_return_15m IS NOT NULL
              )
            """
        ).fetchone()
        return {"outcome_count": int(row["count"]), "max_outcome_id": int(row["max_id"])}

    def _existing_experiment(self, experiment_key: str) -> dict[str, Any] | None:
        row = self.database.conn.execute(
            "SELECT * FROM ml_training_experiments WHERE experiment_key = ?",
            (experiment_key,),
        ).fetchone()
        return dict(row) if row else None

    def _save_experiment(
        self,
        experiment: dict[str, Any],
        *,
        status: str,
        sample_count: int,
        paper_count: int,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_iso(utc_now())
        metrics = (result or {}).get("promotion_metrics")
        with self.database.conn:
            self.database.conn.execute(
                """
                INSERT INTO ml_training_experiments(
                    experiment_key, dataset_fingerprint, artifact_path, playbook, horizon_minutes,
                    status, sample_count, paper_sample_count, candidate_version, candidate_path,
                    metrics_json, manifest_path, started_at, completed_at, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_key) DO UPDATE SET
                    status=excluded.status,
                    sample_count=excluded.sample_count,
                    paper_sample_count=excluded.paper_sample_count,
                    candidate_version=COALESCE(excluded.candidate_version, ml_training_experiments.candidate_version),
                    candidate_path=COALESCE(excluded.candidate_path, ml_training_experiments.candidate_path),
                    metrics_json=COALESCE(excluded.metrics_json, ml_training_experiments.metrics_json),
                    manifest_path=COALESCE(excluded.manifest_path, ml_training_experiments.manifest_path),
                    started_at=COALESCE(ml_training_experiments.started_at, excluded.started_at),
                    completed_at=excluded.completed_at,
                    error_message=excluded.error_message
                """,
                (
                    experiment["experiment_key"],
                    experiment["dataset_fingerprint"],
                    str(self.artifact),
                    experiment["playbook"],
                    experiment["horizon_minutes"],
                    status,
                    sample_count,
                    paper_count,
                    (result or {}).get("model_version"),
                    (result or {}).get("path"),
                    json.dumps(metrics, sort_keys=True, default=str) if metrics is not None else None,
                    (result or {}).get("manifest_path"),
                    now if status == "running" else None,
                    now if status in {"completed", "failed", "skipped"} else None,
                    error,
                    now,
                ),
            )

    def _previous_best(self, playbook: str, horizon: int) -> dict[str, Any] | None:
        candidates = []
        rows = self.database.conn.execute(
            """
            SELECT candidate_version, metrics_json FROM ml_training_experiments
            WHERE playbook = ? AND horizon_minutes = ? AND status = 'completed'
            """,
            (playbook, horizon),
        ).fetchall()
        for row in rows:
            metrics = json.loads(row["metrics_json"] or "{}")
            candidates.append({"candidate_version": row["candidate_version"], "metrics": metrics})
        return max(candidates, key=lambda item: float(item["metrics"].get("net_return", -1e9) or -1e9), default=None)

    def _ranked_prior_experiments(self) -> list[dict[str, Any]]:
        rows = self.database.conn.execute(
            "SELECT playbook, horizon_minutes, metrics_json FROM ml_training_experiments WHERE status = 'completed'"
        ).fetchall()
        items = []
        for row in rows:
            metrics = json.loads(row["metrics_json"] or "{}")
            items.append({"playbook": row["playbook"], "horizon_minutes": row["horizon_minutes"], "score": metrics.get("net_return", -1e9)})
        return sorted(items, key=lambda item: float(item["score"] or -1e9), reverse=True)

    def _best_completed(self) -> dict[str, Any] | None:
        rows = self._ranked_prior_experiments()
        return rows[0] if rows else None

    def _round_best(self, dataset_fingerprint: str) -> dict[str, Any] | None:
        rows = self.database.conn.execute(
            """
            SELECT candidate_version, playbook, horizon_minutes, metrics_json
            FROM ml_training_experiments
            WHERE dataset_fingerprint = ? AND status = 'completed'
            """,
            (dataset_fingerprint,),
        ).fetchall()
        candidates = []
        for row in rows:
            metrics = json.loads(row["metrics_json"] or "{}")
            score = metrics.get("net_return")
            if score is not None:
                candidates.append(
                    {
                        "candidate_version": row["candidate_version"],
                        "playbook": row["playbook"],
                        "horizon_minutes": int(row["horizon_minutes"]),
                        "score": float(score),
                    }
                )
        return max(candidates, key=lambda item: item["score"], default=None)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(self.state_path)

    def _wait(self, interval_minutes: int) -> None:
        deadline = time.monotonic() + interval_minutes * 60
        while time.monotonic() < deadline:
            if self.stop_path.exists():
                return
            time.sleep(min(30.0, max(0.1, deadline - time.monotonic())))


def prepare_experiment_records(
    archive_records: list[dict[str, Any]],
    paper_records: list[dict[str, Any]],
    *,
    playbook: str,
    horizon_minutes: int,
    paper_weight: int,
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    paper_selected: list[dict[str, Any]] = []
    for record in archive_records:
        converted = _record_for_horizon(record, horizon_minutes)
        if converted is not None and _playbook_matches(converted, playbook):
            selected.append(converted)
    for record in paper_records:
        converted = _record_for_horizon(record, horizon_minutes)
        if converted is not None and _playbook_matches(converted, playbook):
            paper_selected.append(converted)
    for _ in range(max(1, paper_weight)):
        selected.extend({**record, "features": dict(record["features"]), "outcome": dict(record["outcome"])} for record in paper_selected)
    return sorted(selected, key=lambda item: item["timestamp"]), len(paper_selected)


def _record_for_horizon(record: dict[str, Any], horizon: int) -> dict[str, Any] | None:
    outcomes = record.get("outcomes_by_horizon") or {}
    outcome = outcomes.get(horizon) or outcomes.get(str(horizon))
    if outcome is None and int(record.get("horizon_minutes") or 0) == horizon:
        outcome = record.get("outcome")
    if not outcome:
        return None
    label = outcome.get("label")
    if label not in {"long_good", "short_good", "no_trade"}:
        net_long = outcome.get("net_return_long")
        net_short = outcome.get("net_return_short")
        if net_long is None or net_short is None:
            return None
        if float(net_long) > 0.0 and float(net_long) > float(net_short):
            label = "long_good"
        elif float(net_short) > 0.0 and float(net_short) > float(net_long):
            label = "short_good"
        else:
            label = "no_trade"
    return {
        **record,
        "label": str(label),
        "outcome": {**dict(outcome), "label": label},
        "horizon_minutes": horizon,
    }


def _playbook_matches(record: dict[str, Any], requested: str) -> bool:
    if requested == "all":
        return True
    if requested == "fast_microstructure":
        return str(record.get("strategy_path") or "minute") == "fast"
    if requested == "minute_setups":
        return str(record.get("strategy_path") or "minute") == "minute"
    return str(record.get("playbook") or "all") == requested


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_fingerprint() -> str:
    digest = hashlib.sha256()
    for relative in (
        "src/gld_scalper/ml/archive_dataset.py",
        "src/gld_scalper/ml/dataset_builder.py",
        "src/gld_scalper/ml/evaluator.py",
        "src/gld_scalper/ml/trainer.py",
        "src/gld_scalper/ml/walk_forward.py",
    ):
        path = PROJECT_ROOT / relative
        digest.update(path.read_bytes())
    return digest.hexdigest()
