# Task 5 Review Package


## FILE: plans\sdd\historical-universe\task-5-brief.md
```
# Task 5 Brief

### Task 5: Historical Universe Verification

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\verify.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_verify.py`

**Interfaces:**
- Consumes: universe manifests and parquet artifacts.
- Produces: fail-closed `blocked_step="historical_universe"` when historical universe rules fail.

- [ ] **Step 1: Write failing verification tests**

Create `tests/test_historical_universe_verify.py`:

```python
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.paths import DataAnalystsRoot
from data_analysts.verify import verify_runtime


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in ["mongodb_sources.json", "source_family_profiles.json", "universe_specs.json", "source_catalog.json", "pit_registry.json"]:
        (tmp_path / "configs" / name).write_text((ROOT / "configs" / name).read_text(encoding="utf-8"), encoding="utf-8")


def _write_universe_artifact(tmp_path: Path, rows: list[dict[str, object]]) -> None:
    artifact = tmp_path / "runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact)
    manifests = tmp_path / "runtime/manifests"
    manifests.mkdir(parents=True)
    (manifests / "universe_tw_equity_liquid_top500.json").write_text(json.dumps({
        "artifact_id": "universe_tw_equity_liquid_top500",
        "schema_version": "1.0",
        "layer": "derived",
        "source_families": ["security_panel_history"],
        "source_collections": [],
        "row_count": len(rows),
        "date_range": ["2025-01-02", "2025-01-02"],
        "availability_date_range": ["2025-01-03", "2025-01-03"],
        "columns": list(rows[0].keys()),
        "partitioning": ["as_of_year"],
        "artifact_paths": ["runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"],
        "pit_policy": "effective_next_trading_day_membership",
        "data_cutoff_at": "2025-01-02T00:00:00Z",
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": "2026-07-07T00:00:00Z"
    }), encoding="utf-8")


def test_verify_blocks_historical_universe_same_day_effective_date(tmp_path):
    _copy_configs(tmp_path)
    _write_universe_artifact(tmp_path, [{
        "as_of_date": "2025-01-02",
        "effective_date": "2025-01-02",
        "universe_id": "tw_equity_liquid_top500",
        "ticker": "2330",
        "rank": 1,
        "included": True,
        "reason": "selected",
    }])
    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))
    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "effective_date" in result["message"]
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_verify.py -q
```

Expected: FAIL because verify does not enforce historical effective-date rules.

- [ ] **Step 3: Implement verification**

Modify `_check_universe_manifest()` in `verify.py`:

```python
def _check_universe_manifest(root: DataAnalystsRoot, manifest: dict[str, Any]) -> str | None:
    artifact_id = manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("universe_"):
        return None
    is_historical = manifest.get("partitioning") == ["as_of_year"] or manifest.get("pit_policy") == "effective_next_trading_day_membership"
    for artifact_path in manifest.get("artifact_paths", []):
        rows = pq.ParquetFile(root.resolve_output(artifact_path)).read().to_pylist()
        if is_historical:
            error = _check_historical_universe_rows(artifact_id, rows)
            if error:
                return error
        else:
            error = _check_latest_universe_rows(rows)
            if error:
                return error
    return None
```

Add:

```python
def _check_historical_universe_rows(artifact_id: str, rows: list[dict[str, Any]]) -> str | None:
    required = {"as_of_date", "effective_date", "universe_id", "ticker", "rank"}
    seen_memberships: set[tuple[Any, Any, Any]] = set()
    seen_ranks: set[tuple[Any, Any, Any]] = set()
    for row in rows:
        missing = [field for field in required if row.get(field) in {None, ""}]
        if missing:
            return f"historical universe {artifact_id} missing required fields: {', '.join(missing)}"
        if str(row["effective_date"]) <= str(row["as_of_date"]):
            return f"historical universe {artifact_id} has effective_date <= as_of_date"
        membership_key = (row.get("effective_date"), row.get("universe_id"), row.get("ticker"))
        if membership_key in seen_memberships:
            return "duplicate historical universe effective membership key"
        seen_memberships.add(membership_key)
        rank_key = (row.get("effective_date"), row.get("universe_id"), row.get("rank"))
        if rank_key in seen_ranks:
            return "duplicate historical universe rank"
        seen_ranks.add(rank_key)
    return None
```

In `verify_runtime()`, when `_check_universe_manifest()` returns a string for a historical manifest, block with `blocked_step="historical_universe"` instead of `"universe"`.

- [ ] **Step 4: Add small-file and top-N diagnostics check**

Extend historical universe verification to count paths matching `membership_by_date/as_of_date=` under historical manifests. If any exist during a historical range run, block:

```text
small_file_daily_partition_count > 0
```

Use universe specs to check top-N manifests:

```text
row_count_by_effective_date <= limit
```

For `eligible_count >= limit`, enforce included count equals limit through diagnostics parquet if present.

- [ ] **Step 5: Run verification tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_verify.py tests\test_raw_family_verify.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src\data_analysts\verify.py tests\test_historical_universe_verify.py
git commit -m "test: verify historical universe gates"
```


```


## FILE: plans\sdd\historical-universe\task-5-report.md
```
STATUS: GREEN

changed files
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\verify.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_verify.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-5-report.md`

RED test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests/test_historical_universe_verify.py -q`
- Result: FAIL, `6 failed in 0.70s`
- Failure summary: `verify_runtime()` returned `status == "ready"` for all historical-universe violations because `verify.py` only checked latest-universe uniqueness on `(as_of_date, universe_id, ticker/rank)` and had no historical gate routing.

GREEN test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests/test_historical_universe_verify.py tests/test_raw_family_verify.py tests/test_pit_foundation_verify.py -q`
- Result: PASS, `19 passed in 0.73s`

self-review
- Followed TDD order: added historical verify tests first, captured RED, then implemented the minimum verification changes in `verify.py`.
- Historical manifests are now detected via `partitioning == ["as_of_year"]` or `pit_policy == "effective_next_trading_day_membership"` and fail with `blocked_step="historical_universe"`.
- Historical membership validation now fails closed on:
  - missing required fields
  - `effective_date <= as_of_date`
  - duplicate membership key by `(effective_date, universe_id, ticker)`
  - duplicate rank by `(effective_date, universe_id, rank)`
  - historical manifests that still reference `membership_by_date/as_of_date=*`
  - top-N overflow by `effective_date`
  - underfilled top-N when diagnostics show a single-date run had enough candidates
- Latest universe verification path remains unchanged, so existing non-historical behavior stays on the original `blocked_step="universe"` contract.

concerns
- The underfilled top-N diagnostics gate is intentionally conservative: it only fires when diagnostics exist and `as_of_date_count == 1`, because current diagnostics are aggregate-at-run level rather than per-`effective_date`.
- Historical diagnostics are discovered from the canonical `membership_by_year/...` artifact path. If future publishing changes that directory contract, verify must be updated in lockstep.

```


## FILE: src\data_analysts\verify.py
```
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pyarrow.parquet as pq

from data_analysts.config import ConfigError, RuntimeConfig, load_runtime_config
from data_analysts.diagnostics import write_diagnostic
from data_analysts.inspect import check_raw_family_diagnostics
from data_analysts.paths import DataAnalystsRoot, PathBoundaryError
from data_analysts.pit import PitError, normalize_date
from data_analysts.source_catalog import load_pit_registry, load_source_catalog


LEAKAGE_TOKENS = ("future", "forward", "next", "realized", "outcome", "label_return")


def verify_runtime(root: DataAnalystsRoot, as_of_date: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        config = load_runtime_config(root)
    except ConfigError as exc:
        result = _blocked("config", str(exc), ["fix configs/*.json"], as_of_date, checks)
        _write_verification_result(root, result)
        return result

    pit_foundation = _pit_foundation_metrics(root, config)
    write_diagnostic(root, "pit_foundation/source_catalog", pit_foundation)
    if _pit_foundation_blocked(pit_foundation):
        result = _blocked(
            "pit_foundation",
            "PIT foundation source catalog checks failed",
            ["fix PIT source catalog fields and forbidden source usage"],
            as_of_date,
            checks,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(root, result)
        return result

    raw_error, raw_metrics = check_raw_family_diagnostics(root)
    checks.append(
        {
            "check": "raw_family_diagnostics",
            "status": "ready" if raw_error is None else "blocked",
            **raw_metrics,
        }
    )
    if raw_error:
        result = _blocked(
            "raw_family_diagnostics",
            raw_error,
            ["fix raw family diagnostics and rebuild affected families"],
            as_of_date,
            checks,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(root, result)
        return result

    manifests_dir = root.runtime_path("manifests")
    if not manifests_dir.exists():
        result = _blocked(
            "manifests",
            "missing runtime/manifests",
            ["run a DataAnalysts refresh before verify"],
            as_of_date,
            checks,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(root, result)
        return result

    for manifest_path in sorted(manifests_dir.glob("*.json")):
        manifest = _load_manifest(manifest_path)
        path_error = _check_manifest_paths(root, manifest)
        if path_error:
            result = _blocked("manifest_paths", path_error, ["fix manifest artifact_paths"], as_of_date, checks)
            result["pit_foundation"] = pit_foundation
            _write_verification_result(root, result)
            return result

        selected_pit_error = _check_selected_pit_manifest(root, manifest)
        if selected_pit_error:
            result = _blocked(
                "selected_pit_artifacts",
                selected_pit_error,
                ["rebuild selected PIT artifacts from PIT-safe source rows"],
                as_of_date,
                checks,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(root, result)
            return result

        security_panel_error = _check_security_panel_manifest(manifest)
        if security_panel_error:
            result = _blocked("security_panel", security_panel_error, ["remove leakage columns"], as_of_date, checks)
            result["pit_foundation"] = pit_foundation
            _write_verification_result(root, result)
            return result

        universe_error = _check_universe_manifest(root, manifest, config.universe_specs)
        if universe_error:
            blocked_step = (
                "historical_universe" if _is_historical_universe_manifest(manifest) else "universe"
            )
            result = _blocked(blocked_step, universe_error, ["rebuild universe membership"], as_of_date, checks)
            result["pit_foundation"] = pit_foundation
            _write_verification_result(root, result)
            return result

    result = {
        "status": "ready",
        "checked_at": _utc_now(),
        "scope": as_of_date or "all",
        "checks": checks,
        "pit_foundation": pit_foundation,
    }
    _write_verification_result(root, result)
    return result


def _pit_foundation_metrics(
    root: DataAnalystsRoot, config: RuntimeConfig | None = None
) -> dict[str, object]:
    catalog = config.source_catalog if config is not None else load_source_catalog(root)
    registry = config.pit_registry if config is not None else load_pit_registry(root)
    sources = [item for item in catalog.get("sources", []) if isinstance(item, dict)]
    forbidden = [item for item in catalog.get("forbidden_sources", []) if isinstance(item, dict)]
    missing_pit = [item.get("family_id") for item in sources if not item.get("pit_field")]
    missing_key = [item.get("family_id") for item in sources if not item.get("logical_key")]
    forbidden_usage_count = _forbidden_manifest_source_usage_count(root, catalog)
    return {
        "forbidden_source_count": len(forbidden),
        "approved_source_count": len(sources),
        "pit_registry_family_count": len(registry.get("families", {})),
        "forbidden_source_usage_count": forbidden_usage_count,
        "missing_pit_field_count": len(missing_pit),
        "missing_logical_key_count": len(missing_key),
        "missing_pit_field_families": missing_pit,
        "missing_logical_key_families": missing_key,
    }


def _pit_foundation_blocked(metrics: dict[str, object]) -> bool:
    return (
        metrics["forbidden_source_usage_count"] != 0
        or metrics["missing_pit_field_count"] != 0
        or metrics["missing_logical_key_count"] != 0
    )


def _blocked(
    blocked_step: str,
    message: str,
    next_actions: list[str],
    as_of_date: str | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "checked_at": _utc_now(),
        "scope": as_of_date or "all",
        "blocked_step": blocked_step,
        "message": message,
        "next_actions": next_actions,
        "checks": checks,
    }


def _write_verification_result(root: DataAnalystsRoot, result: dict[str, Any]) -> None:
    result_path = root.runtime_path("jobs", "verification_result.json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def _load_manifest(path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path.name}")
    return payload


def _forbidden_manifest_source_usage_count(root: DataAnalystsRoot, catalog: dict[str, Any]) -> int:
    forbidden_pairs = _forbidden_pairs(catalog)
    if not forbidden_pairs:
        return 0
    manifests_dir = root.runtime_path("manifests")
    if not manifests_dir.exists():
        return 0
    usage_count = 0
    for manifest_path in sorted(manifests_dir.glob("*.json")):
        manifest = _load_manifest(manifest_path)
        usage_count += _count_forbidden_source_mentions(manifest, forbidden_pairs)
    return usage_count


def _forbidden_pairs(catalog: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for item in catalog.get("forbidden_sources", []):
        if not isinstance(item, dict):
            continue
        database = str(item.get("database") or "")
        collection = str(item.get("collection") or "")
        if database and collection:
            pairs.add((database, collection))
    return pairs


def _count_forbidden_source_mentions(
    value: Any,
    forbidden_pairs: set[tuple[str, str]],
    inherited_database: str | None = None,
) -> int:
    if isinstance(value, dict):
        database = _normalized_text(value.get("database")) or inherited_database
        count = 0
        for field in ("collection", "source_collection", "source_collections"):
            if field in value:
                count += _count_forbidden_collections(database, value[field], forbidden_pairs)
        for field, child in value.items():
            if field in {"database", "collection", "source_collection", "source_collections"}:
                continue
            count += _count_forbidden_source_mentions(child, forbidden_pairs, database)
        return count
    if isinstance(value, list):
        return sum(
            _count_forbidden_source_mentions(item, forbidden_pairs, inherited_database)
            for item in value
        )
    return _count_forbidden_text(value, forbidden_pairs, inherited_database)


def _count_forbidden_collections(
    database: str | None,
    value: Any,
    forbidden_pairs: set[tuple[str, str]],
) -> int:
    if isinstance(value, dict):
        return _count_forbidden_source_mentions(value, forbidden_pairs, database)
    if isinstance(value, list):
        return sum(_count_forbidden_collections(database, item, forbidden_pairs) for item in value)
    return _count_forbidden_text(value, forbidden_pairs, database)


def _count_forbidden_text(
    value: Any,
    forbidden_pairs: set[tuple[str, str]],
    database: str | None,
) -> int:
    text = _normalized_text(value)
    if not text:
        return 0
    count = 0
    if database and (database, text) in forbidden_pairs:
        count += 1
    for forbidden_database, forbidden_collection in forbidden_pairs:
        if text == f"{forbidden_database}.{forbidden_collection}":
            count += 1
    return count


def _normalized_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _check_manifest_paths(root: DataAnalystsRoot, manifest: dict[str, Any]) -> str | None:
    artifact_paths = manifest.get("artifact_paths")
    if not isinstance(artifact_paths, list) or not artifact_paths:
        return f"manifest {manifest.get('artifact_id')} has no artifact_paths"
    for artifact_path in artifact_paths:
        if not isinstance(artifact_path, str):
            return f"manifest {manifest.get('artifact_id')} has non-string artifact_path"
        try:
            resolved = root.resolve_output(artifact_path)
        except PathBoundaryError:
            return f"manifest {manifest.get('artifact_id')} artifact path resolves outside DataAnalysts root"
        if not resolved.exists():
            return f"manifest {manifest.get('artifact_id')} artifact path does not exist: {artifact_path}"
    return None


def _check_security_panel_manifest(manifest: dict[str, Any]) -> str | None:
    if manifest.get("artifact_id") != "security_panel":
        return None
    columns = manifest.get("columns") or []
    leakage_columns = [
        column
        for column in columns
        if isinstance(column, str) and any(token in column.lower() for token in LEAKAGE_TOKENS)
    ]
    if leakage_columns:
        return f"security panel contains leakage columns: {', '.join(leakage_columns)}"
    return None


def _check_selected_pit_manifest(root: DataAnalystsRoot, manifest: dict[str, Any]) -> str | None:
    artifact_id = manifest.get("artifact_id")
    if artifact_id not in {"financial_statement_pit_selected", "self_reported_numbers_pit_selected"}:
        return None
    required_columns = {"source_available_date", "decision_date"}
    for artifact_path in manifest.get("artifact_paths", []):
        path = root.resolve_output(artifact_path)
        parquet_file = pq.ParquetFile(path)
        columns = set(parquet_file.schema.names)
        missing = sorted(required_columns - columns)
        if missing:
            return f"selected PIT artifact {artifact_id} missing required columns: {', '.join(missing)}"
        rows = parquet_file.read(columns=sorted(required_columns)).to_pylist()
        for index, row in enumerate(rows):
            try:
                source_available_date = normalize_date(row.get("source_available_date"))
                decision_date = normalize_date(row.get("decision_date"))
            except PitError as exc:
                return f"selected PIT artifact {artifact_id} has invalid PIT date at row {index}: {exc}"
            if source_available_date is None:
                return f"selected PIT artifact {artifact_id} has blank source_available_date at row {index}"
            if decision_date is None:
                return f"selected PIT artifact {artifact_id} has blank decision_date at row {index}"
            if source_available_date > decision_date:
                return (
                    f"selected PIT artifact {artifact_id} has source_available_date > decision_date "
                    f"at row {index}: {source_available_date} > {decision_date}"
                )
    return None


def _check_universe_manifest(
    root: DataAnalystsRoot,
    manifest: dict[str, Any],
    universe_specs: dict[str, Any],
) -> str | None:
    artifact_id = manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("universe_"):
        return None
    if _is_historical_universe_manifest(manifest):
        return _check_historical_universe_manifest(root, manifest, universe_specs)
    for artifact_path in manifest.get("artifact_paths", []):
        rows = pq.ParquetFile(root.resolve_output(artifact_path)).read().to_pylist()
        seen_memberships: set[tuple[Any, Any, Any]] = set()
        seen_ranks: set[tuple[Any, Any, Any]] = set()
        for row in rows:
            membership_key = (row.get("as_of_date"), row.get("universe_id"), row.get("ticker"))
            if membership_key in seen_memberships:
                return "duplicate membership key in universe artifact"
            seen_memberships.add(membership_key)
            rank_key = (row.get("as_of_date"), row.get("universe_id"), row.get("rank"))
            if rank_key in seen_ranks:
                return "duplicate rank in universe artifact"
            seen_ranks.add(rank_key)
    return None


def _is_historical_universe_manifest(manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("partitioning") == ["as_of_year"]
        or manifest.get("pit_policy") == "effective_next_trading_day_membership"
    )


def _check_historical_universe_manifest(
    root: DataAnalystsRoot,
    manifest: dict[str, Any],
    universe_specs: dict[str, Any],
) -> str | None:
    artifact_id = str(manifest["artifact_id"])
    universe_id = artifact_id.removeprefix("universe_")
    limit = _universe_top_n_limit(universe_specs, universe_id)
    small_file_daily_partition_count = 0
    effective_counts: dict[str, int] = {}
    seen_memberships: set[tuple[Any, Any, Any]] = set()
    seen_ranks: set[tuple[Any, Any, Any]] = set()
    required = {"as_of_date", "effective_date", "universe_id", "ticker", "rank"}
    for artifact_path in manifest.get("artifact_paths", []):
        artifact_path_text = str(artifact_path)
        if "membership_by_date" in artifact_path_text and "as_of_date=" in artifact_path_text:
            small_file_daily_partition_count += 1
            continue
        rows = pq.ParquetFile(root.resolve_output(artifact_path)).read().to_pylist()
        for row in rows:
            missing = sorted(field for field in required if row.get(field) in {None, ""})
            if missing:
                return (
                    f"historical universe {artifact_id} missing required fields: "
                    f"{', '.join(missing)}"
                )
            as_of_date = str(row["as_of_date"])
            effective_date = str(row["effective_date"])
            if effective_date <= as_of_date:
                return f"historical universe {artifact_id} has effective_date <= as_of_date"
            membership_key = (row.get("effective_date"), row.get("universe_id"), row.get("ticker"))
            if membership_key in seen_memberships:
                return "duplicate historical universe effective membership key"
            seen_memberships.add(membership_key)
            rank_key = (row.get("effective_date"), row.get("universe_id"), row.get("rank"))
            if rank_key in seen_ranks:
                return "duplicate historical universe effective rank"
            seen_ranks.add(rank_key)
            effective_counts[effective_date] = effective_counts.get(effective_date, 0) + 1
    if small_file_daily_partition_count > 0:
        return f"historical universe {artifact_id} has small_file_daily_partition_count > 0"
    if isinstance(limit, int):
        for effective_date, row_count in sorted(effective_counts.items()):
            if row_count > limit:
                return (
                    f"historical universe {artifact_id} row_count per effective_date exceeds top-n limit: "
                    f"{effective_date} has {row_count} rows > {limit}"
                )
        diagnostics_error = _check_historical_universe_diagnostics(root, manifest, limit)
        if diagnostics_error:
            return diagnostics_error
    return None


def _universe_top_n_limit(universe_specs: dict[str, Any], universe_id: str) -> int | None:
    for spec in universe_specs.get("universes", []):
        if isinstance(spec, dict) and spec.get("universe_id") == universe_id:
            limit = spec.get("limit")
            return limit if isinstance(limit, int) else None
    return None


def _check_historical_universe_diagnostics(
    root: DataAnalystsRoot,
    manifest: dict[str, Any],
    limit: int,
) -> str | None:
    diagnostics_path = _historical_universe_diagnostics_path(root, manifest)
    if diagnostics_path is None or not diagnostics_path.exists():
        return None
    rows = pq.ParquetFile(diagnostics_path).read().to_pylist()
    if not rows:
        return None
    row = rows[0]
    max_included_count = row.get("max_included_count")
    if isinstance(max_included_count, int) and max_included_count > limit:
        return (
            f"historical universe {manifest.get('artifact_id')} diagnostics max_included_count "
            f"exceeds top_n_limit: {max_included_count} > {limit}"
        )
    candidate_count = row.get("candidate_count")
    as_of_date_count = row.get("as_of_date_count")
    if (
        isinstance(candidate_count, int)
        and isinstance(max_included_count, int)
        and isinstance(as_of_date_count, int)
        and as_of_date_count == 1
        and candidate_count >= limit
        and max_included_count != limit
    ):
        return (
            f"historical universe {manifest.get('artifact_id')} included_count must equal top_n_limit "
            f"when eligible_count >= limit"
        )
    return None


def _historical_universe_diagnostics_path(
    root: DataAnalystsRoot,
    manifest: dict[str, Any],
):
    artifact_paths = manifest.get("artifact_paths")
    if not isinstance(artifact_paths, list):
        return None
    for artifact_path in artifact_paths:
        if not isinstance(artifact_path, str):
            continue
        marker = "/membership_by_year/"
        normalized = artifact_path.replace("\\", "/")
        if marker not in normalized:
            continue
        universe_root = normalized.split(marker, 1)[0]
        return root.resolve_output(f"{universe_root}/diagnostics/diagnostics.parquet")
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

```


## FILE: tests\test_historical_universe_verify.py
```
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.paths import DataAnalystsRoot
from data_analysts.verify import verify_runtime


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        (tmp_path / "configs" / name).write_text(
            (ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _base_manifest(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "artifact_id": "universe_tw_equity_liquid_top500",
        "schema_version": "1.0",
        "layer": "derived",
        "source_families": ["security_panel_history"],
        "source_collections": [],
        "row_count": len(rows),
        "date_range": ["2025-01-02", "2025-01-03"],
        "availability_date_range": ["2025-01-03", "2025-01-06"],
        "columns": list(rows[0].keys()),
        "partitioning": ["as_of_year"],
        "artifact_paths": [
            "runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"
        ],
        "pit_policy": "effective_next_trading_day_membership",
        "data_cutoff_at": "2025-01-03T00:00:00Z",
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": "2026-07-07T00:00:00Z",
    }


def _write_universe_fixture(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    diagnostics_rows: list[dict[str, object]] | None = None,
    extra_manifest_paths: list[str] | None = None,
) -> None:
    artifact = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "membership_by_year"
        / "as_of_year=2025"
        / "part.parquet"
    )
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact)

    if extra_manifest_paths:
        for relative_path in extra_manifest_paths:
            extra_artifact = tmp_path / Path(relative_path)
            extra_artifact.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows[:1]), extra_artifact)

    if diagnostics_rows is not None:
        diagnostics_path = (
            tmp_path
            / "runtime"
            / "data_canonical"
            / "derived"
            / "universes"
            / "tw_equity_liquid_top500"
            / "diagnostics"
            / "diagnostics.parquet"
        )
        diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(diagnostics_rows), diagnostics_path)

    manifests = tmp_path / "runtime" / "manifests"
    manifests.mkdir(parents=True)
    manifest = _base_manifest(rows)
    if extra_manifest_paths:
        manifest["artifact_paths"] = [*manifest["artifact_paths"], *extra_manifest_paths]
    (manifests / "universe_tw_equity_liquid_top500.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _set_universe_limit(tmp_path: Path, universe_id: str, limit: int) -> None:
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for spec in payload.get("universes", []):
        if isinstance(spec, dict) and spec.get("universe_id") == universe_id:
            spec["limit"] = limit
            break
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _historical_row(
    *,
    as_of_date: str = "2025-01-02",
    effective_date: str = "2025-01-03",
    ticker: str = "2330",
    rank: int = 1,
    included: bool = True,
    reason: str = "selected",
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "effective_date": effective_date,
        "universe_id": "tw_equity_liquid_top500",
        "ticker": ticker,
        "rank": rank,
        "included": included,
        "reason": reason,
    }


def test_verify_blocks_historical_universe_same_day_effective_date(tmp_path):
    _copy_configs(tmp_path)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(effective_date="2025-01-02")],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "effective_date <= as_of_date" in result["message"]


def test_verify_blocks_historical_universe_missing_required_field(tmp_path):
    _copy_configs(tmp_path)
    rows = [_historical_row()]
    rows[0]["ticker"] = ""
    _write_universe_fixture(tmp_path, rows)

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "missing required fields" in result["message"]
    assert "ticker" in result["message"]


def test_verify_blocks_historical_universe_duplicate_effective_membership_key(tmp_path):
    _copy_configs(tmp_path)
    _write_universe_fixture(
        tmp_path,
        [
            _historical_row(as_of_date="2025-01-02", effective_date="2025-01-06", ticker="2330", rank=1),
            _historical_row(as_of_date="2025-01-03", effective_date="2025-01-06", ticker="2330", rank=2),
        ],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "duplicate historical universe effective membership key" in result["message"]


def test_verify_blocks_historical_universe_small_file_daily_partition(tmp_path):
    _copy_configs(tmp_path)
    _write_universe_fixture(
        tmp_path,
        [_historical_row()],
        extra_manifest_paths=[
            "runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_date/as_of_date=2025-01-02/membership.parquet"
        ],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "small_file_daily_partition_count > 0" in result["message"]


def test_verify_blocks_historical_universe_top_n_overflow_per_effective_date(tmp_path):
    _copy_configs(tmp_path)
    _set_universe_limit(tmp_path, "tw_equity_liquid_top500", 2)
    _write_universe_fixture(
        tmp_path,
        [
            _historical_row(ticker="1101", rank=1),
            _historical_row(ticker="1216", rank=2),
            _historical_row(ticker="1301", rank=3),
        ],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "row_count per effective_date exceeds top-n limit" in result["message"]


def test_verify_blocks_historical_universe_top_n_underfilled_when_diagnostics_show_enough_candidates(tmp_path):
    _copy_configs(tmp_path)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 600,
                "included_count": 1,
                "excluded_count": 599,
                "top_n_limit": 500,
                "max_included_count": 1,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "included_count must equal top_n_limit when eligible_count >= limit" in result["message"]

```


## FILE: tests\test_raw_family_verify.py
```
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.inspect import inspect_artifacts
from data_analysts.paths import DataAnalystsRoot
from data_analysts.verify import verify_runtime


def _copy_configs(src_root: Path, dst_root: Path) -> None:
    (dst_root / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        (dst_root / "configs" / name).write_text(
            (src_root / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _write_raw_family_fixture(
    tmp_path: Path,
    *,
    pit_parse_failure_count: int = 0,
    unresolved_duplicate_count: int = 0,
    forbidden_source_usage_count: int = 0,
) -> None:
    manifests = tmp_path / "runtime" / "manifests"
    manifests.mkdir(parents=True)
    artifact = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "raw"
        / "trading_calendar"
        / "trading_calendar.parquet"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"not-read-by-this-check")
    (manifests / "trading_calendar.json").write_text(
        json.dumps(
            {
                "artifact_id": "trading_calendar",
                "artifact_paths": [
                    "runtime/data_canonical/raw/trading_calendar/trading_calendar.parquet"
                ],
                "columns": ["date"],
                "source_collections": ["TEJ.TRADEDAY_TWSE"],
            }
        ),
        encoding="utf-8",
    )
    diagnostic_dir = (
        tmp_path / "runs" / "real_all_products" / "diagnostics" / "raw_families"
    )
    diagnostic_dir.mkdir(parents=True)
    (diagnostic_dir / "trading_calendar.json").write_text(
        json.dumps(
            {
                "source_row_count": 1,
                "published_row_count": 1,
                "pit_parse_failure_count": pit_parse_failure_count,
                "unresolved_duplicate_count": unresolved_duplicate_count,
                "forbidden_source_usage_count": forbidden_source_usage_count,
            }
        ),
        encoding="utf-8",
    )


def _write_selected_pit_fixture(
    tmp_path: Path,
    *,
    artifact_id: str = "financial_statement_pit_selected",
    rows: list[dict[str, object]],
) -> None:
    manifests = tmp_path / "runtime" / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    artifact = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "pit"
        / artifact_id
        / "decision_year=2025"
        / "part.parquet"
    )
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact)
    (manifests / f"{artifact_id}.json").write_text(
        json.dumps(
            {
                "artifact_id": artifact_id,
                "artifact_paths": [
                    f"runtime/data_canonical/derived/pit/{artifact_id}/decision_year=2025/part.parquet"
                ],
                "columns": list(rows[0].keys()) if rows else [],
                "source_collections": ["TEJ.AINVFINB"],
            }
        ),
        encoding="utf-8",
    )


def test_verify_blocks_on_raw_family_pit_parse_failure(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _write_raw_family_fixture(tmp_path, pit_parse_failure_count=1)

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "raw_family_diagnostics"


def test_verify_blocks_on_raw_family_unresolved_duplicate(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _write_raw_family_fixture(tmp_path, unresolved_duplicate_count=1)

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "raw_family_diagnostics"


def test_verify_blocks_on_raw_family_forbidden_source_usage(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _write_raw_family_fixture(tmp_path, forbidden_source_usage_count=1)

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "raw_family_diagnostics"


def test_verify_blocks_selected_pit_when_source_available_after_decision_date(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _write_selected_pit_fixture(
        tmp_path,
        rows=[
            {
                "ticker": "2330",
                "decision_date": "2025-08-31",
                "source_available_date": "2025-09-01",
                "revision_date": "2025-09-02",
            }
        ],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "selected_pit_artifacts"
    assert "source_available_date > decision_date" in result["message"]


def test_verify_blocks_selected_pit_when_required_columns_are_missing(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _write_selected_pit_fixture(
        tmp_path,
        artifact_id="self_reported_numbers_pit_selected",
        rows=[
            {
                "ticker": "2330",
                "source_available_date": "2025-07-20",
                "revision_date": "2025-07-21",
            }
        ],
    )

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "selected_pit_artifacts"
    assert "missing required columns" in result["message"]


def test_inspect_reports_raw_family_diagnostics_without_reading_parquet(tmp_path):
    _write_raw_family_fixture(tmp_path, pit_parse_failure_count=2)

    result = inspect_artifacts(DataAnalystsRoot.from_path(tmp_path))

    assert result["raw_family_diagnostics"] == {
        "status": "blocked",
        "family_count": 1,
        "raw_family_diagnostic_count": 1,
        "pit_parse_failure_count_total": 2,
        "unresolved_duplicate_count_total": 0,
        "forbidden_source_usage_count_total": 0,
    }

```


## FILE: tests\test_pit_foundation_verify.py
```
import json
import shutil
from pathlib import Path

import pytest

from data_analysts.diagnostics import write_diagnostic
from data_analysts.paths import DataAnalystsRoot
from data_analysts.verify import verify_runtime


def copy_configs(src_root, dst_root):
    (dst_root / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        shutil.copy2(src_root / "configs" / name, dst_root / "configs" / name)


def test_write_diagnostic_stays_under_runtime_diagnostics(tmp_path):
    root = DataAnalystsRoot.from_path(tmp_path)
    path = write_diagnostic(
        root,
        "pit_foundation/source_catalog",
        {"status": "ready", "forbidden_source_usage_count": 0},
    )

    assert (
        path
        == tmp_path
        / "runs"
        / "real_all_products"
        / "diagnostics"
        / "pit_foundation"
        / "source_catalog.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["forbidden_source_usage_count"] == 0


def test_write_diagnostic_uses_diagnostics_under_run_root(tmp_path):
    run_root = tmp_path / "runs" / "real_all_products"
    root = DataAnalystsRoot.from_path(run_root)

    path = write_diagnostic(
        root,
        "pit_foundation/source_catalog",
        {"status": "ready", "forbidden_source_usage_count": 0},
    )

    assert (
        path
        == tmp_path
        / "runs"
        / "real_all_products"
        / "diagnostics"
        / "pit_foundation"
        / "source_catalog.json"
    )
    assert not (
        run_root
        / "runs"
        / "real_all_products"
        / "diagnostics"
        / "pit_foundation"
        / "source_catalog.json"
    ).exists()


@pytest.mark.parametrize(
    "name",
    [
        r"C:\outside\x",
        "C:/outside/x",
        "/outside/x",
    ],
)
def test_write_diagnostic_rejects_absolute_like_names(tmp_path, name):
    root = DataAnalystsRoot.from_path(tmp_path)

    with pytest.raises(ValueError):
        write_diagnostic(root, name, {"status": "blocked"})


def test_verify_reports_pit_foundation_metrics(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)

    result = verify_runtime(root)

    assert "pit_foundation" in result
    metrics = result["pit_foundation"]
    assert metrics["forbidden_source_count"] == 2
    assert metrics["forbidden_source_usage_count"] == 0
    assert metrics["missing_pit_field_count"] == 0
    assert metrics["missing_logical_key_count"] == 0


def test_verify_blocks_forbidden_source_reference_in_runtime_manifest(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)
    artifact = tmp_path / "runtime" / "artifacts" / "dummy.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    manifests_dir = tmp_path / "runtime" / "manifests"
    manifests_dir.mkdir(parents=True)
    (manifests_dir / "dummy.json").write_text(
        json.dumps(
            {
                "artifact_id": "dummy",
                "artifact_paths": ["runtime/artifacts/dummy.json"],
                "source": {
                    "database": "TEJ",
                    "collection": "AINVFQ1",
                },
            }
        ),
        encoding="utf-8",
    )
    root = DataAnalystsRoot.from_path(tmp_path)

    result = verify_runtime(root)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "pit_foundation"
    assert result["pit_foundation"]["forbidden_source_usage_count"] > 0

```

