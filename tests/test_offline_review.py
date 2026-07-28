from datetime import datetime, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.offline_review import LocalRAGCoach


def test_local_rag_coach_persists_review(tmp_path):
    knowledge = tmp_path / "Knowledge"
    knowledge.mkdir()
    (knowledge / "setup_notes.txt").write_text(
        "Clean GLD scalping setup requires buildup, liquidity, risk control, and patience.",
        encoding="utf-8",
    )
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'coach.db'}")
    db = Database(settings=settings)
    db.init_db()
    db.insert_no_trade(
        {
            "timestamp": datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
            "symbol": "GLD",
            "reason": "spread too wide",
            "feature_snapshot_json": {"spread_pct": 0.003},
        }
    )

    review = LocalRAGCoach(settings, db, knowledge_dir=knowledge).build_review(query="GLD liquidity risk setup")

    assert "BullCaseAgent" not in review
    assert review["bull_case"]
    assert db.count_rows("llm_reviews") == 1
