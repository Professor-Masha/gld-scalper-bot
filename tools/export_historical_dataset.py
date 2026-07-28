from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gld_scalper.config import load_settings
from gld_scalper.database import Database
from gld_scalper.reports.csv_exporter import export_database_to_csv
from gld_scalper.utils.time_utils import utc_now


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a standalone historical dataset SQLite database to CSV.")
    parser.add_argument("--dataset-name", required=True, help="Dataset folder name, for example gld_2021_2026.")
    args = parser.parse_args()

    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in args.dataset_name)
    dataset_dir = PROJECT_ROOT / "data" / "paper" / "historical" / safe_name
    export_dir = PROJECT_ROOT / "exports" / "paper" / "historical" / safe_name / "hourly"
    db_path = dataset_dir / "gld_scalper_historical.db"
    if not db_path.exists():
        raise SystemExit(f"Historical dataset database not found: {db_path}")

    settings = replace(
        load_settings(),
        data_mode="paper",
        database_url=f"sqlite:///{db_path}",
        csv_export_dir=str(export_dir),
        enable_hourly_csv_export=False,
    )
    db = Database(settings=settings)
    db.init_db()
    result = export_database_to_csv(db, export_dir, run_at=utc_now())
    print(json.dumps({"export_dir": str(result.export_dir), "files": result.files}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
