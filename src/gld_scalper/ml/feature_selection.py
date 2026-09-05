from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Sequence


def select_stable_features(
    records: Sequence[dict[str, Any]],
    columns: Sequence[str],
    *,
    maximum_features: int,
    maximum_missing_fraction: float = 0.35,
    correlation_limit: float = 0.985,
) -> tuple[list[str], dict[str, Any]]:
    """Select train-only features using coverage, variance, drift and redundancy."""

    import numpy as np

    candidates: list[str] = []
    diagnostics: dict[str, dict[str, float | str]] = {}
    split = max(1, int(len(records) * 0.70))
    for column in columns:
        raw = np.asarray([_number(record.get("features", {}).get(column), math.nan) for record in records], dtype=float)
        finite = np.isfinite(raw)
        missing = 1.0 - float(finite.mean())
        values = raw[finite]
        variance = float(np.var(values)) if len(values) else 0.0
        if missing > maximum_missing_fraction or variance <= 1e-12:
            diagnostics[column] = {"status": "rejected", "missing_fraction": missing, "variance": variance}
            continue
        early = raw[:split]
        late = raw[split:]
        early = early[np.isfinite(early)]
        late = late[np.isfinite(late)]
        scale = max(float(np.std(early)) if len(early) else 0.0, 1e-9)
        drift = abs(float(np.mean(late)) - float(np.mean(early))) / scale if len(early) and len(late) else 0.0
        score = math.log1p(variance) + (1.0 - missing) - min(drift, 10.0) * 0.05
        diagnostics[column] = {
            "status": "candidate", "missing_fraction": missing, "variance": variance, "drift_z": drift, "score": score
        }
        candidates.append(column)
    candidates.sort(key=lambda column: float(diagnostics[column]["score"]), reverse=True)
    selected: list[str] = []
    vectors: dict[str, Any] = {}
    medians: dict[str, float] = defaultdict(float)
    for column in candidates:
        vector = np.asarray([_number(record.get("features", {}).get(column), math.nan) for record in records], dtype=float)
        median = float(np.nanmedian(vector)) if np.isfinite(vector).any() else 0.0
        vector = np.nan_to_num(vector, nan=median, posinf=median, neginf=median)
        redundant = False
        for kept in selected:
            if len(vector) > 2 and abs(float(np.corrcoef(vector, vectors[kept])[0, 1])) >= correlation_limit:
                redundant = True
                diagnostics[column]["status"] = "rejected_correlated"
                diagnostics[column]["correlated_with"] = kept
                break
        if not redundant:
            selected.append(column)
            vectors[column] = vector
            medians[column] = median
            diagnostics[column]["status"] = "selected"
        if len(selected) >= maximum_features:
            break
    return selected, {
        "input_feature_count": len(columns),
        "selected_feature_count": len(selected),
        "maximum_features": maximum_features,
        "selected_features": selected,
        "diagnostics": diagnostics,
    }


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default
