# Tier 3 Research-only Allocation Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline task-by-task execution. Steps use checkbox syntax for tracking.

**Goal:** Build manifest-backed, PIT-safe Tier 3 allocation and constituent-ledger contracts without producing research performance, orders, or paper-trade eligibility from the currently `NOT_SEALED` Tier 2 result.

**Architecture:** A Tier 3 input adapter validates finalized, ETF-local Tier 2 OOF handoffs and aligns accepted streams to a common daily return calendar by availability time. Allocation policy functions accept the same already-admitted signal snapshot and past-only covariance state, returning only weights and diagnostics. The existing `AllocationPlanner` remains the sole constituent integer-share/cost/schedule engine; a later sealed admission may compose it, but this stage does not invoke it on historical performance paths.

**Tech Stack:** Python 3.12, pandas, NumPy, SciPy/scikit-learn clustering where necessary, pytest, Parquet/JSON manifests.

**Spec:** `docs/afml/prompts/00-goal-prompt.md`, `03-tiered-ml-strategy-master-prompt.md`, `06-tier3-allocation-and-paper-execution-prompt.md`, `07-strategy-governance-dsr-acceptance-prompt.md`.

## Global Constraints

- Accept only finalized Tier 2 OOF handoffs with explicit `research_only=true`, `sealed_status=NOT_SEALED`, one ETF per source manifest, and no target/return/order fields.
- The contract may not return portfolio return, NAV, PnL, paper order, trade, or DSR/PSR fields while input is `NOT_SEALED`.
- All policies receive an identical signal snapshot, total capital, caps and cost-policy identifier. A policy may alter only allocation weights.
- Equal allocation is deterministic over accepted ETF ids. Inverse-vol and HRP use only observations whose availability time is no later than the decision timestamp; non-synchronous Dollar bar ids are never joined.
- If fewer than two eligible ETF streams exist, record `INSUFFICIENT_CROSS_ETF_UNIVERSE`; do not manufacture an HRP hierarchy or call identical singleton weights a policy comparison.
- Constituent execution is deferred until a sealed Tier 3 admission. When allowed, it must call `AllocationPlanner` and raw constituent OPEN data only; no synthetic ETF NAV OHLC may be used.

---

### Task 1: Validate research-only Tier 3 signal inputs

**Files:**
- Create: `etf_tricks/tier3/input_contract.py`
- Create: `tests/etf_tricks/tier3/test_tier3_input_contract.py`

**Interfaces:**
- Consumes: one or more Tier 2 OOF handoff dataframes and manifests.
- Produces: `Tier3SignalSnapshot` with accepted ETF ids, decision timestamp, score and immutable lineage hashes.

- [ ] Write failing tests for manifest status, forbidden fields, source ETF scope, unavailable decision timestamp, duplicate ETF/event keys and one-stream status.
- [ ] Implement strict schema/hash/status checks and return no allocation/PnL fields.
- [ ] Verify focused tests.

### Task 2: Implement pure, past-only policy-weight functions

**Files:**
- Create: `etf_tricks/tier3/policies.py`
- Create: `tests/etf_tricks/tier3/test_tier3_policies.py`

**Interfaces:**
- Consumes: an identical `Tier3SignalSnapshot`, a past-only common-calendar return frame, caps and decision timestamp.
- Produces: `AllocationWeights(policy, weights, covariance_asof, status, diagnostics)`.

- [ ] Test equal weights, inverse-vol normalization, covariance future-append invariance, identical signal inputs across policies, and singleton refusal.
- [ ] Implement deterministic equal and inverse-vol; implement HRP only when at least two eligible streams and a positive-definite, past-only covariance estimate are available.
- [ ] Verify all policy tests without computing PnL.

### Task 3: Gate research-only materialization and future sealed execution

**Files:**
- Create: `etf_tricks/tier3/artifact.py`
- Create: `tests/etf_tricks/tier3/test_tier3_artifact.py`
- Modify: `docs/afml/progress/decision-log.jsonl`

- [ ] Persist research-only signal/weight diagnostics with no PnL/order fields; refuse output claims of policy performance, paper ledger, PSR/DSR or eligibility.
- [ ] Record the current low-volatility-only input as `INSUFFICIENT_CROSS_ETF_UNIVERSE`, with no allocation comparison attempted.
- [ ] Commit source/tests/plan and run Tier 1, Tier 2, Tier 3 and governance tests. Do not commit `.artifacts` or user notebook outputs.

### Task 4: Future sealed admission only

**Files:**
- Modify only after a new, demonstrably unopened outcome interval exists: sealed admission records, Tier 3 materializer and shared execution wiring.

- [ ] Pre-register one locked lineage per ETF scope before access to test outcomes.
- [ ] Run paired Equal/Inverse-vol/HRP constituent ledgers through `AllocationPlanner`, raw next-session OPEN, integer shares and ticket-level costs.
- [ ] Run acceptance/DSR only after ledger reconciliation and sealed evidence; never retrofit this task to historical `NOT_SEALED` artifacts.
