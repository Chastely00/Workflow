# Task 7 Brief: Verify and Inspect Formal Data Store

## Context

After Tasks 2-6, DataAnalysts has formal `DataAnalystsContext`, formal `data_store` artifact publishing, formal CLI args, and metadata snapshots. This task migrates verify and inspect to read only `data_store` and report quantitative path/metadata metrics.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Verify and inspect must not read legacy `runtime/` or `runs/` as formal artifacts.
- Legacy `runtime/` and `runs/` are diagnostics only and do not block verify by themselves.
- Manifest artifact path checks must use path segments, not substring matching.
- Use TDD: add/update failing tests first, then implementation.

## Files

- Modify: `src/data_analysts/verify.py`
- Modify: `src/data_analysts/inspect.py`
- Modify: `tests/test_pit_foundation_verify.py`
- Modify: `tests/test_raw_family_verify.py`
- Modify: `tests/test_historical_universe_verify.py`
- Write report: `plans/sdd/data-store-formalization/task-7-report.md`

## Required Metrics

Verify and inspect must report:

```python
{
    "artifact_path_count": int,
    "absolute_artifact_path_count": int,
    "artifact_path_escape_count": int,
    "forbidden_path_segment_count": int,
    "manifest_count": int,
    "required_manifest_missing_count": int,
    "config_snapshot_file_count": int,
    "config_snapshot_hash_mismatch_count": int,
    "legacy_project_runtime_exists": bool,
    "legacy_project_runs_exists": bool,
}
```

## Verify Requirements

- Manifests dir is `context.store_path("manifests")`.
- Verification result path is `context.store_path("jobs", "verification_result.json")`.
- Artifact path resolution uses `context.artifact_path(path)`.
- Missing `data_store/metadata/data_store_manifest.json` blocks with `blocked_step == "metadata"`.
- Missing config snapshot blocks with `blocked_step == "metadata"`.
- Hash mismatch blocks with `blocked_step == "metadata"`.
- Absolute artifact path blocks.
- Artifact path escaping data_store blocks.
- Artifact path with forbidden path segment blocks.
- Legacy `project_root/runtime` or `project_root/runs` is reported but does not block by itself.

## Inspect Requirements

- Read manifests from `context.store_path("manifests")`.
- Read diagnostics from `context.store_path("diagnostics")`.
- Include top-level:
  - `project_root`
  - `data_store`
  - `legacy_layout_detected`
  - `legacy_project_runtime_exists`
  - `legacy_project_runs_exists`
  - metrics listed above.

## Tests To Add/Update First

Add tests covering:

```python
def test_verify_blocks_absolute_artifact_path(tmp_path):
    ...
    assert result["status"] == "blocked"
    assert result["path_metrics"]["absolute_artifact_path_count"] == 1


def test_verify_blocks_missing_data_store_metadata(tmp_path):
    ...
    assert result["status"] == "blocked"
    assert result["blocked_step"] == "metadata"


def test_inspect_reports_legacy_layout_without_using_it(tmp_path):
    (tmp_path / "runtime").mkdir()
    context = DataAnalystsContext.from_paths(tmp_path)
    result = inspect_artifacts(context)
    assert result["legacy_layout_detected"] is True
    assert result["legacy_project_runtime_exists"] is True
```

Update old expected paths:

- `runtime/manifests` -> `data_store/manifests`
- `runtime/jobs` -> `data_store/jobs`
- `runs/real_all_products/diagnostics` -> `data_store/diagnostics`

## Implementation Requirements

- Replace `DataAnalystsRoot` type usage in touched files with `DataAnalystsContext`.
- Use `verify_config_snapshot_hashes(context)` for metadata gate.
- Add a helper that iterates manifests and counts artifact paths.
- Use `context.validate_artifact_path()` / `context.artifact_path()` where possible.
- Preserve existing fail-closed checks for PIT foundation, raw family diagnostics, selected PIT, security panel, historical universe diagnostics.

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q
```

Expected:

```text
all selected tests pass
```

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-7-report.md
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
