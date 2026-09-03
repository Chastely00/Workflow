STATUS: DONE

Files changed:
- src/data_analysts/verify.py
- src/data_analysts/inspect.py
- tests/test_pit_foundation_verify.py
- tests/test_raw_family_verify.py
- tests/test_historical_universe_verify.py
- plans/sdd/data-store-formalization/task-7-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`

Results:
- RED run: 3 failed, 31 passed. Failures matched Task 7 gaps: missing metadata gate blocked at `manifests` instead of `metadata`, inspect lacked `legacy_layout_detected`, and verify lacked `path_metrics.absolute_artifact_path_count`.
- GREEN run: 34 passed.
- Regression run with metadata/context coverage: 52 passed.
- `verify.py` now gates on formal `data_store/metadata`, reports quantitative path/config/legacy metrics, and blocks on absolute paths, escape attempts, forbidden path segments, missing config snapshot files, and snapshot hash mismatches.
- `inspect.py` now reports `project_root`, `data_store`, legacy layout diagnostics, and the required quantitative metrics while reading only the formal `data_store` surface.

Concerns:
- None.

## Fix Pass 1
STATUS: DONE_WITH_CONCERNS

Files changed:
- src/data_analysts/inspect.py
- src/data_analysts/paths.py
- src/data_analysts/verify.py
- tests/test_data_store_context.py
- tests/test_historical_universe_verify.py
- tests/test_raw_family_verify.py
- plans/sdd/data-store-formalization/task-7-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py tests\test_data_store_context.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`

Results:
- RED run (`tests\test_raw_family_verify.py tests\test_data_store_context.py -q`): `3 failed, 17 passed in 0.88s`
- GREEN rerun (`tests\test_raw_family_verify.py tests\test_data_store_context.py -q`): `20 passed in 0.88s`
- Required command 1: `36 passed in 1.94s`
- Required command 2 final rerun: `54 passed in 2.29s`
- `required_manifest_missing_count` now counts missing enabled raw-family manifests derived from formal config/runtime contract and blocks verify at `manifest_paths` when nonzero.
- Absolute path classification now treats both Windows drive-letter paths and POSIX rooted paths such as `/outside/x` as absolute before later boundary checks.

Concerns:
- One intermediate run of required command 2 hit a transient Windows `PermissionError: [WinError 5]` while renaming a metadata snapshot directory in `publish_data_store_metadata()`. Immediate rerun of the same command passed unchanged, so this fix pass leaves metadata snapshot publish behavior untouched.

## Fix Pass 2
STATUS: DONE

Files changed:
- src/data_analysts/inspect.py
- tests/test_raw_family_verify.py
- plans/sdd/data-store-formalization/task-7-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py -q -k invalid_metadata_config_snapshot_path`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py -q -k absolute_artifact_path`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q`

Results:
- RED run: `tests\test_raw_family_verify.py -q` reproduced the bug; `test_inspect_handles_invalid_metadata_config_snapshot_path_without_escaping_boundary` failed with `data_analysts.paths.PathBoundaryError` raised from `inspect.py` while resolving metadata `config_snapshot_path=/outside/x`.
- Targeted GREEN reruns passed after the fix: `1 passed, 9 deselected in 0.14s` for the new regression test and `2 passed, 8 deselected in 0.22s` for absolute-path coverage.
- Required command 1: `37 passed in 1.94s`.
- Required command 2: `55 passed in 2.36s`.
- `inspect_artifacts()` now treats invalid metadata snapshot paths as inspect-time metadata absence for required-manifest inference, returns structured in-boundary metrics, and does not weaken verify's fail-closed metadata gate.

Concerns:
- None.
