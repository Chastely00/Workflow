# Authoritative Prompt - Tier 2 Meta-Labeling

Status: Approved authority as of 2026-09-03. Parent: `03-tiered-ml-strategy-master-prompt.md`.

## 1. Single responsibility

Build only the AFML Tier 2 meta-label layer. It filters a positive Tier 1 long candidate; it never discovers a new side, fixes an upstream label, allocates NTD, or issues orders.

```text
Tier 1 candidate side=+1 -> y_meta in {0,1} -> calibrated p2, accept/pass, capital-neutral risk-budget cap
```

## 2. Data contract and target

Read finalized `06` hand-off and immutable AFML state. Tier 1 and Tier 2 may share historical events and PIT features. Tier 2 training may use only Tier 1 out-of-fold or strictly walk-forward predictions for matching rows; in-sample Tier 1 predictions are forbidden.

For a Tier 1 candidate, define `y_meta=1` when finalized directional outcome is `+1`, otherwise `0` when it is `-1`. Exclude unresolved, data-quality-failed, or non-candidate rows under a predeclared rule. Never select this definition after seeing test results.

## 3. Fitting and hand-off

Fit transforms, meta model, calibration, and acceptance threshold on the training side only, with the same purged/embargo/sample-dependence discipline as Tier 1. Report incremental value against taking every Tier 1 candidate: precision, recall, F1, calibration, acceptance rate, expected net edge, holding interval, turnover implication, and fold stability.

Output only `p2`, accept/pass, capital-neutral confidence or `risk_budget_cap`, decision availability time, source/model hashes, and explicit reason codes. A rejected candidate has no order. An accepted candidate is not a capital allocation.

The `08` hand-off is PIT-safe accepted candidates only: no future label, `t1`, touch path, or realized return.

Test rejection of in-sample Tier 1 predictions, OOF-fold lineage, Tier 2 inability to create an absent Tier 1 candidate, fold-local calibration/thresholds, absence of future labels, and absence of allocation/order fields.
