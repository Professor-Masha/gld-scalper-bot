from __future__ import annotations

import hashlib
import json
import os
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .database import Database
from .llm_analysis import LLMAnalysisService, require_ollama_enabled
from .offline_review import LocalRAGCoach
from .research_data import ResearchDataCollector
from .utils.time_utils import market_session, utc_now


@dataclass(frozen=True, slots=True)
class FinGPTSourceProfile:
    root: Path
    fingerprint: str
    files: tuple[str, ...]


class OfflineResearchLock(AbstractContextManager):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"Another offline LLM research cycle holds {self.path}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
            json.dump({"pid": os.getpid(), "started_at": utc_now().isoformat()}, file_handle)
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.acquired:
            self.path.unlink(missing_ok=True)
        return False


class FinGPTOfflineResearch:
    """Offline-only FinGPT-inspired data preparation, RAG, and Ollama review."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def run(self, *, cadence: str = "daily", force: bool = False) -> dict[str, Any]:
        if cadence not in {"hourly", "daily"}:
            raise ValueError("cadence must be hourly or daily")
        require_ollama_enabled(self.settings)
        self._assert_offline(force=force)
        profile = load_fingpt_source_profile(self.settings)
        lock_path = self.settings.data_root / "locks" / "fingpt_ollama_offline.lock"
        started = utc_now()
        with OfflineResearchLock(lock_path):
            try:
                collector = ResearchDataCollector(self.settings, self.database)
                price_link_result = collector.label_news_price_moves()
                service = LLMAnalysisService(self.settings, self.database)
                news_labels = service.classify_recent_news(limit=30 if cadence == "hourly" else 100)
                linked_fraction = _news_linked_fraction(self.database, limit=100 if cadence == "hourly" else 500)
                macro = service.build_llm_macro_context(
                    now=utc_now(),
                    horizon="hourly" if cadence == "hourly" else "daily_weekly",
                )
                rag = LocalRAGCoach(
                    self.settings,
                    self.database,
                    knowledge_dir=PROJECT_ROOT / "Knowledge",
                ).build_review(
                    query="Review GLD trades, missed opportunities, news reactions, exit quality, and model weaknesses.",
                    limit=40 if cadence == "hourly" else 100,
                )
                outputs: dict[str, Any] = {
                    "price_linking": {
                        "status": price_link_result.status,
                        "rows": price_link_result.rows,
                        "message": price_link_result.message,
                    },
                    "news_classifications": news_labels,
                    "macro_context": macro,
                    "rag_review": rag,
                    "fingpt_source": {"root": str(profile.root), "files": profile.files},
                }
                if cadence == "daily":
                    outputs["journal_analysis"] = service.run_data_analysis(
                        query=(
                            "Analyze after-cost outcomes, exit quality, missed opportunities, setup quality, "
                            "news reactions, execution errors, and model drift. Advice is offline only."
                        )
                    )
                    outputs["training_advice"] = service.generate_training_advice()
                    outputs["advisory_labels"] = service.label_recent_signals(limit=25)
                result = {
                    "timestamp": started,
                    "cadence": cadence,
                    "provider": service.client.provider,
                    "model": service.client.model,
                    "fingpt_source_fingerprint": profile.fingerprint,
                    "news_linked_fraction": linked_fraction,
                    "status": "completed",
                    "outputs": outputs,
                    "completed_at": utc_now(),
                }
                self.database.insert_llm_offline_cycle(result)
                return result
            except Exception as exc:
                failed = {
                    "timestamp": started,
                    "cadence": cadence,
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "fingpt_source_fingerprint": profile.fingerprint,
                    "news_linked_fraction": _news_linked_fraction(self.database, limit=100),
                    "status": "failed",
                    "outputs": {},
                    "error_message": str(exc),
                    "completed_at": utc_now(),
                }
                self.database.insert_llm_offline_cycle(failed)
                raise

    def _assert_offline(self, *, force: bool) -> None:
        if self.settings.enable_llm_live_trading:
            raise RuntimeError("LLM broker access is prohibited")
        if not force and market_session(utc_now(), extended_hours=False) == "regular":
            raise RuntimeError("FinGPT/Ollama research is restricted to after-market hours")
        if self.database.fetch_active_execution_episodes(self.settings.bot_symbol):
            raise RuntimeError("Offline LLM research refused because execution episodes are still active")


def load_fingpt_source_profile(settings: Settings) -> FinGPTSourceProfile:
    root = Path(settings.fingpt_source_dir)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    targets = (
        root / "FinGPT_Forecaster" / "prompt.py",
        root / "FinGPT_Forecaster" / "data.py",
        root / "FinGPT_RAG" / "README.md",
        root / "FinGPT_Benchmark" / "benchmarks" / "sentiment_templates.txt",
    )
    available = tuple(path for path in targets if path.is_file())
    if not available:
        raise RuntimeError(f"Local FinGPT source was not found under {root}")
    digest = hashlib.sha256()
    for path in available:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return FinGPTSourceProfile(root=root, fingerprint=digest.hexdigest(), files=tuple(str(path.relative_to(root)) for path in available))


def _news_linked_fraction(database: Database, *, limit: int) -> float:
    rows = database.conn.execute(
        "SELECT outcome_linked FROM news_items ORDER BY julianday(timestamp) DESC, id DESC LIMIT ?",
        (max(1, limit),),
    ).fetchall()
    return sum(bool(row["outcome_linked"]) for row in rows) / max(len(rows), 1)
