## Task 7: Verify and Inspect Raw Family Outputs

**Files:**
- Modify: `src/data_analysts/verify.py`
- Modify: `src/data_analysts/inspect.py`
- Test: `tests/test_raw_family_verify.py`

**Boundary:**
- This task verifies and inspects already-published raw family artifacts.
- It must not query MongoDB.
- It must not produce canonical parquet.

**Interfaces:**
- Consumes manifests and diagnostics.
- Produces verification checks:

```python
{
    "check": "raw_family_diagnostics",
    "status": "ready",
    "family_count": 17,
    "pit_parse_failure_count_total": 0,
    "unresolved_duplicate_count_total": 0
}
```

- [ ] **Step 1: Write failing verify tests**

Create `tests/test_raw_family_verify.py`:

```python
import json
from pathlib import Path

from data_analysts.paths import DataAnalystsRoot
from data_analysts.verify import verify_runtime


def _copy_configs(src_root: Path, dst_root: Path) -> None:
    (dst_root / "configs").mkdir()
    for name in ["mongodb_sources.json", "source_family_profiles.json", "universe_specs.json", "source_catalog.json", "pit_registry.json"]:
        (dst_root / "configs" / name).write_text((src_root / "configs" / name).read_text(encoding="utf-8"), encoding="utf-8")


def test_verify_blocks_on_raw_family_pit_parse_failure(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    manifests = tmp_path / "runtime" / "manifests"
    manifests.mkdir(parents=True)
    artifact = tmp_path / "runtime" / "data_canonical" / "raw" / "trading_calendar" / "trading_calendar.parquet"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"not-read-by-this-check")
    (manifests / "trading_calendar.json").write_text(json.dumps({
        "artifact_id": "trading_calendar",
        "artifact_paths": ["runtime/data_canonical/raw/trading_calendar/trading_calendar.parquet"],
        "columns": ["date"],
        "source_collections": ["TEJ.TRADEDAY_TWSE"]
    }), encoding="utf-8")
    diagnostic_dir = tmp_path / "runs" / "real_all_products" / "diagnostics" / "raw_families"
    diagnostic_dir.mkdir(parents=True)
    (diagnostic_dir / "trading_calendar.json").write_text(json.dumps({
        "source_row_count": 1,
        "published_row_count": 1,
        "pit_parse_failure_count": 1,
        "unresolved_duplicate_count": 0,
        "forbidden_source_usage_count": 0
    }), encoding="utf-8")

    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "raw_family_diagnostics"
```

- [ ] **Step 2: Run verify tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_raw_family_verify.py -q
```

Expected: fails because verify does not aggregate raw family diagnostics.

- [ ] **Step 3: Add raw diagnostics verification**

In `verify.py`, add before manifest path checks:

```python
raw_error, raw_metrics = _check_raw_family_diagnostics(root)
checks.append({"check": "raw_family_diagnostics", "status": "ready" if raw_error is None else "blocked", **raw_metrics})
if raw_error:
    result = _blocked("raw_family_diagnostics", raw_error, ["fix raw family diagnostics and rebuild affected families"], as_of_date, checks)
    result["pit_foundation"] = pit_foundation
    _write_verification_result(root, result)
    return result
```

Implement:

```python
def _check_raw_family_diagnostics(root: DataAnalystsRoot) -> tuple[str | None, dict[str, Any]]:
    diagnostics_dir = root.diagnostics_path("raw_families")
    if not diagnostics_dir.exists():
        return None, {"raw_family_diagnostic_count": 0}
    totals = {
        "raw_family_diagnostic_count": 0,
        "pit_parse_failure_count_total": 0,
        "unresolved_duplicate_count_total": 0,
        "forbidden_source_usage_count_total": 0,
    }
    for path in sorted(diagnostics_dir.glob("*.json")):
        payload = _load_json_object(path)
        totals["raw_family_diagnostic_count"] += 1
        totals["pit_parse_failure_count_total"] += int(payload.get("pit_parse_failure_count") or 0)
        totals["unresolved_duplicate_count_total"] += int(payload.get("unresolved_duplicate_count") or 0)
        totals["forbidden_source_usage_count_total"] += int(payload.get("forbidden_source_usage_count") or 0)
    if totals["pit_parse_failure_count_total"] != 0:
        return "raw family PIT parse failures are nonzero", totals
    if totals["unresolved_duplicate_count_total"] != 0:
        return "raw family unresolved duplicate count is nonzero", totals
    if totals["forbidden_source_usage_count_total"] != 0:
        return "raw family forbidden source usage is nonzero", totals
    return None, totals
```

`_load_json_object(path)` must raise `ValueError` if JSON is missing or not an object.

- [ ] **Step 4: Run verify tests**

Run:

```powershell
python -m pytest tests/test_raw_family_verify.py tests/test_pit_foundation_verify.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- verify blocks when any raw family has `pit_parse_failure_count > 0`.
- verify blocks when any raw family has `unresolved_duplicate_count > 0`.
- verify blocks when any raw family has `forbidden_source_usage_count > 0`.

---

