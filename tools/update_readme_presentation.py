from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

DIAGRAMS = {
    "### Complete Runtime Architecture": (
        "![GLD Scalper Bot complete runtime architecture]"
        "(docs/architecture/runtime-architecture.jpg)"
    ),
    "### Decision And Order Flow": (
        "![GLD Scalper Bot decision and order flow]"
        "(docs/architecture/decision-and-order-flow.jpg)"
    ),
    "### Learning And Promotion Architecture": (
        "![GLD Scalper Bot training, validation, and promotion flow]"
        "(docs/architecture/training-validation-promotion.jpg)"
    ),
}

SYMBOL_GUIDE = r"""
#### How To Read The Equations

GitHub renders each display equation below as centered mathematical notation.
Every equation family is followed by an explanation of its variables and its
role in the bot. Subscripts are time indexes, not multiplication.

**Common symbols**

- **\(t\):** the decision timestamp. A value with subscript \(t\) must be known
  at that timestamp; otherwise it would leak future information.
- **\(h\):** a forward horizon such as 1, 3, 5, or 15 minutes.
- **\(n\):** the number of historical observations in a rolling lookback.
- **\(P_t\):** the selected price at time \(t\), normally close or midpoint
  depending on the feature.
- **\(O_t,H_t,L_t,C_t\):** open, high, low, and close for the bar ending at
  time \(t\).
- **\(V_t\):** traded volume for the observation ending at \(t\).
- **\(\sum\):** add all indexed observations in the stated range.
- **\(\max\) and \(\min\):** select the largest or smallest candidate value.
- **\(\mu\) and \(\sigma\):** arithmetic mean and standard deviation.
- **\(\epsilon\):** a small positive number used to prevent division by zero.
- **\(\hat{p}\):** an estimated, calibrated probability rather than a
  guaranteed outcome.

**Important interpretation:** an indicator equation creates evidence. It does
not create broker authority. A trade still needs a confirmed playbook, fresh
data, acceptable microstructure, risk approval, broker reconciliation, and an
idempotent protected order intent.
""".strip()


def replace_mermaid_after_heading(text: str, heading: str, image: str) -> str:
    pattern = re.compile(
        rf"({re.escape(heading)}\s*\n\n)```mermaid\n.*?\n```",
        flags=re.DOTALL,
    )
    updated, count = pattern.subn(
        rf"\1{image}\n\n"
        "The editable renderer is "
        "[tools/render_architecture_diagrams.py]"
        "(tools/render_architecture_diagrams.py). Regenerate the JPEG whenever "
        "architecture labels or connections change.",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Expected one Mermaid block after {heading!r}; found {count}.")
    return updated


def main() -> None:
    text = README.read_text(encoding="utf-8")
    for heading, image in DIAGRAMS.items():
        text = replace_mermaid_after_heading(text, heading, image)

    text = re.sub(r"(?m)^\\\[$", "$$", text)
    text = re.sub(r"(?m)^\\\]$", "$$", text)

    marker = "### Core Market Mathematics\n"
    if SYMBOL_GUIDE not in text:
        text = text.replace(marker, f"{marker}\n{SYMBOL_GUIDE}\n", 1)

    README.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
