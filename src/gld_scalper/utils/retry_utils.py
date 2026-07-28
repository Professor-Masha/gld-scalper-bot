from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 10.0,
    logger: logging.Logger | None = None,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - exercised by integration paths
            last_error = exc
            if attempt == attempts:
                break
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            if logger:
                logger.warning("retrying after failure", extra={"attempt": attempt, "error": str(exc)})
            time.sleep(delay)
    assert last_error is not None
    raise last_error
