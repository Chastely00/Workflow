# Raw Family Expansion Task 9 Smoke Fix Report

Date: 2026-07-07

## Change

- Added a regression test for `daily_chip` rows that have `mdate` but no `date`.
- Updated daily panel normalization so `mdate` is the canonical `date` and `source_available_date`.
- Kept ticker normalization from `coid` or `ticker`.

## Commands

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py::test_daily_chip_normalizes_mdate_to_canonical_date_and_source_available_date -q
```

Initial red output:

```text
F                                                                        [100%]
E           data_analysts.raw_families.RawFamilyError: missing required PIT field for daily_chip: date
1 failed in 0.08s
```

After fix:

```text
.                                                                        [100%]
1 passed in 0.02s
```

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py tests/test_raw_family_pipeline.py -q
```

Final output:

```text
.....................                                                    [100%]
21 passed in 0.49s
```

## Notes

- Real Mongo smoke was not run; controller owns that rerun.

## Financial Statement Same-Day Timestamp Tie Fix

- Added a regression for `financial_statement_raw` where two selected PIT candidates share the same logical key, normalized `key3` date, and `mdate`, but have different raw `key3` timestamps and different values.
- Added selected PIT pre-resolution for same-day raw source timestamps:
  - `financial_statement_pit_selected` uses raw `key3`.
  - `self_reported_numbers_pit_selected` can use raw `annd`.
- The later raw timestamp wins only when it uniquely resolves the tie.
- If the raw timestamp is also tied, duplicate PIT rows still fail closed.
- Diagnostics now include `resolved_same_day_source_timestamp_count`, and `resolved_duplicate_count` includes those resolved same-day timestamp ties.

## Financial Statement Same-Day Timestamp Tie Test

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_uses_later_raw_key3_timestamp_for_same_day_tie -q
```

Initial red output:

```text
F                                                                        [100%]
E               data_analysts.pit.PitError: unresolved duplicate PIT rows for key=('3576', 'A', '1', 'NTD', 'Y', '2022-03-31')
E               data_analysts.raw_families.RawFamilyError: unresolved duplicate PIT rows for key=('3576', 'A', '1', 'NTD', 'Y', '2022-03-31')
1 failed in 0.09s
```

Boundary check after fix:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_uses_later_raw_key3_timestamp_for_same_day_tie tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_unresolved_duplicate_fails_closed -q
```

```text
..                                                                       [100%]
2 passed in 0.02s
```

Required test output:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q
```

```text
.............................                                            [100%]
29 passed in 0.03s
```

Real Mongo smoke was not run; controller owns that rerun.

## Real-Data Type and AFESTM1 Schema Fix

- Root cause 1: `ArtifactPublisher.publish_parquet` passed raw row values directly into `pa.table(data)`. Real source rows can contain mixed Python, numpy, and pandas scalar objects; a bytes-like column with `NaN` reproduced the real `ArrowTypeError: Expected bytes, got a 'float' object`.
- Fix 1: normalized parquet-bound scalars at the artifact boundary before Arrow table creation:
  - numpy/pandas scalar objects with `.item()` are converted to stable Python scalars;
  - float `NaN` becomes `None`;
  - `bytes` are preserved and `bytearray` becomes `bytes`;
  - existing `date` / `datetime` values are passed through.
- Root cause 2: real `TEJ.AFESTM1` rows have `coid,mdate,key3,no,sem,merg,curr,annd,...` and no `endd`, while `_normalize_self_reported_numbers()` required `endd`.
- Fix 2: `self_reported_numbers_raw.period_end_date` now uses `endd` when present, otherwise normalized `mdate`; `source_available_date` remains `annd`, `revision_date` remains `mdate`, and `key3` remains a category.
- Added regression coverage for AFESTM1-like rows without `endd` and for parquet publishing mixed Python/numpy/pandas integer scalars plus bytes/NaN values.

## Type and Schema Regression Tests

```powershell
python -m pytest tests/test_raw_family_normalization.py::test_self_reported_numbers_uses_mdate_as_period_end_when_endd_missing -q
```

Initial red output:

```text
F                                                                        [100%]
E           data_analysts.raw_families.RawFamilyError: missing required PIT field for self_reported_numbers_raw: endd
1 failed in 0.11s
```

After fix:

```text
.                                                                        [100%]
1 passed in 0.02s
```

```powershell
python -m pytest tests/test_raw_family_pipeline.py::test_artifact_publisher_normalizes_mixed_integer_scalars_before_parquet -q
```

Initial red output:

```text
F                                                                        [100%]
E   pyarrow.lib.ArrowTypeError: Expected bytes, got a 'float' object
1 failed in 0.49s
```

After fix:

```text
.                                                                        [100%]
1 passed in 0.44s
```

Required test output:

```powershell
python -m pytest tests/test_raw_family_normalization.py tests/test_raw_family_pipeline.py -q
```

```text
...........................                                              [100%]
27 passed in 0.55s
```

```powershell
python -m pytest -q
```

```text
...........................................................              [100%]
59 passed in 0.70s
```

Real Mongo smoke was not run; controller owns that rerun.

## Selected View Range Scope Fix

- Changed pipeline decision-date expansion so selected PIT materialization uses:
  - `as_of_date` as the only decision snapshot;
  - equal `start_date` / `end_date` as the single decision snapshot;
  - bounded range backfills as one `end_date` decision snapshot;
  - no bounds as no selected decision snapshots.
- Added a pipeline regression for `financial_statement_raw` range backfill proving raw rows remain published while `financial_statement_pit_selected` emits only one `end_date` partition row.

## Selected View Range Scope Test

```powershell
python -m pytest tests/test_raw_family_pipeline.py::test_financial_statement_range_backfill_publishes_only_end_date_selected_snapshot -q
```

Initial red output:

```text
F                                                                        [100%]
E       AssertionError: assert 18 == 1
1 failed in 0.99s
```

After fix:

```text
.                                                                        [100%]
1 passed in 0.46s
```

Required test output:

```powershell
python -m pytest tests/test_raw_family_pipeline.py tests/test_raw_family_normalization.py -q
```

```text
.........................                                                [100%]
25 passed in 0.64s
```

Real Mongo smoke was not run; controller owns that rerun.

## Financial Statement Multi-Date PIT Performance Fix

- Replaced per-decision-date full-row selected PIT scans with an incremental selected PIT path in `raw_families.py`.
- The new path pre-groups rows by logical key, normalized availability date, and revision date, applies same-day raw timestamp tie-breaks once per availability event, then advances through sorted decision dates.
- Selection semantics are preserved:
  - eligible rows require `source_available_date <= decision_date`;
  - selected row uses latest `source_available_date`;
  - ties use latest `revision_date`;
  - AINVFINB same-day raw `key3` timestamp uniquely resolves same availability/revision ties;
  - unresolved ties at the selected availability/revision/timestamp still fail closed.
- Added a multi-date regression covering 3 decision dates and row evolution across availability, revision, and same-day timestamp changes.
- Added a structural performance guard showing multi-date normalization no longer calls `select_latest_pit_rows` once per decision date.

## Multi-Date PIT Performance Test

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_evolves_across_multiple_decision_dates tests/test_raw_family_normalization.py::test_financial_statement_multi_date_selection_does_not_call_selector_per_decision_date -q
```

Initial red output:

```text
.F                                                                       [100%]
E       assert 3 == 0
1 failed, 1 passed in 0.10s
```

Boundary check after fix:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_evolves_across_multiple_decision_dates tests/test_raw_family_normalization.py::test_financial_statement_multi_date_selection_does_not_call_selector_per_decision_date tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_unresolved_duplicate_fails_closed tests/test_raw_family_normalization.py::test_financial_statement_selected_pit_uses_later_raw_key3_timestamp_for_same_day_tie -q
```

```text
....                                                                     [100%]
4 passed in 0.03s
```

Required test output:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_raw_family_normalization.py tests/test_raw_family_pipeline.py tests/test_pit_selection.py -q
```

```text
..................................                                       [100%]
34 passed in 0.81s
```

Real Mongo smoke was not run; controller owns that rerun.
