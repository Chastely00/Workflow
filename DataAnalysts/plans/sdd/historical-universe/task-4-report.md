STATUS: GREEN

changed files
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\pipeline.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_pipeline.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-4-report.md`

RED test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q`
- Result: FAIL with `assert security_panel_path.exists()` because the pipeline only published latest `security_panel` / `membership_by_date`, not historical year-partition outputs.

GREEN test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py tests\test_raw_family_pipeline.py -q`
- Result: PASS, `6 passed in 0.62s`

self-review
- Followed the brief's TDD order: added the historical pipeline test first, captured RED, then implemented the minimum publishing changes for GREEN.
- Historical `security_panel_history` now publishes to `runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet` with `effective_date` as availability range and a diagnostic JSON written under `historical_universe/security_panel_history`.
- Historical universe memberships now publish to `runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet` plus `diagnostics/diagnostics.parquet`; range backfill no longer emits `membership_by_date/as_of_date=*/membership.parquet`.
- Kept latest `membership_by_date` publishing only for explicit `as_of_date` runs when no historical history was produced, so existing latest-only path remains available without reintroducing one-day-per-file backfill output.
- To preserve next-trading-day PIT semantics for the range end, the historical branch rebuilds a calendar view without the `end_date` cap for `build_historical_security_panel()` only; raw `trading_calendar` publishing still uses the originally requested range.
- Scope stayed inside the allowed `DataAnalysts` files only; verify/inspect/docs gates were not changed.

concerns
- `src/data_analysts/verify.py` still validates universe uniqueness on `(as_of_date, universe_id, ticker/rank)` and does not yet know about `membership_by_year` historical publishing. The brief explicitly deferred verify-gate updates to a later task.
- Latest `security_panel` publishing still runs alongside the new historical branch. This preserves current consumers, but downstream tasks should decide whether range backfills should continue emitting that latest-only convenience artifact.

---

STATUS: GREEN

fix summary
- Root cause: `_publish_dataset()` hardcoded `source_families=[artifact_id]`, so any historical manifest published through the helper recorded itself as its own lineage.
- Fix: added optional `_publish_dataset(..., source_families=...)` with default fallback to `[artifact_id]` to preserve existing behavior.
- Historical `security_panel_history` manifest now publishes canonical upstream lineage:
  - `daily_price_volume`
  - `security_master`
  - `trading_calendar`
  - `daily_tradability`
- Historical universe membership manifests now publish `source_families=["security_panel_history"]`.
- Added regression assertions in `tests/test_historical_universe_pipeline.py` for both historical manifest lineage cases.

verification
- RED: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q`
  - failed at `assert manifest["source_families"] == ["security_panel_history"]`
- GREEN: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py tests\test_raw_family_pipeline.py -q`
  - passed: `6 passed in 0.66s`

concerns
- This change intentionally leaves non-historical `_publish_dataset()` call sites on the default lineage path; only the two historical call sites override `source_families`.
