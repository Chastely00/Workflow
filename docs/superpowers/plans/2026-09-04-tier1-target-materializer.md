# Tier 1 Target Materializer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace one-off Tier 1 target construction with a reproducible, hash-linked CLI materializer.

**Architecture:** A small library function converts immutable AFML bars, ETF daily NAV, prior holdings, bounded raw constituent prices, and market-state data into a Tier 1 target table. The CLI performs only manifest-declared reads, derives daily close availability from `bar_daily_membership`, invokes the existing barrier engine, and writes an immutable artifact. It must never read global `daily_price_volume` or mutate MongoDB.

**Tech Stack:** Python 3.12, pandas, PyArrow, pytest.

**Spec:** `docs/afml/prompts/00-goal-prompt.md`, `docs/afml/prompts/04-tier1-directional-label-and-model-prompt.md`

## Global Constraints

- Use only `daily_price_volume_etf_constituents` plus `daily_market_state` manifest-declared partitions.
- Input prices must be raw, unadjusted execution prices.
- Event information must be available before the next eligible raw-open execution.
- Refuse missing, non-ready, out-of-root, duplicate, or overwrite inputs; do not silently fall back.
- Persist input hashes, source manifest hashes, requested date interval, cost policy, and target configuration.
- Do not commit generated artifacts or raw data.

---

### Task 1: Test the pure materialization contract

**Files:**

- Create: `tests/etf_tricks/tier1/test_target_materializer.py`
- Create: `etf_tricks/tier1/target_materializer.py`

**Interfaces:**

- Consumes: `Tier1TargetBuilder.build(bars, opens, daily_closes, event_start_date, event_end_date)`.
- Produces: `build_target_table(bars, holdings, prices, states, daily_nav, daily_membership, start_date, end_date) -> pd.DataFrame`.

- [ ] Write a failing test proving the table derives close availability from membership and executes at the raw-open proxy.
- [ ] Run it and confirm failure because `build_target_table` does not exist.
- [ ] Implement the smallest function using `ExecutionMarketSnapshot` and `Tier1TargetBuilder`.
- [ ] Run the focused test and confirm it passes.

### Task 2: Add immutable CLI publication

**Files:**

- Create: `scripts/materialize_tier1_targets.py`
- Modify: `tests/etf_tricks/tier1/test_target_materializer.py`

**Interfaces:**

- Consumes: Task 1 inputs plus AFML/ETF/data-store roots and requested dates.
- Produces: `<output-root>/targets.parquet` and `<output-root>/manifest.json` via `write_target_artifact`.

- [ ] Write a failing test asserting metadata binds AFML, ETF, price, and market-state manifest hashes.
- [ ] Run it and confirm failure because the metadata builder does not exist.
- [ ] Add minimal helpers and CLI validation with no global-price fallback.
- [ ] Run focused tests and all Tier 1 tests.

### Task 3: Materialize and certify the full-history target artifact

**Files:**

- Generate only (gitignored): `.artifacts/tier1/full-history-13etf-20050103-20260707-daily-close-cost-v1/`
- Modify: `docs/afml/progress/decision-log.jsonl`

**Interfaces:**

- Consumes: finalized 2005–2026 bounded price/chip sources and existing AFML v5.
- Produces: hash-linked target artifact and an evidence-only decision-log record.

- [ ] Verify each bounded source manifest is ready, covers 2005–2026, has actual partitions, unique keys, and a non-epoch cutoff.
- [ ] Run the target CLI once to a new immutable output path.
- [ ] Validate schema, unique event IDs, costs, resolved/tail counts, and hashes.
- [ ] Append only factual hashes and coverage; do not draw performance conclusions.
