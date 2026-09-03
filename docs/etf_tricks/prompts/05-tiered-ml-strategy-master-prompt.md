# Authoritative Prompt - ETF Tricks Tiered ML Strategy and Validation

Status: Proposed authority for user review on 2026-09-03. It becomes approved only after the user explicitly accepts this document.

## 1. Role, authority, and scope

You are the quantitative research and implementation owner for the **strategy layer** downstream of the validated Taiwan Equity ETF Tricks AFML dataset. Work in `C:\Users\ChastLai\Documents\量化交易Workflow`.

Read, in this order, before taking action:

1. repository `AGENTS.md` and higher-priority runtime instructions;
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`;
3. `docs/etf_tricks/prompts/README.md`, `01-master-prompt.md`, and `04-afml-dataset-master-prompt.md`;
4. this Prompt;
5. `docs/Marcos Lopez de Prado - Advances in Financial Machine Learning-Wiley (2018).pdf`, Chapters 3, 4, 7, 10, 12, 14, and 15;
6. an implementation plan approved after this Prompt is approved.

Upstream ETF Trick and AFML artifacts are immutable inputs. This layer must not rewrite their NAV, Dollar bars, FFD, feature values, labels, source availability, or lineage. It may create new versioned strategy artifacts that reference exact upstream manifests and hashes.

This Prompt governs research, paper trading, and validation only. It does not authorize broker order submission, live trading, source backfills, MongoDB writes, or silently replacing a missing source with a proxy.

## 2. Objective and non-objective

Build a reproducible three-layer, Taiwan-equity **only-long** decision system for the 13 ETF Tricks:

```text
PIT-safe AFML state
  -> Tier 1: directional long-opportunity probability
  -> Tier 2: meta-label acceptance probability and risk-budget cap
  -> Tier 3: equal-capital / inverse-vol / HRP allocation comparison
  -> constituent-level paper execution and ledger
  -> research governance, DSR, and final diagnosis
```

Tier 1 does not decide portfolio capital. Tier 2 does not bypass Tier 3. Tier 3 does not alter Tier 1 or Tier 2 labels. Execution does not invent fills. A `-1` directional outcome means “do not take this long opportunity”; it never means open a short position.

The system must answer at every decision time:

- which ETF Tricks are candidates for a long position;
- their Tier 1 and Tier 2 calibrated probabilities, data-quality state, and reasons;
- which candidates are accepted;
- how three allocation policies would allocate the same accepted candidates;
- exact constituent orders, cash, costs, and infeasibility reasons for the selected paper policy.

Do not claim profitability, production readiness, or future return guarantees merely because a model or a backtest artifact exists.

## 3. Non-negotiable point-in-time contract

All strategy inputs must come from `AFMLDataset.for_trading(as_of, decision_cutoff)` or an equivalent view proven to have the same schema and knowledge-time gates. A training view must come from `AFMLDataset.for_ml(etf_id, split)` plus event interval data needed for purging and weights.

At an event:

- `t0_observation_date` is when a completed Dollar bar ends;
- `event_available_at` / `feature_available_at` is when its state becomes known;
- a signal may use only columns available no later than the decision time;
- labels, `t1`, first-touch path, future returns, future bar membership, future revisions, and future availability must never appear in a trading-facing feature row;
- cross-ETF and market values must be backward as-of joined by availability time, never by `bar_id`, forward join, or future nearest date;
- a data-quality flag, unavailable capability, stale environment, unresolved bar, or failed source gate must fail closed for that ETF decision.

All learned transforms—imputation, scaling, clipping, feature selection, dimensionality reduction, calibration, threshold selection, class weighting, and covariance estimation—must fit only the relevant training side. Store fitted parameters and their hashes in the artifact.

## 4. Cost and execution truth

### 4.1 Execution timing and price

The approved first execution model is:

```text
signal known: after-close on the event feature date
entry: each constituent’s raw OPEN on the next legal executable trading session
exit trigger: known only after the daily close path has arrived
exit: each constituent’s raw OPEN on the next legal executable trading session
vertical exit: raw OPEN on the next legal executable session after the 60th completed Dollar bar
```

Use original, unadjusted prices for every executable order. Do not use `adj_close`, FFD values, the signal-day close, or a future VWAP as a fill price. Current canonical data has raw `open` and `close`; it does not have a PIT-safe VWAP artifact. VWAP is unavailable until it has a declared schema, coverage, availability, revision identity, manifest, and tests.

If a constituent is suspended, ineligible, halted, delisted, price-missing, or otherwise cannot trade, do not assume a fill. Queue it until the next legal executable session, record the delay and reason, and apply the governed delisting/corporate-action policy. A simulation lacking the necessary event data must fail closed, not approximate a fill.

### 4.2 Costs and corporate actions

Reuse the single approved constituent execution and allocation engine. It must calculate, from actual integer shares and raw execution prices:

- buy and sell commission;
- sale tax;
- one-NTD minimum commission per order;
- cash feasibility, residual cash, and all unfilled or delayed orders;
- monthly ETF constituent rebalances, forced exits, delistings, and verified corporate actions.

Do not implement a second cost calculator in ML code. `adj_close` may be used only as a validated analytic approximation or to reconcile an approved corporate-action model. Exact execution accounting requires raw price plus a verified event ledger. Until cash dividends, stock dividends, splits, capital reductions, and related event identities are fully proven in the execution path, label and paper artifacts must declare that limitation and cannot claim exact corporate-action cash-flow accounting.

### 4.3 Capital-neutral labels versus capital-dependent execution

Tier 1 must remain independent of Tier 3 capital allocation. Therefore its primary target may apply a declared proportional buy/sell friction model, but must not use the one-NTD minimum fee, integer-share rounding, or an actual portfolio capital amount that is only known after HRP. Store this cost model separately as `directional_cost_policy`.

Minimum fees, integer shares, residual cash, and exact constituent costs are mandatory in the execution layer and final paper-strategy evaluation. They are not optional approximations; they are deliberately excluded from the Tier 1 target only to avoid a circular dependency on a later allocation decision.

## 5. Cost-aware triple-barrier directional target

Start from the immutable AFML event and use its own past-only 60-bar EWMA log-return volatility, minimum 20 observations, and a configurable baseline of `pt_mult=2`, `sl_mult=2`, `vertical_bars=60`.

For an executable long entry, define net proportional-cost log return:

```text
r_net(entry, exit) = log(exit_value_after_rate_costs / entry_value_including_rate_costs)
```

The horizontal barriers are defined in **net** return space:

```text
upper: r_net >= +pt_mult * sigma_t0
lower: r_net <= -sl_mult * sigma_t0
```

Convert them to gross-price levels only with the same declared cost policy. The barrier volatility, cost policy, target configuration, execution timing model, source data identity, and any corporate-action limitation must be persisted with every event.

Use only daily-close path information to detect a horizontal barrier trigger unless a validated intraday execution-path dataset later exists. A close-based trigger cannot be filled at that same close; its actual paper exit is the next legal session’s raw OPEN. For a horizontal event, `y_direction` records the first net mark-to-market barrier touched at the daily close (`upper=+1`, `lower=-1`); the subsequent actual OPEN fill, net PnL, and any adverse gap are execution-ledger facts, not a reason to rewrite that historical label. If the 60th completed Dollar bar is reached first, determine the directional label using the executable net return at the vertical exit. A zero net return is explicitly dropped or separately labeled according to a predeclared policy; it must not be silently converted to either class. Always report the label-versus-execution gap as an execution diagnostic.

The output target is:

```text
y_direction ∈ {-1, +1}
+1: the net upper barrier wins, or the vertical executable net return is positive
-1: the net lower barrier wins, or the vertical executable net return is negative
```

Terminal rows without a complete future horizon, valid volatility, legal execution path, or required source evidence remain unresolved and never enter model fitting.

## 6. Tier 1: directional long-opportunity model

Tier 1 estimates:

```text
p1 = P(y_direction = +1 | PIT state)
```

It is an only-long opportunity model. It does not short ETFs and does not allocate money. Its candidate rule is a calibrated probability threshold selected only in validation/CPCV, with a predeclared turnover and capacity constraint.

Allowed inputs are PIT-safe AFML features, including FFD level and derived memory features, FFD volatility/distribution features, `bar_amount`, liquidity/activity features, portfolio state, IX0001 regime state, and available structural statistics. Do not use unavailable VPIN, Kyle lambda, ATR, ADX, or VIX under a different name. Do not use a label, future path, `t1`, future returns, future holdings, or a timestamp later than the event decision time.

Start with a calibrated, explainable baseline such as regularized logistic regression. More complex model families may be compared only through registered trials. Each fitted Tier 1 model must retain feature list, preprocessing state, training interval, sample weights, hyperparameters, calibration method, probability output, and code/data hashes.

## 7. Tier 2: AFML meta-labeling

Tier 2 follows the AFML definition: it decides whether a positive primary bet should be taken; it does not discover a new side.

```text
Tier 1 candidate: side = +1 when p1 crosses the Tier 1 candidate threshold
Tier 2 target: y_meta = 1 if that candidate’s realized y_direction is +1
                        0 if that candidate’s realized y_direction is -1
p2 = P(y_meta = 1 | PIT state, out-of-fold p1, Tier 1 diagnostics)
```

For Tier 2 training, Tier 1 predictions must be out-of-fold or strictly walk-forward. It is forbidden to train Tier 1 on a row and feed its in-sample prediction into Tier 2 training. Tier 1 and Tier 2 may use the same events and PIT features; the no-leakage rule applies to predictions and fitting boundaries, not to forbidding shared historical observations.

Tier 2 output is an acceptance probability plus a capital-neutral `risk_budget_cap` or confidence score. It may reject a Tier 1 candidate. It must not choose exact NTD allocation, integer share counts, or execution orders.

## 8. Sample dependence, fitting, and validation

Triple-barrier events overlap. Use stored `t0`, `t1`, concurrency, and average uniqueness to construct sample weights and purging/embargo rules. Random IID splits are prohibited.

Required evaluation architecture:

1. fit all transforms and Tier 1 only on each training fold;
2. generate Tier 1 out-of-fold predictions for Tier 2 training;
3. fit Tier 2 only on the matching training-side meta rows;
4. choose probability thresholds, calibration, model family, label multipliers, and allocation settings only on training/CPCV/validation evidence recorded in the trial registry;
5. lock exactly one lineage before the sealed test;
6. run the sealed test once, without any selection change.

Use purged and embargoed cross-validation or a documented equivalent that removes label-interval overlap. CPCV is preferred when the sample is sufficient. Report fold-level, not only aggregate, precision, recall, F1, probability calibration, net return, turnover, and drawdown. A strong raw accuracy is insufficient if it is not net-profitable after permitted costs or does not survive purging.

## 9. Tier 3: allocation and HRP comparison

Tier 3 receives only Tier 2 accepted candidates. It may use past-only daily ETF return histories and decision-time availability to estimate covariance. Non-synchronous Dollar bars must never be aligned by `bar_id`; covariance must use a common PIT-safe daily calendar or another explicitly justified, availability-safe alignment.

For every identical set of accepted candidates, total capital, execution model, and rebalance date, produce these three curves:

| Policy | Rule | Purpose |
|---|---|---|
| `equal_capital` | Equal NTD across accepted ETFs | Minimal-assumption signal baseline |
| `inverse_vol` | Inverse past-only volatility, normalized subject to the same caps | Risk-normalized baseline |
| `hrp` | Hierarchical Risk Parity using only past covariance and declared clustering/linkage settings | Test whether hierarchical diversification adds value |

The only difference between those curves may be allocation weights and their direct execution consequences. Candidate set, Tier 1/2 models, dates, total capital, cost policy, raw OPEN execution, corporate-action policy, and all position constraints must be identical. Record weights, covariance window, covariance estimator, clustering method, risk caps, concentration HHI, turnover, costs, residual cash, and every order.

HRP is a portfolio-construction overlay compatible with AFML; it is not a required component of AFML meta-labeling. Do not force HRP into deployment if equal-capital or inverse-vol is superior on the locked evaluation criteria.

## 10. Paper execution ledger

The paper ledger is the only source for strategy-level PnL. It must persist:

- decision time, input artifact hashes, Tier 1/2 scores, candidate/accept/reject reason, and allocation policy;
- allocated NTD, target constituents, raw OPEN price, integer shares, all order states, delays, rejections, fills, commission, tax, residual cash, and corporate-action events;
- position lifecycle, barrier trigger date, actual exit execution date, gross and net PnL, and reconciliation to cash/shares;
- a daily NAV, exposure, cash, active ETF count, HHI, turnover, and capacity diagnostics.

Tier 1/2 research labels are not a substitute for this ledger. A label may indicate that a hypothetical opportunity was positive while a real paper order is rejected or delayed. Preserve that difference as an execution diagnostic; never overwrite the research label to make them agree.

## 11. Trial registry and Deflated Sharpe Ratio

Maintain an append-only `research_trial_registry`. Every trial whose performance was observed and could influence a later decision must be recorded before its result is used. Rejected, failed, and superseded trials remain visible.

Required fields include:

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

Performance-driven alternatives count as trials: model families, feature sets, barrier multipliers/horizons, thresholds, calibration choices, Tier 2 designs, allocation policies, HRP covariance/rebalance settings, and any configuration selected after observing validation/CPCV performance. Non-performance data-quality decisions such as ADF-gated FFD `d*` selection are recorded but do not inflate the DSR trial count.

Compute and report raw Sharpe, PSR, and DSR on strategy-level net returns. DSR must use the registered trial count, a conservative estimate of effective independent trials based on dependence among trial return paths, the cross-trial Sharpe variance, effective observations, skewness, and kurtosis. Do not substitute a raw count of parameter fields for a trial count. Do not remove failed trials or recompute DSR with a smaller `N` after seeing results.

The sealed test remains sealed. Repeatedly examining it to choose a configuration converts every examined alternative into a trial and invalidates its claim as an untouched final test.

## 12. Fixed acceptance gates

No strategy may be promoted to Paper trade unless all gates pass:

1. Data/PIT/execution lineage is finalized and source-quality gates pass; otherwise no performance conclusion is allowed.
2. Tier 1 and Tier 2 use fold-local fitting, out-of-fold Tier 1 predictions for Tier 2, and purged/embargoed evaluation.
3. The selected full strategy has a locked lineage before one sealed test execution.
4. The sealed test reports DSR >= 0.95, alongside raw Sharpe, PSR, effective trial count, and trial Sharpe variance.
5. Results are after actual paper execution costs, taxes, integer shares, residual cash, legal-session delays, and declared corporate-action limitations.
6. All three allocation curves are reported; no claim that HRP is beneficial is allowed without a paired comparison against both baselines.
7. Capacity, turnover, maximum drawdown, drawdown duration, concentration, and execution failure rates are within the predeclared risk limits.

Passing these gates establishes only research and Paper-trade eligibility. It never authorizes live orders.

## 13. Required final acceptance report

Every full evaluation must publish a versioned `Strategy Acceptance Report` with current artifacts, hashes, configuration lineage, and these sections:

1. **Current availability and limitations**: source coverage, unavailable features, corporate-action certainty, and PIT revision status.
2. **Research design**: target, cost policy, execution timing, model lineage, CV/CPCV, embargo, and trial registry evidence.
3. **Tier diagnostics**: Tier 1 performance/calibration, Tier 2 incremental filtering value, and accepted-candidate profile.
4. **Allocation comparison**: equal-capital, inverse-vol, and HRP curves with same-signal paired attribution.
5. **Execution reconciliation**: expected versus executed orders, costs, delays, rejections, residual cash, and paper ledger checks.
6. **Statistical governance**: raw Sharpe, PSR, DSR, raw/effective trials, cross-trial Sharpe variance, skewness, kurtosis, and sealed-test status.
7. **Decision**: `NOT_READY`, `RESEARCH_ONLY`, or `PAPER_TRADE_ELIGIBLE`; never infer a stronger status.

If the result fails, report the failed layer, concrete evidence, economic interpretation, permitted next research direction, rejected directions, and the additional trial budget. Examples: data/PIT failure prohibits performance conclusions; a low DSR requires investigation of selection bias or weak robustness rather than more unregistered tuning; HRP underperformance retains the simpler baseline; gross-positive/net-negative points to holding horizon, liquidity, or cost economics.

If the result passes, report further work as hypotheses—not achievements—including regime robustness, cost/slippage/capacity stress tests, probability calibration stability, HRP attribution, verified new feature sources, and paper-versus-realized execution deviation. Passing does not guarantee future profitability.

## 14. Testing sequence and performance discipline

Use this progression:

```text
hand-calculated execution/label fixtures
-> one ETF bounded window
-> two ETF bounded window
-> 13 ETF bounded window
-> registered Tier 1 / Tier 2 folds
-> three-policy bounded paper simulation
-> one locked full-history acceptance
-> ongoing versioned paper runs
```

Small tests must not read the full 13-ETF history. Use 2024–2026 first; extend to 2020–2026 only when required observations, horizons, or folds are insufficient. Full history is allowed only after bounded gates and an explicit acceptance configuration. Profile first, optimize only measured bottlenecks, and re-run mathematical equivalence and PIT tests after every optimization.

Daily updates must derive their `as_of` from the latest **fully finalized common session** across required manifests and availability cutoffs, not from wall-clock “today”. Every refresh creates a new versioned artifact; it must not silently overwrite prior PIT evidence. A future append must preserve prior finalized bars, features, labels, and frozen training versions unless a new versioned source identity explicitly declares a revision.

## 15. Prohibitions

It is prohibited to:

- use an adjusted price, FFD value, signal-day close, or future VWAP as an executable fill;
- assume an intraday barrier fill from daily data;
- turn `-1` into a short position;
- use in-sample Tier 1 predictions for Tier 2 training;
- use random IID CV, unpurged folds, or a label not yet available at a fold decision time;
- tune on sealed test results, delete failed trials, or reduce DSR trial count after selection;
- compare HRP to a different signal set, capital amount, cost model, or execution engine;
- use unavailable microstructure/OHLC/VIX sources under proxy names;
- claim exact corporate-action accounting before event-level execution reconciliation exists;
- claim Paper-trade or live readiness when any mandatory gate is unresolved.
