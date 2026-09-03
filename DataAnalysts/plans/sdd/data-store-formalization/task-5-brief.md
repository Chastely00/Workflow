# Task 5 Brief: Pipeline Layout Migration

## Context

After Tasks 2-4, context/config/diagnostics/artifacts use `DataAnalystsContext` and `data_store`. This task migrates pipeline output paths from old `runtime/data_canonical/...` to formal data-store-relative paths.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Use TDD: update/add failing tests first, then implementation.

## Files

- Modify: `src/data_analysts/pipeline.py`
- Modify: `tests/test_raw_family_pipeline.py`
- Modify: `tests/test_historical_universe_pipeline.py`
- Modify if needed: `tests/test_historical_universe_verify.py`
- Write report: `plans/sdd/data-store-formalization/task-5-report.md`

## Required Interface

```python
def run_pipeline(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    *,
    families: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
    mongo_databases: dict[str, DatabaseLike] | None = None,
    allow_full_history: bool = False,
) -> dict[str, Any]: ...
```

## Required Path Migration

Replace old formal output paths:

```text
runtime/data_canonical/raw/...          -> canonical/raw/...
runtime/data_canonical/derived/...      -> canonical/derived/...
runtime/data_canonical/derived/pit/...  -> canonical/derived/pit/...
runtime/manifests/...                   -> manifests/... via ArtifactPublisher
runtime/jobs/...                        -> jobs/...
runtime/diagnostics/...                 -> diagnostics/... via write_diagnostic
```

Stale historical universe cleanup must target:

```text
canonical/derived/universes/<universe_id>/membership_by_date
```

not:

```text
runtime/data_canonical/derived/universes/<universe_id>/membership_by_date
```

## Tests To Update/Add First

Update expected paths in:

- `tests/test_raw_family_pipeline.py`
- `tests/test_historical_universe_pipeline.py`

Add a regression test:

```python
def test_pipeline_default_layout_does_not_create_runtime_or_runs(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    assert result["status"] == "ready"
    assert (tmp_path / "data_store" / "canonical").exists()
    assert (tmp_path / "data_store" / "manifests").exists()
    assert (tmp_path / "data_store" / "metadata" / "data_store_manifest.json").exists()
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path / "runs").exists()
```

## Implementation Requirements

- Replace function parameter name `root` with `context`.
- Use `ArtifactPublisher(context)`.
- Use `write_diagnostic(context, ...)`.
- Use `context.store_path("jobs", "pipeline_result.json")`.
- Call `publish_data_store_metadata(context, config)` once after successful artifact publishing and before writing `pipeline_result.json`.
- Do not create `runtime/`.
- Do not create `runs/`.

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_pipeline.py tests\test_historical_universe_pipeline.py -q
```

Expected:

```text
all selected tests pass
```

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-5-report.md
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
