# Task 2 Report: Raw Family Normalization Module

## Status

Completed.

## Changed Files

- `src/data_analysts/raw_families.py`
- `tests/test_raw_family_normalization.py`
- `plans/sdd/raw-family-expansion/task-2-report.md`

## Commands Run

```powershell
python -m pytest tests/test_raw_family_normalization.py -q
```

RED result before implementation:

```text
ModuleNotFoundError: No module named 'data_analysts.raw_families'
1 error in 0.08s
```

GREEN result after implementation:

```text
.....                                                                    [100%]
5 passed in 0.03s
```

```powershell
python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q
```

Final result:

```text
...............                                                          [100%]
15 passed in 0.02s
```

## Test Output Summary

- Normalization tests pass for trading calendar, monthly sales, AINVFINB financial statements, AFESTM1 self-reported numbers, and missing required PIT fields.
- AINVFINB raw normalization preserves all 3 raw revisions while selected PIT output chooses 1 row for `2025-08-31`.
- Clean fixtures produce `pit_parse_failure_count == 0` and `published_row_count == source_row_count`.
- AFESTM1 `key3` remains the category string `Q`; PIT availability uses `annd`.
- Existing `tests/test_pit_selection.py` still passes.

## Self-Review Notes

- Scope stayed inside `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- No MongoDB reads, parquet writes, diagnostics writes, pipeline changes, config changes, or verify changes were made.
- `RawFamilyError` is used to fail closed on missing, blank, or invalid required PIT fields.
- `selected_rows` delegates PIT eligibility and latest revision choice to `select_latest_pit_rows()` after raw normalization.
- `_with_source_metadata()` emits canonical fields first and then preserves raw source fields, including `source_dataset_id`, `source_collection`, `source_row_id`, and `data_cutoff_at` when present.
- `量化積木/` is excluded by this repo's `.git/info/exclude`; therefore `git status` does not show these changed files even though they exist in the requested workspace.
