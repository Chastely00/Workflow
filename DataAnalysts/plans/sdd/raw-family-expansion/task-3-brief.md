## Task 3: Raw Publishing Orchestrator

**Files:**
- Modify: `src/data_analysts/pipeline.py`
- Modify: `src/data_analysts/extract.py`
- Test: `tests/test_raw_family_pipeline.py`

**Boundary:**
- This task publishes raw family artifacts from normalized rows.
- It must not implement historical security panel or universe behavior.
- It must not change existing price/event adjusted-price outputs except by sharing `ArtifactPublisher`.

**Interfaces:**
- Consumes `normalize_raw_family()`.
- Produces:

```python
def publish_raw_family_outputs(
    root: DataAnalystsRoot,
    publisher: ArtifactPublisher,
    family_id: str,
    normalized: dict[str, object],
) -> list[str]: ...
```

- [ ] **Step 1: Write failing pipeline tests**

Create `tests/test_raw_family_pipeline.py`:

```python
import json
from pathlib import Path

import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.paths import DataAnalystsRoot
from data_analysts.pipeline import run_pipeline


def _write_configs(root: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = root / "configs"
    target.mkdir(parents=True)
    for name in ["mongodb_sources.json", "source_family_profiles.json", "universe_specs.json", "source_catalog.json", "pit_registry.json"]:
        payload = json.loads((source / name).read_text(encoding="utf-8"))
        if name == "source_family_profiles.json":
            payload["families"] = [
                {
                    "family_id": "trading_calendar",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "TRADEDAY_TWSE",
                    "source_profile": "small_snapshot",
                    "primary_key": ["date", "market"],
                    "date_fields": {"source_date": "zdate"},
                    "availability": {"type": "source_available_date", "field": "zdate"},
                    "partitioning": ["single_file"],
                    "pit_policy": "source_available_date",
                    "fixture_rows": [
                        {"zdate": "2025-01-02", "mkt": "TWSE", "date_rmk": "", "source_row_id": "a"},
                        {"zdate": "2025-01-03", "mkt": "TWSE", "date_rmk": "休市", "source_row_id": "b"},
                    ],
                },
                {
                    "family_id": "financial_statement_raw",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "AINVFINB",
                    "source_profile": "medium_pit_table",
                    "primary_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
                    "date_fields": {"source_date": "key3"},
                    "availability": {"type": "source_available_date", "field": "key3"},
                    "partitioning": ["available_year"],
                    "pit_policy": "source_available_date",
                    "fixture_rows": [
                        {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-15", "eps": 10, "source_row_id": "a"},
                        {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-20", "eps": 11, "source_row_id": "b"},
                    ],
                },
            ]
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_pipeline_publishes_raw_family_artifacts_and_diagnostics(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)

    result = run_pipeline(root, config, families={"trading_calendar", "financial_statement_raw"}, as_of_date="2025-08-31")

    assert result["status"] == "ready"
    calendar_path = tmp_path / "runtime" / "data_canonical" / "raw" / "trading_calendar" / "trading_calendar.parquet"
    assert calendar_path.exists()
    calendar_rows = pq.read_table(calendar_path).to_pylist()
    assert calendar_rows[0]["is_trading_day"] is True

    raw_path = tmp_path / "runtime" / "data_canonical" / "raw" / "financial_statement_raw" / "available_year=2025" / "part.parquet"
    assert raw_path.exists()
    assert len(pq.read_table(raw_path).to_pylist()) == 2

    selected_path = tmp_path / "runtime" / "data_canonical" / "derived" / "pit" / "financial_statement_pit_selected" / "decision_year=2025" / "part.parquet"
    assert selected_path.exists()
    selected_rows = pq.read_table(selected_path).to_pylist()
    assert selected_rows[0]["eps"] == 11

    diagnostic = json.loads((tmp_path / "runs" / "real_all_products" / "diagnostics" / "raw_families" / "financial_statement_raw.json").read_text(encoding="utf-8"))
    assert diagnostic["source_row_count"] == 2
    assert diagnostic["unresolved_duplicate_count"] == 0
```

- [ ] **Step 2: Run pipeline tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_raw_family_pipeline.py -q
```

Expected: fails because pipeline does not call `normalize_raw_family()` or publish these raw families.

- [ ] **Step 3: Add raw-family publishing helpers to `pipeline.py`**

Add:

```python
RAW_EXPANSION_FAMILIES = {
    "trading_calendar",
    "daily_tradability",
    "daily_chip",
    "monthly_sales",
    "financial_statement_raw",
    "self_reported_numbers_raw",
    "director_supervisor_holdings",
    "board_reelection_statistics",
    "executive_change_events",
    "merger_acquisition_events",
    "private_placement_relation_events",
    "insider_transfer_completed",
    "insider_transfer_declared_not_completed",
    "treasury_stock_events",
    "taiwan_index_futures_near_month",
}

SELECTED_OUTPUT_BY_RAW_FAMILY = {
    "financial_statement_raw": "financial_statement_pit_selected",
    "self_reported_numbers_raw": "self_reported_numbers_pit_selected",
}
```

At the start of `run_pipeline()` after `family_rows = _rows_by_family(...)`, add:

```python
raw_expansion_ids = RAW_EXPANSION_FAMILIES.intersection(family_rows)
for family_id in sorted(raw_expansion_ids):
    normalized = normalize_raw_family(
        family_id,
        family_rows[family_id],
        config.pit_registry,
        decision_dates=_decision_dates(start_date=start_date, end_date=end_date, as_of_date=as_of_date),
    )
    _publish_raw_family_outputs(root, publisher, family_id, normalized)
```

Implement:

```python
def _decision_dates(*, start_date: str | None, end_date: str | None, as_of_date: str | None) -> list[str] | None:
    if as_of_date:
        return [as_of_date]
    if start_date and end_date and start_date == end_date:
        return [start_date]
    if start_date and end_date:
        return _calendar_dates(start_date, end_date)
    return None


def _calendar_dates(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current = current.fromordinal(current.toordinal() + 1)
    return days
```

Implement `_publish_raw_family_outputs()`:

```python
def _publish_raw_family_outputs(
    root: DataAnalystsRoot,
    publisher: ArtifactPublisher,
    family_id: str,
    normalized: dict[str, object],
) -> None:
    raw_rows = list(normalized["raw_rows"])
    selected_rows = list(normalized.get("selected_rows") or [])
    diagnostics = dict(normalized["diagnostics"])
    write_diagnostic(root, f"raw_families/{family_id}", diagnostics)
    if raw_rows:
        date_field, partition_name, base_path, required = _raw_output_contract(family_id)
        _publish_dataset(
            root,
            publisher,
            family_id,
            "raw",
            raw_rows,
            base_path,
            required,
            date_field=date_field,
            partition_name=partition_name,
            pit_policy="source_available_date",
        )
    selected_family_id = SELECTED_OUTPUT_BY_RAW_FAMILY.get(family_id)
    if selected_family_id and selected_rows:
        _publish_dataset(
            root,
            publisher,
            selected_family_id,
            "derived",
            selected_rows,
            f"runtime/data_canonical/derived/pit/{selected_family_id}",
            ["decision_date", "ticker", "source_available_date", "revision_date", "data_cutoff_at"],
            date_field="decision_date",
            partition_name="decision_year",
            pit_policy="selected_pit_decision_date",
        )
```

Implement `_raw_output_contract(family_id)` with exact contracts:

```python
def _raw_output_contract(family_id: str) -> tuple[str | None, str | None, str, list[str]]:
    if family_id == "trading_calendar":
        return None, None, "runtime/data_canonical/raw/trading_calendar", ["date", "market", "is_trading_day", "source_available_date", "data_cutoff_at"]
    if family_id in {"daily_tradability", "daily_chip", "taiwan_index_futures_near_month"}:
        return "date", "year", f"runtime/data_canonical/raw/{family_id}", ["date", "source_available_date", "data_cutoff_at"]
    if family_id == "financial_statement_raw":
        return "source_available_date", "available_year", "runtime/data_canonical/raw/financial_statement_raw", ["ticker", "no", "period_end_date", "source_available_date", "revision_date", "data_cutoff_at"]
    if family_id == "self_reported_numbers_raw":
        return "source_available_date", "available_year", "runtime/data_canonical/raw/self_reported_numbers_raw", ["ticker", "key3", "period_end_date", "source_available_date", "revision_date", "data_cutoff_at"]
    return "source_available_date", "available_year", f"runtime/data_canonical/raw/{family_id}", ["source_available_date", "data_cutoff_at"]
```

- [ ] **Step 4: Keep existing price/event pipeline behavior**

Do not remove or reorder existing blocks that publish:

```text
security_master
dividend_events
capital_action_events
daily_price_volume
corporate_actions
security_panel
universe membership
```

Raw expansion publishing must be additive. If `families` contains only raw expansion families, the existing security panel block must remain skipped because `daily_price_volume` and `security_master` are absent.

- [ ] **Step 5: Run pipeline tests**

Run:

```powershell
python -m pytest tests/test_raw_family_pipeline.py tests/test_raw_family_normalization.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- `artifact_file_count == 1` for `trading_calendar`.
- selected financial fixture resolves duplicate by latest `mdate`.
- raw financial row count remains greater than selected financial row count when revisions exist.

---

