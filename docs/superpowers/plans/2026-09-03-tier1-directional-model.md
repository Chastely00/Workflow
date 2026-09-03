# Tier 1 Directional Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an immutable, capital-neutral Tier 1 target and OOF long-candidate stream from the finalized AFML dataset, without altering AFML dataset labels or shared execution accounting.

**Architecture:** Add a focused `etf_tricks.tier1` package. It reads `AFMLDataset` through PIT-safe views, computes its own raw-OPEN/cost-aware target evidence, creates purged/embargoed OOF logistic predictions, and writes a separate manifest-backed artifact. Tier 3 remains the sole owner of integer-share execution.

**Tech Stack:** Python 3.12, pandas, NumPy, scikit-learn, pytest, PyArrow.

**Spec:** `docs/afml/prompts/03-tiered-ml-strategy-master-prompt.md` and `docs/afml/prompts/04-tier1-directional-label-and-model-prompt.md`

## Global Constraints

- Read `.artifacts/etf_afml/full-history-20050103-20260707-v5` only through `AFMLDataset.read`; never modify it.
- Target entry/exit prices are next legal-session constituent raw OPEN; daily-close trigger evidence and execution facts remain separate.
- Target friction is declared proportional buy/sell rate only; no integer shares, one-NTD minimum commission, allocation, or NTD sizing.
- All transforms, feature selection, calibration, threshold selection, and weights are fit inside the training fold only.
- Test on 2024--2026 before a separately authorized full-history run; never report strategy PnL in Tier 1.

### Task 1: Define the target and artifact contracts

**Files:** Create `etf_tricks/tier1/config.py`, `targets.py`, `artifact.py`, `__init__.py`; create `tests/etf_tricks/tier1/test_targets.py`.

- [ ] Write failing fixtures for: 60-bar EWMA/min-20 volatility, proportional buy/sell costs, next-open entry/exit timing, unresolved tails, and daily-close double-touch non-applicability.
- [ ] Implement pure target calculation with explicit `trigger_*`, `entry_*`, `exit_*`, `target_status`, `y_direction`, and availability columns.
- [ ] Write/read a versioned parquet artifact plus manifest and reject duplicate keys, unavailable source rows, and schema/hash mismatch.
- [ ] Run the focused target tests and commit the self-contained target contract.

### Task 2: Implement event-aware OOF fitting

**Files:** Create `etf_tricks/tier1/splits.py`, `model.py`, `lab.py`; create `tests/etf_tricks/tier1/test_splits.py` and `test_model.py`.

- [ ] Write failing tests proving no train interval overlaps validation event intervals after purge/embargo, no in-sample prediction is emitted, and fold-local scaling/calibration is used.
- [ ] Implement deterministic chronological purged folds with embargo, concurrency/average-uniqueness sample weights, regularized logistic baseline, and fold-local calibration.
- [ ] Persist OOF `p1`, candidate indicator, fold/model/feature hashes, decision time, and reason; omit labels, realized returns, `t1`, and allocation fields from the hand-off view.
- [ ] Run focused tests and commit the model/OOF contract.

### Task 3: Build bounded evidence and acceptance gate

**Files:** Create `tests/etf_tricks/tier1/test_integration.py`; modify `etf_tricks/tier1/lab.py` only as needed for public bounded-run API; create a manifest-backed bounded artifact under `.artifacts/` without adding it to Git.

- [ ] Verify source artifact readiness/hash and produce a 2024--2026 two-ETF run before 13 ETFs.
- [ ] Verify future-append invariance, source-delay rejection, target timing/cost reconciliation, OOF-only hand-off, and no forbidden future columns.
- [ ] If every Tier 1 gate passes, run the 13-ETF bounded acceptance and publish only diagnostic/model lineage evidence; otherwise record the precise failed gate and stop Tier 2.
- [ ] Run the focused Tier 1 suite, review Git scope, commit code/tests/docs only, and push main.
