# Task 7 Report

STATUS: DONE

## Commands and Exact Results

1. `python -m pytest -q`
   - Result: `85 passed in 1.43s`

2. `python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31`
   - Result: `ready`

3. `python -m data_analysts.cli verify --root runs\real_all_products`
   - Result: `ready`

4. `python -m data_analysts.cli inspect-artifacts --root runs\real_all_products`
   - Result: `ready`
   - Historical universe summary: `status=ready`, `historical_universe_file_count=6`, `historical_universe_count=6`, `historical_universe_date_min=2026-01-02`, `historical_universe_date_max=2026-01-30`, `small_file_daily_partition_count=0`

5. Compact artifact check
   - Result: `{'files': 6, 'rows': 156388, 'bad_effective': 0, 'small_file_daily_partition_count': 0, 'by_universe': {'tw_common_stock_all': 40846, 'tw_common_stock_tradable': 40521, 'tw_equity_all_listed': 56121, 'tw_equity_liquid_top100': 2100, 'tw_equity_liquid_top300': 6300, 'tw_equity_liquid_top500': 10500}}`

## Real Smoke Source Families Actually Used

- `trading_calendar`
- `daily_price_volume`
- `security_master`
- `daily_tradability`

## Fixes Made

- `src/data_analysts/security_panel.py`
  - Historical security panel now accepts raw trading-calendar shapes from the live source path.
  - It normalizes dates from `date`, `zdate`, `mdate`, or `source_date`.
  - It derives trading-day truth from `is_trading_day` when present, otherwise from `date_rmk == ""`, matching the raw calendar contract.
  - It also tolerates raw market/ticker fields via `mkt` and `coid`.
- `tests/test_historical_universe_pipeline.py`
  - Added a regression test that uses raw trading-calendar shapes without `is_trading_day`.
  - This test ensures the historical universe path still publishes `security_panel_history` and year-partitioned memberships.

## Concerns

- None after verification. The historical universe output is present, year-partitioned, and `bad_effective == 0`.

## 2026-07-07 Final Review Fix Append

Final review found Important fail-closed gaps in historical universe verification. Fix scope:

- `src/data_analysts/universe.py`
- `src/data_analysts/pipeline.py`
- `src/data_analysts/verify.py`
- `tests/test_historical_universe_verify.py`
- `tests/test_historical_universe_pipeline.py`

Behavior changes:

- Historical universe diagnostics now include `top_n_underfilled_date_count` so multi-day top-N underfill is verified per decision date instead of inferred from aggregate counts.
- Verification now blocks nonzero `duplicate_universe_effective_ticker_count` or `duplicate_universe_effective_rank_count` diagnostics.
- Verification now requires top-N historical universe diagnostics to exist and include required fail-closed counters.
- Verification now requires top-N historical universe diagnostics to have exactly one row and full non-bool integer counters.
- Verification now checks `security_panel_history` artifacts for required `as_of_date`, `effective_date`, and `ticker`, plus strict `effective_date > as_of_date`.
- Historical publishing now clears stale `membership_by_date` paths for every enabled historical universe, including universes with zero current rows.

Regression tests added:

- Multi-day top-N underfill diagnostics block verification.
- Nonzero duplicate diagnostics counters block verification.
- Missing diagnostics or missing required diagnostics counters block verification for top-N historical universes.
- Invalid diagnostics counter types, bool counters, missing core counters, or multiple diagnostics rows block verification for top-N historical universes.
- Null `security_panel_history.effective_date` blocks verification.
- Empty historical universe still clears stale `membership_by_date`.

Post-fix verification:

- `python -m pytest tests\test_historical_universe_verify.py -q` -> `15 passed`
- `python -m pytest tests\test_historical_universe_pipeline.py::test_historical_publish_removes_stale_membership_by_date_for_empty_universe -q` -> `1 passed`
- `python -m pytest tests\test_historical_universe_config.py tests\test_historical_security_panel.py tests\test_historical_universe.py tests\test_historical_universe_pipeline.py tests\test_historical_universe_verify.py -q` -> `32 passed`
- `python -m pytest tests\test_raw_family_pipeline.py -q` -> `5 passed`
- `python -m pytest -q` -> `95 passed`
- `python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31` -> `ready`
- `python -m data_analysts.cli verify --root runs\real_all_products` -> `ready`
- Compact artifact check -> `files=6`, `rows=156388`, `bad_effective=0`, `duplicate_membership_keys=0`, `duplicate_rank_keys=0`, `order_violations_asof_eff_rank=0`, `small_file_daily_partition_count=0`

## 2026-07-07 stale-artifact fix append

- Root cause confirmed: orphan `membership_by_date/as_of_date=*/membership.parquet` under `runs\real_all_products` can survive outside current historical manifests and inflate downstream filesystem-glob small-file checks.
- Fix scope:
  - `src/data_analysts/pipeline.py`
  - `tests/test_historical_universe_pipeline.py`
- Behavior change:
  - Historical universe publish now removes `runtime/data_canonical/derived/universes/<universe_id>/membership_by_date` before publishing canonical `membership_by_year` outputs.
  - Latest-only `as_of_date` universe publish path is unchanged and still writes `membership_by_date` when no historical history is being published.
- Regression:
  - Added a test that seeds stale `runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_date/as_of_date=2025-12-31/membership.parquet`, runs historical range publish, and asserts the stale path is removed while `membership_by_year/as_of_year=2025/part.parquet` remains.
- Post-fix verification:
  - `python -m pytest tests\test_historical_universe_pipeline.py tests\test_raw_family_pipeline.py -q` -> `9 passed`
  - `python -m pytest -q` -> `85 passed`
  - `python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31` -> `ready`
  - `python -m data_analysts.cli verify --root runs\real_all_products` -> `ready`
  - `inspect_artifacts(...)` compact summary -> `historical_universe.status=ready`, `historical_universe_file_count=6`, `historical_universe_count=6`, `historical_universe_date_min=2026-01-02`, `historical_universe_date_max=2026-01-30`, `small_file_daily_partition_count=0`
