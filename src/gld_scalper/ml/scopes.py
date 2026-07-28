from __future__ import annotations

from typing import Any


MAJOR_PLAYBOOKS = {
    "proper_breakout",
    "false_break_reversal",
    "pullback_continuation",
    "compression_breakout",
    "trend_continuation",
    "spread_capture",
    "news_event",
}


def model_scope_from_features(features: dict[str, Any]) -> str:
    playbook = str(features.get("playbook") or "").lower()
    strategy_path = str(features.get("strategy_path") or "minute").lower()
    if playbook == "news_event" or bool(features.get("event_post_release")):
        return "entry:news_event"
    if strategy_path == "fast":
        return "entry:fast_microstructure"
    if playbook in MAJOR_PLAYBOOKS:
        return f"entry:playbook:{playbook}"
    return "entry:minute"


def model_scope_from_context(context: dict[str, Any] | None) -> str:
    context = context or {}
    explicit = str(context.get("model_scope") or "").strip()
    if explicit:
        return explicit
    playbook = str(context.get("playbook") or "all").lower()
    if playbook == "fast_microstructure":
        return "entry:fast_microstructure"
    if playbook == "minute_setups":
        return "entry:minute"
    if playbook == "news_event":
        return "entry:news_event"
    if playbook in MAJOR_PLAYBOOKS:
        return f"entry:playbook:{playbook}"
    strategy_path = str(context.get("strategy_path") or "").lower()
    if strategy_path == "fast":
        return "entry:fast_microstructure"
    if strategy_path == "minute":
        return "entry:minute"
    return "entry:all"


def scope_fallbacks(scope: str) -> list[str]:
    if scope == "entry:news_event":
        return [scope, "entry:minute", "entry:all"]
    if scope.startswith("entry:playbook:"):
        return [scope, "entry:minute", "entry:all"]
    if scope == "entry:fast_microstructure":
        return [scope, "entry:all"]
    return [scope, "entry:all"] if scope != "entry:all" else [scope]
