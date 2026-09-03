## Task 5: Verify Integration and Quantitative PIT Foundation Metrics

**Files:**
- Modify: `src/data_analysts/verify.py`
- Modify: `src/data_analysts/config.py`
- Test: `tests/test_pit_foundation_verify.py`

**Boundary:**
- This task integrates PIT Foundation into `verify`.
- It must not require new raw family artifacts yet.
- It must not mark runtime blocked merely because future raw families are not published.

**Produces:**
- `runtime/jobs/verification_result.json` includes `pit_foundation`.
- `runs/real_all_products/diagnostics/pit_foundation/source_catalog.json`.

- [ ] **Step 1: Add failing verify test**

Append to `tests/test_pit_foundation_verify.py`:

```python
import shutil

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
```

- [ ] **Step 2: Run test and verify expected failure**

Run:

```powershell
python -m pytest tests/test_pit_foundation_verify.py -q
```

Expected: fails because `verify_runtime` has no `pit_foundation` metrics.

- [ ] **Step 3: Add PIT foundation metric builder in `verify.py`**

Add a private helper:

```python
def _pit_foundation_metrics(root: DataAnalystsRoot, config: RuntimeConfig | None = None) -> dict[str, object]:
    catalog = config.source_catalog if config is not None else load_source_catalog(root)
    registry = config.pit_registry if config is not None else load_pit_registry(root)
    sources = [item for item in catalog.get("sources", []) if isinstance(item, dict)]
    forbidden = [item for item in catalog.get("forbidden_sources", []) if isinstance(item, dict)]
    missing_pit = [item.get("family_id") for item in sources if not item.get("pit_field")]
    missing_key = [item.get("family_id") for item in sources if not item.get("logical_key")]
    metrics = {
        "forbidden_source_count": len(forbidden),
        "approved_source_count": len(sources),
        "pit_registry_family_count": len(registry.get("families", {})),
        "forbidden_source_usage_count": 0,
        "missing_pit_field_count": len(missing_pit),
        "missing_logical_key_count": len(missing_key),
        "missing_pit_field_families": missing_pit,
        "missing_logical_key_families": missing_key,
    }
    return metrics
```

In the current `verify_runtime`, change the config load line to retain the config object:

```python
config = load_runtime_config(root)
```

Then call `_pit_foundation_metrics(root, config)` immediately after config loading and before manifest checks. Keep all existing manifest checks unchanged.

- [ ] **Step 4: Write PIT foundation diagnostic**

Inside `verify_runtime`, call:

```python
write_diagnostic(root, "pit_foundation/source_catalog", metrics)
```

Mark verification blocked if:

```python
metrics["forbidden_source_usage_count"] != 0
metrics["missing_pit_field_count"] != 0
metrics["missing_logical_key_count"] != 0
```

- [ ] **Step 5: Run PIT foundation verify tests**

Run:

```powershell
python -m pytest tests/test_pit_foundation_verify.py -q
```

Expected: all tests pass.

