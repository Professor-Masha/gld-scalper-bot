# Machine Learning Subsystem

Dataset construction, classical and Transformer training, validation, promotion, drift, and inference.

Return to the [project manual](../../../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`__init__.py`](../../../src/gld_scalper/ml/__init__.py) | Machine-learning helpers for controlled trade-quality filtering. |
| [`archive_dataset.py`](../../../src/gld_scalper/ml/archive_dataset.py) | Python module exposing `ArchiveDatasetResult`, `build_archive_training_records`, `save_archive_training_artifact`, `load_archive_training_artifact`. |
| [`continual_training.py`](../../../src/gld_scalper/ml/continual_training.py) | Repeatable experiment loop with fingerprints, resume state, and no-improvement stopping. |
| [`dataset_builder.py`](../../../src/gld_scalper/ml/dataset_builder.py) | Builds cost-aware, causal training records and excludes execution-corrupted episodes. |
| [`drift.py`](../../../src/gld_scalper/ml/drift.py) | Python module exposing `generate_drift_report`. |
| [`evaluator.py`](../../../src/gld_scalper/ml/evaluator.py) | Python module exposing `policy_predictions`, `optimize_policy_thresholds`, `classification_metrics`, `trading_metrics`. |
| [`exit_trainer.py`](../../../src/gld_scalper/ml/exit_trainer.py) | Python module exposing `train_exit_candidate`. |
| [`model_registry.py`](../../../src/gld_scalper/ml/model_registry.py) | Python module exposing `ModelRegistry`, `candidate_beats_champion`. |
| [`predictor.py`](../../../src/gld_scalper/ml/predictor.py) | Python module exposing `Predictor`. |
| [`retraining_scheduler.py`](../../../src/gld_scalper/ml/retraining_scheduler.py) | After-hours retraining gate that requires enough trustworthy closed episodes. |
| [`scopes.py`](../../../src/gld_scalper/ml/scopes.py) | Python module exposing `model_scope_from_features`, `model_scope_from_context`, `scope_fallbacks`. |
| [`trainer.py`](../../../src/gld_scalper/ml/trainer.py) | Classical candidate training, chronological validation, calibration, and artifact persistence. |
| [`transformer_authority.py`](../../../src/gld_scalper/ml/transformer_authority.py) | Python module exposing `apply_transformer_to_signal`, `apply_transformer_to_fast_decision`. |
| [`transformer_continual.py`](../../../src/gld_scalper/ml/transformer_continual.py) | Python module exposing `TransformerContinualTrainingRunner`, `combine_transformer_artifacts`, `parse_scope_artifacts`. |
| [`transformer_dataset.py`](../../../src/gld_scalper/ml/transformer_dataset.py) | Python module exposing `TransformerDatasetResult`, `LoadedSequenceArtifact`, `build_transformer_sequence_artifact`, `load_transformer_sequence_artifact`. |
| [`transformer_evaluation.py`](../../../src/gld_scalper/ml/transformer_evaluation.py) | Python module exposing `evaluate_transformer_paper_models`. |
| [`transformer_model.py`](../../../src/gld_scalper/ml/transformer_model.py) | Small causal encoder-only time-series Transformer definitions and prediction heads. |
| [`transformer_runtime.py`](../../../src/gld_scalper/ml/transformer_runtime.py) | Python module exposing `TransformerShadowPrediction`, `AsyncTransformerShadowRuntime`. |
| [`transformer_trainer.py`](../../../src/gld_scalper/ml/transformer_trainer.py) | Python module exposing `TransformerTrainingOptions`, `train_transformer_candidate`. |
| [`walk_forward.py`](../../../src/gld_scalper/ml/walk_forward.py) | Python module exposing `run_walk_forward_validation`, `save_walk_forward_experiment`. |

## Python Interfaces, Variables, And Linkage

#### `archive_dataset.py`
**Public interfaces:** `ArchiveDatasetResult`, `build_archive_training_records`, `save_archive_training_artifact`, `load_archive_training_artifact`.

#### `continual_training.py`
**Public interfaces:** `TrainingLock`, `ContinualTrainingRunner`, `prepare_experiment_records`.
**Module constants:** `TERMINAL_STATUSES`, `HISTORICAL_EVALUATION_POLICY`.

#### `dataset_builder.py`
**Public interfaces:** `build_training_dataset`, `build_training_records`, `align_records`, `label_quality`.
**Module constants:** `NON_FEATURE_COLUMNS`, `TRADE_GOOD_LABELS`, `VALID_LABELS`, `UNCLEAN_EXECUTION_EXIT_REASONS`.

#### `drift.py`
**Public interfaces:** `generate_drift_report`.

#### `evaluator.py`
**Public interfaces:** `policy_predictions`, `optimize_policy_thresholds`, `classification_metrics`, `trading_metrics`, `validation_trade_metrics`.
**Module constants:** `CLASSES`.

#### `exit_trainer.py`
**Public interfaces:** `train_exit_candidate`.

#### `model_registry.py`
**Public interfaces:** `ModelRegistry`, `candidate_beats_champion`.

#### `predictor.py`
**Public interfaces:** `Predictor`.

#### `retraining_scheduler.py`
**Public interfaces:** `SafeRetrainingScheduler`.

#### `scopes.py`
**Public interfaces:** `model_scope_from_features`, `model_scope_from_context`, `scope_fallbacks`.
**Module constants:** `MAJOR_PLAYBOOKS`.

#### `trainer.py`
**Public interfaces:** `ProbabilityCalibratedModel`, `train_candidate_model`.
**Module constants:** `FAST_FEATURE_TERMS`.

#### `transformer_authority.py`
**Public interfaces:** `apply_transformer_to_signal`, `apply_transformer_to_fast_decision`.
**Module constants:** `LABEL_TO_ACTION`.

#### `transformer_continual.py`
**Public interfaces:** `TransformerContinualTrainingRunner`, `combine_transformer_artifacts`, `parse_scope_artifacts`.
**Module constants:** `SEARCH_CONFIGURATIONS`.

#### `transformer_dataset.py`
**Public interfaces:** `TransformerDatasetResult`, `LoadedSequenceArtifact`, `build_transformer_sequence_artifact`, `load_transformer_sequence_artifact`, `sequence_defaults`, `transformer_registry_scope`.
**Module constants:** `NY`, `FEATURE_PRIORITY_TERMS`.

#### `transformer_evaluation.py`
**Public interfaces:** `evaluate_transformer_paper_models`.

#### `transformer_model.py`
**Public interfaces:** `require_torch`, `TransformerModelConfig`, `build_causal_transformer`.
**Module constants:** `HORIZONS_MINUTES`, `ENTRY_CLASSES`, `EXIT_CLASSES`.

#### `transformer_runtime.py`
**Public interfaces:** `TransformerShadowPrediction`, `AsyncTransformerShadowRuntime`.
**Module constants:** `LOGGER`, `NY`, `SUPPORTED_SCOPES`.

#### `transformer_trainer.py`
**Public interfaces:** `TransformerTrainingOptions`, `train_transformer_candidate`.

#### `walk_forward.py`
**Public interfaces:** `run_walk_forward_validation`, `save_walk_forward_experiment`.

The interface list is generated from public top-level classes/functions and uppercase module constants. Read type annotations and tests before changing semantics; private helpers are implementation details but can still participate in safety invariants.

## Linkage And Change Discipline

1. Start at the composition root in `src/gld_scalper/main.py` or the invoking tool/script.
2. Follow typed settings from `config.py`; environment values should not be read ad hoc elsewhere.
3. Follow persistence through `database.py` and `schema.sql`; multi-row execution state must remain transactional.
4. Follow behavioral evidence into the matching tests before changing a public interface.
5. Run focused tests first, then the complete suite. Paper execution is the final verification stage, not the first.

## Data And Security

Tracked code and promoted model memory may be committed. Raw market data, account data, exports, logs, API keys, and local Ollama model blobs stay outside Git. Model artifacts must retain their checksum, manifest, training range, exact feature profile, metrics, and rollback lineage.

---

Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.
