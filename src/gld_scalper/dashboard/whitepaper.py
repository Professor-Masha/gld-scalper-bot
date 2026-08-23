from __future__ import annotations

from pathlib import Path
from typing import Any


class WhitePaperRepository:
    """Read the versioned white paper as a local dashboard document."""

    def __init__(self, project_root: Path) -> None:
        self.path = project_root / "docs" / "BOT_WHITE_PAPER.md"

    def payload(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"available": False, "markdown": "", "path": str(self.path)}
        text = self.path.read_text(encoding="utf-8")
        headings = [line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("## ")]
        return {
            "available": True,
            "markdown": text,
            "path": str(self.path),
            "sections": headings,
            "updated_at": self.path.stat().st_mtime,
        }
