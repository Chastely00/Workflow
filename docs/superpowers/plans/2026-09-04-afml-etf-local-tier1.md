# ETF-local Tier 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline task-by-task execution. Steps use checkbox syntax for tracking.

**Goal:** Replace the pooled Tier 1 panel research path with independently fitted, PIT-safe Tier 1 models and gates for every ETF Trick.

**Architecture:** `Tier1Lab` will partition the already PIT-joined training frame by `etf_id`, construct chronological purged folds inside each partition, and fit only partition-local preprocessing, HGB/logistic state, calibration, and threshold. It will return per-ETF runs and a combined metadata-only hand-off; governance will evaluate each ETF separately. The previous pooled HGB artifacts remain immutable rejected evidence.

**Tech Stack:** Python 3.12, pandas, NumPy, scikit-learn, pytest, Parquet/JSON manifests.

**Spec:** `docs/afml/prompts/00-goal-prompt.md`, `03-tiered-ml-strategy-master-prompt.md`, `04-tier1-directional-label-and-model-prompt.md`, `05-tier2-meta-labeling-prompt.md`, `07-strategy-governance-dsr-acceptance-prompt.md`.

## Global Constraints

- Do not modify MongoDB, upstream ETF Tricks, AFML dataset v5, target artifacts, or existing immutable research artifacts.
- Tier 1 model fit must use rows from exactly one `etf_id`; `etf_id` is metadata and cannot enter a model feature matrix.
- Each ETF uses ETF-local chronological expanding, event-end-purged folds and fold-local imputation, scaling, calibration, uniqueness, and threshold selection.
- Preserve prior pooled results as rejected benchmark evidence; never reinterpret pooled metrics as ETF-local evidence.
- Register each ETF-local model specification before performance results; include ETF and model scope in trial/DSR records.
- Execute only bounded 2020–2026 smoke/acceptance before an explicitly registered long-history run; do not call a result sealed without proven unopened outcome scope.

---

### Task 1: Add an ETF-local OOF run interface

**Files:**
- Modify: `etf_tricks/tier1/lab.py`
- Test: `tests/etf_tricks/tier1/test_lab.py`

**Interfaces:**
- Consumes: `Tier1Lab.run_oof(...)` input frame and feature list.
- Produces: `Tier1LocalOOFRun` keyed by `etf_id`, with `training_frame`, `predictions`, `handoff`, and ETF-local folds.

- [ ] **Step 1: Write failing tests**

```python
run = Tier1Lab.from_artifacts(afml, targets).run_oof_per_etf(["f"], outer_splits=1)
assert set(run.by_etf) == {"a", "b"}
assert run.by_etf["a"].training_frame["etf_id"].eq("a").all()
assert run.by_etf["b"].training_frame["etf_id"].eq("b").all()
```

- [ ] **Step 2: Run the focused tests and confirm the missing interface fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/etf_tricks/tier1/test_lab.py -q`

- [ ] **Step 3: Implement `run_oof_per_etf`**

```python
for etf_id, etf_frame in frame.groupby("etf_id", sort=True):
    folds = chronological_purged_folds(etf_frame[["t0", "t1"]], n_splits=outer_splits)
    predictions = oof_logistic_predictions(etf_frame, folds, feature_columns, ...)
```

Reject categorical columns containing `etf_id`. Build the hand-off only from ETF-local OOF rows; retain `etf_id` as metadata.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/etf_tricks/tier1/test_lab.py -q`

### Task 2: Prove cross-ETF training isolation

**Files:**
- Modify: `etf_tricks/tier1/lab.py`
- Test: `tests/etf_tricks/tier1/test_lab.py`, `tests/etf_tricks/tier1/test_model.py`

**Interfaces:**
- Consumes: two otherwise valid ETF partitions.
- Produces: deterministic same-ETF OOF predictions unaffected by a second ETF's rows.

- [ ] **Step 1: Write a failing invariance test**

```python
base = local_predictions(frame_for_a)
with_other = local_predictions(pd.concat([frame_for_a, adversarial_rows_for_b]))
pd.testing.assert_frame_equal(base, with_other.loc[with_other.etf_id.eq("a")])
```

- [ ] **Step 2: Confirm it fails against pooled fitting**

Run: `.\.venv\Scripts\python.exe -m pytest tests/etf_tricks/tier1/test_lab.py -q`

- [ ] **Step 3: Enforce model-matrix boundary**

Reject `categorical_columns=("etf_id",)` in ETF-local APIs and calculate `average_uniqueness` with no cross-ETF entity grouping. Persist local fold identifiers as `(etf_id, outer_fold)`.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/etf_tricks/tier1/test_lab.py tests/etf_tricks/tier1/test_model.py -q`

### Task 3: Make diagnostics and gates ETF-local

**Files:**
- Modify: `etf_tricks/tier1/diagnostics.py`, `scripts/materialize_tier1_long_history_oof.py`
- Test: `tests/etf_tricks/tier1/test_diagnostics.py`, new `tests/etf_tricks/tier1/test_local_gate.py`

**Interfaces:**
- Consumes: one `Tier1LocalOOFRun` per ETF.
- Produces: per-ETF diagnostics and gate reports, each marked with `etf_scope` and `model_scope="ETF_LOCAL"`.

- [ ] **Step 1: Write failing tests**

```python
assert report["etf_scope"] == "momentum"
assert report["model_scope"] == "ETF_LOCAL"
assert "pooled_auc" not in report["metrics"]
```

- [ ] **Step 2: Implement a per-ETF materialization loop**

Use one registered trial record per `(etf_id, model specification)`. Write distinct, non-overwriting output roots and manifests. Mark lack of mature samples or a one-class local fold `INSUFFICIENT_MATURE_EVENTS`; do not call it a failed model.

- [ ] **Step 3: Run diagnostics/governance tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/etf_tricks/tier1 tests/etf_tricks/governance -q`

### Task 4: Register the independent-model experiment before fitting

**Files:**
- Modify: `docs/afml/progress/decision-log.jsonl`
- Modify: `.artifacts/afml_governance/tier1_trials.jsonl` only through `TrialRegistry`
- Create: versioned output directories under `.artifacts/tier1/` and `.artifacts/afml_governance/`

**Interfaces:**
- Consumes: immutable dataset/target/feature-extension manifests and the code commit containing Tasks 1–3.
- Produces: append-only trial contracts, with ETF scope, model scope, fold boundary, trial counts, and output paths.

- [ ] **Step 1: Define and persist the contract before calling `run_oof_per_etf`**

Use base15 logistic sanity and base15 shallow-HGB nonlinear specifications. Preserve the prior effective trial count of at least 30, then add the 13 ETF-local model choices conservatively before interpreting outcomes.

- [ ] **Step 2: Run bounded verification first**

Run only 2020–2026 for 13 ETFs after focused unit tests. Record event maturity, per-ETF timings, and no-model conditions. Do not use this result for a sealed or long-history claim.

- [ ] **Step 3: Run long-history OOF only after bounded run passes contracts**

Use an ETF-local 2005–2024 chronological diagnostic. Produce only per-ETF gates; preserve 2025–2026 status as `NOT_SEALED` unless an access audit proves it unopened for that ETF lineage.

- [ ] **Step 4: Run full verification**

Run: `.\.venv\Scripts\python.exe -m pytest tests/etf_tricks/tier1 tests/etf_tricks/governance -q`

### Task 5: Update the authoritative status and hand-off

**Files:**
- Modify: `docs/afml/progress/decision-log.jsonl`
- Modify: `docs/afml/prompts/00-goal-prompt.md` only if verification exposes an unanticipated contract ambiguity.

**Interfaces:**
- Consumes: Task 4 manifests and reports.
- Produces: an explicit per-ETF state table: `PASSED`, `FAILED`, `INSUFFICIENT_MATURE_EVENTS`, or `NOT_SEALED`.

- [ ] **Step 1: Compare only ETF-local results to each ETF's gate**

Do not calculate an aggregate score for admission. A passed ETF becomes eligible for its own Tier 2 implementation only; it is not a portfolio allocation decision.

- [ ] **Step 2: Commit the source/prompt/plan changes and report immutable artifact hashes**

Run: `git diff --check`; commit only touched source, tests, prompts, plan, and non-sensitive governance records. Do not commit `.artifacts` datasets or Notebook outputs.
