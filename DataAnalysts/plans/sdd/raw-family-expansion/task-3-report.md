# Task 3 Report: Raw Publishing Orchestrator

## Status

Done.

Task 3 implemented additive raw-family publishing in `run_pipeline()`:

- Calls `normalize_raw_family()` for configured raw expansion families.
- Publishes raw parquet artifacts under `runtime/data_canonical/raw/<family_id>`.
- Publishes selected PIT parquet artifacts for `financial_statement_raw` and `self_reported_numbers_raw`.
- Writes raw-family diagnostics under `runs/real_all_products/diagnostics/raw_families`.
- Leaves existing price, event, security panel, and universe publishing blocks in place.

## Changed Files

- `src/data_analysts/pipeline.py`
- `tests/test_raw_family_pipeline.py`
- `plans/sdd/raw-family-expansion/task-3-report.md`

`src/data_analysts/extract.py` was not changed.

## Commands Run

```powershell
python -m pytest tests/test_raw_family_pipeline.py -q
```

Initial RED result:

```text
FAILED tests/test_raw_family_pipeline.py::test_pipeline_publishes_raw_family_artifacts_and_diagnostics
AssertionError: assert False
where False = calendar_path.exists()
```

GREEN result:

```text
1 passed in 0.48s
```

```powershell
python -m pytest tests/test_raw_family_pipeline.py tests/test_raw_family_normalization.py -q
```

Final result:

```text
6 passed in 0.45s
```

## Test Output Summary

- Pipeline now publishes `trading_calendar.parquet`.
- Pipeline publishes two raw financial statement revision rows.
- Pipeline publishes one selected financial PIT row for decision date `2025-08-31`.
- Selected financial fixture resolves the duplicate by latest `mdate`, keeping `eps == 11`.
- Raw financial row count remains greater than selected financial row count.
- Raw-family diagnostic reports `source_row_count == 2` and `unresolved_duplicate_count == 0`.

## Self-Review Notes

- Raw expansion publishing is additive and runs before existing publishing blocks without deleting or reordering those blocks.
- No historical security panel or universe behavior was implemented.
- No adjusted price or corporate action semantics were changed.
- No contract/config files were changed.
- `publish_raw_family_outputs()` exposes the briefed interface and delegates to the internal helper used by the pipeline.
- Raw output contracts use the exact family ids, base paths, partition names, and required columns from the brief.
