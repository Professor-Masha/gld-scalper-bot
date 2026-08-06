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

## Programmer Guide: The Complete ML Lifecycle

The ML package has four separate responsibilities: prepare causal evidence,
fit candidates, evaluate/promote immutable versions, and serve fast inference.
Keeping them separate prevents test metrics from leaking into training and
prevents live inference from silently refitting itself.

### Classical Model Data Flow

```text
signals + decision_executions + outcomes + optional LLM labels
 -> dataset_builder.build_training_records()
 -> label_quality()
 -> chronological train/holdout split with purge gap
 -> trainer.train_candidate_model()
 -> probability calibration
 -> threshold optimization on allowed validation evidence
 -> walk-forward validation
 -> artifact + feature profile + metrics
 -> ModelRegistry registration/promotion decision
```

`dataset_builder.py` is the contract between SQLite and sklearn. It selects the
action that was actually executed, joins only matured outcomes, excludes known
execution-corrupted episodes, flattens nested feature JSON, and checks that the
target has enough samples and classes. If this layer is wrong, a sophisticated
model will learn the wrong problem correctly.

`trainer.py` fits the classical candidate and its probability calibration. It
saves the exact feature column order, preprocessing/fitted model state,
hyperparameters, class mapping, decision thresholds, data range, metrics, and
fingerprint. `predictor.py` must use that saved profile exactly; it cannot infer
a new column order from a live dictionary.

### Archive And Continual Training

`archive_dataset.py` creates a reusable joblib artifact from large historical
evidence. The artifact contains records and provenance, not a champion model.
`continual_training.py` expands one archive into scoped experiments across
horizons, playbooks, and search rounds. It fingerprints experiment identity,
persists completion state, skips already-completed work, uses a process lock,
and can stop after repeated rounds produce no meaningful improvement.

Historical records supply breadth. Clean paper outcomes receive configurable
additional weight because they represent the current strategy and broker
simulation. Weighting duplicates influence during fitting; it does not invent
new independent observations, so evaluation must still report paper and
historical performance separately.

### Scope Selection

`scopes.py` prevents one model from pretending every trading problem is the
same. Example scopes include fast microstructure, minute entry, news/event, and
major playbooks. At inference, `model_scope_from_features()` chooses the most
specific applicable scope. `scope_fallbacks()` provides a controlled lookup
order when that scope has no eligible model.

The registry key is therefore not merely a model version. It is approximately:

```text
(model family, model scope, feature profile, training fingerprint, version)
```

### Registry And Promotion

`model_registry.py` is the authority over model status. Candidate files alone
do not participate in trading. A registry row records status such as candidate,
paper shadow, champion, demoted, or archived, along with checksums and evidence.

`candidate_beats_champion()` compares like-for-like validated results. Promotion
must use the actual saved model, feature order, preprocessing, hyperparameters,
thresholds, and costs. Older champions are retained for audit and rollback.
`drift.py` compares current feature/performance behavior with the validated
range and can demote a champion without deleting it.

### Live Classical Inference

`Predictor` loads eligible registry artifacts during construction. For each
feature snapshot it:

1. Chooses a model scope and fallback order.
2. Verifies artifact integrity and feature compatibility.
3. Builds one row in the saved column order.
4. Runs calibrated class-probability inference.
5. Applies saved confidence and margin thresholds.
6. Returns `MLPrediction`, including an abstention reason when it declines.
7. Persists the prediction for later outcome evaluation.

The predictor advises the deterministic strategy. Its role and configuration
decide whether it is shadow-only, bounded, or required. It never calls Alpaca.

### Transformer Sequence Data Flow

The Transformer learns temporal order rather than one flat snapshot:

```text
timestamp-aligned raw observations
 -> transformer_dataset
 -> memmap sequence artifact + masks + targets + manifest
 -> transformer_trainer
 -> causal Transformer candidate + TorchScript export
 -> historical holdout/walk-forward and exact RF baseline
 -> paper shadow predictions
 -> transformer_evaluation
 -> registry promotion decision
```

`transformer_dataset.py` owns sequence length, stride, feature priority,
padding/missing masks, market-session masks, causal targets, and registry scope.
It must never include an observation after the decision timestamp in the input
window.

`transformer_model.py` defines the compact encoder, causal attention mask,
classification head, expected-return heads for 1/3/5/15 minutes, expected-cost
head, and uncertainty output. `transformer_trainer.py` owns optimization,
validation, early stopping, calibration evidence, artifact persistence, and
latency measurements.

### Live Transformer Inference

`AsyncTransformerShadowRuntime` runs outside the minute and websocket threads.
The live path submits current observations and reads only a recent cached
`TransformerShadowPrediction`. If the worker is unavailable, late, stale, or
invalid, deterministic trading continues without waiting.

`transformer_authority.py` is the policy boundary:

- `shadow` records predictions but changes no decision.
- `bounded_adviser` may make only the configured small adjustment or veto and
  cannot originate a trade.
- `paper_champion` may recommend a paper action only with independent technical
  confluence and all normal safety gates.

The Transformer never receives a broker client.

### Exit Models

`exit_trainer.py` is deliberately separate from entry training. It uses only a
minimum number of trustworthy completed episodes and learns from state since
entry. Entry labels answer whether opening exposure had edge; exit labels answer
how an existing position should be managed. Mixing them creates ambiguous
targets.

### Walk-Forward And Metrics

`evaluator.py` contains pure metric and threshold functions. Classification
quality and trading quality are both required because a high overall accuracy
can come from predicting `NO_TRADE` almost everywhere. Important outputs
include per-class metrics, calibration, coverage/abstention, after-cost return,
profit factor, drawdown, and trade count.

`walk_forward.py` repeatedly trains on the past and evaluates on the next unseen
period. No fold may train on data later than its test interval. Holdout,
walk-forward, historical, and paper results are stored and reported separately.

### Scheduled Retraining

`retraining_scheduler.py` is a gate around the trainer, not a second trainer. It
requires the configured after-hours window, no active execution episodes,
enough trustworthy labels, and no conflicting training lock. Completion returns
a candidate/promotion result to `main.py`, which reloads the predictor only when
promotion actually occurred.

### What Persists As Model Memory

The learned memory is the immutable artifact plus registry metadata, not Python
variables left in RAM. A restart recovers knowledge by loading:

- fitted parameters or trees;
- preprocessing and calibration state;
- exact feature names/order;
- class mapping and thresholds;
- training fingerprint and data range;
- holdout/walk-forward/paper metrics;
- checksum, scope, status, and rollback lineage.

Raw training data remains outside Git. Approved model artifacts and sanitized
manifests may be versioned according to the repository policy.

### Safe Modification Checklist

When adding or changing an ML feature, update the live feature builder, archive
builder, dataset builder, saved feature profile, predictor compatibility logic,
and feature-compatibility tests together. When changing labels, rebuild the
dataset artifact and use a new fingerprint. When changing model architecture,
do not overwrite an old artifact path. When changing promotion criteria, add a
test proving a weak candidate cannot pass.

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
