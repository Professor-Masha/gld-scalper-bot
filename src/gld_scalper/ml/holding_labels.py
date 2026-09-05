from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence


@dataclass(slots=True, frozen=True)
class HoldingLabel:
    action: str
    barrier: str
    time_to_barrier_seconds: float
    remaining_net_return: float
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float
    profit_given_back: float

    def as_dict(self) -> dict[str, float | str]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class EntryLabel:
    label: str
    barrier: str
    time_to_barrier_seconds: float
    terminal_return: float
    maximum_upside: float
    maximum_downside: float

    def as_dict(self) -> dict[str, float | str]:
        return asdict(self)


def triple_barrier_entry_label(
    *,
    current_price: float,
    future_prices: Sequence[float],
    seconds_per_observation: float,
    profit_barrier_pct: float,
    risk_barrier_pct: float,
    round_trip_cost_pct: float,
) -> EntryLabel:
    """Label direction from the first economically meaningful barrier touched."""

    path = [float(price) / max(float(current_price), 1e-12) - 1.0 for price in future_prices]
    long_barrier = max(float(profit_barrier_pct), float(round_trip_cost_pct))
    short_barrier = max(float(risk_barrier_pct), float(round_trip_cost_pct))
    label, barrier, barrier_index = "no_trade", "time", max(len(path) - 1, 0)
    for index, value in enumerate(path):
        if value >= long_barrier:
            label, barrier, barrier_index = "long_good", "upper", index
            break
        if value <= -short_barrier:
            label, barrier, barrier_index = "short_good", "lower", index
            break
    terminal = path[barrier_index] if path else 0.0
    if barrier == "time":
        net_terminal = abs(terminal) - max(float(round_trip_cost_pct), 0.0)
        if net_terminal > 0:
            label = "long_good" if terminal > 0 else "short_good"
    return EntryLabel(
        label=label,
        barrier=barrier,
        time_to_barrier_seconds=float((barrier_index + 1) * seconds_per_observation),
        terminal_return=terminal,
        maximum_upside=max([0.0, *path]),
        maximum_downside=abs(min([0.0, *path])),
    )


def triple_barrier_holding_label(
    *,
    direction: str,
    current_price: float,
    future_prices: Sequence[float],
    seconds_per_observation: float,
    profit_barrier_pct: float,
    risk_barrier_pct: float,
    round_trip_cost_pct: float,
    invalidated: bool = False,
) -> HoldingLabel:
    """Create a path-aware HOLD/REDUCE/CLOSE label without looking past its horizon."""

    sign = 1.0 if direction.upper() == "LONG" else -1.0
    path = [sign * (float(price) / max(float(current_price), 1e-12) - 1.0) for price in future_prices]
    mfe = max([0.0, *path])
    mae = abs(min([0.0, *path]))
    barrier = "time"
    barrier_index = max(len(path) - 1, 0)
    for index, value in enumerate(path):
        if value >= profit_barrier_pct:
            barrier, barrier_index = "profit", index
            break
        if value <= -risk_barrier_pct:
            barrier, barrier_index = "risk", index
            break
    terminal = path[barrier_index] if path else 0.0
    remaining_net = terminal - max(round_trip_cost_pct, 0.0)
    giveback = max(0.0, mfe - max(terminal, 0.0))
    if invalidated or barrier == "risk":
        action = "close"
    elif barrier == "profit" and giveback >= max(mfe * 0.35, round_trip_cost_pct):
        action = "reduce"
    elif remaining_net > max(round_trip_cost_pct * 0.5, 0.00005):
        action = "hold"
    elif mfe > round_trip_cost_pct and giveback > mfe * 0.50:
        action = "reduce"
    else:
        action = "close"
    return HoldingLabel(
        action=action,
        barrier=barrier,
        time_to_barrier_seconds=float((barrier_index + 1) * seconds_per_observation),
        remaining_net_return=remaining_net,
        maximum_favorable_excursion=mfe,
        maximum_adverse_excursion=mae,
        profit_given_back=giveback,
    )
