# Authoritative Prompt - Tier 3 Allocation and Paper Execution

Status: Approved authority as of 2026-09-03. Parent: `03-tiered-ml-strategy-master-prompt.md`.

## 1. Single responsibility

Given Tier 2 accepted long candidates, compare allocation policies and simulate constituent-level paper execution. Do not retrain Tier 1/2, select a model using sealed-test outcomes, or redefine labels.

## 2. Same-signal allocation comparison

At every decision time, hold candidate set, Tier 1/2 lineage, total capital, cost policy, raw-OPEN execution, position constraints, and corporate-action policy fixed. Produce exactly:

| Policy | Rule |
|---|---|
| `equal_capital` | Equal NTD among accepted ETFs |
| `inverse_vol` | Inverse past-only volatility, normalized under same caps |
| `hrp` | Hierarchical Risk Parity from past-only, availability-safe covariance |

HRP is an allocation overlay, not a required AFML meta-label component. Non-synchronous Dollar bars may not join by `bar_id`; covariance uses a common PIT-safe daily return calendar or another documented availability-safe method.

## 3. Execution ledger

Use the shared allocation/execution engine only. Entry and every actual exit use constituent raw OPEN on the next legal executable session. Compute integer shares, commission, sale tax, one-NTD minimum commission, cash feasibility, residual cash, delayed/rejected orders, forced exits, and verified corporate actions. Each submitted constituent order must contain at least one whole share; do not net fractional share intent across unrelated tickers or erase a sub-one-share residual. Apply commission/tax and NTD rounding to each actual constituent ticket, including every rolling-rebalance slice, rather than once to an ETF-level theoretical notional. The ledger's ticket-level cost policy must be versioned, explicit about buy/sell commission and sale-tax components, and reconciled separately from Tier 1's fixed all-in `0.001425/0.003` label policy; it must never infer that the latter is a legal or broker-specific ticket schedule. Do not assume fills during suspension, price absence, limit-state/non-executable market conditions, delisting, or unverified corporate-action transitions.

Persist a reconciled paper ledger: decision time, candidate/acceptance scores, policy, allocated NTD, target basket, order lifecycle, fills, costs, cash, holdings, daily NAV, gross/net PnL, exposure, HHI, turnover, active ETF count, and label-versus-execution gap. Split the gap into price/timing, minimum-commission/rounding, residual-cash, and execution-feasibility components where each is measurable. The ledger is the only strategy-level PnL source.

## 4. Required evidence

For every policy report paired same-signal net return, annualized volatility, Sharpe, Sortino, Calmar, maximum drawdown/duration, turnover, commission/tax, cost-to-assets, residual cash, HHI, maximum ETF weight, active ETF count, execution failure/delay rate, and capacity diagnostics.

Test equal inputs across policies, past-only covariance, exact cost reconciliation, delayed-trade behavior, allocation infeasibility, raw-price usage, and that allocation—not candidate—differences explain policy comparisons. Hand off three finalized net-return paths and ledger lineage to `09`.
