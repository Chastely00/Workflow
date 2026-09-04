# ETF-local Tier 2 Meta-Labeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline task-by-task execution. Steps use checkbox syntax for tracking.

**Goal:** Build a reproducible, PIT-safe Tier 2 OOF meta-label pipeline for only the ETF-local Tier 1 lineages that pass the long-history research gate.

**Architecture:** A Tier 2 frame joins a finalized Tier 1 OOF handoff one-to-one with the immutable target and AFML features by `(etf_id, event_id, t0_bar_id)`. It retains only `candidate_indicator=true`, derives `y_meta` from the resolved Tier 1 direction, and rejects any row whose Tier 1 prediction is not OOF/walk-forward or was unavailable at the Tier 2 decision time. Per ETF, expanding purged folds fit all preprocessors, calibration and acceptance thresholds only on that ETF's training candidates. The output is an OOF-only, allocation-free handoff.

**Tech Stack:** Python 3.12, pandas, NumPy, scikit-learn, pytest, Parquet/JSON manifests.

**Spec:** `docs/afml/prompts/00-goal-prompt.md`, `03-tiered-ml-strategy-master-prompt.md`, `05-tier2-meta-labeling-prompt.md`, `07-strategy-governance-dsr-acceptance-prompt.md`.

## Global Constraints

- Do not modify MongoDB, ETF Tricks upstream outputs, AFML dataset v5, Tier 1 target artifacts, or immutable OOF artifacts.
- Tier 2 defaults to exactly one ETF-local lineage. `etf_id` is provenance metadata and must not enter any model matrix.
- Use only Tier 1 `prediction_kind=OOF_CALIBRATED` (or a future explicitly defined walk-forward equivalent), `candidate_indicator=true`, and `decision_available_at <= Tier 2 decision time`.
- Derive `y_meta = 1[y_direction=+1]` only from outcome-mature Tier 1 candidates. Never expose `t1`, exit/touch dates, realized returns, labels, or future target fields in the Tier 2 handoff.
- Use chronological event-end-purged folds, ETF-local concurrency/uniqueness and fold-local imputation, scaling, calibration and acceptance thresholding. IID random CV is prohibited.
- This is research-only while the historical target exposes later outcomes. No report may claim `SEALED`, paper-trade eligibility, Tier 3 admission, order generation, PSR, or DSR.
- Pre-register each `(etf_id, feature set, model, threshold)` before materializing performance results. Preserve the current effective trial-count floor of 134 and increase it for every newly observed Tier 2 alternative.

---

### Task 1: Define and test the Tier 2 input contract

**Files:**
- Create: `etf_tricks/tier2/frame.py`
- Create: `tests/etf_tricks/tier2/test_frame.py`

**Interfaces:**
- Consumes: Tier 1 OOF handoff, immutable target table, immutable AFML feature table.
- Produces: `build_meta_training_frame(tier1_oof, targets, features, feature_columns) -> pd.DataFrame`.

- [ ] **Step 1: Write failing contract tests**

```python
frame = build_meta_training_frame(oof, targets, features, ["f"])
assert frame["y_meta"].tolist() == [1, 0]
assert frame["etf_id"].eq("low_volatility").all()
assert {"t1", "net_log_return", "exit_date"}.isdisjoint(frame.columns)
```

Add rejection tests for duplicate keys, non-OOF `prediction_kind`, non-candidate rows, mismatched ETF/event/bar keys, unavailable Tier 1 decisions, missing feature availability and cross-ETF rows.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/etf_tricks/tier2/test_frame.py -q`

- [ ] **Step 3: Implement one-to-one PIT joining and target derivation**

Use `validate="one_to_one"` joins. Require `decision_available_at >= feature_available_at`; set the Tier 2 decision timestamp to the maximum of the Tier 1 and feature availability clocks. Derive `y_meta` before removing label fields; retain only `event_id`, `etf_id`, `t0_bar_id`, `t0`, `tier2_decision_available_at`, `p1`, declared features and `y_meta`.

- [ ] **Step 4: Run focused tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/etf_tricks/tier2/test_frame.py -q`

### Task 2: Add ETF-local Tier 2 OOF predictions

**Files:**
- Create: `etf_tricks/tier2/model.py`
- Create: `tests/etf_tricks/tier2/test_model.py`

**Interfaces:**
- Consumes: one ETF-local meta training frame and explicit purged folds.
- Produces: `oof_meta_predictions(frame, folds, feature_columns, model_family, ...) -> pd.DataFrame` with `p2`, `accepted`, `acceptance_threshold`, `acceptance_reason`, `prediction_kind`.

- [ ] **Step 1: Write failing OOF tests**

```python
predictions = oof_meta_predictions(frame, [([0, 1, 2, 3], [4, 5])], ["f"])
assert predictions.loc[:3, "p2"].isna().all()
assert predictions.loc[4:, "prediction_kind"].eq("OOF_CALIBRATED").all()
```

Test fold-local imputation/calibration, no cross-ETF sensitivity, one-class training status, no supported training threshold as explicit no-trade, and that mutating validation outcomes cannot alter validation `p2` or threshold.

- [ ] **Step 2: Confirm the tests fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/etf_tricks/tier2/test_model.py -q`

- [ ] **Step 3: Implement fold-local logistic baseline and registered HGB candidate**

Reuse the established chronological fold semantics but do not reuse Tier 1 fitted objects. Fit preprocessing, estimator and calibrator inside each training fold. Select an acceptance threshold only from training-side calibrated candidates using pre-registered net outcome objective and minimum support. Do not use the same-row realized target to decide acceptance.

- [ ] **Step 4: Run focused tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/etf_tricks/tier2/test_model.py -q`

### Task 3: Materialize research-only ETF-local Tier 2 artifacts

**Files:**
- Create: `etf_tricks/tier2/artifact.py`
- Create: `etf_tricks/tier2/lab.py`
- Create: `scripts/materialize_tier2_etf_local_oof.py`
- Create: `tests/etf_tricks/tier2/test_artifact.py`

**Interfaces:**
- Consumes: immutable manifests, one passed Tier 1 OOF artifact and pre-registered Tier 2 contract.
- Produces: manifest-backed `oof_handoff.parquet`, diagnostics, research-only gate report, and append-only trial records.

- [ ] **Step 1: Write failing materialization tests**

```python
manifest = write_tier2_oof_artifact(handoff, output, metadata)
assert manifest["schema_version"] == "tier2-oof-v1"
assert "t1" not in pd.read_parquet(output / "oof_handoff.parquet")
assert metadata["research_only"] is True
```

Test hash verification, non-overwrite behavior, immutable input manifest binding, rejected in-sample Tier 1 artifacts, and absence of order/allocation/realized-return fields.

- [ ] **Step 2: Implement materialization and registry logic**

Register before fitting one trial for low-volatility and one for volume-ratio. Use only their 2005-2024 Tier 1 long-history handoffs; write each output under a new non-overwriting `.artifacts/tier2/` root. Set `research_only=true`, `sealed_status=NOT_SEALED`, `tier3_permitted=false`, and retain the 134-trial floor plus both Tier 2 alternatives.

- [ ] **Step 3: Run bounded Tier 2 materialization**

Run the script for `low_volatility` and `volume_ratio`, inspect OOF counts, candidate counts, calibration and metric definitions. Do not choose a winner or change a parameter after observing them.

### Task 4: Verify governance and publish the next-state decision

**Files:**
- Modify: `docs/afml/progress/decision-log.jsonl`
- Test: `tests/etf_tricks/tier2/`, `tests/etf_tricks/governance/`

**Interfaces:**
- Consumes: Tier 2 manifest-backed results and trial registry records.
- Produces: explicit `RESEARCH_ONLY` or failed Tier 2 lineage state, without paper-trade admission.

- [ ] **Step 1: Run verification**

Run: `./.venv/Scripts/python.exe -m pytest tests/etf_tricks/tier2 tests/etf_tricks/governance -q`

Run: `git diff --check`

- [ ] **Step 2: Record outcomes without overclaiming**

Append one decision record for each Tier 2 lineage, including ETF/model scope, OOF rows, candidate/acceptance count, calibration metrics, economic comparison, input/output manifest hashes, effective trial count, and `NOT_SEALED` status.

- [ ] **Step 3: Commit only source/tests/prompts/plan/governance records**

Do not commit `.artifacts`, source data, generated notebooks or unrelated user changes.
