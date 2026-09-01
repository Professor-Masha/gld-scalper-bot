from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


API_VERSION = 1
CONTROL_PLANE_SOURCE = "gld-dashboard-gateway"
DASHBOARD_JOB_ACTIONS = frozenset({
    "ml_train", "ml_loop", "transformer_dataset", "transformer_train",
    "transformer_loop", "transformer_batch", "backtest", "labels",
    "research", "report", "llm_analysis", "llm_cycle", "llm_macro",
    "llm_coach", "llm_council", "llm_advice", "llm_labels", "llm_train",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: new_identifier("evt"))
    event_type: str
    version: int = API_VERSION
    timestamp: str = Field(default_factory=utc_now)
    trace_id: str = Field(default_factory=lambda: new_identifier("trace"))
    source: str = CONTROL_PLANE_SOURCE
    symbol: str | None = None
    sequence: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandRequest(BaseModel):
    command_id: str = Field(default_factory=lambda: new_identifier("cmd"))
    actor_id: str = "local-operator"
    command_type: Literal["bot.start", "bot.stop", "job.start", "job.stop"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    requested_at: str = Field(default_factory=utc_now)
    idempotency_key: str = Field(default_factory=lambda: new_identifier("idem"), min_length=8, max_length=200)
    confirmation_token: str | None = None


class CommandResult(BaseModel):
    command_id: str
    accepted: bool
    status: str
    message: str
    correlation_id: str
    idempotent_replay: bool = False
    result: dict[str, Any] = Field(default_factory=dict)


class TrainingJobRequest(BaseModel):
    action: Literal[
        "ml_train",
        "ml_loop",
        "transformer_dataset",
        "transformer_train",
        "transformer_loop",
        "transformer_batch",
        "backtest",
        "labels",
        "research",
        "report",
        "llm_analysis",
        "llm_cycle",
        "llm_macro",
        "llm_coach",
        "llm_council",
        "llm_advice",
        "llm_labels",
        "llm_train",
    ]
    options: dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "local-operator"
    idempotency_key: str = Field(default_factory=lambda: new_identifier("idem"), min_length=8, max_length=200)


class BotCommandRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "local-operator"
    idempotency_key: str = Field(default_factory=lambda: new_identifier("idem"), min_length=8, max_length=200)
