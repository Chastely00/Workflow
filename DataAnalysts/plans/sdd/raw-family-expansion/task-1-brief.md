## Task 1: Raw Family Config Profiles

**Files:**
- Modify: `configs/mongodb_sources.json`
- Modify: `configs/source_family_profiles.json`
- Test: `tests/test_raw_family_config.py`

**Boundary:**
- This task only expands config.
- It must not modify extraction, normalization, pipeline, or runtime data.

**Interfaces:**
- Consumes: PIT Foundation `configs/source_catalog.json` and `configs/pit_registry.json`.
- Produces: enabled source-family profiles for approved raw families and Mongo connections for new databases.

- [ ] **Step 1: Write failing config tests**

Create `tests/test_raw_family_config.py`:

```python
import json
from pathlib import Path

from data_analysts.config import load_runtime_config
from data_analysts.paths import DataAnalystsRoot


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))


def test_raw_family_connections_are_declared():
    payload = _load("mongodb_sources.json")
    connections = payload["connections"]
    assert connections["apistkattr"]["database"] == "APISTKATTR"
    assert connections["apishract"]["database"] == "APISHRACT"
    assert connections["futures_taifex_tx"]["database"] == "Futures_TAIFEX_TX"
    assert connections["apistkattr"]["default_uri"] == "mongodb://localhost:27017/"
    assert connections["apishract"]["default_uri"] == "mongodb://localhost:27017/"
    assert connections["futures_taifex_tx"]["default_uri"] == "mongodb://localhost:27017/"


def test_raw_family_profiles_cover_registry_families():
    registry = _load("pit_registry.json")["families"]
    profiles = _load("source_family_profiles.json")["families"]
    profile_ids = {item["family_id"] for item in profiles}
    required = {family_id for family_id, rule in registry.items() if rule["database"] != "derived"}
    assert required <= profile_ids
    assert "financial_statement_pit_selected" not in profile_ids
    assert "self_reported_numbers_pit_selected" not in profile_ids


def test_raw_family_profiles_do_not_use_forbidden_sources():
    profiles = _load("source_family_profiles.json")["families"]
    forbidden = {("tej", "AINVFQ1"), ("tej", "APISHRACTW")}
    used = {
        (str(item.get("connection")), str(item.get("collection")))
        for item in profiles
        if item.get("collection")
    }
    assert forbidden.isdisjoint(used)


def test_runtime_config_loads_with_raw_family_profiles():
    config = load_runtime_config(DataAnalystsRoot.from_path(ROOT))
    assert "trading_calendar" in config.family_ids
    assert "daily_tradability" in config.family_ids
    assert "financial_statement_raw" in config.family_ids
    assert "taiwan_index_futures_near_month" in config.family_ids
```

- [ ] **Step 2: Run the config tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_raw_family_config.py -q
```

Expected: fails because new connections and family profiles do not exist.

- [ ] **Step 3: Add Mongo connections**

Modify `configs/mongodb_sources.json` so `connections` contains:

```json
{
  "apistkattr": {
    "uri_env": "DATA_ANALYSTS_MONGODB_URI",
    "default_uri": "mongodb://localhost:27017/",
    "database": "APISTKATTR"
  },
  "apishract": {
    "uri_env": "DATA_ANALYSTS_MONGODB_URI",
    "default_uri": "mongodb://localhost:27017/",
    "database": "APISHRACT"
  },
  "futures_taifex_tx": {
    "uri_env": "DATA_ANALYSTS_MONGODB_URI",
    "default_uri": "mongodb://localhost:27017/",
    "database": "Futures_TAIFEX_TX"
  }
}
```

Keep existing `apiprcd` and `tej` entries unchanged.

- [ ] **Step 4: Add source family profiles**

Append these enabled family profiles to `configs/source_family_profiles.json`:

```json
[
  {
    "family_id": "trading_calendar",
    "enabled": true,
    "connection": "tej",
    "collection": "TRADEDAY_TWSE",
    "source_profile": "small_snapshot",
    "primary_key": ["date", "market"],
    "date_fields": {"source_date": "zdate"},
    "availability": {"type": "source_available_date", "field": "zdate"},
    "partitioning": ["single_file"],
    "pit_policy": "source_available_date"
  },
  {
    "family_id": "daily_tradability",
    "enabled": true,
    "connection": "apistkattr",
    "collection_pattern": "{ticker}",
    "source_profile": "large_daily_panel",
    "primary_key": ["date", "ticker"],
    "date_fields": {"source_date": "mdate"},
    "availability": {"type": "source_available_date", "field": "mdate"},
    "partitioning": ["year"],
    "pit_policy": "source_available_date"
  },
  {
    "family_id": "daily_chip",
    "enabled": true,
    "connection": "apishract",
    "collection_pattern": "{ticker}",
    "source_profile": "large_daily_panel",
    "primary_key": ["date", "ticker"],
    "date_fields": {"source_date": "mdate"},
    "availability": {"type": "source_available_date", "field": "mdate"},
    "partitioning": ["year"],
    "pit_policy": "source_available_date"
  },
  {
    "family_id": "monthly_sales",
    "enabled": true,
    "connection": "tej",
    "collection": "APISALE",
    "source_profile": "medium_pit_table",
    "primary_key": ["ticker", "source_period_date", "source_available_date"],
    "date_fields": {"source_date": "annd_s"},
    "availability": {"type": "source_available_date", "field": "annd_s"},
    "partitioning": ["available_year"],
    "pit_policy": "source_available_date"
  },
  {
    "family_id": "financial_statement_raw",
    "enabled": true,
    "connection": "tej",
    "collection": "AINVFINB",
    "source_profile": "medium_pit_table",
    "primary_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
    "date_fields": {"source_date": "key3"},
    "availability": {"type": "source_available_date", "field": "key3"},
    "partitioning": ["available_year"],
    "pit_policy": "source_available_date"
  },
  {
    "family_id": "self_reported_numbers_raw",
    "enabled": true,
    "connection": "tej",
    "collection": "AFESTM1",
    "source_profile": "medium_pit_table",
    "primary_key": ["ticker", "key3", "period_end_date", "source_available_date", "revision_date"],
    "date_fields": {"source_date": "annd"},
    "availability": {"type": "source_available_date", "field": "annd"},
    "partitioning": ["available_year"],
    "pit_policy": "source_available_date"
  },
  {
    "family_id": "taiwan_index_futures_near_month",
    "enabled": true,
    "connection": "futures_taifex_tx",
    "collection": "TX_1",
    "source_profile": "large_daily_panel",
    "primary_key": ["date", "contract"],
    "date_fields": {"source_date": "日期"},
    "availability": {"type": "source_available_date", "field": "日期"},
    "partitioning": ["year"],
    "pit_policy": "source_available_date"
  }
]
```

Append the eight governance/event families as `medium_pit_table` profiles using `connection = "tej"`, PIT field `mdate`, partitioning `["available_year"]`, and the exact collection names:

```json
[
  ["director_supervisor_holdings", "APIBSTN1"],
  ["board_reelection_statistics", "APICHGSTAT"],
  ["executive_change_events", "APIDIRCHG"],
  ["merger_acquisition_events", "APIMA"],
  ["private_placement_relation_events", "APISTKPRV"],
  ["insider_transfer_completed", "APITRANS1"],
  ["insider_transfer_declared_not_completed", "APITRANS2"],
  ["treasury_stock_events", "APITRS"]
]
```

Each governance/event profile must use:

```json
{
  "source_profile": "medium_pit_table",
  "primary_key": ["ticker", "source_date", "source_available_date"],
  "date_fields": {"source_date": "mdate"},
  "availability": {"type": "source_available_date", "field": "mdate"},
  "partitioning": ["available_year"],
  "pit_policy": "source_available_date"
}
```

- [ ] **Step 5: Run config tests**

Run:

```powershell
python -m pytest tests/test_raw_family_config.py tests/test_pit_foundation_config.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- `raw_registry_family_count == 15`.
- `derived_selected_family_count == 2`.
- `forbidden_source_usage_count == 0`.
- `small_snapshot_family_count >= 2`.
- `large_daily_panel_family_count >= 4`.

---

