from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCOPES = ("fast_microstructure", "minute", "news_event", "exit")


class TransformerCatalog:
    """Discover sequence archives and provide safe scope-specific UI defaults."""

    PRESETS: dict[str, dict[str, Any]] = {
        "fast_microstructure": {
            "source": "raw", "output": "fast_2021_2026_v2.seq", "max_samples": 50_000,
            "sequence_length": 120, "window_seconds": 120, "max_features": 64,
        },
        "minute": {
            "source": "raw", "output": "minute_2021_2026_v2.seq", "max_samples": 50_000,
            "sequence_length": 90, "max_features": 64,
        },
        "news_event": {
            "source": "raw", "output": "news_2021_2026_v2.seq", "max_samples": 50_000,
            "sequence_length": 90, "max_features": 64,
        },
        "exit": {
            "source": "raw", "output": "exit_paper_v2.seq", "max_samples": 50_000,
            "sequence_length": 120, "max_features": 64,
        },
    }

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.root = self.project_root / "data" / "paper" / "ml_training" / "transformer"

    def payload(self) -> dict[str, Any]:
        return {"presets": self.presets(), "artifacts": self.artifacts()}

    def presets(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for scope, values in self.PRESETS.items():
            result[scope] = {
                **values,
                "database": "data/paper/historical/gld_2021_2026_ai_full/gld_scalper_historical.db",
                "output": str((self.root / values["output"]).relative_to(self.project_root)).replace("\\", "/"),
            }
        return result

    def artifacts(self) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        if not self.root.exists():
            return artifacts
        for path in sorted(self.root.glob("*.seq")):
            manifest_path = path / "manifest.json"
            if not path.is_dir() or not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scope = str(manifest.get("scope") or "")
            if scope not in SCOPES:
                continue
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
                    "scope": scope,
                    "sample_count": int(manifest.get("sample_count") or manifest.get("rows") or 0),
                    "sequence_length": int(manifest.get("sequence_length") or 0),
                    "feature_count": int(manifest.get("feature_count") or 0),
                    "start": manifest.get("start"),
                    "end": manifest.get("end"),
                    "classes": manifest.get("classes") or [],
                    "fingerprint": manifest.get("fingerprint"),
                }
            )
        return artifacts
