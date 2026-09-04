# Tier 1 Long-History OOF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a pre-registered, chronological 2005–2024 Tier 1 diagnostic while reserving 2025–2026 untouched for a later sealed test.

**Architecture:** Extend the read-only Tier 1 lab with explicit research-entry and research-outcome cutoffs. The lab must use only resolved events whose decision and outcome both precede the sealed interval, then make expanding purged OOF folds. A command-line runner will bind artifact hashes, append a registry record before model fitting, write only OOF handoff/diagnostics artifacts, and never inspect sealed performance.

**Tech Stack:** Python 3.12, pandas, scikit-learn, PyArrow, pytest.

**Spec:** `docs/afml/prompts/04-tier1-directional-label-and-model-prompt.md`

## Global Constraints

- Diagnostic selection data: `t0 <= 2024-12-31` and `t1 < 2025-01-01`.
- Sealed data: any event with `t0 >= 2025-01-01`; never model-fit, predict, score, or inspect it in this task.
- Use chronological expanding folds, event end-time purging, governed trading sessions, and ETF-local average uniqueness.
- Every model/feature/threshold decision must be recorded before performance observation.
- Tier 2 and Tier 3 remain prohibited unless the immutable Tier 1 gate passes.

---

### Task 1: Make the lab enforce an outcome-mature research cut-off

**Files:**

- Modify: `etf_tricks/tier1/lab.py`
- Modify: `tests/etf_tricks/tier1/test_lab.py`

- [ ] Write a failing test with one pre-sealed mature event and one event resolving in the sealed interval; assert only the mature event reaches OOF construction.
- [ ] Verify the test fails because `run_oof` has no cut-off interface.
- [ ] Add `research_t0_end` and `research_outcome_before` arguments; reject invalid boundary ordering and filter before folds.
- [ ] Run focused and full Tier 1 tests.

### Task 2: Publish a pre-registered OOF runner

**Files:**

- Create: `scripts/materialize_tier1_long_history_oof.py`
- Modify: `tests/etf_tricks/tier1/test_artifact.py`

- [ ] Write a failing helper test that rejects a sealed boundary which is not strictly after research outcome coverage.
- [ ] Implement the runner with immutable input hashes, pre-fit `TrialRegistry` append, OOF-only handoff output, per-ETF diagnostics, and immutable gate report.
- [ ] Run all Tier 1 tests and a `--help` smoke test.

### Task 3: Run and certify the diagnostic

**Files:**

- Generate only (gitignored): `.artifacts/tier1/long-history-.../`
- Generate only (gitignored): `.artifacts/afml_governance/tier1-gate-long-history-.../`
- Modify: `docs/afml/progress/decision-log.jsonl`

- [ ] Append the registry record before fitting.
- [ ] Run the runner once using the fixed pre-registered feature/model configuration.
- [ ] Verify no sealed event appears in frame, predictions, handoff, diagnostics, or gate metrics.
- [ ] Append factual hashes, coverage, and gate status without beginning Tier 2/3 if the gate fails.
