from datetime import datetime, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.reports.csv_exporter import HourlyCSVExportScheduler, export_database_to_csv


def test_export_database_to_csv_creates_timestamped_and_latest_dirs(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'export.db'}")
    db = Database(settings=settings)
    db.init_db()
    db.upsert_bars(
        [
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1000,
            }
        ]
    )
    result = export_database_to_csv(db, tmp_path / "exports" / "hourly", run_at=datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert result.files["bars"] == 1
    assert (result.export_dir / "bars.csv").exists()
    assert (result.export_dir / "manifest.csv").exists()
    assert (tmp_path / "exports" / "latest" / "bars.csv").exists()


def test_export_database_to_csv_can_filter_to_collection_window(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'export.db'}")
    db = Database(settings=settings)
    db.init_db()
    db.upsert_bars(
        [
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1000,
                "created_at": datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
            },
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": datetime(2026, 1, 1, 14, 31, tzinfo=timezone.utc),
                "open": 101,
                "high": 102,
                "low": 100,
                "close": 101.5,
                "volume": 1200,
                "created_at": datetime(2026, 1, 1, 14, 45, tzinfo=timezone.utc),
            },
        ]
    )

    result = export_database_to_csv(
        db,
        tmp_path / "exports" / "hourly",
        run_at=datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc),
        since=datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc),
        until=datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc),
    )

    assert result.files["bars"] == 1
    rows = (result.export_dir / "bars.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert "101.5" in rows[1]


def test_hourly_scheduler_waits_one_interval_before_exporting(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'export.db'}",
        csv_export_interval_minutes=60,
        csv_export_dir=str(tmp_path / "exports" / "hourly"),
    )
    db = Database(settings=settings)
    db.init_db()
    start = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    scheduler = HourlyCSVExportScheduler(settings, start_at=start)

    early = scheduler.maybe_export(db, datetime(2026, 1, 1, 14, 59, tzinfo=timezone.utc))
    on_time = scheduler.maybe_export(db, datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc))

    assert early is None
    assert on_time is not None
    assert on_time.since == start
    assert on_time.until == datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
