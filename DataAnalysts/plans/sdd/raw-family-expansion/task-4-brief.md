## Task 4: Extraction Query and Collection Diagnostics

**Files:**
- Modify: `src/data_analysts/extract.py`
- Modify: `src/data_analysts/pipeline.py`
- Test: `tests/test_raw_family_pipeline.py`

**Boundary:**
- This task improves extraction safety and diagnostics.
- It must not change normalized output schema.

**Interfaces:**
- Produces extraction diagnostics attached to normalized diagnostics:

```python
{
    "source_collection_count": 2,
    "source_collections": ["2330", "2317"]
}
```

- [ ] **Step 1: Add extraction diagnostics tests**

Append to `tests/test_raw_family_pipeline.py`:

```python
class FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def find(self, query=None):
        self.queries.append(query or {})
        return list(self.rows)


class FakeDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]

    def list_collection_names(self):
        return list(self.collections)


def test_per_ticker_daily_extraction_reports_source_collection_count(tmp_path):
    _write_configs(tmp_path)
    config_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["families"] = [
        {
            "family_id": "daily_tradability",
            "enabled": True,
            "connection": "apistkattr",
            "collection_pattern": "{ticker}",
            "source_profile": "large_daily_panel",
            "primary_key": ["date", "ticker"],
            "date_fields": {"source_date": "mdate"},
            "availability": {"type": "source_available_date", "field": "mdate"},
            "partitioning": ["year"],
            "pit_policy": "source_available_date",
        }
    ]
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)
    fake_db = FakeDatabase({
        "2330": FakeCollection([{"coid": "2330", "mdate": "2025-01-02", "source_row_id": "a"}]),
        "2317": FakeCollection([{"coid": "2317", "mdate": "2025-01-02", "source_row_id": "b"}]),
    })

    run_pipeline(root, config, families={"daily_tradability"}, start_date="2025-01-01", end_date="2025-01-31", mongo_databases={"apistkattr": fake_db})

    diagnostic = json.loads((tmp_path / "runs" / "real_all_products" / "diagnostics" / "raw_families" / "daily_tradability.json").read_text(encoding="utf-8"))
    assert diagnostic["source_collection_count"] == 2
    assert diagnostic["published_row_count"] == 2
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_raw_family_pipeline.py -q
```

Expected: fails because source collection count is not propagated.

- [ ] **Step 3: Add collection diagnostics without changing public extraction return type**

In `pipeline._rows_by_family()`, after rows are extracted and normalized, compute:

```python
source_collections = sorted({str(row.get("source_collection")) for row in rows if row.get("source_collection")})
```

Attach collection diagnostics before publishing:

```python
normalized["diagnostics"]["source_collection_count"] = len(source_collections)
normalized["diagnostics"]["source_collections"] = source_collections[:200]
normalized["diagnostics"]["source_collection_sample_truncated"] = len(source_collections) > 200
```

Keep the full `source_collections` in manifests through `_publish_dataset()` because it already collects them from rows.

- [ ] **Step 4: Verify small-table extraction uses single collection**

Append a test:

```python
def test_small_snapshot_uses_single_collection_for_trading_calendar(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)
    fake_collection = FakeCollection([{"zdate": "2025-01-02", "mkt": "TWSE", "date_rmk": ""}])
    fake_db = FakeDatabase({"TRADEDAY_TWSE": fake_collection})

    run_pipeline(root, config, families={"trading_calendar"}, mongo_databases={"tej": fake_db})

    assert fake_collection.queries == [{}]
```

This test protects against splitting small DB tables into tiny reads.

- [ ] **Step 5: Run extraction diagnostics tests**

Run:

```powershell
python -m pytest tests/test_raw_family_pipeline.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- `source_collection_count == 1` for `trading_calendar`.
- `source_collection_count > 1` for per-ticker daily panels in fixture tests.
- Small snapshot families use one Mongo `find({})` call.

---

