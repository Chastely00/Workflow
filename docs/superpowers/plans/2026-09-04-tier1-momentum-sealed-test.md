# Tier 1 Momentum Sealed-Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run exactly one sealed 2025–2026 evaluation for the post-selected Momentum ETF under the fixed pooled HGB-base15 Tier 1 lineage.

**Architecture:** Use all outcome-mature 2005–2024 events from all 13 ETFs only for model fitting, fold-local calibration, and training-only economic threshold selection. Predict and score only Momentum events whose decision time is in the 2025–2026 sealed interval. The sealed artifact must be distinct from OOF handoff artifacts and must never expose other ETFs' sealed labels or predictions.

**Tech Stack:** Python 3.12, pandas, scikit-learn, PyArrow, pytest.

**Spec:** `docs/afml/prompts/00-goal-prompt.md`, `docs/afml/prompts/04-tier1-directional-label-and-model-prompt.md`

## Global Constraints

- The selected ETF is `momentum`, based on the recorded 13-ETF diagnostic; count the selection conservatively in DSR trials.
- Train events require `t0 <= 2024-12-31` and `t1 < 2025-01-01`.
- Test events require `etf_id == momentum` and `t0 >= 2025-01-01`.
- Do not construct, persist, score, or inspect sealed prediction rows for any other ETF.
- Use the fixed `hgb_base_15_v1` feature set and training-only `economic_net_log_return` threshold selection.
- A sealed result cannot enter Tier 2/3 by itself; admission still requires full Tier 1 and DSR gates.

---

### Task 1: Test a strictly separated train/sealed frame builder

**Files:**

- Create: `etf_tricks/tier1/sealed.py`
- Create: `tests/etf_tricks/tier1/test_sealed.py`

- [ ] Write a failing test that asserts all training outcomes predate the sealed boundary and that the sealed frame contains only Momentum rows.
- [ ] Implement the minimal frame builder and boundary validator.
- [ ] Run focused tests.

### Task 2: Create an immutable sealed evaluator

**Files:**

- Create: `scripts/materialize_tier1_sealed_test.py`
- Modify: `etf_tricks/tier1/artifact.py`
- Modify: `tests/etf_tricks/tier1/test_artifact.py`

- [ ] Write a failing test that rejects a sealed artifact containing an unselected ETF or training label columns.
- [ ] Implement training-only calibration/threshold selection and a sealed-only output schema.
- [ ] Register the trial before any sealed prediction is generated.
- [ ] Run focused and full Tier 1 tests, then commit before live sealed evaluation.

### Task 3: Run one sealed Momentum evaluation and record only evidence

**Files:**

- Generate only (gitignored): `.artifacts/tier1/sealed-momentum-.../`
- Modify: `docs/afml/progress/decision-log.jsonl`

- [ ] Confirm no prior sealed artifact exists for this lineage.
- [ ] Run once and validate selected-ETF-only coverage, hash links, costs, and metrics.
- [ ] Record outcome and DSR-count implications; do not start Tier 2/3 unless all Tier 1 gates are independently satisfied.
