from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter
from datetime import datetime
from typing import Any

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import utc_now
from .model_registry import ModelRegistry


def train_exit_candidate(
    database: Database,
    settings: Settings,
    *,
    strategy_path: str = "all",
    playbook: str = "all",
) -> dict[str, Any]:
    rows = database.conn.execute(
        """
        SELECT e.*, o.strategy_path, o.playbook, o.net_pnl_after_costs,
               o.estimated_live_cost, o.exit_time
        FROM position_management_events e
        JOIN trade_outcomes o ON o.trade_id = e.trade_id
        WHERE e.status = 'submitted'
          AND e.action IN ('STOP_REPLACED', 'PROTECTED_EXIT_REQUESTED')
          AND o.exit_time IS NOT NULL
          AND o.net_pnl_after_costs IS NOT NULL
        ORDER BY julianday(e.timestamp), e.id
        """
    ).fetchall()
    records = []
    for row in rows:
        if strategy_path != "all" and str(row["strategy_path"] or "minute") != strategy_path:
            continue
        if playbook != "all" and str(row["playbook"] or "unclassified") != playbook:
            continue
        label = "protect_profit" if row["action"] == "STOP_REPLACED" else "exit_position"
        details = json.loads(row["details_json"] or "{}")
        records.append(
            {
                "timestamp": row["timestamp"],
                "label": label,
                "features": [
                    float(row["pnl_pct"] or 0.0),
                    float(row["max_favorable_excursion"] or 0.0),
                    float(row["max_adverse_excursion"] or 0.0),
                    float(row["mark_price"] or 0.0),
                    float(row["old_stop_price"] or 0.0),
                    float(row["new_stop_price"] or 0.0),
                    float(details.get("economic_breakeven_pct") or 0.0),
                    float(details.get("spread_pct") or 0.0),
                ],
            }
        )
    if len(records) < settings.exit_model_min_trustworthy_outcomes:
        return {
            "status": "skipped",
            "reason": "not enough trustworthy exit outcomes",
            "samples": len(records),
            "minimum": settings.exit_model_min_trustworthy_outcomes,
        }
    if len({record["label"] for record in records}) < 2:
        return {"status": "skipped", "reason": "exit outcomes need at least two action classes", "samples": len(records)}
    split = max(1, min(len(records) - 1, int(len(records) * 0.80)))
    train, test = records[:split], records[split:]
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import balanced_accuracy_score, f1_score

    model = RandomForestClassifier(
        n_estimators=120,
        max_depth=7,
        min_samples_leaf=10,
        class_weight="balanced_subsample",
        random_state=41,
        n_jobs=1,
    )
    model.fit([record["features"] for record in train], [record["label"] for record in train])
    predictions = model.predict([record["features"] for record in test])
    metrics = {
        "balanced_accuracy": float(balanced_accuracy_score([record["label"] for record in test], predictions)),
        "macro_f1": float(f1_score([record["label"] for record in test], predictions, average="macro", zero_division=0)),
        "sample_count": len(records),
        "class_counts": dict(Counter(record["label"] for record in records)),
        "target": "exit_action",
        "entry_target_shared": False,
    }
    scope = f"exit:{strategy_path}:{playbook}"
    model_state_fingerprint = hashlib.sha256(
        pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
    ).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "scope": scope,
                "start": records[0]["timestamp"],
                "end": records[-1]["timestamp"],
                "metrics": metrics,
                "model_state_fingerprint": model_state_fingerprint,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    version = f"exit-candidate-{utc_now().strftime('%Y%m%d-%H%M%S-%f')}"
    path = PROJECT_ROOT / "models" / settings.data_mode / "exit" / f"{version}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump(
        {
            "format_version": 1,
            "model": model,
            "model_scope": scope,
            "artifact_fingerprint": fingerprint,
            "model_state_fingerprint": model_state_fingerprint,
            "feature_columns": [
                "pnl_pct", "mfe", "mae", "mark_price", "old_stop", "new_stop",
                "economic_breakeven_pct", "spread_pct",
            ],
            "target": "exit_action",
            "metrics": metrics,
        },
        path,
    )
    ModelRegistry(database, settings).register_candidate(
        model_version=version,
        model_type="exit_random_forest",
        model_scope=scope,
        path=str(path),
        feature_columns=[
            "pnl_pct", "mfe", "mae", "mark_price", "old_stop", "new_stop",
            "economic_breakeven_pct", "spread_pct",
        ],
        feature_profile="exit_management",
        model_parameters=model.get_params(),
        artifact_fingerprint=fingerprint,
        metrics=metrics,
        training_start=utc_now(),
        training_end=utc_now(),
        training_data_start=datetime.fromisoformat(records[0]["timestamp"]),
        training_data_end=datetime.fromisoformat(records[-1]["timestamp"]),
    )
    return {"status": "completed", "model_version": version, "model_scope": scope, "path": str(path), "metrics": metrics}
