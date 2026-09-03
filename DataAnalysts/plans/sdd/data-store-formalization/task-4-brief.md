# Task 4 Brief: Config Loader, Diagnostics, and Artifact Publisher Migration

## Context

After Task 2, `DataAnalystsContext` exists. This task migrates config loading, diagnostics writing, and artifact publishing to the formal `data_store` layout.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- Artifact path validation must use path segments, not substring matching.
- Use TDD: add/update failing tests first, then implementation.

## Files

- Modify: `src/data_analysts/config.py`
- Modify: `src/data_analysts/diagnostics.py`
- Modify: `src/data_analysts/artifacts.py`
- Modify: `tests/test_pit_foundation_config.py`
- Modify: `tests/test_pit_foundation_verify.py`
- Modify: `tests/test_raw_family_verify.py`
- Modify or create a focused test for `ArtifactPublisher`
- Write report: `plans/sdd/data-store-formalization/task-4-report.md`

## Required Interfaces

Use `DataAnalystsContext`:

- `load_runtime_config(context: DataAnalystsContext) -> RuntimeConfig`
- `write_diagnostic(context: DataAnalystsContext, name: str, payload: dict[str, object]) -> Path`
- `ArtifactPublisher(context: DataAnalystsContext)`

## Test Requirements

Add or update tests to verify:

1. `load_runtime_config()` reads from `<project_root>/configs`.
2. `write_diagnostic()` writes to `<data_store>/diagnostics/<name>.json`.
3. `ArtifactPublisher.publish_parquet()` writes to `<data_store>/canonical/...`.
4. `ArtifactPublisher.publish_manifest()` writes to `<data_store>/manifests/<artifact_id>.json`.
5. Publisher rejects absolute artifact paths.
6. Publisher rejects artifact paths with forbidden path segments.
7. Publisher does not create `<project_root>/runtime`.

Example publisher test:

```python
from data_analysts.artifacts import ArtifactError, ArtifactPublisher
from data_analysts.paths import DataAnalystsContext


def test_artifact_publisher_writes_manifest_under_data_store(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)
    publisher.publish_parquet(
        "canonical/raw/sample/part.parquet",
        rows=[{"date": "2025-01-01", "ticker": "2330"}],
        required_columns=["date", "ticker"],
    )
    publisher.publish_manifest(
        artifact_id="sample",
        layer="raw",
        source_families=["sample"],
        source_collections=[],
        columns=["date", "ticker"],
        artifact_paths=["canonical/raw/sample/part.parquet"],
        row_count=1,
        date_range=["2025-01-01", "2025-01-01"],
        availability_date_range=["2025-01-01", "2025-01-01"],
        partitioning=["single_file"],
        pit_policy="test",
        data_cutoff_at="2025-01-01T00:00:00Z",
        duplicate_count=0,
        omitted_row_count=0,
        status="ready",
    )

    assert (tmp_path / "data_store" / "canonical" / "raw" / "sample" / "part.parquet").exists()
    assert (tmp_path / "data_store" / "manifests" / "sample.json").exists()
    assert not (tmp_path / "runtime").exists()
```

## Implementation Requirements

- Replace `DataAnalystsRoot` imports in touched files with `DataAnalystsContext`.
- `load_runtime_config()` uses `context.config_path(name)`.
- `write_diagnostic()` uses `context.store_path("diagnostics", *safe_parts).with_suffix(".json")`.
- `ArtifactPublisher.publish_manifest()` writes to `context.store_path("manifests", f"{artifact_id}.json")`.
- `ArtifactPublisher.publish_parquet()` resolves target with `context.artifact_path(path)`.
- `ArtifactPublisher._validate_relative_artifact_path()` delegates to `context.validate_artifact_path(path)`.
- Preserve current atomic write behavior.

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py -q
```

Expected:

```text
all selected tests pass
```

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-4-report.md
```

Report format:

```text
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED

Files changed:
- ...

Commands run:
- ...

Results:
- ...

Concerns:
- ...
```
