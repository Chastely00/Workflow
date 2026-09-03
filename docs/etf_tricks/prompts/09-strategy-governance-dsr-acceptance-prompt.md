# Authoritative Prompt - Strategy Governance, DSR, and Final Acceptance

Status: Approved authority as of 2026-09-03. Parent: `05-tiered-ml-strategy-master-prompt.md`.

## 1. Single responsibility

Govern research selection and issue the final evidence-backed strategy decision. Do not alter strategy code, rerun unregistered alternatives, conceal failed results, or execute live trades.

## 2. Append-only trial registry

Before a performance result affects a decision, record its trial. Keep rejected, failed, and superseded trials permanently.

```text
trial_id, parent_trial_id, created_at, completed_at,
research_question, hypothesis, code_commit,
upstream_artifact_hashes, feature_set_hash, label_config_hash,
tier1_config_hash, tier2_config_hash, allocation_config_hash,
execution_cost_policy_hash, fold_definition_hash,
train_validation_test_boundaries,
raw_trial_count, effective_independent_trial_count,
validation_metrics, selection_status, selection_reason
```

Count performance-observed alternatives that could influence selection: models, features, barriers, thresholds, calibration, Tier 2 variants, allocation policies, and HRP settings. Record non-performance ADF-gated FFD selection separately, but do not count it as a performance trial. Estimate effective independent trials conservatively from dependence among trial return paths.

## 3. Sealed test and statistics

Only one lineage may reach sealed test. If test outcomes change a decision, it is no longer sealed and every observed alternative joins the trial count.

On net paper-ledger returns report raw Sharpe, PSR, DSR, effective observations, skewness, kurtosis, raw/effective trial counts, cross-trial Sharpe variance, and all `08` allocation comparisons. `PAPER_TRADE_ELIGIBLE` requires `DSR >= 0.95`; DSR never compensates for broken data/PIT/execution evidence.

## 4. Final Strategy Acceptance Report

Publish a versioned, hash-linked report with source availability/PIT limits; locked design and folds; Tier 1/2 diagnostics; equal-capital/inverse-vol/HRP attribution; execution reconciliation; Sharpe/PSR/DSR/trial evidence; and status `NOT_READY`, `RESEARCH_ONLY`, or `PAPER_TRADE_ELIGIBLE`.

On failure, identify the failed layer and evidence: data/PIT, target/OOF/CV, statistical robustness, economic costs/capacity, or allocation. State justified next work, rejected directions, and the additional trial budget. A low DSR is not permission for unregistered tuning; HRP underperformance retains the superior simpler baseline.

On success, state that Paper-trade eligibility is not a future-profit guarantee. Propose only hypothesis-driven next work: regime robustness, cost/slippage/capacity stress, probability-calibration stability, HRP attribution, verified new features, and paper-versus-realized execution deviation.

## 5. Final gates

`PAPER_TRADE_ELIGIBLE` requires finalized lineage; fold-local Tier 1/2 and OOF hand-off; one locked sealed test; DSR >= 0.95; actual execution costs/taxes/integer shares/delays; all three allocation comparisons; and predeclared limits for drawdown, turnover, concentration, capacity, and execution failure. Passing never authorizes live orders.
