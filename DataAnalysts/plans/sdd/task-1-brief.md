## Task 1: Catalog and Registry Config Files

**Files:**
- Create: `configs/source_catalog.json`
- Create: `configs/pit_registry.json`
- Test: `tests/test_pit_foundation_config.py`

**Boundary:**
- This task creates static config only.
- It must not modify extraction, pipeline, or verification behavior yet.

**Produces:**
- A source catalog with approved and forbidden sources.
- A PIT registry with normalized PIT rules.

- [ ] **Step 1: Create failing tests for config file presence and core contents**

Create `tests/test_pit_foundation_config.py`:

```python
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_source_catalog_declares_forbidden_sources():
    catalog = load_json("configs/source_catalog.json")
    forbidden = {(item["database"], item["collection"]) for item in catalog["forbidden_sources"]}
    assert ("TEJ", "AINVFQ1") in forbidden
    assert ("TEJ", "APISHRACTW") in forbidden
    assert len(forbidden) == 2


def test_pit_registry_declares_ainvfinb_revision_rule():
    registry = load_json("configs/pit_registry.json")
    rule = registry["families"]["financial_statement_raw"]
    assert rule["database"] == "TEJ"
    assert rule["collection"] == "AINVFINB"
    assert rule["availability_field"] == "key3"
    assert rule["revision_field"] == "mdate"
    assert rule["date_normalization"] == "date_only"
    assert rule["preserve_revisions"] is True
```

- [ ] **Step 2: Run tests and verify they fail because files do not exist**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py -q
```

Expected: fails with `FileNotFoundError` for `source_catalog.json` or `pit_registry.json`.

- [ ] **Step 3: Add `configs/source_catalog.json`**

Create a JSON object with:

```json
{
  "schema_version": "1.0",
  "forbidden_sources": [
    {
      "database": "TEJ",
      "collection": "AINVFQ1",
      "reason": "deprecated financial source; use TEJ.AINVFINB only"
    },
    {
      "database": "TEJ",
      "collection": "APISHRACTW",
      "reason": "deprecated source; do not use"
    }
  ],
  "sources": [
    {
      "family_id": "trading_calendar",
      "database": "TEJ",
      "collection": "TRADEDAY_TWSE",
      "pit_field": "zdate",
      "date_normalization": "date_only",
      "logical_key": ["date", "market"],
      "revision_key": [],
      "include_phase": 1
    },
    {
      "family_id": "daily_tradability",
      "database": "APISTKATTR",
      "collection_pattern": "{ticker}",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["date", "ticker"],
      "revision_key": [],
      "include_phase": 1
    },
    {
      "family_id": "daily_chip",
      "database": "APISHRACT",
      "collection_pattern": "{ticker}",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["date", "ticker"],
      "revision_key": [],
      "include_phase": 1
    },
    {
      "family_id": "monthly_sales",
      "database": "TEJ",
      "collection": "APISALE",
      "pit_field": "annd_s",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_period_date"],
      "revision_key": ["source_available_date", "mdate"],
      "include_phase": 2
    },
    {
      "family_id": "financial_statement_raw",
      "database": "TEJ",
      "collection": "AINVFINB",
      "pit_field": "key3",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
      "revision_key": [],
      "include_phase": 2
    },
    {
      "family_id": "financial_statement_pit_selected",
      "database": "derived",
      "collection": "financial_statement_raw",
      "pit_field": "source_available_date",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date", "decision_date"],
      "revision_key": ["source_available_date", "revision_date"],
      "include_phase": 2
    },
    {
      "family_id": "self_reported_numbers_raw",
      "database": "TEJ",
      "collection": "AFESTM1",
      "pit_field": "annd",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "key3", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
      "revision_key": [],
      "include_phase": 3
    },
    {
      "family_id": "self_reported_numbers_pit_selected",
      "database": "derived",
      "collection": "self_reported_numbers_raw",
      "pit_field": "source_available_date",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "key3", "sem", "curr", "merg", "period_end_date", "decision_date"],
      "revision_key": ["source_available_date", "revision_date"],
      "include_phase": 3
    },
    {
      "family_id": "director_supervisor_holdings",
      "database": "TEJ",
      "collection": "APIBSTN1",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "board_reelection_statistics",
      "database": "TEJ",
      "collection": "APICHGSTAT",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "executive_change_events",
      "database": "TEJ",
      "collection": "APIDIRCHG",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "merger_acquisition_events",
      "database": "TEJ",
      "collection": "APIMA",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "private_placement_relation_events",
      "database": "TEJ",
      "collection": "APISTKPRV",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "insider_transfer_completed",
      "database": "TEJ",
      "collection": "APITRANS1",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "insider_transfer_declared_not_completed",
      "database": "TEJ",
      "collection": "APITRANS2",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "treasury_stock_events",
      "database": "TEJ",
      "collection": "APITRS",
      "pit_field": "mdate",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "source_date"],
      "revision_key": ["mdate"],
      "include_phase": 3
    },
    {
      "family_id": "taiwan_index_futures_near_month",
      "database": "Futures_TAIFEX_TX",
      "collection": "TX_1",
      "pit_field": "日期",
      "date_normalization": "date_only",
      "logical_key": ["date", "contract"],
      "revision_key": [],
      "include_phase": 4
    }
  ]
}
```

- [ ] **Step 4: Add `configs/pit_registry.json`**

Create `families` keyed by the `family_id` values above. Each rule must include:

```json
{
  "schema_version": "1.0",
  "families": {
    "financial_statement_raw": {
      "database": "TEJ",
      "collection": "AINVFINB",
      "availability_field": "key3",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
      "revision_field": "mdate",
      "preserve_revisions": true,
      "selected_view": false
    },
    "financial_statement_pit_selected": {
      "database": "derived",
      "collection": "financial_statement_raw",
      "availability_field": "source_available_date",
      "date_normalization": "date_only",
      "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date"],
      "revision_field": "revision_date",
      "preserve_revisions": false,
      "selected_view": true
    }
  }
}
```

Then add the rest of the approved families with the same fields. For non-revision families, use `"revision_field": null` and `"selected_view": false`.

- [ ] **Step 5: Run the config tests**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py -q
```

Expected: `2 passed`.

