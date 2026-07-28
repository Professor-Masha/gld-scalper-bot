from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


HORIZONS_MINUTES = (1, 3, 5, 15)
ENTRY_CLASSES = ("long_good", "short_good", "no_trade")
EXIT_CLASSES = ("hold", "reduce", "close")


def require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised without the optional dependency
        raise RuntimeError(
            "Transformer support requires the project transformer dependencies. "
            "Install them with: .\\.venv\\Scripts\\python.exe -m pip install -e .[transformer]"
        ) from exc
    return torch


@dataclass(slots=True, frozen=True)
class TransformerModelConfig:
    feature_count: int
    class_count: int = 3
    sequence_length: int = 90
    d_model: int = 48
    nhead: int = 4
    num_layers: int = 2
    dim_feedforward: int = 128
    dropout: float = 0.10
    horizon_count: int = len(HORIZONS_MINUTES)

    def validate(self) -> None:
        if self.feature_count < 1:
            raise ValueError("feature_count must be positive")
        if self.class_count < 2:
            raise ValueError("class_count must be at least two")
        if self.sequence_length < 2:
            raise ValueError("sequence_length must be at least two")
        if self.d_model not in {32, 48, 64}:
            raise ValueError("d_model must be 32, 48, or 64 for the bounded laptop profile")
        if self.nhead != 4:
            raise ValueError("nhead must remain four for the approved architecture")
        if self.num_layers not in {2, 3}:
            raise ValueError("num_layers must be two or three")
        if self.d_model % self.nhead:
            raise ValueError("d_model must be divisible by nhead")
        if not 0.0 <= self.dropout <= 0.5:
            raise ValueError("dropout must be between zero and 0.5")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_causal_transformer(config: TransformerModelConfig):
    """Build the PyTorch module lazily so the rule/RF bot works without Torch."""

    config.validate()
    torch = require_torch()
    nn = torch.nn

    class CausalTimeSeriesTransformer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            # Missingness is explicit: each value is paired with a missing-value bit.
            self.input_projection = nn.Linear(config.feature_count * 2, config.d_model)
            layer = nn.TransformerEncoderLayer(
                d_model=config.d_model,
                nhead=config.nhead,
                dim_feedforward=config.dim_feedforward,
                dropout=config.dropout,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(
                layer,
                num_layers=config.num_layers,
                norm=nn.LayerNorm(config.d_model),
                enable_nested_tensor=False,
            )
            self.dropout = nn.Dropout(config.dropout)
            self.classification_head = nn.Linear(config.d_model, config.class_count)
            self.return_head = nn.Linear(config.d_model, config.horizon_count)
            self.cost_head = nn.Linear(config.d_model, 1)
            self.log_variance_head = nn.Linear(config.d_model, config.horizon_count)
            self.register_buffer("position_encoding", _sinusoidal_encoding(torch, config))
            self.register_buffer("temperature", torch.ones(1, dtype=torch.float32))

        def set_temperature(self, value: float) -> None:
            self.temperature.fill_(max(float(value), 0.05))

        def forward(self, values, missing_mask, valid_mask, session_mask):
            sequence_length = values.shape[1]
            observed = torch.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
            combined = torch.cat((observed, missing_mask.to(dtype=observed.dtype)), dim=-1)
            encoded = self.input_projection(combined)
            encoded = encoded + self.position_encoding[:, :sequence_length, :]

            usable = valid_mask.to(dtype=torch.bool) & session_mask.to(dtype=torch.bool)
            # The data loader guarantees at least one usable point, but this guard also
            # keeps exported inference stable if a malformed caller supplies all padding.
            empty = ~usable.any(dim=1)
            if empty.any():
                usable = usable.clone()
                usable[empty, :] = True
            padding_mask = ~usable
            causal_mask = torch.triu(
                torch.ones((sequence_length, sequence_length), dtype=torch.bool, device=values.device),
                diagonal=1,
            )
            encoded = self.encoder(encoded, mask=causal_mask, src_key_padding_mask=padding_mask)

            positions = torch.arange(sequence_length, device=values.device).unsqueeze(0)
            last_indices = positions.masked_fill(~usable, -1).max(dim=1).values
            batch_indices = torch.arange(values.shape[0], device=values.device)
            pooled = self.dropout(encoded[batch_indices, last_indices])

            logits = self.classification_head(pooled) / self.temperature.clamp_min(0.05)
            expected_returns = self.return_head(pooled)
            expected_cost = torch.nn.functional.softplus(self.cost_head(pooled))
            log_variance = self.log_variance_head(pooled).clamp(min=-12.0, max=4.0)
            return logits, expected_returns, expected_cost, log_variance

    return CausalTimeSeriesTransformer()


def _sinusoidal_encoding(torch, config: TransformerModelConfig):
    position = torch.arange(config.sequence_length, dtype=torch.float32).unsqueeze(1)
    divisor = torch.exp(
        torch.arange(0, config.d_model, 2, dtype=torch.float32)
        * (-math.log(10_000.0) / config.d_model)
    )
    encoding = torch.zeros((1, config.sequence_length, config.d_model), dtype=torch.float32)
    encoding[0, :, 0::2] = torch.sin(position * divisor)
    encoding[0, :, 1::2] = torch.cos(position * divisor)
    return encoding
