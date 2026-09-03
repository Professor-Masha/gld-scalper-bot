from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class ManagedProcess:
    name: str
    pid: int
    command: list[str]
    started_at: str
    log_path: str
    state: str = "running"
    return_code: int | None = None


class ProcessManager:
    """Own allowlisted CLI child processes; it never accepts a raw shell command."""

    def __init__(self, project_root: Path, environment_factory: Callable[[], dict[str, str]]) -> None:
        self.project_root = project_root
        self.environment_factory = environment_factory
        self.log_root = project_root / "logs" / "dashboard"
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.log_root / "process_registry.json"
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._records: dict[str, ManagedProcess] = {}
        self._handles: dict[str, object] = {}
        self._lock = threading.RLock()
        self._load_registry()

    def start(self, name: str, arguments: list[str]) -> ManagedProcess:
        with self._lock:
            current = self._records.get(name)
            if current and self._is_alive(current.pid):
                raise RuntimeError(f"{name} is already running (PID {current.pid})")
            command = [sys.executable, "-m", "gld_scalper.main", *arguments]
            log_path = self.log_root / f"{name}.log"
            handle = log_path.open("ab", buffering=0)
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=self.environment_factory(),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            record = ManagedProcess(
                name=name, pid=process.pid, command=arguments,
                started_at=datetime.now(timezone.utc).isoformat(), log_path=str(log_path),
            )
            self._processes[name] = process
            self._records[name] = record
            self._handles[name] = handle
            self._save_registry()
            threading.Thread(target=self._watch, args=(name, process), daemon=True).start()
            return record

    def stop(self, name: str) -> ManagedProcess:
        with self._lock:
            record = self._records.get(name)
            if record is None or not self._is_alive(record.pid):
                raise RuntimeError(f"{name} is not running")
            process = self._processes.get(name)
            try:
                if process is not None:
                    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                elif os.name == "nt":
                    os.kill(record.pid, signal.CTRL_BREAK_EVENT)
                else:
                    os.kill(record.pid, signal.SIGINT)
                record.state = "stopping"
            except (OSError, ProcessLookupError) as exc:
                record.state = "unknown"
                raise RuntimeError(f"Could not stop {name}: {exc}") from exc
            self._save_registry()
            return record

    def statuses(self) -> list[dict[str, object]]:
        with self._lock:
            changed = False
            for record in self._records.values():
                if record.state in {"running", "stopping"} and not self._is_alive(record.pid):
                    record.state = "exited"
                    changed = True
            if changed:
                self._save_registry()
            return [asdict(record) for record in sorted(self._records.values(), key=lambda item: item.name)]

    def tail(self, name: str, lines: int = 160) -> list[str]:
        path = self.project_root / "logs" / "bot.log" if name == "bot" else Path(
            self._records[name].log_path if name in self._records else self.log_root / f"{name}.log"
        )
        if not path.exists():
            return []
        try:
            return self._tail_file(path, max(10, min(lines, 1000)))
        except OSError:
            # Log rotation and antivirus scanners can briefly invalidate an open.
            return []

    @staticmethod
    def _tail_file(
        path: Path,
        line_count: int,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
    ) -> list[str]:
        """Read a bounded tail without scanning an ever-growing process log."""
        if line_count <= 0 or max_bytes <= 0 or chunk_size <= 0:
            return []

        chunks: list[bytes] = []
        bytes_read = 0
        newline_count = 0
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            while position > 0 and bytes_read < max_bytes and newline_count <= line_count:
                size = min(chunk_size, position, max_bytes - bytes_read)
                position -= size
                handle.seek(position)
                chunk = handle.read(size)
                chunks.append(chunk)
                bytes_read += len(chunk)
                newline_count += chunk.count(b"\n")

        payload = b"".join(reversed(chunks))
        truncated = position > 0
        if truncated:
            # The first bytes normally begin inside a line; never expose that fragment.
            first_newline = payload.find(b"\n")
            payload = payload[first_newline + 1:] if first_newline >= 0 else b""

        result = payload.decode("utf-8", errors="replace").splitlines()[-line_count:]
        if truncated:
            marker = "[dashboard log tail truncated at 8 MiB read limit]"
            result = [marker, *result[-max(0, line_count - 1):]]
        return result

    def _watch(self, name: str, process: subprocess.Popen[bytes]) -> None:
        return_code = process.wait()
        with self._lock:
            record = self._records.get(name)
            if record:
                record.state = "completed" if return_code == 0 else "failed"
                record.return_code = return_code
            handle = self._handles.pop(name, None)
            if handle:
                handle.close()
            self._processes.pop(name, None)
            self._save_registry()

    def _is_alive(self, pid: int) -> bool:
        process = next((item for item in self._processes.values() if item.pid == pid), None)
        if process is not None:
            return process.poll() is None
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _load_registry(self) -> None:
        if not self.registry_path.exists():
            return
        try:
            for item in json.loads(self.registry_path.read_text(encoding="utf-8")):
                record = ManagedProcess(**item)
                if record.state in {"running", "stopping"} and not self._is_alive(record.pid):
                    record.state = "exited"
                self._records[record.name] = record
        except (OSError, ValueError, TypeError):
            self._records = {}

    def _save_registry(self) -> None:
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(record) for record in self._records.values()], indent=2), encoding="utf-8")
        temporary.replace(self.registry_path)
