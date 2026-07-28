from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .database import Database
from .utils.time_utils import utc_now


@dataclass(slots=True)
class EvidenceChunk:
    source: str
    text: str
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "text": self.text[:700], "score": round(self.score, 4)}


class LocalRAGCoach:
    def __init__(self, settings: Settings, database: Database, knowledge_dir: str | Path = "Knowledge") -> None:
        self.settings = settings
        self.database = database
        self.knowledge_dir = _resolve_path(knowledge_dir)

    def build_review(self, *, query: str = "GLD scalping performance risk execution missed opportunities", limit: int = 8) -> dict[str, Any]:
        evidence = self.retrieve(query, limit=limit)
        recent = _recent_bot_summary(self.database)
        agent_outputs = OfflineReviewerDebate().run(evidence=evidence, recent=recent)
        recommendations = _recommendations(agent_outputs, recent)
        summary = (
            f"Local RAG review found {len(evidence)} evidence chunks. "
            f"Recent bot data: {recent['signals']} signals, {recent['trades']} trades, "
            f"{recent['missed_opportunities']} missed-opportunity labels."
        )
        record = {
            "timestamp": utc_now(),
            "review_type": "local_rag_coach",
            "summary": summary,
            "bull_case": agent_outputs["BullCaseAgent"],
            "bear_case": agent_outputs["BearCaseAgent"],
            "risk_critique": agent_outputs["RiskCriticAgent"],
            "execution_critique": agent_outputs["ExecutionCriticAgent"],
            "journal_review": agent_outputs["JournalReviewerAgent"],
            "recommendations": recommendations,
            "evidence": [chunk.as_dict() for chunk in evidence],
        }
        self.database.insert_llm_review(record)
        return record

    def retrieve(self, query: str, *, limit: int = 8) -> list[EvidenceChunk]:
        chunks = [*_knowledge_chunks(self.knowledge_dir), *_bot_data_chunks(self.database)]
        terms = {term.lower() for term in query.split() if len(term) >= 3}
        for chunk in chunks:
            text = chunk.text.lower()
            chunk.score = sum(text.count(term) for term in terms) + _source_weight(chunk.source)
        ranked = sorted(chunks, key=lambda item: item.score, reverse=True)
        return [chunk for chunk in ranked[:limit] if chunk.score > 0]


class OfflineReviewerDebate:
    def run(self, *, evidence: list[EvidenceChunk], recent: dict[str, Any]) -> dict[str, str]:
        return {
            "BullCaseAgent": BullCaseAgent().review(evidence, recent),
            "BearCaseAgent": BearCaseAgent().review(evidence, recent),
            "RiskCriticAgent": RiskCriticAgent().review(evidence, recent),
            "ExecutionCriticAgent": ExecutionCriticAgent().review(evidence, recent),
            "JournalReviewerAgent": JournalReviewerAgent().review(evidence, recent),
        }


class BullCaseAgent:
    def review(self, evidence: list[EvidenceChunk], recent: dict[str, Any]) -> str:
        if recent["trades"] == 0:
            return "No live trade evidence yet; strongest bull case is to keep collecting data until clean price-action setups appear."
        wins = recent.get("wins", 0)
        return f"Bull case: {wins} winning outcomes recorded; preserve setups where pattern quality, liquidity, and macro alignment agree."


class BearCaseAgent:
    def review(self, evidence: list[EvidenceChunk], recent: dict[str, Any]) -> str:
        no_trade_reasons = recent.get("top_no_trade_reasons", [])
        if no_trade_reasons:
            return f"Bear case: recurring no-trade causes are {', '.join(no_trade_reasons[:3])}; avoid loosening thresholds until these clear."
        return "Bear case: data is still thin; avoid treating early paper results as statistically reliable."


class RiskCriticAgent:
    def review(self, evidence: list[EvidenceChunk], recent: dict[str, Any]) -> str:
        missed = recent.get("missed_labels", {})
        event_risk = recent.get("latest_macro_event_risk")
        return (
            "Risk critique: keep the LLM/macro layer advisory only. "
            f"Missed labels={missed}; latest macro event risk={event_risk}."
        )


class ExecutionCriticAgent:
    def review(self, evidence: list[EvidenceChunk], recent: dict[str, Any]) -> str:
        failures = recent.get("order_submit_failed", 0)
        return f"Execution critique: order submit failures={failures}; broker reconciliation must continue blocking trades on uncertainty."


class JournalReviewerAgent:
    def review(self, evidence: list[EvidenceChunk], recent: dict[str, Any]) -> str:
        journal_rows = recent.get("journal_rows", 0)
        return f"Journal review: {journal_rows} journal rows available; review pattern, liquidity, macro bias, and missed-opportunity fields together."


def _knowledge_chunks(root: Path) -> list[EvidenceChunk]:
    if not root.exists():
        return []
    chunks: list[EvidenceChunk] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".csv"}:
            chunks.extend(_text_file_chunks(path))
        elif suffix == ".pdf":
            chunks.extend(_pdf_chunks(path))
    return chunks


def _text_file_chunks(path: Path) -> list[EvidenceChunk]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    return _split_chunks(text, str(path))


def _pdf_chunks(path: Path) -> list[EvidenceChunk]:
    try:
        from pypdf import PdfReader
    except Exception:
        return []
    try:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages[:12])
    except Exception:
        return []
    return _split_chunks(text, str(path))


def _bot_data_chunks(database: Database) -> list[EvidenceChunk]:
    chunks: list[EvidenceChunk] = []
    for table in ["trading_journal", "no_trade_logs", "missed_opportunities", "macro_context", "rl_experiments"]:
        try:
            rows = database.fetch_recent_table_rows(table, limit=30)
        except Exception:
            continue
        for row in rows:
            chunks.append(EvidenceChunk(source=f"sqlite:{table}", text=json.dumps(row, sort_keys=True, default=str)))
    latest_dir = PROJECT_ROOT / "exports" / "latest"
    if latest_dir.exists():
        for path in latest_dir.glob("*.csv"):
            chunks.extend(_csv_summary_chunks(path))
    return chunks


def _csv_summary_chunks(path: Path) -> list[EvidenceChunk]:
    try:
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            rows = []
            for idx, row in enumerate(reader):
                if idx > 15:
                    break
                rows.append(row)
    except Exception:
        return []
    return [EvidenceChunk(source=str(path), text=json.dumps(rows, default=str))]


def _split_chunks(text: str, source: str, *, chunk_size: int = 1200) -> list[EvidenceChunk]:
    cleaned = " ".join(text.split())
    chunks: list[EvidenceChunk] = []
    for start in range(0, min(len(cleaned), 20_000), chunk_size):
        piece = cleaned[start : start + chunk_size]
        if len(piece) >= 80:
            chunks.append(EvidenceChunk(source=source, text=piece))
    return chunks


def _recent_bot_summary(database: Database) -> dict[str, Any]:
    signals = database.fetch_recent_table_rows("signals", limit=500)
    journal = database.fetch_recent_table_rows("trading_journal", limit=500)
    no_trades = database.fetch_recent_table_rows("no_trade_logs", limit=500)
    missed = database.fetch_recent_table_rows("missed_opportunities", limit=500)
    macros = database.fetch_recent_table_rows("macro_context", limit=1)
    outcomes = database.fetch_recent_table_rows("trade_outcomes", limit=500)
    no_trade_reason_counts = Counter(str(row.get("reason") or "unknown")[:90] for row in no_trades)
    missed_label_counts = Counter(str(row.get("label") or "unknown") for row in missed)
    return {
        "signals": len(signals),
        "journal_rows": len(journal),
        "trades": len(outcomes),
        "wins": sum(1 for row in outcomes if str(row.get("win_loss")).lower() == "win"),
        "missed_opportunities": len(missed),
        "missed_labels": dict(missed_label_counts),
        "top_no_trade_reasons": [reason for reason, _ in no_trade_reason_counts.most_common(5)],
        "order_submit_failed": sum(1 for row in journal if row.get("event_type") == "ORDER_SUBMIT_FAILED"),
        "latest_macro_event_risk": macros[0].get("headline_event_risk") if macros else None,
    }


def _recommendations(agent_outputs: dict[str, str], recent: dict[str, Any]) -> list[str]:
    recommendations = ["Keep LLM/RAG output outside the live order-submit path."]
    if recent["trades"] == 0:
        recommendations.append("Collect enough paper trades before promoting any ML or RL policy.")
    if recent.get("missed_labels", {}).get("MISSED_LONG", 0) or recent.get("missed_labels", {}).get("MISSED_SHORT", 0):
        recommendations.append("Review missed-opportunity labels against pattern_quality and liquidity_score before changing thresholds.")
    if recent.get("latest_macro_event_risk") and float(recent["latest_macro_event_risk"]) > 0.6:
        recommendations.append("During high event risk, keep sizing conservative even when price action is clean.")
    return recommendations


def _source_weight(source: str) -> float:
    if source.startswith("sqlite:"):
        return 1.0
    if "Knowledge" in source:
        return 0.6
    if "exports" in source:
        return 0.4
    return 0.0


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path
