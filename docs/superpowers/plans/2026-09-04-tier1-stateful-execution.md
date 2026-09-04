# Tier 1 Stateful Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task.

**Goal:** Convert ETF-local OOF probabilities into PIT-safe non-overlapping ETF position ledgers and barrier diagnostics.

**Architecture:** Keep event labels and OOF `p1` immutable. A new Tier 1 policy module consumes only ordered OOF predictions and predeclared state-policy parameters, emits transitions and an ETF-level ledger; a separate diagnostics module consumes labels and candidate IDs.

**Tech Stack:** Python, pandas, pytest, immutable parquet artifacts.

**Spec:** `docs/afml/prompts/00-goal-prompt.md`, `04-tier1-directional-label-and-model-prompt.md`, `07-strategy-governance-dsr-acceptance-prompt.md`.

## Global Constraints

- Each ETF is isolated; no pooled fitted state.
- OOF predictions only; no label, `t1`, return, or future path enters the policy input.
- Policy parameters are registered before OOF performance is read.
- Only state transitions charge execution costs; no live orders.

### Task 1: Stateful signal-policy contract

**Files:** Create `etf_tricks/tier1/stateful_policy.py`; create `tests/etf_tricks/tier1/test_stateful_policy.py`.

- [ ] Write a failing test with ordered `p1=[0.6,0.7,0.4]`, enter threshold `0.2`, exit threshold `-0.1`, asserting one `flat_to_long` and one `long_to_flat` transition.
- [ ] Run `pytest tests/etf_tricks/tier1/test_stateful_policy.py -q` and confirm failure.
- [ ] Implement `build_stateful_transitions(oof, entry_score, exit_score) -> DataFrame`; score is cumulative `p1-0.5` only while flat/long, reset after a transition, require unique ordered `etf_id,t0_bar_id` and reject future columns.
- [ ] Re-run the test and commit `feat(afml): add stateful Tier1 signal policy`.

### Task 2: OOF execution ledger

**Files:** Create `etf_tricks/tier1/stateful_ledger.py`; create `tests/etf_tricks/tier1/test_stateful_ledger.py`.

- [ ] Write a failing hand-calculated two-transition test asserting only entry/exit transitions receive `0.001425`/`0.003` proportional costs and candidates during an open position receive zero ticket cost.
- [ ] Run the test and confirm failure.
- [ ] Implement `materialize_etf_ledger(transitions, execution_snapshot) -> (daily_nav, trades)` using raw OPEN, valid sessions, and no overlapping ETF positions; return explicit unavailable status on missing execution prices.
- [ ] Re-run tests and commit `feat(afml): materialize non-overlapping Tier1 ledger`.

### Task 3: Barrier diagnostics

**Files:** Create `etf_tricks/tier1/barrier_diagnostics.py`; create `tests/etf_tricks/tier1/test_barrier_diagnostics.py`.

- [ ] Write a failing fixture test asserting separate ALL_EVENTS/CANDIDATES summaries with touch counts, time-to-touch, MFE, MAE and post-upper continuation.
- [ ] Run the test and confirm failure.
- [ ] Implement `summarize_barriers(events, candidates, bars) -> DataFrame`; reject unresolved events and prohibit diagnostic data from the policy input.
- [ ] Re-run tests and commit `feat(afml): add Tier1 barrier diagnostics`.

### Task 4: Momentum materialization and governance

**Files:** Create `scripts/materialize_tier1_stateful_momentum.py`; modify `etf_tricks/governance/trials.py`; test `tests/etf_tricks/tier1/test_stateful_policy.py`.

- [ ] Register the fixed baseline policy before reading its ledger result.
- [ ] Materialize versioned Momentum OOF policy, diagnostics, ledger manifests, and trial result without overwriting prior event-level OOF artifacts.
- [ ] Run targeted tests plus the script against immutable v5; record turnover, actual costs, holding duration, barrier diagnostics, and OOF status.
- [ ] Commit `feat(afml): materialize Momentum stateful OOF evidence`.
