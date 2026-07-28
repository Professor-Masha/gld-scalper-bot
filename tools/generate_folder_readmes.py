from __future__ import annotations

import ast
import subprocess
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FOLDERS = {
    ".githooks": (
        "Repository Hooks",
        "Local Git hooks that enforce repository hygiene before selected Git operations.",
    ),
    ".github": (
        "GitHub Configuration",
        "Repository-level GitHub automation and contribution infrastructure.",
    ),
    ".github/workflows": (
        "GitHub Actions",
        "Continuous-integration workflows that validate commits and pull requests.",
    ),
    "Knowledge": (
        "Trading Knowledge Library",
        "Human-authored strategy and research references used by developers and the offline RAG reviewer.",
    ),
    "docs": (
        "Project Documentation Assets",
        "Version-controlled visual and supporting documentation used by the root manual.",
    ),
    "docs/architecture": (
        "Architecture Diagrams",
        "Rendered JPEG views of runtime, execution, and learning architecture.",
    ),
    "models": (
        "Versioned Model Artifacts",
        "Approved and retained model memory. Raw training datasets do not belong here.",
    ),
    "models/paper": (
        "Paper Model Registry",
        "Versioned paper-training candidates, champions, checksums, metadata, and rollback artifacts.",
    ),
    "models/paper/manifests": (
        "Paper Model Manifests",
        "Immutable lineage and evaluation manifests paired with paper model artifacts.",
    ),
    "scripts": (
        "Operations Scripts",
        "PowerShell and shell entry points for Windows and server deployment.",
    ),
    "src": (
        "Python Source Tree",
        "Installable application source rooted at the gld_scalper package.",
    ),
    "src/gld_scalper": (
        "GLD Scalper Runtime Package",
        "Live trading, persistence, strategy, execution, research, reporting, and orchestration modules.",
    ),
    "src/gld_scalper/ml": (
        "Machine Learning Subsystem",
        "Dataset construction, classical and Transformer training, validation, promotion, drift, and inference.",
    ),
    "src/gld_scalper/reports": (
        "Report Builders",
        "Programmatic performance and research report generation.",
    ),
    "src/gld_scalper/utils": (
        "Shared Utilities",
        "Small cross-cutting helpers used by runtime and offline tooling.",
    ),
    "systemd": (
        "Linux Service Definitions",
        "systemd templates for supervised server operation.",
    ),
    "tests": (
        "Automated Test Suite",
        "Unit and integration-style regression tests for safety, strategy, persistence, and learning.",
    ),
    "tools": (
        "Developer And Data Tools",
        "Standalone maintenance, download, export, reporting, diagram, and documentation utilities.",
    ),
}

HANDWRITTEN = {
    "config.py": "Typed environment configuration, defaults, validation, data-mode separation, and credential guards.",
    "database.py": "SQLite connection owner, schema migrations, transactional repositories, and episode/order/outcome persistence.",
    "main.py": "Command-line composition root that wires settings, databases, clients, services, and commands.",
    "execution_engine.py": "Converts approved order plans into serialized, idempotent, protected broker intents.",
    "execution_safety.py": "Broker/database reconciliation, bracket grace, residual confirmation, circuit breakers, and safety flattening.",
    "order_reconciler.py": "Imports broker order/fill truth and materializes atomic closed-episode outcomes.",
    "schema.sql": "Canonical SQLite schema for market data, decisions, execution, models, diagnostics, and research.",
    "runner.py": "Long-running paper loop coordinating streams, decisions, execution, exports, labels, and scheduled work.",
    "strategy.py": "Deterministic agents, score fusion, patterns, playbooks, target exposure, and NO_TRADE reasoning.",
    "risk.py": "Position sizing and mandatory entry/exit risk gates.",
    "live_data.py": "Alpaca WebSocket subscriptions, live counters, snapshots, persistence, and fast-event callbacks.",
    "trainer.py": "Classical candidate training, chronological validation, calibration, and artifact persistence.",
    "transformer_model.py": "Small causal encoder-only time-series Transformer definitions and prediction heads.",
    "transformer_training.py": "Offline sequence construction, causal training, evaluation, and export.",
    "dataset_builder.py": "Builds cost-aware, causal training records and excludes execution-corrupted episodes.",
    "continual_training.py": "Repeatable experiment loop with fingerprints, resume state, and no-improvement stopping.",
    "retraining_scheduler.py": "After-hours retraining gate that requires enough trustworthy closed episodes.",
    "promotion.py": "Strict candidate-versus-champion promotion rules and immutable audit records.",
    "README.md": "This folder guide.",
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    files = {Path(line.strip()) for line in output.splitlines() if line.strip()}
    for path in (ROOT / "docs").rglob("*"):
        if path.is_file():
            files.add(path.relative_to(ROOT))
    for path in (ROOT / "tools").glob("*.py"):
        files.add(path.relative_to(ROOT))
    return sorted(files, key=lambda item: item.as_posix().lower())


def module_details(path: Path) -> tuple[str, list[str], list[str], list[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return "", [], [], []
    doc = ast.get_docstring(tree) or ""
    symbols: list[str] = []
    variables: list[str] = []
    links: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                symbols.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    variables.append(target.id)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("gld_scalper"):
                links.append(node.module)
        elif isinstance(node, ast.Import):
            links.extend(alias.name for alias in node.names if alias.name.startswith("gld_scalper"))
    first_sentence = doc.strip().split("\n\n", 1)[0].replace("\n", " ")
    return first_sentence, symbols[:18], variables[:18], sorted(set(links))[:12]


def description(path: Path) -> str:
    if path.name in HANDWRITTEN:
        return HANDWRITTEN[path.name]
    suffix = path.suffix.lower()
    if suffix == ".py":
        doc, symbols, _, _ = module_details(ROOT / path)
        if doc:
            return doc
        if path.name.startswith("test_"):
            return f"Regression tests for {path.stem.removeprefix('test_').replace('_', ' ')}."
        if symbols:
            return f"Python module exposing {', '.join(f'`{item}`' for item in symbols[:4])}."
        return "Python implementation module."
    if suffix in {".yaml", ".yml"}:
        return "Declarative automation or configuration."
    if suffix == ".sql":
        return "Structured database definition or query resource."
    if suffix == ".ps1":
        return "Windows PowerShell operations entry point."
    if suffix == ".sh":
        return "POSIX shell operations entry point."
    if suffix == ".service":
        return "systemd service unit template."
    if suffix in {".md", ".txt"}:
        return "Human-readable reference or operational documentation."
    if suffix == ".json":
        return "Machine-readable metadata, metrics, lineage, or model manifest."
    if suffix in {".pkl", ".joblib", ".pt", ".pth", ".onnx", ".torchscript"}:
        return "Serialized learned model or preprocessing artifact; load through the model registry."
    if suffix == ".sha256":
        return "Integrity checksum paired with a model artifact."
    if suffix in {".jpg", ".jpeg", ".png"}:
        return "Rendered documentation image."
    return "Version-controlled project resource."


def file_inventory(folder: str, files: list[Path]) -> str:
    rows = []
    prefix = Path(folder)
    root_prefix = "../" * len(prefix.parts)
    direct = [path for path in files if path.parent == prefix and path.name != "README.md"]
    if not direct:
        return "_This directory contains only child directories and this guide._"
    for path in direct:
        rows.append(f"| [`{path.name}`]({root_prefix}{path.as_posix()}) | {description(path)} |")
    return "\n".join(
        [
            "| File | Responsibility |",
            "|---|---|",
            *rows,
        ]
    )


def python_interfaces(folder: str, files: list[Path]) -> str:
    prefix = Path(folder)
    sections: list[str] = []
    for path in files:
        if path.parent != prefix or path.suffix != ".py":
            continue
        doc, symbols, variables, links = module_details(ROOT / path)
        if not (symbols or variables or links):
            continue
        sections.append(f"#### `{path.name}`")
        if doc:
            sections.append(doc)
        if symbols:
            sections.append(
                "**Public interfaces:** " + ", ".join(f"`{symbol}`" for symbol in symbols) + "."
            )
        if variables:
            sections.append(
                "**Module constants:** " + ", ".join(f"`{variable}`" for variable in variables) + "."
            )
        if links:
            sections.append(
                "**Internal dependencies:** " + ", ".join(f"`{link}`" for link in links) + "."
            )
        sections.append("")
    return "\n".join(sections).strip() or "_No Python interfaces live directly in this directory._"


def model_inventory(folder: str, files: list[Path]) -> str:
    prefix = Path(folder)
    direct = [path for path in files if path.parent == prefix and path.name != "README.md"]
    if not direct:
        return ""
    counts = Counter(path.suffix.lower() or "<none>" for path in direct)
    lines = [
        "### Artifact Inventory",
        "",
        "This inventory is intentionally explicit so a developer can account for every retained file. "
        "Do not edit serialized weights by hand; create a new version through training and promotion.",
        "",
        "**File-type counts:** "
        + ", ".join(f"`{suffix}`: {count}" for suffix, count in sorted(counts.items()))
        + ".",
        "",
        "<details>",
        "<summary>Show every artifact in this folder</summary>",
        "",
        "| File | Role |",
        "|---|---|",
    ]
    lines.extend(f"| `{path.name}` | {description(path)} |" for path in direct)
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def write_readme(folder: str, title: str, purpose: str, files: list[Path]) -> None:
    target = ROOT / folder / "README.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    depth = len(Path(folder).parts)
    root_link = "../" * depth + "README.md"
    content = [
        f"# {title}",
        "",
        purpose,
        "",
        f"Return to the [project manual]({root_link}).",
        "",
        "## Folder Contract",
        "",
        "- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.",
        "- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.",
        "- Preserve paper, historical, and future live state separation.",
        "- Add or update tests when behavior changes, and update this guide when file ownership changes.",
        "",
        "## Files In This Folder",
        "",
        file_inventory(folder, files),
        "",
    ]
    if any(path.parent == Path(folder) and path.suffix == ".py" for path in files):
        content.extend(
            [
                "## Python Interfaces, Variables, And Linkage",
                "",
                python_interfaces(folder, files),
                "",
                "The interface list is generated from public top-level classes/functions and uppercase "
                "module constants. Read type annotations and tests before changing semantics; private "
                "helpers are implementation details but can still participate in safety invariants.",
                "",
            ]
        )
    content.extend(
        [
            "## Linkage And Change Discipline",
            "",
            "1. Start at the composition root in `src/gld_scalper/main.py` or the invoking tool/script.",
            "2. Follow typed settings from `config.py`; environment values should not be read ad hoc elsewhere.",
            "3. Follow persistence through `database.py` and `schema.sql`; multi-row execution state must remain transactional.",
            "4. Follow behavioral evidence into the matching tests before changing a public interface.",
            "5. Run focused tests first, then the complete suite. Paper execution is the final verification stage, not the first.",
            "",
            "## Data And Security",
            "",
            "Tracked code and promoted model memory may be committed. Raw market data, account data, "
            "exports, logs, API keys, and local Ollama model blobs stay outside Git. Model artifacts "
            "must retain their checksum, manifest, training range, exact feature profile, metrics, "
            "and rollback lineage.",
            "",
            "---",
            "",
            "Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.",
            "",
        ]
    )
    if folder in {"models/paper", "models/paper/manifests"}:
        content.insert(-3, model_inventory(folder, files))
        content.insert(-3, "")
    target.write_text("\n".join(content), encoding="utf-8", newline="\n")


def main() -> None:
    files = tracked_files()
    for folder, (title, purpose) in FOLDERS.items():
        write_readme(folder, title, purpose, files)
    print(f"Generated {len(FOLDERS)} folder README files.")


if __name__ == "__main__":
    main()
