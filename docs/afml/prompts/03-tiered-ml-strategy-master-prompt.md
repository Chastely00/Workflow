# Authoritative Prompt - ETF Tricks Tiered ML Strategy Coordinator

Status: Approved authority as of 2026-09-03.

## 1. Purpose and authority

You coordinate—not implement indiscriminately—the three-layer only-long strategy downstream of the immutable ETF Tricks AFML dataset. Work in `C:\Users\ChastLai\Documents\量化交易Workflow`.

Read `AGENTS.md`, the approved upstream ETF prompts and AFML dataset prompts (`01`, `02`), this coordinator, then exactly the child prompt for the current task. Read AFML Chapters 3, 4, 7, 10, 12, 14, and 15 before model, validation, allocation, or DSR work.

This coordinator owns cross-layer contracts only. It does not authorize a child agent to alter upstream NAV, Dollar bars, FFD, features, labels, DataAnalysts artifacts, source manifests, or shared execution accounting.

## 2. Fixed architecture

```text
immutable AFML PIT state
  -> 04 Tier 1: directional {-1,+1} long opportunity
  -> 05 Tier 2: meta-label {0,1} accept/pass and capital-neutral confidence
  -> 06 Tier 3: same candidates under equal-capital, inverse-vol, and HRP
  -> 06 constituent-level paper execution ledger
  -> 07 trial governance, DSR, sealed-test decision, final report
```

`-1` never opens a short. Tier 1 does not allocate NTD. Tier 2 does not issue orders. Tier 3 does not alter Tier 1/2 labels. The paper ledger, not a research label, is the source of strategy PnL.

## 3. Global PIT and execution invariants

- Trading-facing inputs come only from `AFMLDataset.for_trading(as_of, decision_cutoff)` or a proven equivalent; model fitting uses `for_ml(...)` plus event interval evidence.
- No feature, calibration, imputer, scaler, threshold, covariance estimate, label, or model may read data unavailable at its decision time.
- Cross-ETF values join backward by availability time, never by bar id or a future observation.
- Entry and actual exits use each constituent’s raw OPEN on the next legal executable session. A daily-close trigger cannot fill at that same close.
- Tier 1's capital-neutral target uses only registered proportional friction `buy_cost_rate=0.001425` and `sell_cost_rate=0.003`; the constituent ledger additionally applies actual integer shares and the one-NTD minimum commission after Tier 3 allocates capital.
- ETF Trick has no tradable synthetic OHLC. `close_path_*` values are not execution prices or Tier 1 OHLC features; horizontal barriers are confirmed solely from the daily NAV close path.
- Reuse the shared allocation/execution engine for integer shares, commission, sale tax, minimum one-NTD commission, cash, delays, delisting, and verified corporate actions. No child may create a competing cost model.
- Every output is versioned and manifest-backed with code/config/input hashes. Future append may not rewrite finalized historical evidence.

## 4. Child hand-offs and gates

| Child | May produce | Must hand off | Gate before next child |
|---|---|---|---|
| `04` | Tier 1 target, OOF predictions, model lineage | directional candidate stream | PIT, cost-label, double-touch, purged-CV tests pass |
| `05` | meta labels, OOF-aware Tier 2 probabilities | accepted candidates and risk-budget caps | no in-sample Tier 1 prediction; calibration tests pass |
| `06` | three allocation curves, paper orders, ledger | net-return strategy paths | same candidates/capital/execution across policies; ledger reconciles |
| `07` | trial registry, DSR/PSR, final report | decision and next actions | sealed test, all trial evidence, full diagnostics complete |

No child begins its next-stage work until the prior hand-off artifact is finalized and reviewed. A failed gate is evidence, not permission to bypass the child contract.

## 5. Promotion policy

Only `07` may assign `NOT_READY`, `RESEARCH_ONLY`, or `PAPER_TRADE_ELIGIBLE`. Neither passing unit tests nor a high in-sample Sharpe permits a stronger status. This Prompt never authorizes live orders.
