# Task 3 Brief: Metadata and Config Snapshot Publisher

## Context

`data_store` must be self-describing. A copied data store must include the configs used to create it and hashes that verify those snapshots.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Use TDD: add failing tests first, then implementation.

## Files

- Create: `src/data_analysts/metadata.py`
- Create: `tests/test_data_store_metadata.py`

## Required Interfaces

```python
REQUIRED_CONFIG_SNAPSHOT_FILES = [
    "mongodb_sources.json",
    "source_family_profiles.json",
    "universe_specs.json",
    "source_catalog.json",
    "pit_registry.json",
]


def publish_data_store_metadata(
    context: DataAnalystsContext,
    config: RuntimeConfig,
) -> dict[str, object]: ...


def load_data_store_metadata(
    context: DataAnalystsContext,
) -> dict[str, object]: ...


def verify_config_snapshot_hashes(
    context: DataAnalystsContext,
) -> dict[str, int]: ...
```

## Tests To Add First

Create `tests/test_data_store_metadata.py`:

```python
import json
from pathlib import Path

from data_analysts.config import load_runtime_config
from data_analysts.metadata import publish_data_store_metadata, verify_config_snapshot_hashes
from data_analysts.paths import DataAnalystsContext


CONFIG_NAMES = [
    "mongodb_sources.json",
    "source_family_profiles.json",
    "universe_specs.json",
    "source_catalog.json",
    "pit_registry.json",
]


def _copy_configs(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = tmp_path / "configs"
    target.mkdir()
    for name in CONFIG_NAMES:
        (target / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")


def test_publish_data_store_metadata_writes_manifest_and_config_snapshot(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    metadata = publish_data_store_metadata(context, config)

    manifest_path = tmp_path / "data_store" / "metadata" / "data_store_manifest.json"
    assert manifest_path.exists()
    snapshot_dir = tmp_path / "data_store" / "metadata" / "config_snapshot"
    assert sorted(path.name for path in snapshot_dir.glob("*.json")) == sorted(CONFIG_NAMES)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["config_snapshot_file_count"] == len(CONFIG_NAMES)
    assert sorted(payload["config_hashes"]) == sorted(CONFIG_NAMES)
    assert metadata["config_snapshot_file_count"] == len(CONFIG_NAMES)


def test_verify_config_snapshot_hashes_detects_mismatch(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    publish_data_store_metadata(context, config)
    snapshot = tmp_path / "data_store" / "metadata" / "config_snapshot" / "universe_specs.json"
    snapshot.write_text("{}", encoding="utf-8")

    result = verify_config_snapshot_hashes(context)

    assert result["config_snapshot_hash_mismatch_count"] == 1
```

## Implementation Requirements

- Copy required configs from `context.config_path(name)` to `context.store_path("metadata", "config_snapshot", name)`.
- Hash snapshot bytes with SHA-256.
- Write `context.store_path("metadata", "data_store_manifest.json")`.
- Manifest required fields:
  - `schema_version`
  - `created_at`
  - `project_root_at_build_time`
  - `data_store`
  - `config_hashes`
  - `config_snapshot_file_count`
  - `source_family_count`
  - `universe_spec_count`
- `verify_config_snapshot_hashes()` returns:

```python
{
    "config_snapshot_file_count": int,
    "config_snapshot_missing_count": int,
    "config_snapshot_hash_mismatch_count": int,
}
```

- Use temp file + replace for JSON writes.

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q
```

Expected after implementation:

```text
2 passed
```

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-3-report.md
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
