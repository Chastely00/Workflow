# Raw Family Expansion Task 7 Report

## Status

Completed.

## Scope

Implemented and verified only Task 7 inside `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.

## Changed Files

- `src/data_analysts/verify.py`
- `src/data_analysts/inspect.py`
- `tests/test_raw_family_verify.py`
- `plans/sdd/raw-family-expansion/task-7-report.md`

## TDD Evidence

1. Added failing verify tests in `tests/test_raw_family_verify.py` for:
   - `pit_parse_failure_count_total != 0`
   - `unresolved_duplicate_count_total != 0`
   - `forbidden_source_usage_count_total != 0`
2. Ran `python -m pytest tests/test_raw_family_verify.py -q`.
3. Observed RED: all three verify tests returned `ready` instead of `blocked`.
4. Implemented raw family diagnostics aggregation and fail-closed blocking.
5. Re-ran `python -m pytest tests/test_raw_family_verify.py -q`.
6. Observed GREEN: `3 passed in 0.22s`.
7. Added failing inspect coverage proving diagnostics are summarized without reading parquet.
8. Observed RED: `KeyError: 'raw_family_diagnostics'`.
9. Implemented inspect diagnostics summary using the same JSON-only aggregator.
10. Ran the required full command.

## Behavior

- `verify_runtime()` now appends a `raw_family_diagnostics` check after PIT foundation checks and before manifest artifact path checks.
- Verify blocks at `blocked_step == "raw_family_diagnostics"` when any aggregated raw-family diagnostic counter is nonzero:
  - `pit_parse_failure_count_total`
  - `unresolved_duplicate_count_total`
  - `forbidden_source_usage_count_total`
- `inspect_artifacts()` now returns `raw_family_diagnostics` with the same status and totals.
- The raw diagnostics aggregator only reads JSON diagnostics under `runs/real_all_products/diagnostics/raw_families`.
- It does not query MongoDB.
- It does not create canonical parquet.

## Verification

```text
python -m pytest tests/test_raw_family_verify.py -q
FFF
FAILED tests/test_raw_family_verify.py::test_verify_blocks_on_raw_family_pit_parse_failure
FAILED tests/test_raw_family_verify.py::test_verify_blocks_on_raw_family_unresolved_duplicate
FAILED tests/test_raw_family_verify.py::test_verify_blocks_on_raw_family_forbidden_source_usage
3 failed in 0.25s
```

```text
python -m pytest tests/test_raw_family_verify.py -q
...                                                                      [100%]
3 passed in 0.22s
```

```text
python -m pytest tests/test_raw_family_verify.py -q
...F
FAILED tests/test_raw_family_verify.py::test_inspect_reports_raw_family_diagnostics_without_reading_parquet
KeyError: 'raw_family_diagnostics'
1 failed, 3 passed in 0.26s
```

```text
python -m pytest tests/test_raw_family_verify.py tests/test_pit_foundation_verify.py -q
...........                                                              [100%]
11 passed in 0.27s
```

## Concerns

- `git ls-files` from `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts` did not list these task files, and targeted `git diff/status` had no output for the changed paths. The files were updated on disk and the required pytest command passed.
- The brief's example used `family_count`; its implementation snippet used `raw_family_diagnostic_count`. The implementation reports both with the same diagnostics-file count to preserve both surfaces.
