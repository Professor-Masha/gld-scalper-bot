from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .decision_explanation import explain_decision


GRAPH_TYPES = frozenset({"decision", "market", "model", "trade", "playbook", "dataset", "training", "llm", "risk"})
GRAPH_COLORS = {
    "decision": "#2de1ff",
    "market": "#2de1ff",
    "model": "#8b7cff",
    "trade": "#5cf2b5",
    "playbook": "#ffbf69",
    "dataset": "#3f7cff",
    "training": "#51a8ff",
    "llm": "#b56dff",
    "risk": "#ff4d6d",
}


class MemoryGraphRepository:
    """Build a bounded, cached and strictly read-only map of learned evidence."""

    def __init__(
        self,
        project_root: Path,
        database_path: Callable[[], Path],
        *,
        ttl_seconds: float = 15.0,
        maximum_nodes: int = 180,
    ) -> None:
        self.project_root = project_root.resolve()
        self.database_path = database_path
        self.ttl_seconds = max(float(ttl_seconds), 1.0)
        self.maximum_nodes = max(40, min(int(maximum_nodes), 400))
        self._cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._detail_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def graph(self, *, types: set[str] | None = None, window: str = "30d") -> dict[str, Any]:
        selected = GRAPH_TYPES if not types else frozenset(types) & GRAPH_TYPES
        if not selected:
            selected = GRAPH_TYPES
        if window not in {"1d", "7d", "30d", "90d", "all"}:
            raise ValueError("memory graph window must be 1d, 7d, 30d, 90d, or all")
        path = self.database_path().resolve()
        # Live trading writes to SQLite continuously. A file-mtime cache key would
        # therefore rebuild the graph on every dashboard poll and defeat the cache.
        key = (str(path), tuple(sorted(selected)), window)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.ttl_seconds:
                return {**cached[1], "cache": {"hit": True, "age_seconds": round(now - cached[0], 3), "ttl_seconds": self.ttl_seconds}}
        payload = self._build(path, selected, window)
        with self._lock:
            self._cache = {
                cache_key: value
                for cache_key, value in self._cache.items()
                if now - value[0] < self.ttl_seconds
            }
            self._cache[key] = (now, payload)
        return {**payload, "cache": {"hit": False, "age_seconds": 0.0, "ttl_seconds": self.ttl_seconds}}

    def _build(self, path: Path, types: frozenset[str], window: str) -> dict[str, Any]:
        generated = datetime.now(timezone.utc)
        if not path.exists():
            return self._payload([], [], [], generated, path, types, window, "database unavailable")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        cutoff = _cutoff(window, generated)
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        timeline: list[dict[str, Any]] = []

        def node(identifier: str, kind: str, label: str, **details: Any) -> str:
            if kind not in types or len(nodes) >= self.maximum_nodes:
                return identifier
            status = str(details.pop("status", "available") or "available")
            color = _status_color(kind, status, details)
            clean_details = _clean(details)
            nodes.setdefault(identifier, {
                "id": identifier, "type": kind, "label": label, "status": status,
                "color": color, "size": _node_size(kind, details),
                "subtitle": _subtitle(kind, clean_details), "preview": _preview(kind, clean_details),
            })
            return identifier

        def edge(source: str, target: str, relation: str, *, weight: float = 1.0, active: bool = False) -> None:
            if source not in nodes or target not in nodes:
                return
            identifier = f"{source}|{relation}|{target}"
            edges.setdefault(identifier, {
                "id": identifier, "source": source, "target": target, "relation": relation,
                "weight": max(0.1, min(float(weight), 5.0)), "active": bool(active),
            })

        try:
            signal = _one(connection, "SELECT * FROM signals ORDER BY timestamp DESC,id DESC LIMIT 1")
            if signal and "decision" in types:
                features = _json(signal.get("feature_snapshot_json"))
                node("decision:current", "decision", str(signal.get("decision") or "NO_TRADE"),
                     status="current", timestamp=signal.get("timestamp"), confidence=signal.get("confidence"),
                     bullish_score=signal.get("bullish_score"), bearish_score=signal.get("bearish_score"),
                     no_trade_score=signal.get("no_trade_score"), regime=signal.get("regime"),
                     reason=signal.get("reason"), model_version=signal.get("model_version"), agent_votes=features.get("agent_votes"),
                     expected_net_edge=features.get("ml_expected_net_edge"), expected_cost=features.get("ml_expected_cost"))
                if "market" in types:
                    node("market:gld", "market", "GLD live state", status="live",
                         regime=signal.get("regime"), spread_pct=features.get("spread_pct"),
                         liquidity_score=features.get("liquidity_score"), quote_age_seconds=features.get("quote_age_seconds"),
                         trade_age_seconds=features.get("trade_age_seconds"), volatility_burst=features.get("volatility_burst"))
                    edge("market:gld", "decision:current", "informs", weight=2.0, active=True)
                model_version = str(signal.get("model_version") or "")
                if model_version:
                    edge(f"model:{model_version}", "decision:current", "advises", weight=2.2, active=True)

            model_limit = 60 if len(types) <= 4 else 24
            model_rows = _all(connection, f"""SELECT * FROM model_versions ORDER BY
                CASE status WHEN 'champion' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END, created_at DESC LIMIT {model_limit}""")
            manifests = self._manifests()
            model_parents: list[tuple[str, str]] = []
            registered_models: set[str] = set()
            for row in model_rows:
                version = str(row.get("model_version"))
                registered_models.add(version)
                metrics = _json(row.get("metrics_json"))
                thresholds = _json(row.get("thresholds_json"))
                parameters = _json(row.get("model_parameters_json"))
                features = _json(row.get("feature_columns_json"))
                manifest = manifests.get(version, {})
                identifier = node(f"model:{version}", "model", _compact(version, 34),
                    status=row.get("status"), model_version=version, model_type=row.get("model_type"),
                    scope=row.get("model_scope"), feature_profile=row.get("feature_profile"),
                    parent_champion=row.get("parent_champion_version"), training_start=row.get("training_start"),
                    training_end=row.get("training_end"), training_data_start=row.get("training_data_start"),
                    training_data_end=row.get("training_data_end"), sample_count=metrics.get("sample_count") or metrics.get("total_samples"),
                    feature_count=len(features) if isinstance(features, list) else None,
                    selected_features=features, hyperparameters=parameters, calibration=metrics.get("calibration"),
                    expected_calibration_error=metrics.get("expected_calibration_error"), thresholds=thresholds,
                    rejection_reason=row.get("rejection_reason"), artifact_path=row.get("path"),
                    artifact_fingerprint=row.get("artifact_fingerprint"))
                parent = str(row.get("parent_champion_version") or "")
                if parent:
                    model_parents.append((parent, identifier))
                _timeline(timeline, row.get("created_at"), "model", f"{version} registered", identifier, row.get("status"))

            for version, manifest in list(manifests.items())[:model_limit]:
                if version in registered_models:
                    continue
                identifier = node(f"model:{version}", "model", _compact(version, 34),
                    status=manifest.get("status") or "archived", model_version=version,
                    model_type=manifest.get("model_type") or manifest.get("architecture"),
                    scope=manifest.get("model_scope") or manifest.get("scope"),
                    parent_champion=manifest.get("parent_champion_version"),
                    training_start=manifest.get("training_start"), training_end=manifest.get("training_end"),
                    sample_count=manifest.get("sample_count"), selected_features=manifest.get("feature_columns"),
                    hyperparameters=manifest.get("hyperparameters") or manifest.get("model_parameters"),
                    calibration=manifest.get("calibration"), rejection_reason=manifest.get("rejection_reason"),
                    artifact_path=manifest.get("artifact_path") or manifest.get("path"),
                    artifact_fingerprint=manifest.get("artifact_fingerprint") or manifest.get("fingerprint"))
                parent = str(manifest.get("parent_champion_version") or "")
                if parent:
                    model_parents.append((parent, identifier))
            for parent, child in model_parents:
                edge(f"model:{parent}", child, "parent_of", weight=1.8)

            training_limit = 60 if len(types) <= 4 else 20
            training_rows = _all(connection, f"""SELECT * FROM ml_training_experiments
                ORDER BY COALESCE(completed_at,started_at,created_at) DESC LIMIT {training_limit}""")
            for row in training_rows:
                identifier = node(f"training:{row.get('id')}", "training", f"{row.get('playbook')} / {row.get('horizon_minutes')}m",
                    status=row.get("status"), experiment_key=row.get("experiment_key"), playbook=row.get("playbook"),
                    horizon_minutes=row.get("horizon_minutes"), sample_count=row.get("sample_count"),
                    paper_sample_count=row.get("paper_sample_count"), started_at=row.get("started_at"),
                    completed_at=row.get("completed_at"), error=row.get("error_message"))
                candidate = str(row.get("candidate_version") or "")
                if candidate:
                    edge(identifier, f"model:{candidate}", "produced", weight=2.0)
                dataset = str(row.get("dataset_fingerprint") or "")
                if dataset:
                    dataset_id = node(f"dataset:fingerprint:{dataset}", "dataset", f"Training archive {_compact(dataset, 12)}",
                                      status="available", fingerprint=dataset, artifact_path=row.get("artifact_path"))
                    edge(dataset_id, identifier, "trained", weight=1.6)
                _timeline(timeline, row.get("completed_at") or row.get("started_at"), "training",
                          f"{row.get('playbook')} {row.get('status')}", identifier, row.get("status"))

            source_limit = 40 if len(types) <= 4 else 15
            source_rows = _all(connection, f"SELECT * FROM data_source_runs ORDER BY timestamp DESC,id DESC LIMIT {source_limit}")
            for row in source_rows:
                if cutoff and not _recent(row.get("timestamp"), cutoff):
                    continue
                identifier = node(f"dataset:run:{row.get('id')}", "dataset", f"{row.get('source')} / {row.get('data_type')}",
                    status=row.get("status"), source=row.get("source"), data_type=row.get("data_type"),
                    strategy_path=row.get("strategy_path"), playbook=row.get("playbook"), rows=row.get("rows_inserted"),
                    start=row.get("start_time"), end=row.get("end_time"), timestamp=row.get("timestamp"), message=row.get("message"))
                _timeline(timeline, row.get("timestamp"), "dataset", str(row.get("data_type")), identifier, row.get("status"))

            trade_limit = 100 if len(types) <= 4 else 40
            trade_rows = _all(connection, f"""SELECT * FROM trade_outcomes
                ORDER BY COALESCE(exit_time,entry_time) DESC,id DESC LIMIT {trade_limit}""")
            for row in trade_rows:
                timestamp = row.get("exit_time") or row.get("entry_time")
                if cutoff and not _recent(timestamp, cutoff):
                    continue
                trade_id = str(row.get("trade_id") or row.get("id"))
                pnl = _number(row.get("net_pnl_after_costs"))
                identifier = node(f"trade:{trade_id}", "trade", f"{row.get('direction')} {row.get('playbook') or 'unclassified'}",
                    status="win" if pnl > 0 else "loss" if pnl < 0 else "flat", trade_id=trade_id,
                    direction=row.get("direction"), strategy_path=row.get("strategy_path"), playbook=row.get("playbook"),
                    entry_time=row.get("entry_time"), exit_time=row.get("exit_time"), entry_price=row.get("entry_price"),
                    exit_price=row.get("exit_price"), quantity=row.get("qty"), gross_pnl=row.get("gross_pnl"),
                    net_pnl_after_costs=row.get("net_pnl_after_costs"), spread_cost=row.get("spread_cost"),
                    slippage_cost=row.get("slippage_cost"), estimated_fees=row.get("estimated_fees"),
                    expected_net_edge=None, model_prediction=row.get("ml_prediction"), confidence=row.get("confidence"),
                    mfe=row.get("max_favorable_excursion"), mae=row.get("max_adverse_excursion"),
                    profit_given_back=row.get("profit_given_back"), close_reason=row.get("exit_reason"),
                    training_label=row.get("win_loss"), regime=row.get("regime"))
                model = str(row.get("model_version") or "")
                if model:
                    edge(f"model:{model}", identifier, "predicted", weight=1.4)
                playbook = str(row.get("playbook") or "unclassified")
                playbook_id = node(f"playbook:{playbook}", "playbook", playbook.replace("_", " ").title(), status="observed")
                edge(playbook_id, identifier, "executed_as", weight=1.2)
                _timeline(timeline, timestamp, "trade", f"{row.get('direction')} {playbook}: {pnl:+.2f}", identifier,
                          "win" if pnl > 0 else "loss" if pnl < 0 else "flat")

            review_limit = 30 if len(types) <= 4 else 15
            review_rows = _all(connection, f"SELECT * FROM llm_reviews ORDER BY timestamp DESC,id DESC LIMIT {review_limit}")
            for row in review_rows:
                if cutoff and not _recent(row.get("timestamp"), cutoff):
                    continue
                identifier = node(f"llm:{row.get('id')}", "llm", str(row.get("review_type") or "LLM review"),
                    status="advisory", timestamp=row.get("timestamp"), summary=row.get("summary"),
                    bull_case=row.get("bull_case"), bear_case=row.get("bear_case"), risk_critique=row.get("risk_critique"),
                    execution_critique=row.get("execution_critique"), journal_review=row.get("journal_review"),
                    broker_authority=False)
                _timeline(timeline, row.get("timestamp"), "llm", str(row.get("review_type")), identifier, "advisory")

            if "risk" in types:
                safety = _one(connection, "SELECT * FROM execution_safety_events ORDER BY timestamp DESC,id DESC LIMIT 1")
                if safety:
                    risk_id = node("risk:latest", "risk", "Latest execution safety", status=safety.get("severity") or "observed", **safety)
                    edge(risk_id, "decision:current", "can_veto", weight=2.5, active=True)
        finally:
            connection.close()

        # A referenced model may be inserted after its edge was first seen.
        if "decision:current" in nodes and signal:
            model = str(signal.get("model_version") or "")
            if model:
                edge(f"model:{model}", "decision:current", "advises", weight=2.2, active=True)
        return self._payload(list(nodes.values()), list(edges.values()), timeline, generated, path, types, window, None)

    def node_detail(self, node_id: str) -> dict[str, Any]:
        """Return bounded human-readable evidence for one selected graph node."""
        if not node_id or len(node_id) > 220 or ":" not in node_id:
            raise ValueError("invalid memory node id")
        path = self.database_path().resolve()
        key = (str(path), node_id)
        now = time.monotonic()
        with self._lock:
            cached = self._detail_cache.get(key)
            if cached and now - cached[0] < 30.0:
                return {**cached[1], "cache": {"hit": True}}
        result = self._build_detail(path, node_id)
        with self._lock:
            self._detail_cache[key] = (now, result)
            self._detail_cache = {k: v for k, v in self._detail_cache.items() if now - v[0] < 60.0}
        return {**result, "cache": {"hit": False}}

    def _build_detail(self, path: Path, node_id: str) -> dict[str, Any]:
        kind, identity = node_id.split(":", 1)
        if kind not in GRAPH_TYPES or not path.exists():
            raise ValueError("memory node is unavailable")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            row: dict[str, Any] | None = None
            if node_id == "decision:current":
                row = _one(connection, "SELECT * FROM signals ORDER BY timestamp DESC,id DESC LIMIT 1")
            elif node_id == "market:gld":
                row = _one(connection, "SELECT * FROM signals ORDER BY timestamp DESC,id DESC LIMIT 1")
            elif kind == "model":
                row = _one_param(connection, "SELECT * FROM model_versions WHERE model_version=? LIMIT 1", identity)
            elif kind == "trade":
                row = _one_param(connection, "SELECT * FROM trade_outcomes WHERE CAST(COALESCE(trade_id,id) AS TEXT)=? LIMIT 1", identity)
            elif kind == "training":
                row = _one_param(connection, "SELECT * FROM ml_training_experiments WHERE CAST(id AS TEXT)=? LIMIT 1", identity)
            elif node_id.startswith("dataset:run:"):
                row = _one_param(connection, "SELECT * FROM data_source_runs WHERE CAST(id AS TEXT)=? LIMIT 1", node_id.rsplit(":", 1)[-1])
            elif kind == "llm":
                row = _one_param(connection, "SELECT * FROM llm_reviews WHERE CAST(id AS TEXT)=? LIMIT 1", identity)
            elif node_id == "risk:latest":
                row = _one(connection, "SELECT * FROM execution_safety_events ORDER BY timestamp DESC,id DESC LIMIT 1")
            elif kind == "playbook":
                row = _one_param(connection, "SELECT playbook,COUNT(*) samples,SUM(CASE WHEN net_pnl_after_costs>0 THEN 1 ELSE 0 END) wins,SUM(COALESCE(net_pnl_after_costs,0)) net_pnl,AVG(COALESCE(net_pnl_after_costs,0)) average_pnl FROM trade_outcomes WHERE COALESCE(playbook,'unclassified')=? GROUP BY playbook", identity)
            if row is None:
                return _detail_payload(node_id, kind, identity, "No persisted detail is available for this node.", {})
            return _human_detail(node_id, kind, row)
        finally:
            connection.close()

    def _manifests(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        roots = [self.project_root / "models" / "paper" / "manifests", self.project_root / "models" / "live" / "manifests"]
        paths = list(path for root in roots if root.exists() for path in root.glob("*.json"))
        paths.sort(key=_modified_time, reverse=True)
        paths = paths[:80]
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                version = str(payload.get("model_version") or path.stem)
                result[version] = payload
            except (OSError, json.JSONDecodeError):
                continue
        return result

    @staticmethod
    def _payload(nodes, edges, timeline, generated, path, types, window, warning) -> dict[str, Any]:
        timeline.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        topology = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"), default=str)
        layout = json.dumps({"nodes": [node.get("id") for node in nodes],
                             "edges": [(edge.get("source"), edge.get("target")) for edge in edges]},
                            sort_keys=True, separators=(",", ":"), default=str)
        return {
            "schema_version": "memory-graph.v2", "generated_at": generated.isoformat(),
            "topology_fingerprint": hashlib.sha256(topology.encode("utf-8")).hexdigest(),
            "layout_fingerprint": hashlib.sha256(layout.encode("utf-8")).hexdigest(),
            "database": str(path), "read_only": True, "raw_market_tables_queried": False,
            "filters": {"types": sorted(types), "window": window},
            "limits": {"nodes": len(nodes), "edges": len(edges), "timeline": min(len(timeline), 100)},
            "nodes": nodes, "edges": edges, "timeline": timeline[:100], "warning": warning,
        }


def _one(connection: sqlite3.Connection, sql: str) -> dict[str, Any] | None:
    try:
        row = connection.execute(sql).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def _one_param(connection: sqlite3.Connection, sql: str, value: str) -> dict[str, Any] | None:
    try:
        row = connection.execute(sql, (value,)).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def _all(connection: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in connection.execute(sql).fetchall()]
    except sqlite3.OperationalError:
        return []


def _json(value: Any) -> Any:
    if not isinstance(value, str):
        return value if value is not None else {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean(item) for item in value[:120]]
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        return None
    return value


def _status_color(kind: str, status: str, details: dict[str, Any]) -> str:
    normalized = status.lower()
    if normalized in {"loss", "rejected", "failed", "error", "critical", "halted"}:
        return "#ff4d6d"
    if normalized in {"champion", "win", "completed", "promoted", "live", "current"}:
        return "#5cf2b5" if kind not in {"decision", "market"} else "#2de1ff"
    if normalized in {"candidate", "pending", "running", "uncertain", "degraded"}:
        return "#ffbf69"
    return GRAPH_COLORS.get(kind, "#84939a")


def _node_size(kind: str, details: dict[str, Any]) -> float:
    base = 10.0 if kind == "decision" else 7.0
    evidence = _number(details.get("sample_count") or details.get("rows") or 0.0)
    return min(15.0, base + max(0.0, evidence) ** 0.25 * 0.18)


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cutoff(window: str, now: datetime) -> datetime | None:
    days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}.get(window)
    return now - timedelta(days=days) if days else None


def _recent(value: Any, cutoff: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) >= cutoff
    except (TypeError, ValueError):
        return False


def _timeline(items: list[dict[str, Any]], timestamp: Any, kind: str, label: str, node_id: str, status: Any) -> None:
    if timestamp:
        items.append({"timestamp": timestamp, "type": kind, "label": label, "node_id": node_id, "status": status})


def _compact(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 3] + "..."


def _modified_time(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _subtitle(kind: str, details: dict[str, Any]) -> str:
    candidates = {
        "decision": ("regime", "timestamp"), "model": ("model_type", "scope"),
        "trade": ("close_reason", "exit_time"), "training": ("status", "completed_at"),
        "dataset": ("data_type", "timestamp"), "llm": ("timestamp",), "risk": ("timestamp",),
        "playbook": ("status",), "market": ("regime",),
    }.get(kind, ())
    return " | ".join(str(details[key]) for key in candidates if details.get(key))[:150]


def _preview(kind: str, details: dict[str, Any]) -> str:
    keys = {
        "decision": ("reason",), "model": ("sample_count", "expected_calibration_error"),
        "trade": ("net_pnl_after_costs", "confidence"), "training": ("sample_count", "error"),
        "dataset": ("rows", "message"), "llm": ("summary",), "risk": ("message", "event_type"),
        "market": ("spread_pct", "liquidity_score"),
    }.get(kind, ())
    values = [f"{key.replace('_', ' ')}: {_display(details[key])}" for key in keys if details.get(key) is not None]
    return " | ".join(values)[:280]


def _human_detail(node_id: str, kind: str, row: dict[str, Any]) -> dict[str, Any]:
    identity = node_id.split(":", 1)[-1]
    decoded = {key: _json(value) if key.endswith("_json") else value for key, value in row.items()}
    if kind in {"decision", "market"}:
        decoded["explanation"] = explain_decision(decoded)
    title = {
        "decision": f"Latest decision: {str(decoded.get('decision') or 'NO_TRADE').replace('_', ' ')}",
        "market": "GLD live market state",
        "model": str(decoded.get("model_version") or node_id),
        "trade": f"{decoded.get('direction') or 'Trade'} / {decoded.get('playbook') or 'unclassified'}",
        "training": f"Training / {decoded.get('playbook') or identity}",
        "dataset": f"Dataset / {decoded.get('data_type') or identity}",
        "llm": str(decoded.get("review_type") or "LLM research review"),
        "risk": "Latest execution safety event",
        "playbook": str(decoded.get("playbook") or identity).replace("_", " ").title(),
    }.get(kind, node_id)
    sections: list[dict[str, Any]] = []
    groups = {
        "Identity": ("timestamp", "status", "decision", "direction", "playbook", "model_version", "model_type", "model_scope"),
        "Evidence": ("confidence", "bullish_score", "bearish_score", "no_trade_score", "regime", "reason", "sample_count", "paper_sample_count"),
        "Performance": ("net_pnl_after_costs", "gross_pnl", "wins", "samples", "average_pnl", "max_favorable_excursion", "max_adverse_excursion", "exit_reason"),
        "Training lineage": ("parent_champion_version", "training_start", "training_end", "training_data_start", "training_data_end", "artifact_fingerprint", "path"),
        "Review": ("summary", "bull_case", "bear_case", "risk_critique", "execution_critique", "journal_review"),
    }
    explanation = decoded.get("explanation")
    if isinstance(explanation, dict):
        fields = [{"label": "Plain-language result", "value": explanation.get("headline")}, {"label": "Primary reason", "value": explanation.get("summary")}]
        for group in explanation.get("groups", []):
            fields.append({"label": group.get("title"), "value": "\n".join(item.get("message", "") for item in group.get("items", []))})
        sections.append({"title": "Decision explanation", "fields": fields})
    for section, keys in groups.items():
        fields = [{"label": key.replace("_", " ").title(), "value": _display(decoded[key])} for key in keys if decoded.get(key) not in (None, "", {}, [])]
        if fields:
            sections.append({"title": section, "fields": fields})
    summary = explanation.get("headline") if isinstance(explanation, dict) else _preview(kind, decoded)
    return _detail_payload(node_id, kind, title, summary, {"sections": sections})


def _detail_payload(node_id: str, kind: str, title: str, summary: str, extra: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "memory-node.v1", "id": node_id, "type": kind, "title": title,
            "summary": summary or "Persisted bot memory and audit evidence.", "read_only": True, **extra}


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.6g}"
    if isinstance(value, dict):
        scalar = [f"{str(k).replace('_', ' ')}: {_display(v)}" for k, v in list(value.items())[:12] if not isinstance(v, (dict, list))]
        return " | ".join(scalar) if scalar else f"{len(value)} structured values"
    if isinstance(value, list):
        return ", ".join(_display(item) for item in value[:12]) + (" ..." if len(value) > 12 else "")
    return str(value)[:1200]
