# ETF Tricks Authoritative Prompt Set

Goal IDs: upstream `GOAL-ETF-TRICKS-001`; downstream `GOAL-ETF-AFML-DATASET-001`.

This directory is the reusable prompt package for the Taiwan-equity ETF Tricks project.

## Authority order

When two instructions conflict, apply this order and report the conflict explicitly:

1. Repository `AGENTS.md` and higher-priority runtime instructions.
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md` — approved quantitative and accounting contract.
3. `docs/etf_tricks/prompts/01-master-prompt.md` — operational implementation and validation prompt.
4. `docs/etf_tricks/prompts/02-performance-optimization-prompt.md` — result-equivalent profiling and performance-optimization contract.
5. `docs/etf_tricks/prompts/04-afml-dataset-master-prompt.md` — approved downstream Dollar bar, FFD, feature, label, PIT, and validation contract; applies only after the upstream outputs are validated.
6. `docs/etf_tricks/prompts/05-tiered-ml-strategy-master-prompt.md` — approved cross-layer Tier 1/Tier 2/Tier 3 coordinator.
7. `docs/etf_tricks/prompts/06-tier1-directional-label-and-model-prompt.md` — approved Tier 1 target, model, and double-touch contract.
8. `docs/etf_tricks/prompts/07-tier2-meta-labeling-prompt.md` — approved Tier 2 OOF meta-label contract.
9. `docs/etf_tricks/prompts/08-tier3-allocation-and-paper-execution-prompt.md` — approved allocation comparison and paper-execution contract.
10. `docs/etf_tricks/prompts/09-strategy-governance-dsr-acceptance-prompt.md` — approved trial, DSR, and final acceptance contract.
11. The relevant file under `docs/etf_tricks/prompts/etfs/` — one ETF's unique contract.
12. `docs/superpowers/plans/2026-08-26-etf-tricks-implementation.md` — task ordering and file-level implementation plan.
13. Later implementation notes that do not amend an authority above.

The Goal Prompt coordinates progress but does not override the approved design.

## Files

- `00-goal-prompt.md`: persistent goal prompt, under 4,000 characters.
- `01-master-prompt.md`: whole-system implementation and validation prompt.
- `02-performance-optimization-prompt.md`: representative-window profiling, mathematical audit, optimization, equivalence validation, and one-time full-history acceptance prompt.
- `03-afml-dataset-goal-prompt.md`: approved downstream Goal for Dollar bars, FFD, structural features, features, and labels.
- `04-afml-dataset-master-prompt.md`: approved downstream authoritative research contract.
- `05-tiered-ml-strategy-master-prompt.md`: cross-layer Tier 1/Tier 2/Tier 3 coordinator.
- `06-tier1-directional-label-and-model-prompt.md`: Tier 1 directional target, model, and double-touch policy.
- `07-tier2-meta-labeling-prompt.md`: Tier 2 OOF meta-labeling and acceptance stream.
- `08-tier3-allocation-and-paper-execution-prompt.md`: allocation comparison and actual paper execution.
- `09-strategy-governance-dsr-acceptance-prompt.md`: trial registry, DSR, sealed test, and final diagnosis.
- `etfs/01-market-cap.md` through `etfs/13-sortino-60d.md`: bounded ETF-specific prompts.

## Approved downstream authority

`GOAL-ETF-AFML-DATASET-001` is intentionally separate from `GOAL-ETF-TRICKS-001`. The upstream Goal still ends at validated Daily NAV, Daily ETF amount, allocation, and readiness. The user approved the downstream authority on 2026-08-27; it may consume those immutable outputs through `03-afml-dataset-goal-prompt.md` and `04-afml-dataset-master-prompt.md`, but may not amend upstream selection or accounting semantics.

## Usage

- Start or resume the whole project with `00-goal-prompt.md` and `01-master-prompt.md`.
- Use `02-performance-optimization-prompt.md` when profiling or accelerating `ETFTrickLab.run_all()`.
- Read `03-afml-dataset-goal-prompt.md` and `04-afml-dataset-master-prompt.md` before consuming the AFML dataset; their artifact-readiness gates remain binding.
- For strategy work, read `05-tiered-ml-strategy-master-prompt.md`, then exactly one of `06` through `09` for the active bounded task. Do not broaden a child prompt into another tier.
- Use one ETF-specific prompt when implementing or reviewing that ETF's registry entry, feature, tests, and evidence.
- Never run a child prompt as permission to create a parallel backtester or duplicate shared accounting logic.
- Completion is determined by fresh artifacts and the fail-closed readiness report, not by prompt completion or test claims alone.
