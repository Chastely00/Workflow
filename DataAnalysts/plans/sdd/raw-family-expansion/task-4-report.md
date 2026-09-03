# Task 4 Report: Extraction Query and Collection Diagnostics

## Scope

- Implemented Task 4 only.
- Modified files stayed under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Did not change normalized output schema, contracts, universe logic, or security panel logic.

## TDD Evidence

### Red

Command:

```powershell
python -m pytest tests/test_raw_family_pipeline.py -q
```

Result:

```text
2 failed, 1 passed
```

Expected failures:

- `test_per_ticker_daily_extraction_reports_source_collection_count` failed with `KeyError: 'source_collection_count'`.
- `test_small_snapshot_uses_single_collection_for_trading_calendar` failed with `KeyError: 'source_collection_count'`.

The small snapshot test also proved the existing extraction path uses one `find({})` call after fixture rows are removed from the temp config.

### Green

Command:

```powershell
python -m pytest tests/test_raw_family_pipeline.py -q
```

Result:

```text
3 passed in 0.55s
```

## Changes

### `tests/test_raw_family_pipeline.py`

- Added `FakeCollection` and `FakeDatabase`.
- Added per-ticker daily extraction diagnostic coverage for `daily_tradability`.
- Added small snapshot Mongo read coverage for `trading_calendar`.
- Verified:
  - per-ticker daily panel reports `source_collection_count == 2`.
  - per-ticker daily panel reports sorted `source_collections == ["2317", "2330"]`.
  - per-ticker daily panel reports `source_collection_sample_truncated is False`.
  - small snapshot uses exactly one `find({})`.
  - small snapshot reports `source_collection_count == 1`.
  - small snapshot reports `source_collections == ["TRADEDAY_TWSE"]`.

### `src/data_analysts/pipeline.py`

- After raw expansion rows are extracted and normalized, attached source collection diagnostics to `normalized["diagnostics"]` before publishing:
  - `source_collection_count`
  - `source_collections`
  - `source_collection_sample_truncated`
- Kept the full manifest `source_collections` behavior unchanged through `_publish_dataset()`.
- Did not change extraction return types.

## Concerns

- `量化積木/` is ignored by `.git/info/exclude`, so `git status` does not show these file changes.
- The daily fixture rows in the new test include both `date` and `mdate` because the current raw family PIT logical key requires `date` while extraction query filtering still uses `mdate`.
