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

