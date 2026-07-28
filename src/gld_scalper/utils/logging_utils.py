from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from ..database import sqlite_write_lock
from .time_utils import utc_iso, utc_now


class SQLiteLogHandler(logging.Handler):
    def __init__(self, database: Any) -> None:
        super().__init__()
        self.database_path = Path(database.path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            details = {
                "pathname": record.pathname,
                "lineno": record.lineno,
                "thread_id": threading.get_ident(),
                "thread_name": record.threadName,
            }
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                details["exc_info"] = formatter.formatException(record.exc_info)
            with sqlite_write_lock():
                with sqlite3.connect(self.database_path, timeout=30) as conn:
                    conn.execute("PRAGMA busy_timeout = 30000")
                    conn.execute(
                        """
                        INSERT INTO system_logs(timestamp, level, module, event_type, message, details_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            utc_iso(utc_now()),
                            record.levelname,
                            record.name,
                            getattr(record, "event_type", "log"),
                            record.getMessage(),
                            json.dumps(details, sort_keys=True, default=str),
                        ),
                    )
        except Exception:
            self.handleError(record)


def configure_logging(level: str = "INFO", log_dir: str | Path = "logs", database: Any | None = None) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(Path(log_dir) / "bot.log", maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if database is not None:
        root.addHandler(SQLiteLogHandler(database))


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
