from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import ensure_utc, market_session, utc_iso, utc_now
from .continual_training import TrainingLock
from .transformer_dataset import (
    LoadedSequenceArtifact,
    build_transformer_sequence_artifact,
    load_transformer_sequence_artifact,
    transformer_registry_scope,
)
from .transformer_trainer import TransformerTrainingOptions, train_transformer_candidate


SEARCH_CONFIGURATIONS = (
    {"d_model": 32, "layers": 2, "dropout": 0.10, "learning_rate": 3e-4, "seed": 73, "sequence_fraction": 1.0, "confidence": 0.58, "margin": 0.08},
    {"d_model": 48, "layers": 2, "dropout": 0.10, "learning_rate": 3e-4, "seed": 97, "sequence_fraction": 1.0, "confidence": 0.60, "margin": 0.10},
    {"d_model": 32, "layers": 3, "dropout": 0.15, "learning_rate": 2e-4, "seed": 131, "sequence_fraction": 0.75, "confidence": 0.62, "margin": 0.10},
    {"d_model": 48, "layers": 3, "dropout": 0.15, "learning_rate": 2e-4, "seed": 173, "sequence_fraction": 0.75, "confidence": 0.64, "margin": 0.12},
    {"d_model": 64, "layers": 2, "dropout": 0.20, "learning_rate": 1e-4, "seed": 211, "sequence_fraction": 0.50, "confidence": 0.60, "margin": 0.12},
    {"d_model": 32, "layers": 2, "dropout": 0.05, "learning_rate": 1e-4, "seed": 257, "sequence_fraction": 0.50, "confidence": 0.66, "margin": 0.14},
)


class TransformerContinualTrainingRunner:
    """Resumable, bounded offline search for independent Transformer scopes."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        historical_artifacts: Mapping[str, str | Path],
        state_root: str | Path | None = None,
        epochs: int = 8,
        walk_forward_epochs: int = 2,
        batch_size: int = 32,
        maximum_paper_samples: int = 20_000,
    ) -> None:
        self.database = database
        self.settings = settings
        self.historical_artifacts = {
            _normalize_scope(scope): Path(path).resolve()
            for scope, path in historical_artifacts.items()
        }
        if not self.historical_artifacts:
            raise ValueError("at least one scope=artifact mapping is required")
        for scope, path in self.historical_artifacts.items():
            if not (path / "manifest.json").is_file():
                raise FileNotFoundError(f"{scope} Transformer artifact not found: {path}")
        self.root = Path(state_root) if state_root else PROJECT_ROOT / "data" / settings.data_mode / "ml_training" / "transformer" / "continual"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.stop_path = self.root / "STOP_TRANSFORMER_TRAINING"
        self.lock_path = self.root / "training.lock"
        self.epochs = max(1, epochs)
        self.walk_forward_epochs = max(1, walk_forward_epochs)
        self.batch_size = max(1, batch_size)
        self.maximum_paper_samples = max(300, maximum_paper_samples)

    def run(
        self,
        *,
        watch: bool = False,
        interval_minutes: int | None = None,
        maximum_cycles: int = 0,
        no_improvement_patience: int | None = None,
        minimum_improvement: float | None = None,
    ) -> dict[str, Any]:
        interval = max(1, interval_minutes or self.settings.transformer_training_interval_minutes)
        patience = max(1, no_improvement_patience or self.settings.transformer_training_patience_rounds)
        threshold = max(
            0.0,
            self.settings.transformer_training_minimum_improvement
            if minimum_improvement is None
            else minimum_improvement,
        )
        summaries: list[dict[str, Any]] = []
        cycles = 0
        with TrainingLock(self.lock_path, stale_hours=self.settings.continual_training_lock_stale_hours):
            while True:
                if self.stop_path.exists():
                    return {"status": "stopped", "cycles": cycles, "stop_file": str(self.stop_path), "summaries": summaries}
                if (
                    self.settings.continual_training_only_outside_regular_hours
                    and market_session(utc_now(), extended_hours=False) == "regular"
                ):
                    summary = {"status": "waiting_market_close", "timestamp": utc_now().isoformat()}
                else:
                    summary = self.run_cycle(minimum_improvement=threshold, patience=patience)
                    if summary["status"] not in {"idle", "converged_waiting_for_new_labels"}:
                        cycles += 1
                summaries.append(summary)
                if not watch:
                    return {"status": summary["status"], "cycles": cycles, "summaries": summaries}
                if maximum_cycles > 0 and cycles >= maximum_cycles:
                    return {"status": "maximum_cycles", "cycles": cycles, "summaries": summaries}
                self._wait(interval)

    def run_cycle(self, *, minimum_improvement: float, patience: int) -> dict[str, Any]:
        state = self._load_state()
        scope_results: list[dict[str, Any]] = []
        dataset_changed = False
        for scope, historical_path in self.historical_artifacts.items():
            historical = load_transformer_sequence_artifact(historical_path)
            paper_path = self._refresh_paper_artifact(scope, historical)
            combined_path = self._combined_artifact(scope, historical_path, paper_path)
            combined = load_transformer_sequence_artifact(combined_path)
            fingerprint = str(combined.manifest["fingerprint"])
            prior_fingerprint = str((state.get("scopes", {}).get(scope) or {}).get("dataset_fingerprint") or "")
            if fingerprint != prior_fingerprint:
                dataset_changed = True
                changed_state = state.setdefault("scopes", {}).setdefault(scope, {})
                changed_state["no_improvement_rounds"] = 0
                # Restart the bounded grid so configuration zero can warm-start
                # the last compatible configuration-zero checkpoint with the
                # newly appended paper labels.
                changed_state["search_round"] = 0
            elif int((state.get("scopes", {}).get(scope) or {}).get("no_improvement_rounds", 0)) >= patience:
                scope_results.append(
                    {
                        "scope": scope,
                        "status": "converged_waiting_for_new_labels",
                        "dataset_fingerprint": fingerprint,
                    }
                )
                continue
            result = self._run_scope_experiment(
                scope,
                combined_path,
                combined,
                state,
                minimum_improvement=minimum_improvement,
            )
            scope_results.append(result)
        no_improvement = [
            int((state.get("scopes", {}).get(scope) or {}).get("no_improvement_rounds", 0))
            for scope in self.historical_artifacts
        ]
        converged = bool(no_improvement) and all(value >= patience for value in no_improvement)
        state["updated_at"] = utc_now().isoformat()
        state["converged"] = converged
        self._write_state(state)
        if converged and not dataset_changed:
            print(
                f"Transformer search converged; no meaningful improvement for {patience} rounds. "
                "Watching for a new paper-data fingerprint.",
                flush=True,
            )
            return {"status": "converged_waiting_for_new_labels", "scopes": scope_results}
        return {"status": "completed", "dataset_changed": dataset_changed, "scopes": scope_results}

    def _run_scope_experiment(
        self,
        scope: str,
        artifact_path: Path,
        artifact: LoadedSequenceArtifact,
        state: dict[str, Any],
        *,
        minimum_improvement: float,
    ) -> dict[str, Any]:
        scope_state = state.setdefault("scopes", {}).setdefault(scope, {})
        round_index = int(scope_state.get("search_round", 0))
        configuration = dict(SEARCH_CONFIGURATIONS[round_index % len(SEARCH_CONFIGURATIONS)])
        sequence_length = max(8, int(int(artifact.manifest["sequence_length"]) * configuration["sequence_fraction"]))
        window_seconds = max(8, int(int(artifact.manifest["window_seconds"]) * configuration["sequence_fraction"]))
        configuration.update({"sequence_length": sequence_length, "window_seconds": window_seconds})
        experiment_key = _fingerprint(
            {
                "scope": scope,
                "dataset": artifact.manifest["fingerprint"],
                "configuration": configuration,
                "policy": "transformer_continual_v1",
            }
        )
        existing = self.database.conn.execute(
            "SELECT * FROM transformer_training_experiments WHERE experiment_key = ?",
            (experiment_key,),
        ).fetchone()
        if existing and existing["status"] == "completed":
            scope_state["search_round"] = round_index + 1
            scope_state["dataset_fingerprint"] = artifact.manifest["fingerprint"]
            print(f"skip completed Transformer experiment scope={scope} key={experiment_key[:12]}", flush=True)
            return {"scope": scope, "status": "skipped_completed", "experiment_key": experiment_key}

        parent = self._compatible_parent(scope, artifact, configuration)
        self._save_experiment(
            experiment_key,
            scope,
            artifact,
            configuration,
            status="running",
            parent=parent,
        )
        print(
            f"Transformer train scope={scope} round={round_index} samples={len(artifact.labels)} "
            f"fingerprint={str(artifact.manifest['fingerprint'])[:12]} warm_start={bool(parent)}",
            flush=True,
        )
        try:
            result = train_transformer_candidate(
                self.database,
                self.settings,
                artifact_path=artifact_path,
                options=TransformerTrainingOptions(
                    d_model=int(configuration["d_model"]),
                    num_layers=int(configuration["layers"]),
                    dim_feedforward=max(64, int(configuration["d_model"]) * 3),
                    dropout=float(configuration["dropout"]),
                    batch_size=self.batch_size,
                    epochs=self.epochs,
                    learning_rate=float(configuration["learning_rate"]),
                    patience=min(4, self.epochs),
                    walk_forward_folds=3,
                    walk_forward_epochs=self.walk_forward_epochs,
                    minimum_confidence=float(configuration["confidence"]),
                    minimum_margin=float(configuration["margin"]),
                    random_seed=int(configuration["seed"]),
                    sequence_length_override=sequence_length,
                    window_seconds_override=window_seconds,
                    warm_start_manifest_path=str(parent["path"]) if parent else None,
                    parent_model_version=str(parent["model_version"]) if parent else None,
                ),
            )
            score = _candidate_score(result)
            previous_best = float(scope_state.get("best_score", float("-inf")))
            improved = score > previous_best + minimum_improvement
            if improved:
                scope_state.update(
                    {
                        "best_score": score,
                        "best_model_version": result["model_version"],
                        "best_manifest_path": result["manifest_path"],
                        "no_improvement_rounds": 0,
                    }
                )
            else:
                scope_state["no_improvement_rounds"] = int(scope_state.get("no_improvement_rounds", 0)) + 1
            scope_state["search_round"] = round_index + 1
            scope_state["dataset_fingerprint"] = artifact.manifest["fingerprint"]
            scope_state["last_completed_at"] = utc_now().isoformat()
            self._save_experiment(
                experiment_key,
                scope,
                artifact,
                configuration,
                status="completed",
                parent=parent,
                result=result,
                score=score,
                improved=improved,
            )
            print(
                f"Transformer completed scope={scope} candidate={result['model_version']} "
                f"score={score:.6f} improved={improved} no_improvement_rounds={scope_state['no_improvement_rounds']}",
                flush=True,
            )
            return {"scope": scope, "status": "completed", "score": score, "improved": improved, **result}
        except Exception as exc:
            self._save_experiment(
                experiment_key,
                scope,
                artifact,
                configuration,
                status="failed",
                parent=parent,
                error=str(exc),
            )
            scope_state["search_round"] = round_index + 1
            scope_state["no_improvement_rounds"] = int(scope_state.get("no_improvement_rounds", 0)) + 1
            return {"scope": scope, "status": "failed", "error": str(exc)}

    def _refresh_paper_artifact(self, scope: str, historical: LoadedSequenceArtifact) -> Path | None:
        boundaries = _paper_boundaries(self.database, scope)
        if boundaries is None:
            return None
        output = self.root / f"paper_{scope}.seq"
        try:
            result = build_transformer_sequence_artifact(
                self.database,
                self.settings,
                scope=scope,
                start=boundaries[0] - timedelta(minutes=180),
                end=boundaries[1] + timedelta(minutes=16),
                output=output,
                source="decisions" if scope != "news_event" else "auto",
                sequence_length=int(historical.manifest["sequence_length"]),
                window_seconds=int(historical.manifest["window_seconds"]),
                max_samples=self.maximum_paper_samples,
                max_features=int(historical.manifest["feature_count"]),
                overwrite=True,
            )
            print(f"paper Transformer artifact scope={scope} samples={result.samples} fingerprint={result.fingerprint[:12]}", flush=True)
            return output
        except RuntimeError as exc:
            print(f"paper Transformer artifact unavailable scope={scope}: {exc}", flush=True)
            return None

    def _combined_artifact(self, scope: str, historical_path: Path, paper_path: Path | None) -> Path:
        if paper_path is None:
            return historical_path
        output = self.root / f"combined_{scope}.seq"
        return combine_transformer_artifacts(historical_path, paper_path, output)

    def _compatible_parent(
        self,
        scope: str,
        artifact: LoadedSequenceArtifact,
        configuration: dict[str, Any],
    ) -> dict[str, Any] | None:
        rows = self.database.conn.execute(
            """
            SELECT * FROM model_versions
            WHERE model_scope = ? AND model_type LIKE 'causal_transformer_%'
              AND status IN ('candidate', 'champion', 'archived')
            ORDER BY CASE status WHEN 'champion' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
                     datetime(created_at) DESC, id DESC
            """,
            (transformer_registry_scope(scope),),
        ).fetchall()
        for row in rows:
            item = dict(row)
            try:
                manifest = json.loads(Path(str(item["path"])).read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            config = manifest.get("config") or {}
            if (
                manifest.get("feature_columns") == artifact.manifest.get("feature_columns")
                and int(config.get("d_model", -1)) == int(configuration["d_model"])
                and int(config.get("num_layers", -1)) == int(configuration["layers"])
                and int(config.get("sequence_length", -1)) == int(configuration["sequence_length"])
            ):
                return item
        return None

    def _save_experiment(
        self,
        key: str,
        scope: str,
        artifact: LoadedSequenceArtifact,
        configuration: dict[str, Any],
        *,
        status: str,
        parent: dict[str, Any] | None,
        result: dict[str, Any] | None = None,
        score: float | None = None,
        improved: bool = False,
        error: str | None = None,
    ) -> None:
        now = utc_iso(utc_now())
        historical_fp = str(artifact.manifest.get("historical_fingerprint") or artifact.manifest["fingerprint"])
        paper_fp = artifact.manifest.get("paper_fingerprint")
        with self.database.conn:
            self.database.conn.execute(
                """
                INSERT INTO transformer_training_experiments(
                    experiment_key, scope, historical_fingerprint, paper_fingerprint,
                    combined_fingerprint, configuration_json, status, candidate_version,
                    parent_version, score, meaningful_improvement, metrics_json,
                    error_message, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_key) DO UPDATE SET
                    status=excluded.status,
                    candidate_version=COALESCE(excluded.candidate_version, candidate_version),
                    parent_version=excluded.parent_version,
                    score=excluded.score,
                    meaningful_improvement=excluded.meaningful_improvement,
                    metrics_json=excluded.metrics_json,
                    error_message=excluded.error_message,
                    started_at=COALESCE(started_at, excluded.started_at),
                    completed_at=excluded.completed_at
                """,
                (
                    key,
                    scope,
                    historical_fp,
                    paper_fp,
                    artifact.manifest["fingerprint"],
                    json.dumps(configuration, sort_keys=True),
                    status,
                    result.get("model_version") if result else None,
                    parent.get("model_version") if parent else None,
                    score,
                    1 if improved else 0,
                    json.dumps(result or {}, sort_keys=True, default=str),
                    error,
                    now,
                    now if status in {"completed", "failed"} else None,
                ),
            )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"format_version": 1, "scopes": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"format_version": 1, "scopes": {}}

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def _wait(self, minutes: int) -> None:
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline:
            if self.stop_path.exists():
                return
            time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))


def combine_transformer_artifacts(
    historical_path: str | Path,
    paper_path: str | Path,
    output: str | Path,
) -> Path:
    historical = load_transformer_sequence_artifact(historical_path)
    paper = load_transformer_sequence_artifact(paper_path)
    if historical.manifest["scope"] != paper.manifest["scope"]:
        raise ValueError("historical and paper Transformer scopes differ")
    if historical.manifest["classes"] != paper.manifest["classes"]:
        raise ValueError("historical and paper Transformer classes differ")
    if float(np.min(paper.timestamps)) <= float(np.max(historical.timestamps)):
        raise ValueError(
            "paper Transformer timeline overlaps the historical archive; use a historical artifact ending before paper collection"
        )
    historical_columns = list(historical.manifest["feature_columns"])
    paper_columns = set(paper.manifest["feature_columns"])
    columns = [column for column in historical_columns if column in paper_columns]
    if len(columns) < 8:
        raise RuntimeError("historical and paper artifacts share fewer than eight features")
    h_indices = [historical_columns.index(column) for column in columns]
    p_columns = list(paper.manifest["feature_columns"])
    p_indices = [p_columns.index(column) for column in columns]
    root = Path(output)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    historical_rows = len(historical.timestamps)
    arrays = {
        "features": np.concatenate((np.asarray(historical.features)[:, h_indices], np.asarray(paper.features)[:, p_indices]), axis=0),
        "timestamps": np.concatenate((np.asarray(historical.timestamps), np.asarray(paper.timestamps))),
        "session_ids": np.concatenate((np.asarray(historical.session_ids), np.asarray(paper.session_ids) + int(np.max(historical.session_ids)) + 1)),
        "sample_end_indices": np.concatenate((np.asarray(historical.sample_end_indices), np.asarray(paper.sample_end_indices) + historical_rows)),
        "labels": np.concatenate((np.asarray(historical.labels), np.asarray(paper.labels))),
        "returns": np.concatenate((np.asarray(historical.returns), np.asarray(paper.returns)), axis=0),
        "costs": np.concatenate((np.asarray(historical.costs), np.asarray(paper.costs))),
        "regime_ids": np.concatenate((np.asarray(historical.regime_ids), np.asarray(paper.regime_ids))),
        "baseline_probabilities": np.concatenate((np.asarray(historical.baseline_probabilities), np.asarray(paper.baseline_probabilities)), axis=0),
    }
    for name, value in arrays.items():
        np.save(root / f"{name}.npy", value, allow_pickle=False)
    fingerprint = _fingerprint(
        {
            "historical": historical.manifest["fingerprint"],
            "paper": paper.manifest["fingerprint"],
            "columns": columns,
            "samples": len(arrays["labels"]),
        }
    )
    manifest = {
        **historical.manifest,
        "path": str(root.resolve()),
        "source": "historical_replay_plus_recent_paper",
        "feature_columns": columns,
        "feature_count": len(columns),
        "timeline_rows": len(arrays["timestamps"]),
        "samples": len(arrays["labels"]),
        "start": historical.manifest["start"],
        "end": paper.manifest["end"],
        "historical_fingerprint": historical.manifest["fingerprint"],
        "paper_fingerprint": paper.manifest["fingerprint"],
        "fingerprint": fingerprint,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return root


def parse_scope_artifacts(values: list[str]) -> dict[str, Path]:
    mappings: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected scope=path, received: {value}")
        scope, raw_path = value.split("=", 1)
        mappings[_normalize_scope(scope)] = Path(raw_path.strip()).resolve()
    return mappings


def _paper_boundaries(database: Database, scope: str):
    if scope in {"minute", "news_event", "fast_microstructure"}:
        source = "fast_scalp" if scope == "fast_microstructure" else "signal"
        row = database.conn.execute(
            """
            SELECT MIN(timestamp) AS first_timestamp, MAX(timestamp) AS last_timestamp, COUNT(*) AS count
            FROM outcome_labels WHERE decision_source = ? AND label_1m IS NOT NULL
            """,
            (source,),
        ).fetchone()
    else:
        row = database.conn.execute(
            """
            SELECT MIN(entry_time) AS first_timestamp, MAX(exit_time) AS last_timestamp, COUNT(*) AS count
            FROM trade_outcomes WHERE exit_time IS NOT NULL AND net_pnl_after_costs IS NOT NULL
            """
        ).fetchone()
    if row is None or int(row["count"] or 0) < 30 or not row["first_timestamp"] or not row["last_timestamp"]:
        return None
    return ensure_utc(row["first_timestamp"]), ensure_utc(row["last_timestamp"])


def _candidate_score(result: dict[str, Any]) -> float:
    walk = (result.get("walk_forward") or {}).get("aggregate") or {}
    holdout = result.get("holdout") or {}
    return (
        float(walk.get("net_return") or 0.0)
        - float(walk.get("max_drawdown") or 0.0)
        + min(float(walk.get("profit_factor") or 0.0), 5.0) * 0.002
        + float(walk.get("balanced_accuracy") or 0.0) * 0.002
        + float(holdout.get("net_return") or 0.0) * 0.25
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _normalize_scope(scope: str) -> str:
    value = str(scope).strip().lower().replace("-", "_")
    aliases = {"fast": "fast_microstructure", "news": "news_event"}
    value = aliases.get(value, value)
    if value not in {"fast_microstructure", "minute", "news_event", "exit"}:
        raise ValueError(f"unsupported Transformer scope: {scope}")
    return value
