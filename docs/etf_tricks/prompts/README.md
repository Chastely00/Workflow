# ETF Tricks Authoritative Prompt Set

Goal ID: `GOAL-ETF-TRICKS-001`

This directory is the reusable prompt package for the Taiwan-equity ETF Tricks project.

## Authority order

When two instructions conflict, apply this order and report the conflict explicitly:

1. Repository `AGENTS.md` and higher-priority runtime instructions.
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md` — approved quantitative and accounting contract.
3. `docs/etf_tricks/prompts/01-master-prompt.md` — operational implementation and validation prompt.
4. `docs/etf_tricks/prompts/02-performance-optimization-prompt.md` — result-equivalent profiling and performance-optimization contract.
5. The relevant file under `docs/etf_tricks/prompts/etfs/` — one ETF's unique contract.
6. `docs/superpowers/plans/2026-08-26-etf-tricks-implementation.md` — task ordering and file-level implementation plan.
7. Later implementation notes that do not amend an authority above.

The Goal Prompt coordinates progress but does not override the approved design.

## Files

- `00-goal-prompt.md`: persistent goal prompt, under 4,000 characters.
- `01-master-prompt.md`: whole-system implementation and validation prompt.
- `02-performance-optimization-prompt.md`: representative-window profiling, mathematical audit, optimization, equivalence validation, and one-time full-history acceptance prompt.
- `etfs/01-market-cap.md` through `etfs/13-sortino-60d.md`: bounded ETF-specific prompts.

## Usage

- Start or resume the whole project with `00-goal-prompt.md` and `01-master-prompt.md`.
- Use `02-performance-optimization-prompt.md` when profiling or accelerating `ETFTrickLab.run_all()`.
- Use one child prompt when implementing or reviewing that ETF's registry entry, feature, tests, and evidence.
- Never run a child prompt as permission to create a parallel backtester or duplicate shared accounting logic.
- Completion is determined by fresh artifacts and the fail-closed readiness report, not by prompt completion or test claims alone.
