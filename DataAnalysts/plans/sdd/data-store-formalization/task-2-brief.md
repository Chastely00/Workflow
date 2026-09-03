# Task 2 Brief: DataAnalystsContext Path Layer

## Context

DataAnalysts is moving from root-relative `runtime/` output to a formal `project_root` / `data_store` split.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Do not introduce an ALF runtime adapter.
- Remove `--root`; do not keep it as a compatibility alias.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Artifact path validation must use path segments, not substring matching.
- Verify and inspect must not read legacy `runtime/` or `runs/` as formal artifacts.
- Legacy `runtime/` and `runs/` must not be auto-migrated or auto-deleted.
- Use TDD: add failing tests first, then implementation.

## Files

- Modify: `src/data_analysts/paths.py`
- Create: `tests/test_data_store_context.py`

## Required Interfaces

Implement:

```python
@dataclass(frozen=True)
class DataAnalystsContext:
    project_root: Path
    data_store: Path

    @classmethod
    def from_paths(
        cls,
        project_root: str | Path = ".",
        data_store: str | Path | None = None,
    ) -> "DataAnalystsContext": ...

    def config_path(self, name: str) -> Path: ...
    def contract_path(self, name: str) -> Path: ...
    def store_path(self, *parts: str) -> Path: ...
    def artifact_path(self, path: str | Path) -> Path: ...
    def validate_artifact_path(self, path: str | Path) -> str: ...
    def legacy_layout_status(self) -> dict[str, bool]: ...
```

Keep `PathBoundaryError`.

Compatibility note: existing code still imports `DataAnalystsRoot`; keep it temporarily if needed, but new code/tests in this task must use `DataAnalystsContext`.

## Tests To Add First

Create `tests/test_data_store_context.py`:

```python
from pathlib import Path

import pytest

from data_analysts.paths import DataAnalystsContext, PathBoundaryError


def test_context_defaults_data_store_under_project_root(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    assert context.project_root == tmp_path.resolve()
    assert context.data_store == (tmp_path / "data_store").resolve()
    assert context.config_path("universe_specs.json") == (tmp_path / "configs" / "universe_specs.json").resolve()
    assert context.store_path("manifests") == (tmp_path / "data_store" / "manifests").resolve()


def test_context_accepts_external_data_store(tmp_path):
    store = tmp_path / "external_store"
    context = DataAnalystsContext.from_paths(tmp_path / "project", store)

    assert context.project_root == (tmp_path / "project").resolve()
    assert context.data_store == store.resolve()


def test_context_rejects_store_path_escape(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.artifact_path("../escape.parquet")


def test_context_rejects_absolute_artifact_path(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path(tmp_path / "data_store" / "canonical" / "x.parquet")


def test_context_rejects_forbidden_path_segments(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("runtime/canonical/x.parquet")
    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("canonical/runs/x.parquet")
    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("canonical/real_all_products/x.parquet")


def test_context_uses_path_segments_not_substrings(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    assert context.validate_artifact_path("canonical/raw/company_runs_metric/year=2025/part.parquet") == (
        "canonical/raw/company_runs_metric/year=2025/part.parquet"
    )


def test_legacy_layout_status_reports_without_blocking(tmp_path):
    (tmp_path / "runtime").mkdir()
    context = DataAnalystsContext.from_paths(tmp_path)

    assert context.legacy_layout_status() == {
        "legacy_project_runtime_exists": True,
        "legacy_project_runs_exists": False,
    }
```

## Implementation Requirements

Modify `src/data_analysts/paths.py`:

- Add `FORBIDDEN_ARTIFACT_PATH_SEGMENTS = {"runtime", "runs", "real_all_products"}`.
- `from_paths(project_root, data_store=None)` resolves `project_root`; if `data_store is None`, use `<project_root>/data_store`.
- If `data_store` is relative, resolve it relative to `project_root`.
- `config_path(name)` returns `<project_root>/configs/<name>` and rejects traversal.
- `contract_path(name)` returns `<project_root>/contracts/<name>` and rejects traversal.
- `store_path(*parts)` returns `<data_store>/<parts>` and rejects traversal outside `data_store`.
- `validate_artifact_path(path)`:
  - rejects absolute paths
  - normalizes separators to posix
  - rejects `..`
  - rejects path segments exactly equal to `runtime`, `runs`, or `real_all_products`
  - returns normalized posix path
- `artifact_path(path)` calls `validate_artifact_path(path)` then resolves under `data_store`.
- `legacy_layout_status()` returns exactly:

```python
{
    "legacy_project_runtime_exists": (project_root / "runtime").exists(),
    "legacy_project_runs_exists": (project_root / "runs").exists(),
}
```

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py -q
```

Expected after implementation:

```text
7 passed
```

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-2-report.md
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
