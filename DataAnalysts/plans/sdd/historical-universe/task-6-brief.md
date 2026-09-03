# Task 6 Brief

### Task 6: Inspect, Diagnostics, and Documentation

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\inspect.py`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\README.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\OUTPUT_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\VERIFICATION_CONTRACT.md`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_pipeline.py`

**Interfaces:**
- Consumes: `runtime/manifests/*`, diagnostics under `runs/real_all_products/diagnostics/historical_universe`.
- Produces: inspect summary fields:
  - `historical_universe_file_count`
  - `historical_universe_count`
  - `historical_universe_date_min`
  - `historical_universe_date_max`
  - `small_file_daily_partition_count`

- [ ] **Step 1: Add inspect assertions**

Extend `tests/test_historical_universe_pipeline.py`:

```python
from data_analysts.inspect import inspect_artifacts


def test_inspect_reports_historical_universe_summary(tmp_path):
    # Reuse the pipeline fixture from the publish test.
    result = inspect_artifacts(DataAnalystsRoot.from_path(tmp_path))
    assert result["historical_universe"]["status"] == "ready"
    assert result["historical_universe"]["small_file_daily_partition_count"] == 0
    assert result["historical_universe"]["historical_universe_count"] >= 1
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q
```

Expected: FAIL because inspect does not summarize historical universes.

- [ ] **Step 3: Implement inspect summary**

Modify `src/data_analysts/inspect.py` to scan manifests whose `artifact_id` starts with `universe_` and whose `partitioning == ["as_of_year"]`. Count artifact paths, date ranges, and any path containing `membership_by_date/as_of_date=`.

- [ ] **Step 4: Update README**

Add a concise section:

```text
Historical Universe:
- `as_of_date`: observation date after close.
- `effective_date`: next trading day from `trading_calendar`; downstream systems may trade membership no earlier than this date.
- Canonical membership is year-partitioned under `membership_by_year/as_of_year=YYYY/part.parquet`.
- Latest `membership_by_date` outputs are convenience artifacts only.
```

- [ ] **Step 5: Run docs/inspect tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src\data_analysts\inspect.py README.md contracts\OUTPUT_CONTRACT.md contracts\VERIFICATION_CONTRACT.md tests\test_historical_universe_pipeline.py
git commit -m "docs: surface historical universe diagnostics"
```

