# Data Store Formalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize DataAnalysts so project code/configs live under `project_root`, all data products live under `data_store`, and formal commands no longer use `--root`, `runtime/`, `runs/`, or `real_all_products/`.

**Architecture:** Introduce `DataAnalystsContext(project_root, data_store)` as the only path boundary object. Move artifact paths from `runtime/data_canonical/...` to data-store-relative `canonical/...`, publish metadata/config snapshots under `metadata/`, and make verify/inspect read only `data_store`.

**Tech Stack:** Python 3.10+, pathlib, argparse, json, hashlib, pyarrow, pytest. No new dependency is required.

## Global Constraints

- All implementation and plan artifacts must stay under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Do not introduce an ALF runtime adapter.
- Remove `--root`; do not keep it as a compatibility alias.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Artifact path validation must use path segments, not substring matching.
- Verify and inspect must not read legacy `runtime/` or `runs/` as formal artifacts.
- Legacy `runtime/` and `runs/` must not be auto-migrated or auto-deleted.
- Use TDD for behavior changes: add failing tests first, then implementation, then full relevant verification.

---

## File Structure

Modify:

- `src/data_analysts/paths.py`
  - Replace root-only path model with `DataAnalystsContext`.
  - Keep `PathBoundaryError`.
  - Add artifact path validation helpers and legacy layout detection.
- `src/data_analysts/cli.py`
  - Remove `--root`.
  - Add `--project-root` and `--data-store`.
  - Construct `DataAnalystsContext`.
- `src/data_analysts/config.py`
  - Load configs from `context.config_path(...)`.
- `src/data_analysts/artifacts.py`
  - Publish manifests under `data_store/manifests`.
  - Publish parquet under data-store-relative paths.
  - Reject forbidden path segments and absolute artifact paths.
- `src/data_analysts/diagnostics.py`
  - Write diagnostics under `data_store/diagnostics`.
- `src/data_analysts/pipeline.py`
  - Replace all `runtime/data_canonical/...` paths with `canonical/...`.
  - Publish jobs under `jobs/`.
  - Publish metadata/config snapshot after successful output publish.
- `src/data_analysts/verify.py`
  - Read manifests/jobs/diagnostics/metadata from `data_store`.
  - Add quantitative path and metadata checks.
- `src/data_analysts/inspect.py`
  - Read only `data_store`.
  - Report project/data-store/legacy/metrics summary.
- `README.md`
- `contracts/CLI_CONTRACT.md`
- `contracts/OUTPUT_CONTRACT.md`
- `contracts/VERIFICATION_CONTRACT.md`
- `contracts/CONFIG_CONTRACT.md`

Create:

- `src/data_analysts/metadata.py`
  - Publish `metadata/data_store_manifest.json`.
  - Copy config snapshot and calculate SHA-256 hashes.
- `tests/test_data_store_context.py`
- `tests/test_data_store_cli.py`
- `tests/test_data_store_metadata.py`

Update existing tests:

- `tests/test_pit_foundation_config.py`
- `tests/test_pit_foundation_verify.py`
- `tests/test_raw_family_pipeline.py`
- `tests/test_raw_family_verify.py`
- `tests/test_historical_universe_pipeline.py`
- `tests/test_historical_universe_verify.py`

---

## Task 1: Contract and Documentation Formalization

**Files:**
- Modify: `README.md`
- Modify: `contracts/CLI_CONTRACT.md`
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`
- Modify: `contracts/CONFIG_CONTRACT.md`
- Reference: `plans/2026-07-08-data-store-formalization-spec.md`

**Interfaces:**
- Consumes: finalized formalization spec.
- Produces: documentation that uses `project_root` and `data_store` as the formal public vocabulary.

- [ ] **Step 1: Update README command surface**

Replace formal command examples with:

```powershell
cd C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
.\.venv\Scripts\Activate.ps1

python -m data_analysts.cli run-full-history
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

Add explicit equivalent form:

```powershell
python -m data_analysts.cli run-full-history --project-root . --data-store .\data_store
```

Move every `--root` example to a short legacy warning section that says:

```text
--root has been removed. Use --project-root and --data-store.
```

- [ ] **Step 2: Update contracts**

Apply these contract edits:

- `CLI_CONTRACT.md`: remove `--root`, add `--project-root`, `--data-store`, and removed-argument rejection rule.
- `OUTPUT_CONTRACT.md`: replace `runtime/data_canonical/...` with `canonical/...`; add `metadata/`.
- `VERIFICATION_CONTRACT.md`: add quantitative metrics from the spec.
- `CONFIG_CONTRACT.md`: clarify configs are loaded from `project_root/configs`, while config snapshots are stored under `data_store/metadata/config_snapshot`.

- [ ] **Step 3: Run documentation scan**

Run:

```powershell
rg -n "--root|runtime/data_canonical|runs/real_all_products|runtime/manifests|runtime/jobs|runtime/diagnostics" README.md contracts
```

Expected:

- Only legacy warning or current-problem sections may match.
- No formal command example may use `--root`.

- [ ] **Step 4: Record review note**

Add a short note to the plan execution ledger, when execution starts, that Task 1 changed docs/contracts only and no runtime behavior.

---

## Task 2: DataAnalystsContext Path Layer

**Files:**
- Modify: `src/data_analysts/paths.py`
- Create: `tests/test_data_store_context.py`

**Interfaces:**
- Produces:
  - `DataAnalystsContext.from_paths(project_root: str | Path = ".", data_store: str | Path | None = None) -> DataAnalystsContext`
  - `context.config_path(name: str) -> Path`
  - `context.contract_path(name: str) -> Path`
  - `context.store_path(*parts: str) -> Path`
  - `context.artifact_path(path: str | Path) -> Path`
  - `context.validate_artifact_path(path: str | Path) -> str`
  - `context.legacy_layout_status() -> dict[str, bool]`
- Consumes: no pipeline changes yet.

- [ ] **Step 1: Write failing context tests**

Create `tests/test_data_store_context.py` with tests for:

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

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py -q
```

Expected:

```text
FAIL: cannot import name 'DataAnalystsContext'
```

- [ ] **Step 3: Implement context**

Modify `src/data_analysts/paths.py`:

- Add `FORBIDDEN_ARTIFACT_PATH_SEGMENTS = {"runtime", "runs", "real_all_products"}`.
- Add `DataAnalystsContext`.
- Keep `DataAnalystsRoot` only if needed temporarily by untouched code, but new code must use `DataAnalystsContext`.
- Implement `validate_artifact_path()` using `PurePosixPath(Path(path).as_posix()).parts`.
- Reject absolute artifact paths.
- Resolve data paths under `data_store`.

- [ ] **Step 4: Run context tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py -q
```

Expected:

```text
7 passed
```

---

## Task 3: Metadata and Config Snapshot Publisher

**Files:**
- Create: `src/data_analysts/metadata.py`
- Create: `tests/test_data_store_metadata.py`

**Interfaces:**
- Consumes:
  - `DataAnalystsContext`
  - loaded `RuntimeConfig`
- Produces:
  - `publish_data_store_metadata(context: DataAnalystsContext, config: RuntimeConfig) -> dict[str, object]`
  - `load_data_store_metadata(context: DataAnalystsContext) -> dict[str, object]`
  - `verify_config_snapshot_hashes(context: DataAnalystsContext) -> dict[str, int]`

- [ ] **Step 1: Write failing metadata tests**

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

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q
```

Expected:

```text
FAIL: No module named 'data_analysts.metadata'
```

- [ ] **Step 3: Implement metadata module**

Implement:

- Required config names exactly:
  - `mongodb_sources.json`
  - `source_family_profiles.json`
  - `universe_specs.json`
  - `source_catalog.json`
  - `pit_registry.json`
- SHA-256 hashing over snapshot file bytes.
- Atomic-ish text writes: write temp file then replace.
- `source_family_count` from `config.source_family_profiles["families"]`.
- `universe_spec_count` from `config.universe_specs["universes"]`.

- [ ] **Step 4: Run metadata tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q
```

Expected:

```text
2 passed
```

---

## Task 4: Config Loader, Diagnostics, and Artifact Publisher Migration

**Files:**
- Modify: `src/data_analysts/config.py`
- Modify: `src/data_analysts/diagnostics.py`
- Modify: `src/data_analysts/artifacts.py`
- Modify: `tests/test_pit_foundation_config.py`
- Modify: `tests/test_pit_foundation_verify.py`
- Modify: `tests/test_raw_family_verify.py`

**Interfaces:**
- Consumes:
  - `DataAnalystsContext`
  - `context.config_path(...)`
  - `context.store_path(...)`
  - `context.validate_artifact_path(...)`
- Produces:
  - `load_runtime_config(context: DataAnalystsContext)`.
  - `write_diagnostic(context, name, payload)` writes under `<data_store>/diagnostics`.
  - `ArtifactPublisher(context)` writes manifests under `<data_store>/manifests`.

- [ ] **Step 1: Write/update failing tests**

Update tests to construct context:

```python
from data_analysts.paths import DataAnalystsContext

context = DataAnalystsContext.from_paths(tmp_path)
config = load_runtime_config(context)
```

Add artifact publisher test in an existing verify or pipeline test:

```python
from data_analysts.artifacts import ArtifactPublisher, ArtifactError


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

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py -q
```

Expected:

- Failures from type/signature mismatch and old `runtime` paths.

- [ ] **Step 3: Update implementation**

Change imports and types:

- Replace `DataAnalystsRoot` with `DataAnalystsContext`.
- `load_runtime_config()` reads `context.config_path(name)`.
- `write_diagnostic()` writes to `context.store_path("diagnostics", *parts).with_suffix(".json")`.
- `ArtifactPublisher.publish_manifest()` writes to `context.store_path("manifests", f"{artifact_id}.json")`.
- `ArtifactPublisher.publish_parquet()` resolves via `context.artifact_path(path)`.
- `ArtifactPublisher._validate_relative_artifact_path()` returns `context.validate_artifact_path(path)`.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_context.py tests\test_data_store_metadata.py tests\test_pit_foundation_config.py tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py -q
```

Expected:

- All selected tests pass after updating expected paths.

---

## Task 5: Pipeline Layout Migration

**Files:**
- Modify: `src/data_analysts/pipeline.py`
- Modify: `tests/test_raw_family_pipeline.py`
- Modify: `tests/test_historical_universe_pipeline.py`
- Modify: `tests/test_historical_universe_verify.py`

**Interfaces:**
- Consumes:
  - `run_pipeline(context: DataAnalystsContext, config: RuntimeConfig, ...)`.
  - `publish_data_store_metadata(context, config)`.
- Produces:
  - Parquet under `canonical/...`.
  - Manifests under `manifests/...`.
  - Diagnostics under `diagnostics/...`.
  - Jobs under `jobs/...`.
  - Metadata under `metadata/...`.

- [ ] **Step 1: Write/update failing pipeline layout tests**

Update expected paths:

Old:

```text
runtime/data_canonical/raw/monthly_sales/available_year=2025/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=2025/part.parquet
runtime/manifests/<artifact_id>.json
```

New:

```text
data_store/canonical/raw/monthly_sales/available_year=2025/part.parquet
data_store/canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=2025/part.parquet
data_store/manifests/<artifact_id>.json
```

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

- [ ] **Step 2: Run pipeline tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_pipeline.py tests\test_historical_universe_pipeline.py -q
```

Expected:

- Failures from old `runtime/data_canonical` paths.

- [ ] **Step 3: Replace pipeline paths**

In `src/data_analysts/pipeline.py`, replace:

- `runtime/data_canonical/raw/...` -> `canonical/raw/...`
- `runtime/data_canonical/derived/...` -> `canonical/derived/...`
- `runtime/data_canonical/derived/pit/...` -> `canonical/derived/pit/...`
- `root.runtime_path("jobs", "pipeline_result.json")` -> `context.store_path("jobs", "pipeline_result.json")`
- stale universe cleanup root from `runtime/data_canonical/derived/universes/...` to `canonical/derived/universes/...`

Call `publish_data_store_metadata(context, config)` once after successful publish work and before writing `pipeline_result.json`.

- [ ] **Step 4: Run pipeline tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_pipeline.py tests\test_historical_universe_pipeline.py -q
```

Expected:

- All selected pipeline tests pass.

---

## Task 6: CLI Migration

**Files:**
- Modify: `src/data_analysts/cli.py`
- Create: `tests/test_data_store_cli.py`

**Interfaces:**
- Consumes:
  - `DataAnalystsContext.from_paths(project_root, data_store)`.
- Produces:
  - CLI commands without `--root`.
  - Common options: `--project-root`, `--data-store`.

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_data_store_cli.py`:

```python
import json
from pathlib import Path

from data_analysts.cli import build_parser, main


def test_parser_rejects_removed_root_argument():
    result = main(["verify", "--root", "."])

    assert result == 1


def test_parser_accepts_project_root_and_data_store(tmp_path):
    parser = build_parser()

    args = parser.parse_args([
        "verify",
        "--project-root",
        str(tmp_path),
        "--data-store",
        str(tmp_path / "store"),
    ])

    assert args.project_root == str(tmp_path)
    assert args.data_store == str(tmp_path / "store")


def test_default_data_store_is_project_root_data_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parser = build_parser()

    args = parser.parse_args(["inspect-artifacts"])

    assert args.project_root == "."
    assert args.data_store is None
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py -q
```

Expected:

- Fail because `--root` is currently accepted and `--project-root` is missing.

- [ ] **Step 3: Implement CLI args**

Modify `src/data_analysts/cli.py`:

- Add `_add_project_and_store(parser)`.
- Every subcommand gets:
  - `--project-root`, default `"."`
  - `--data-store`, default `None`
- Remove `_add_root`.
- Before `parse_args`, explicitly reject `--root` if present in `argv`:

```python
if argv is not None and "--root" in argv:
    print("--root has been removed. Use --project-root and --data-store.", file=sys.stderr)
    return 1
```

For `argv is None`, check `sys.argv[1:]` the same way.

- Construct:

```python
context = DataAnalystsContext.from_paths(args.project_root, args.data_store)
```

- Pass `context` to config, pipeline, verify, inspect, and blocked job write.

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py -q
```

Expected:

```text
3 passed
```

---

## Task 7: Verify and Inspect Formal Data Store

**Files:**
- Modify: `src/data_analysts/verify.py`
- Modify: `src/data_analysts/inspect.py`
- Modify: `tests/test_pit_foundation_verify.py`
- Modify: `tests/test_raw_family_verify.py`
- Modify: `tests/test_historical_universe_verify.py`

**Interfaces:**
- Consumes:
  - `DataAnalystsContext`
  - `verify_config_snapshot_hashes(context)`
  - `context.legacy_layout_status()`
- Produces:
  - Quantitative metrics:
    - `artifact_path_count`
    - `absolute_artifact_path_count`
    - `artifact_path_escape_count`
    - `forbidden_path_segment_count`
    - `manifest_count`
    - `required_manifest_missing_count`
    - `config_snapshot_file_count`
    - `config_snapshot_hash_mismatch_count`
    - `legacy_project_runtime_exists`
    - `legacy_project_runs_exists`

- [ ] **Step 1: Write/update failing verify tests**

Add tests:

```python
def test_verify_blocks_absolute_artifact_path(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    artifact = tmp_path / "data_store" / "canonical" / "dummy.parquet"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"dummy")
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "dummy.json").write_text(json.dumps({
        "artifact_id": "dummy",
        "artifact_paths": [str(artifact)],
        "status": "ready",
        "partitioning": ["single_file"],
        "pit_policy": "test",
    }), encoding="utf-8")

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["path_metrics"]["absolute_artifact_path_count"] == 1


def test_verify_blocks_missing_data_store_metadata(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "metadata"
```

Add inspect test:

```python
def test_inspect_reports_legacy_layout_without_using_it(tmp_path):
    (tmp_path / "runtime").mkdir()
    context = DataAnalystsContext.from_paths(tmp_path)

    result = inspect_artifacts(context)

    assert result["legacy_layout_detected"] is True
    assert result["legacy_project_runtime_exists"] is True
```

- [ ] **Step 2: Run verify/inspect tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q
```

Expected:

- Failures from old `runtime/manifests` and missing metadata metrics.

- [ ] **Step 3: Implement formal verify**

Modify `verify.py`:

- Manifests dir: `context.store_path("manifests")`.
- Verification result: `context.store_path("jobs", "verification_result.json")`.
- Artifact path resolution: `context.artifact_path(path)`.
- Add `_artifact_path_metrics(context, manifests)`.
- Add metadata gate:
  - missing manifest -> blocked step `metadata`.
  - missing snapshot -> blocked step `metadata`.
  - hash mismatch -> blocked step `metadata`.
- Include legacy status as diagnostics only, not blocker.

- [ ] **Step 4: Implement formal inspect**

Modify `inspect.py`:

- Manifests dir: `context.store_path("manifests")`.
- Diagnostics dir: `context.store_path("diagnostics")`.
- Add top-level fields:
  - `project_root`
  - `data_store`
  - `legacy_layout_detected`
  - `legacy_project_runtime_exists`
  - `legacy_project_runs_exists`
  - path and metadata metrics.

- [ ] **Step 5: Run verify/inspect tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_pit_foundation_verify.py tests\test_raw_family_verify.py tests\test_historical_universe_verify.py -q
```

Expected:

- All selected verify tests pass.

---

## Task 8: Full Test Migration and Legacy Layout Guard

**Files:**
- Modify all tests still importing `DataAnalystsRoot`.
- Modify all tests still expecting `runtime/`.
- Modify all tests still expecting `runs/real_all_products`.

**Interfaces:**
- Consumes all previous tasks.
- Produces full test suite green under formal data-store layout.

- [ ] **Step 1: Find remaining legacy references in source/tests**

Run:

```powershell
rg -n "DataAnalystsRoot|runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests
```

Expected before cleanup:

- Some matches may remain.

- [ ] **Step 2: Update remaining source and tests**

Rules:

- Replace `DataAnalystsRoot.from_path(tmp_path)` with `DataAnalystsContext.from_paths(tmp_path)`.
- Replace path expectations:
  - `runtime/data_canonical` -> `data_store/canonical`
  - `runtime/manifests` -> `data_store/manifests`
  - `runtime/jobs` -> `data_store/jobs`
  - `runs/real_all_products/diagnostics` -> `data_store/diagnostics`
- Keep legacy strings only in explicit legacy rejection tests and documentation tests.

- [ ] **Step 3: Run full tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 4: Run legacy scan**

Run:

```powershell
rg -n "runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests README.md contracts
```

Expected:

- No source matches.
- Test/doc matches only in legacy warning or explicit rejection tests.

---

## Task 9: Bounded Real Mongo Smoke and Final Evidence

**Files:**
- Modify: `plans/sdd/data-store-formalization/progress.md`
- Runtime output: `data_store/`

**Interfaces:**
- Consumes formal CLI.
- Produces real data-store smoke evidence.

- [ ] **Step 1: Create progress ledger**

Create:

```text
plans/sdd/data-store-formalization/progress.md
```

Record each task result and reviewer status during execution.

- [ ] **Step 2: Run bounded smoke**

Run:

```powershell
$env:DATA_ANALYSTS_MONGODB_URI = "mongodb://localhost:27017/"
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31
```

Expected:

```text
ready
```

- [ ] **Step 3: Run verify**

Run:

```powershell
python -m data_analysts.cli verify --project-root . --data-store .\data_store
```

Expected:

```text
ready
```

- [ ] **Step 4: Run inspect compact summary**

Run:

```powershell
python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store
```

Expected summary fields:

```text
status == ready
legacy_layout_detected may be true if old runtime/runs already exist
forbidden_path_segment_count == 0
absolute_artifact_path_count == 0
artifact_path_escape_count == 0
config_snapshot_hash_mismatch_count == 0
```

- [ ] **Step 5: Run filesystem guard**

Run this after the smoke:

```powershell
@'
from pathlib import Path
project = Path(".")
store = Path("data_store")
print({
    "canonical_exists": (store / "canonical").exists(),
    "manifests_exists": (store / "manifests").exists(),
    "metadata_exists": (store / "metadata" / "data_store_manifest.json").exists(),
    "jobs_exists": (store / "jobs").exists(),
    "formal_runtime_under_store": (store / "runtime").exists(),
    "formal_runs_under_store": (store / "runs").exists(),
})
'@ | python -
```

Expected:

```text
canonical_exists == True
manifests_exists == True
metadata_exists == True
jobs_exists == True
formal_runtime_under_store == False
formal_runs_under_store == False
```

- [ ] **Step 6: Final review**

Dispatch a read-only reviewer with these questions:

```text
1. Does formal CLI remove --root and use --project-root / --data-store cleanly?
2. Are all artifact paths data-store-relative and free of runtime/runs/real_all_products path segments?
3. Does metadata make data_store self-describing?
4. Does verify use quantitative gates for path safety and metadata hash consistency?
5. Does inspect report legacy layout without treating it as formal artifact surface?
6. Did implementation stay fully inside DataAnalysts?
```

Fix any Critical/Important findings and re-review until clean.

---

## Self-Review Checklist

- Spec coverage:
  - `--root` removal: Task 1, Task 6, Task 8.
  - `project_root` / `data_store`: Task 2, Task 6.
  - formal artifact layout: Task 4, Task 5.
  - metadata/config snapshot: Task 3, Task 7, Task 9.
  - verify/inspect quantitative gates: Task 7, Task 9.
  - legacy layout non-use: Task 1, Task 2, Task 7, Task 8, Task 9.
- Placeholder scan:
  - No `TBD`, `TODO`, or unspecified task remains.
- Type consistency:
  - All new code paths use `DataAnalystsContext`.
  - All commands use `--project-root` and `--data-store`.
  - All formal artifacts are data-store-relative.

## Execution Options

Plan complete. Recommended execution mode is Subagent-Driven Development:

1. Subagent-Driven: dispatch one worker per task, review after every task, fix Critical/Important findings before proceeding.
2. Inline Execution: execute tasks in this session with checkpoints.

