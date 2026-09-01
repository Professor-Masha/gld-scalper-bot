from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .audit import AuditLedger
from .contracts import DASHBOARD_JOB_ACTIONS, CommandRequest, CommandResult, new_identifier


class ControlPlane:
    """Serializes typed operator commands around the existing bot entry points."""

    def __init__(
        self,
        *,
        audit: AuditLedger,
        start: Callable[[str, dict[str, Any]], dict[str, Any]],
        stop: Callable[[str], dict[str, Any]],
        requests_per_minute: int = 20,
    ) -> None:
        self.audit = audit
        self._start = start
        self._stop = stop
        self._requests_per_minute = requests_per_minute
        self._request_times: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.RLock()

    def execute(self, request: CommandRequest) -> CommandResult:
        with self._lock:
            replay = self.audit.find_idempotency_result(request.idempotency_key)
            if replay:
                replay["idempotent_replay"] = True
                return CommandResult.model_validate(replay)
            self._enforce_rate_limit(request.actor_id)
            correlation_id = new_identifier("trace")
            self.audit.append(
                event_type="command.requested",
                actor_id=request.actor_id,
                status="requested",
                correlation_id=correlation_id,
                command_id=request.command_id,
                action=request.command_type,
                details={
                    "idempotency_key": request.idempotency_key,
                    "parameters": request.parameters,
                },
            )
            try:
                payload = self._dispatch(request)
                result = CommandResult(
                    command_id=request.command_id,
                    accepted=True,
                    status=str(payload.get("state") or "accepted"),
                    message=f"{request.command_type} accepted",
                    correlation_id=correlation_id,
                    result=payload,
                )
            except (RuntimeError, ValueError) as exc:
                result = CommandResult(
                    command_id=request.command_id,
                    accepted=False,
                    status="rejected",
                    message=str(exc),
                    correlation_id=correlation_id,
                )
                self._record_completion(request, result)
                raise
            self._record_completion(request, result)
            return result

    def _dispatch(self, request: CommandRequest) -> dict[str, Any]:
        parameters = dict(request.parameters)
        if request.command_type == "bot.start":
            return self._start("paper", dict(parameters.get("options") or parameters))
        if request.command_type == "bot.stop":
            return self._stop("paper")
        action = str(parameters.get("action") or "").strip()
        if not action:
            raise ValueError("Job command requires an action")
        if action not in DASHBOARD_JOB_ACTIONS:
            raise ValueError(f"Unsupported dashboard job: {action}")
        if request.command_type == "job.start":
            return self._start(action, dict(parameters.get("options") or {}))
        if request.command_type == "job.stop":
            return self._stop(action)
        raise ValueError(f"Unsupported command type: {request.command_type}")

    def _record_completion(self, request: CommandRequest, result: CommandResult) -> None:
        self.audit.append(
            event_type="command.completed" if result.accepted else "command.rejected",
            actor_id=request.actor_id,
            status=result.status,
            correlation_id=result.correlation_id,
            command_id=request.command_id,
            action=request.command_type,
            details={
                "idempotency_key": request.idempotency_key,
                "command_result": result.model_dump(mode="json"),
            },
        )

    def _enforce_rate_limit(self, actor_id: str) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=1)
        timestamps = self._request_times[actor_id]
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        if len(timestamps) >= self._requests_per_minute:
            raise RuntimeError("Control-plane command rate limit exceeded")
        timestamps.append(now)
