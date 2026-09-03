STATUS: DONE

Files changed:
- src/data_analysts/config.py
- src/data_analysts/diagnostics.py
- src/data_analysts/artifacts.py
- src/data_analysts/source_catalog.py
- src/data_analysts/inspect.py
- src/data_analysts/verify.py
- tests/test_pit_foundation_config.py
- tests/test_pit_foundation_verify.py
- tests/test_raw_family_verify.py
- tests/test_artifact_publisher.py

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_artifact_publisher.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_artifact_publisher.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_artifact_publisher.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py -q`

Results:
- Updated config loading to read configs from `project_root/configs` through `DataAnalystsContext`.
- Updated diagnostics writing to publish JSON under `data_store/diagnostics/...` and avoid creating legacy `runs/real_all_products`.
- Updated artifact publishing to validate relative artifact paths through `DataAnalystsContext`, write parquet under `data_store/canonical/...`, and write manifests under `data_store/manifests/...`.
- Added focused `ArtifactPublisher` coverage for formal publish paths, absolute-path rejection, and forbidden-segment rejection.
- Updated PIT foundation and raw family verification tests to exercise the formal `data_store` layout.
- Verification:
  - focused task suite: `26 passed`
  - brief-selected verification suite: `39 passed`
  - expanded suite with focused publisher test: `44 passed`

Concerns:
- `verify.py`, `inspect.py`, and `source_catalog.py` required small path/type adjustments so owned tests could execute against `DataAnalystsContext`; no CLI, pipeline, docs, or contracts were modified.

## Fix Pass 1
STATUS: DONE

Files changed:
- src/data_analysts/config.py
- src/data_analysts/diagnostics.py
- src/data_analysts/inspect.py
- src/data_analysts/source_catalog.py
- src/data_analysts/verify.py
- tests/test_pit_foundation_config.py
- tests/test_pit_foundation_verify.py
- tests/test_raw_family_verify.py
- plans/sdd/data-store-formalization/task-4-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_artifact_publisher.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_artifact_publisher.py -q`

Results:
- `tests\test_artifact_publisher.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py -q` -> `27 passed in 0.63s`
- `tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_artifact_publisher.py -q` -> `45 passed in 1.01s`

Concerns:
- Deferred Task-4-external source-reference verification that had been added via config/verify recursion and connection-alias database mapping.
