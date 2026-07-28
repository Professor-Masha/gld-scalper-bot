from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..database import Database
from .performance_report import performance_breakdown_from_database


def generate_daily_report(database: Database, report_date: date) -> str:
    start_local = datetime.combine(report_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))
    start = start_local.astimezone(ZoneInfo("UTC"))
    end = (start_local + timedelta(days=1)).astimezone(ZoneInfo("UTC"))
    report = performance_breakdown_from_database(database, start=start, end=end)
    summary = report["summary"]
    account = database.conn.execute(
        """
        SELECT * FROM account_snapshots WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp DESC, id DESC LIMIT 1
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    audit = database.conn.execute(
        """
        SELECT * FROM performance_consistency_audits WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp DESC, id DESC LIMIT 1
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    no_trades = database.conn.execute(
        """
        SELECT reason, COUNT(*) AS count FROM no_trade_logs
        WHERE timestamp >= ? AND timestamp < ? GROUP BY reason ORDER BY count DESC LIMIT 5
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    lines = [
        f"GLD Scalper Daily Performance Report - {report_date.isoformat()}",
        "",
        "Combined totals below include normal strategy and tagged paper exploration.",
        f"Combined root trading episodes: {summary['root_episode_count']}",
        f"Partial exit tranches: {summary['partial_exit_tranche_count']}",
        f"Gross P/L: {summary['gross_pnl']:.2f}",
        f"Estimated live-trading costs: {summary['estimated_live_cost']:.2f}",
        f"Net P/L after spread, slippage and fees: {summary['net_pnl_after_costs']:.2f}",
        f"Win rate: {summary['win_rate']:.2%}",
        f"Profit factor: {summary['profit_factor']:.2f}",
        f"Opportunity cost: {summary['opportunity_cost']:.2f}",
        f"Profit given back before exit: {summary['profit_given_back']:.2f}",
        f"Maximum favorable excursion: {summary['max_favorable_excursion']:.4%}",
        f"Maximum adverse excursion: {summary['max_adverse_excursion']:.4%}",
        f"Latest account equity: {float(account['equity'] or 0):.2f}" if account else "Latest account equity: unavailable",
        f"Latest realized P/L: {float(account['realized_pl'] or 0):.2f}" if account else "Latest realized P/L: unavailable",
        f"Latest unrealized P/L: {float(account['unrealized_pl'] or 0):.2f}" if account else "Latest unrealized P/L: unavailable",
        f"Session drawdown: {float(account['drawdown'] or 0):.2f} ({float(account['drawdown_pct'] or 0):.2%})" if account else "Session drawdown: unavailable",
        f"End-of-session consistency: {'PASS' if audit and audit['consistent'] else 'NOT CONFIRMED'}",
        f"Normal-strategy summary: {json.dumps(report['normal_strategy_summary'], sort_keys=True)}",
        f"Paper-exploration summary: {json.dumps(report['exploration_summary'], sort_keys=True)}",
        f"By evidence lane: {json.dumps(report['by_evidence_lane'], sort_keys=True)}",
        f"By direction: {json.dumps(report['by_direction'], sort_keys=True)}",
        f"By strategy path: {json.dumps(report['by_strategy_path'], sort_keys=True)}",
        f"By playbook: {json.dumps(report['by_playbook'], sort_keys=True)}",
        f"By regime: {json.dumps(report['by_regime'], sort_keys=True)}",
        f"By New York hour: {json.dumps(report['by_new_york_hour'], sort_keys=True)}",
        f"By exit reason: {json.dumps(report['by_exit_reason'], sort_keys=True)}",
        f"By ML prediction: {json.dumps(report['by_ml_prediction'], sort_keys=True)}",
        f"By confidence band: {json.dumps(report['by_confidence_band'], sort_keys=True)}",
        f"Top no-trade reasons: {json.dumps([dict(row) for row in no_trades], default=str)}",
    ]
    return "\n".join(lines)
