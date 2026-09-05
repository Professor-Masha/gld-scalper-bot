from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Any

from ..config import Settings
from ..database import Database
from ..utils.time_utils import utc_iso, utc_now


class ModelRegistry:
    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings or database.settings

    def register_candidate(
        self,
        *,
        model_version: str,
        model_type: str,
        path: str,
        feature_columns: list[str],
        metrics: dict[str, Any],
        training_start: datetime | None = None,
        training_end: datetime | None = None,
        model_scope: str = "entry:all",
        feature_profile: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        thresholds: dict[str, Any] | None = None,
        artifact_fingerprint: str | None = None,
        training_data_start: datetime | None = None,
        training_data_end: datetime | None = None,
        parent_champion_version: str | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self.database.conn:
            self.database.conn.execute(
                """
                INSERT INTO model_versions(model_version, model_type, model_scope, path, created_at, training_start, training_end,
                    feature_columns_json, feature_profile, model_parameters_json, thresholds_json,
                    artifact_fingerprint, training_data_start, training_data_end, parent_champion_version,
                    metrics_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate')
                ON CONFLICT(model_version) DO UPDATE SET
                    model_type=excluded.model_type,
                    model_scope=excluded.model_scope,
                    path=excluded.path,
                    feature_columns_json=excluded.feature_columns_json,
                    feature_profile=excluded.feature_profile,
                    model_parameters_json=excluded.model_parameters_json,
                    thresholds_json=excluded.thresholds_json,
                    artifact_fingerprint=excluded.artifact_fingerprint,
                    training_data_start=excluded.training_data_start,
                    training_data_end=excluded.training_data_end,
                    parent_champion_version=excluded.parent_champion_version,
                    metrics_json=excluded.metrics_json,
                    status='candidate',
                    rejection_reason=NULL
                """,
                (
                    model_version,
                    model_type,
                    model_scope,
                    path,
                    utc_iso(created_at),
                    utc_iso(training_start) if training_start else None,
                    utc_iso(training_end) if training_end else None,
                    json.dumps(feature_columns),
                    feature_profile,
                    json.dumps(model_parameters or {}, sort_keys=True),
                    json.dumps(thresholds or {}, sort_keys=True),
                    artifact_fingerprint,
                    utc_iso(training_data_start) if training_data_start else None,
                    utc_iso(training_data_end) if training_data_end else None,
                    parent_champion_version,
                    json.dumps(metrics, sort_keys=True),
                ),
            )
        return self.get_model(model_version) or {}

    def get_champion(self, model_scope: str | None = None) -> dict[str, Any] | None:
        if model_scope is None:
            row = self.database.conn.execute(
                "SELECT * FROM model_versions WHERE status = 'champion' ORDER BY promoted_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.database.conn.execute(
                "SELECT * FROM model_versions WHERE status = 'champion' AND model_scope = ? ORDER BY promoted_at DESC LIMIT 1",
                (model_scope,),
            ).fetchone()
        return _row(row)

    def get_model(self, model_version: str) -> dict[str, Any] | None:
        row = self.database.conn.execute(
            "SELECT * FROM model_versions WHERE model_version = ?",
            (model_version,),
        ).fetchone()
        return _row(row)

    def get_paper_shadow_candidates(self, limit: int = 25, *, model_scope: str | None = None) -> list[dict[str, Any]]:
        """Return validated, unpromoted candidates ranked for paper-only advice.

        A shadow candidate is deliberately not a champion. It may advise paper
        learning only after completing purged walk-forward validation, and its
        registry status is never changed by this method.
        """
        sql = "SELECT * FROM model_versions WHERE status = 'candidate'"
        params: tuple[Any, ...] = ()
        if model_scope is not None:
            sql += " AND model_scope = ?"
            params = (model_scope,)
        rows = self.database.conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()
        eligible: list[dict[str, Any]] = []
        for raw in rows:
            candidate = _row(raw)
            if candidate is None:
                continue
            try:
                metrics = json.loads(candidate.get("metrics_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            path = Path(str(candidate.get("path") or ""))
            if not path.is_absolute():
                path = Path.cwd() / path
            if not path.is_file():
                continue
            if _metric(metrics, "walk_forward_completed", 0.0) < 1.0:
                continue
            if _metric(metrics, "walk_forward_fold_count", 0.0) < self.settings.promotion_min_fold_count:
                continue
            if _metric(metrics, "trade_label_count", 0.0) < self.settings.promotion_min_trade_count:
                continue
            if _metric(metrics, "inference_latency_ms", float("inf")) > self.settings.ml_max_inference_latency_ms:
                continue
            candidate["metrics"] = metrics
            candidate["resolved_path"] = str(path)
            eligible.append(candidate)

        eligible.sort(key=_paper_shadow_rank, reverse=True)
        return eligible[: max(1, limit)]

    def promote_if_better(
        self,
        candidate_version: str,
        candidate_metrics: dict[str, Any],
        *,
        model_scope: str | None = None,
    ) -> tuple[bool, str]:
        candidate_row = self.get_model(candidate_version) or {}
        model_scope = model_scope or str(candidate_row.get("model_scope") or "entry:all")
        floors_ok, floor_reason = self._passes_absolute_promotion_rules(candidate_metrics)
        champion = self.get_champion(model_scope)
        champion_metrics = json.loads(champion.get("metrics_json") or "{}") if champion is not None else {}
        if not floors_ok:
            self.reject(candidate_version, floor_reason)
            self.database.insert_model_promotion_audit(
                {
                    "model_version": candidate_version,
                    "candidate_metrics": candidate_metrics,
                    "benchmark_metrics": champion_metrics,
                    "promoted": False,
                    "promotion_reason": floor_reason,
                    "minimum_rules": self._minimum_rules(),
                    "walk_forward": {"model_scope": model_scope},
                }
            )
            return False, floor_reason
        if champion is not None:
            better, reason = candidate_beats_champion(candidate_metrics, champion_metrics)
            if not better:
                self.reject(candidate_version, reason)
                self.database.insert_model_promotion_audit(
                    {
                        "model_version": candidate_version,
                        "candidate_metrics": candidate_metrics,
                        "benchmark_metrics": champion_metrics,
                        "promoted": False,
                        "promotion_reason": reason,
                        "minimum_rules": self._minimum_rules(),
                        "walk_forward": {"model_scope": model_scope},
                    }
                )
                return False, reason
        with self.database.conn:
            self.database.conn.execute(
                "UPDATE model_versions SET status = 'archived' WHERE status = 'champion' AND model_scope = ?",
                (model_scope,),
            )
            self.database.conn.execute(
                "UPDATE model_versions SET status = 'champion', promoted_at = ? WHERE model_version = ?",
                (utc_iso(utc_now()), candidate_version),
            )
            self._record_history(
                model_scope=model_scope,
                model_version=candidate_version,
                action="promoted",
                previous_champion_version=champion.get("model_version") if champion else None,
                reason="candidate promoted to champion",
                metrics=candidate_metrics,
                artifact_fingerprint=candidate_row.get("artifact_fingerprint"),
            )
        self.database.insert_model_promotion_audit(
            {
                "model_version": candidate_version,
                "candidate_metrics": candidate_metrics,
                "benchmark_metrics": champion_metrics,
                "promoted": True,
                "promotion_reason": "candidate promoted to champion",
                "minimum_rules": self._minimum_rules(),
                "walk_forward": {"model_scope": model_scope},
            }
        )
        return True, "candidate promoted to champion"

    def promotion_readiness(self, metrics: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate absolute floors without changing candidate lifecycle state."""

        return self._passes_absolute_promotion_rules(metrics)

    def demote_champion(self, model_scope: str, reason: str) -> tuple[bool, str]:
        champion = self.get_champion(model_scope)
        if champion is None:
            return False, "no champion exists for scope"
        now = utc_now()
        with self.database.conn:
            self.database.conn.execute(
                """
                UPDATE model_versions
                SET status = 'archived', demoted_at = ?, demotion_reason = ?
                WHERE model_version = ? AND status = 'champion'
                """,
                (utc_iso(now), reason, champion["model_version"]),
            )
            self._record_history(
                model_scope=model_scope,
                model_version=champion["model_version"],
                action="demoted",
                previous_champion_version=champion["model_version"],
                reason=reason,
                metrics=json.loads(champion.get("metrics_json") or "{}"),
                artifact_fingerprint=champion.get("artifact_fingerprint"),
            )
        return True, reason

    def rollback(self, model_scope: str, model_version: str, *, reason: str) -> tuple[bool, str]:
        target = self.get_model(model_version)
        if target is None or str(target.get("model_scope") or "entry:all") != model_scope:
            return False, "rollback target does not exist in the requested scope"
        if not Path(str(target.get("path") or "")).is_file():
            return False, "rollback artifact is missing"
        current = self.get_champion(model_scope)
        with self.database.conn:
            self.database.conn.execute(
                "UPDATE model_versions SET status = 'archived' WHERE status = 'champion' AND model_scope = ?",
                (model_scope,),
            )
            self.database.conn.execute(
                "UPDATE model_versions SET status = 'champion', promoted_at = ?, demoted_at = NULL, demotion_reason = NULL WHERE model_version = ?",
                (utc_iso(utc_now()), model_version),
            )
            self._record_history(
                model_scope=model_scope,
                model_version=model_version,
                action="rollback",
                previous_champion_version=current.get("model_version") if current else None,
                reason=reason,
                metrics=json.loads(target.get("metrics_json") or "{}"),
                artifact_fingerprint=target.get("artifact_fingerprint"),
            )
        return True, "rollback champion activated"

    def _record_history(
        self,
        *,
        model_scope: str,
        model_version: str,
        action: str,
        previous_champion_version: str | None,
        reason: str,
        metrics: dict[str, Any],
        artifact_fingerprint: str | None,
    ) -> None:
        self.database.conn.execute(
            """
            INSERT INTO model_champion_history(
                timestamp, model_scope, model_version, action, previous_champion_version,
                reason, metrics_json, artifact_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_iso(utc_now()),
                model_scope,
                model_version,
                action,
                previous_champion_version,
                reason,
                json.dumps(metrics, sort_keys=True),
                artifact_fingerprint,
            ),
        )

    def reject(self, model_version: str, reason: str) -> None:
        with self.database.conn:
            self.database.conn.execute(
                "UPDATE model_versions SET status = 'rejected', rejection_reason = ? WHERE model_version = ?",
                (reason, model_version),
            )

    def _passes_absolute_promotion_rules(self, metrics: dict[str, Any]) -> tuple[bool, str]:
        checks = [
            (float(metrics.get("profit_factor", 0.0) or 0.0) >= self.settings.promotion_min_profit_factor, f"profit_factor below {self.settings.promotion_min_profit_factor}"),
            (float(metrics.get("win_rate", 0.0) or 0.0) >= self.settings.promotion_min_win_rate, f"win_rate below {self.settings.promotion_min_win_rate}"),
            (_metric(metrics, "max_drawdown", 1.0) <= self.settings.promotion_max_drawdown, f"max_drawdown above {self.settings.promotion_max_drawdown}"),
            (float(metrics.get("trade_label_count", 0.0) or 0.0) >= self.settings.promotion_min_trade_count, f"trade_label_count below {self.settings.promotion_min_trade_count}"),
            (_metric(metrics, "walk_forward_completed", 0.0) >= 1.0, "purged walk-forward validation did not complete"),
            (_metric(metrics, "walk_forward_fold_count", 0.0) >= self.settings.promotion_min_fold_count, f"walk-forward fold count below {self.settings.promotion_min_fold_count}"),
            (_metric(metrics, "net_return", 0.0) > 0.0, "after-cost walk-forward net return is not positive"),
            (_metric(metrics, "average_pnl_per_trade", 0.0) > 0.0, "after-cost expectancy is not positive"),
            (
                _metric(metrics, "profitable_fold_ratio", 0.0) >= self.settings.promotion_min_profitable_fold_ratio,
                f"profitable fold ratio below {self.settings.promotion_min_profitable_fold_ratio}",
            ),
            (
                _metric(metrics, "expected_calibration_error", 1.0) <= self.settings.promotion_max_calibration_error,
                f"calibration error above {self.settings.promotion_max_calibration_error}",
            ),
            (
                "selective_accuracy" not in metrics or _metric(metrics, "selective_accuracy", 0.0) >= 0.48,
                "accepted-trade selective accuracy below 0.48",
            ),
            (
                "directional_coverage" not in metrics or _metric(metrics, "directional_coverage", 0.0) >= 0.03,
                "directional opportunity coverage below 0.03",
            ),
            (
                "average_return_ci_low" not in metrics or _metric(metrics, "average_return_ci_low", -1.0) > 0.0,
                "95% bootstrap lower bound for after-cost expectancy is not positive",
            ),
            (
                _metric(metrics, "inference_latency_ms", float("inf")) <= self.settings.ml_max_inference_latency_ms,
                f"inference latency above {self.settings.ml_max_inference_latency_ms} ms",
            ),
            (
                not self.settings.promotion_require_paper_results
                or _metric(metrics, "paper_trade_count", 0.0) >= self.settings.promotion_min_paper_trade_count,
                f"paper trade count below {self.settings.promotion_min_paper_trade_count}",
            ),
            (
                not self.settings.promotion_require_paper_results
                or _metric(metrics, "paper_profit_factor", 0.0) > 1.0,
                "paper after-cost profit factor is not above 1",
            ),
            (
                _metric(metrics, "validated_regime_count", 0.0) >= self.settings.promotion_min_regime_count,
                f"validated regime count below {self.settings.promotion_min_regime_count}",
            ),
            (
                _metric(metrics, "requires_baseline_comparison", 0.0) < 1.0
                or _metric(metrics, "baseline_comparison_passed", 0.0) >= 1.0,
                "candidate did not beat the exact saved random-forest baseline",
            ),
        ]
        failures = [reason for passed, reason in checks if not passed]
        if failures:
            return False, "; ".join(failures)
        return True, "candidate passed strict promotion floors"

    def _minimum_rules(self) -> dict[str, Any]:
        return {
            "promotion_min_profit_factor": self.settings.promotion_min_profit_factor,
            "promotion_min_win_rate": self.settings.promotion_min_win_rate,
            "promotion_max_drawdown": self.settings.promotion_max_drawdown,
            "promotion_min_trade_count": self.settings.promotion_min_trade_count,
            "promotion_min_profitable_fold_ratio": self.settings.promotion_min_profitable_fold_ratio,
            "promotion_max_calibration_error": self.settings.promotion_max_calibration_error,
            "promotion_min_fold_count": self.settings.promotion_min_fold_count,
            "ml_max_inference_latency_ms": self.settings.ml_max_inference_latency_ms,
            "promotion_require_paper_results": self.settings.promotion_require_paper_results,
            "promotion_min_paper_trade_count": self.settings.promotion_min_paper_trade_count,
            "promotion_min_regime_count": self.settings.promotion_min_regime_count,
        }


def candidate_beats_champion(candidate: dict[str, Any], champion: dict[str, Any]) -> tuple[bool, str]:
    checks = {
        "profit_factor": 1.02,
        "average_pnl_per_trade": 1.01,
        "net_return": 1.01,
        "win_rate": 1.0,
        "balanced_accuracy": 1.0,
    }
    wins = 0
    for key, multiplier in checks.items():
        c_value = float(candidate.get(key, 0.0) or 0.0)
        champ_value = float(champion.get(key, 0.0) or 0.0)
        if c_value >= champ_value * multiplier:
            wins += 1
    candidate_drawdown = _metric(candidate, "max_drawdown", 1.0)
    champion_drawdown = _metric(champion, "max_drawdown", 1.0)
    if candidate_drawdown <= champion_drawdown:
        wins += 1
    if _metric(candidate, "expected_calibration_error", 1.0) <= _metric(champion, "expected_calibration_error", 1.0):
        wins += 1
    if _metric(candidate, "paper_profit_factor", 0.0) >= _metric(champion, "paper_profit_factor", 0.0):
        wins += 1
    if _metric(candidate, "profitable_fold_ratio", 0.0) >= _metric(champion, "profitable_fold_ratio", 0.0):
        wins += 1
    if wins >= 6:
        return True, "candidate improved validation metrics"
    return False, "candidate did not outperform champion on validation metrics"


def _metric(metrics: dict[str, Any], key: str, default: float) -> float:
    value = metrics.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _paper_shadow_rank(candidate: dict[str, Any]) -> tuple[float, ...]:
    metrics = candidate.get("metrics") or {}
    # Least-bad after-cost walk-forward performance comes first. The remaining
    # metrics provide deterministic tie-breakers; none bypass promotion rules.
    return (
        _metric(metrics, "net_return", float("-inf")),
        _metric(metrics, "average_pnl_per_trade", float("-inf")),
        _metric(metrics, "profit_factor", 0.0),
        _metric(metrics, "profitable_fold_ratio", 0.0),
        _metric(metrics, "balanced_accuracy", 0.0),
        -_metric(metrics, "max_drawdown", float("inf")),
    )
def _row(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
