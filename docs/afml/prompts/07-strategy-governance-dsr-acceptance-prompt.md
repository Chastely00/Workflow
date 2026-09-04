# Authoritative Prompt - Strategy Governance, DSR, and Final Acceptance

Status: Approved authority as of 2026-09-04. Parent: `03-tiered-ml-strategy-master-prompt.md`.

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
train_validation_test_boundaries, etf_scope, model_scope,
raw_trial_count, effective_independent_trial_count,
validation_metrics, selection_status, selection_reason
```

Count performance-observed alternatives that could influence selection: each ETF-local model/feature/barrier/threshold/calibration/Tier 2 variant, every pooled benchmark, allocation policies, and HRP settings. A choice among 13 ETF-local results is a multiple-comparison decision even when all models share a specification. Record non-performance ADF-gated FFD selection separately, but do not count it as a performance trial. Estimate effective independent trials conservatively from dependence among trial return paths.

Before viewing OOF, register a finite state-action table. `INSUFFICIENT_MATURE_EVENTS` may only extend the same design backward over already available history; if still insufficient, abandon it. It has no comparable performance outcome and adds zero DSR trials. Any OOF-observed action that changes model, feature, barrier/horizon, threshold, calibration, ETF selection, or allocation is a registered performance alternative and increases the effective trial count. Waiting for future data, relaxing costs/labels, or changing ETF definition is not an allowed insufficient-sample action.

## 3. Sealed test and statistics

Each sealed-test admission must state its `etf_scope`, model scope, train end, and untouched outcome interval. Only one pre-locked lineage may enter each ETF scope. If that scope's test outcomes change a decision, it is no longer sealed and every observed alternative joins the trial count. A model that had physical/logical access to the scope's outcome labels before lock must be marked `NOT_SEALED`; it cannot be promoted using a nominal sealed test.

On net paper-ledger returns report raw Sharpe, PSR, DSR, effective observations, skewness, kurtosis, raw/effective trial counts, cross-trial Sharpe variance, and all `08` allocation comparisons. `PAPER_TRADE_ELIGIBLE` requires `DSR >= 0.95`; DSR never compensates for broken data/PIT/execution evidence.

## 4. Final Strategy Acceptance Report

Publish a versioned, hash-linked report with source availability/PIT limits; locked design and folds; Tier 1/2 diagnostics; equal-capital/inverse-vol/HRP attribution; execution reconciliation; Sharpe/PSR/DSR/trial evidence; and status `NOT_READY`, `RESEARCH_ONLY`, or `PAPER_TRADE_ELIGIBLE`.

On failure, identify the failed layer and evidence: data/PIT, target/OOF/CV, statistical robustness, economic costs/capacity, or allocation. State justified next work, rejected directions, and the additional trial budget. A low DSR is not permission for unregistered tuning; HRP underperformance retains the superior simpler baseline.

On success, state that Paper-trade eligibility is not a future-profit guarantee. Propose only hypothesis-driven next work: regime robustness, cost/slippage/capacity stress, probability-calibration stability, HRP attribution, verified new features, and paper-versus-realized execution deviation.

## 5. Final gates

`PAPER_TRADE_ELIGIBLE` requires finalized lineage; fold-local Tier 1/2 and expanding OOF hand-off; DSR >= 0.95; actual execution costs/taxes/integer shares/delays; all three allocation comparisons; and predeclared limits for drawdown, turnover, concentration, capacity, and execution failure. A later sealed/paper interval is optional monitoring, never a reason to withhold already OOF-qualified deployment. Passing never authorizes live orders.
