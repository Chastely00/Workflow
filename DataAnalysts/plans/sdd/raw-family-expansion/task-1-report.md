# Task 1 Report: Raw Family Config Profiles

## Status

Completed.

## Changed Files

- `configs/mongodb_sources.json`
- `configs/source_family_profiles.json`
- `tests/test_raw_family_config.py`
- `plans/sdd/raw-family-expansion/task-1-report.md`

## Commands Run

1. `python -m pytest tests/test_raw_family_config.py -q`
   - RED result: `3 failed, 1 passed in 0.07s`
   - Expected failures:
     - Missing `apistkattr` Mongo connection.
     - Missing raw registry family profiles.
     - Runtime config did not yet include new raw family ids.

2. `python -m pytest tests/test_raw_family_config.py tests/test_pit_foundation_config.py -q`
   - GREEN result: `10 passed in 0.07s`

3. `python -c "...quantitative config checks..."`
   - `raw_registry_family_count 15`
   - `derived_selected_family_count 2`
   - `raw_missing_from_profiles []`
   - `derived_in_profiles []`
   - `forbidden_source_usage_count 0`
   - `small_snapshot_family_count 2`
   - `large_daily_panel_family_count 4`

## Test Output Summary

- Added config tests first and confirmed they failed for the expected missing config reasons.
- Added three Mongo connections:
  - `apistkattr` -> `APISTKATTR`
  - `apishract` -> `APISHRACT`
  - `futures_taifex_tx` -> `Futures_TAIFEX_TX`
- Added raw family profiles for all 15 non-derived PIT registry families.
- Did not add derived selected families to `source_family_profiles.json`.
- Confirmed `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are not referenced by profiles.

## Self-Review Notes

- Scope stayed config-only; no `src`, `contracts`, pipeline, or runtime data files were modified.
- Existing `apiprcd` and `tej` Mongo connections were left unchanged.
- Existing source family profiles were preserved; new raw profiles were appended.
- Governance/event profiles all use `connection = "tej"`, `source_profile = "medium_pit_table"`, PIT field `mdate`, partitioning `["available_year"]`, and the exact requested collection names.
- `量化積木/` is excluded by the parent repo git exclude, so `git status` does not show these file changes even though the filesystem files were updated and tests passed.

## Reviewer Fix: self_reported_numbers_raw Primary Key

### Fix Notes

- Root cause: `configs/source_family_profiles.json` had `self_reported_numbers_raw.primary_key` missing `sem`, `curr`, and `merg`.
- PIT correctness source of truth: `configs/pit_registry.json` and `configs/source_catalog.json` both define AFESTM1 raw logical key as `["ticker", "key3", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"]`.
- Updated `self_reported_numbers_raw.primary_key` to match the registry/catalog logical key exactly.
- Added a regression test comparing the profile primary key to both PIT registry and source catalog logical keys for `self_reported_numbers_raw`.

### Command Output

1. `python -m pytest tests/test_raw_family_config.py -q`
   - RED result after adding regression test before config fix:

```text
...F.                                                                    [100%]
================================== FAILURES ===================================
_____ test_self_reported_numbers_raw_primary_key_matches_pit_logical_key ______
E       AssertionError: assert ['ticker', 'k...evision_date'] == ['ticker', 'k...nd_date', ...]
E         At index 2 diff: 'period_end_date' != 'sem'
E         Right contains 3 more items, first extra item: 'period_end_date'
=========================== short test summary info ===========================
FAILED tests/test_raw_family_config.py::test_self_reported_numbers_raw_primary_key_matches_pit_logical_key
1 failed, 4 passed in 0.09s
```

2. `python -m pytest tests/test_raw_family_config.py tests/test_pit_foundation_config.py -q`
   - GREEN result after updating the profile primary key:

```text
...........                                                              [100%]
11 passed in 0.10s
```
