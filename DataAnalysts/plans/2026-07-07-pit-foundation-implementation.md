# PIT Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the DataAnalysts PIT Foundation: machine-readable source catalog, PIT registry, forbidden-source validation, date normalization, selected PIT row selection, and quantitative verification diagnostics.

**Architecture:** Keep PIT Foundation independent from raw-family expansion. Config loaders validate `source_catalog.json` and `pit_registry.json` before any pipeline extraction runs. `pit.py` owns date normalization and revision-safe selected-row logic; `source_catalog.py` owns catalog/registry validation; `diagnostics.py` owns bounded JSON diagnostic writing under the DataAnalysts root.

**Tech Stack:** Python 3, standard library JSON/date/path handling, existing DataAnalysts `DataAnalystsRoot`, existing CLI verify path, temporary `pytest` tests during implementation, no ALF runtime imports.

## Global Constraints

- All generated and edited DataAnalysts artifacts must stay under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not use ALF main-flow modules as runtime adapters.
- Do not write raw/generated data artifacts to git.
- Fail closed on missing data, unsupported schema, forbidden source usage, ambiguous PIT dates, unresolved duplicate logical rows, invalid universe membership, and universe small-file regressions.
- `TEJ.AINVFQ1` is forbidden and must not be used for financial statements.
- `TEJ.APISHRACTW` is deprecated/forbidden and must not be used.
- `TEJ.AINVFINB` is the only financial statement source.
- `TEJ.AINVFINB.source_available_date = normalize_date(key3)`.
- `TEJ.AINVFINB` PIT selection must enforce `source_available_date <= decision_date`.
- `TEJ.AINVFINB` raw canonical output must preserve all revisions; selected PIT views may choose latest valid revisions.
- For `TEJ.AINVFINB`, if multiple rows share the same logical statement identity and the same normalized `key3`, selected PIT views choose latest normalized `mdate`.
- For `TEJ.TRADEDAY_TWSE`, `date_rmk` blank after trimming means the date is a trading day.

---

## File Structure

- Create `configs/source_catalog.json`: source inventory, PIT field metadata, logical keys, revision keys, inclusion phase, and forbidden source list.
- Create `configs/pit_registry.json`: executable PIT rules keyed by `family_id`.
- Create `contracts/PIT_REGISTRY_CONTRACT.md`: human-readable source/PIT rules.
- Modify `contracts/CONFIG_CONTRACT.md`: require source catalog and PIT registry; define forbidden-source fail-closed behavior.
- Modify `contracts/OUTPUT_CONTRACT.md`: describe PIT Foundation diagnostics and selected-view semantics without promising raw family outputs yet.
- Modify `contracts/VERIFICATION_CONTRACT.md`: add numeric PIT Foundation thresholds.
- Create `src/data_analysts/source_catalog.py`: load and validate catalog/registry.
- Create `src/data_analysts/pit.py`: normalize dates and select latest PIT rows.
- Create `src/data_analysts/diagnostics.py`: write root-bounded diagnostic JSON.
- Modify `src/data_analysts/paths.py`: add `diagnostics_path(*parts)` for root-bounded diagnostics under `runs/real_all_products/diagnostics`.
- Modify `src/data_analysts/config.py`: load catalog/registry into `RuntimeConfig` and fail closed on invalid inputs.
- Modify `src/data_analysts/verify.py`: include PIT Foundation checks and diagnostics.
- Temporary tests during implementation:
  - `tests/test_pit_foundation_config.py`
  - `tests/test_pit_selection.py`
  - `tests/test_pit_foundation_verify.py`

## Interfaces

Implement these exact interfaces:

```python
# src/data_analysts/source_catalog.py
class SourceCatalogError(ValueError): ...

def load_source_catalog(root: DataAnalystsRoot) -> dict[str, object]: ...
def load_pit_registry(root: DataAnalystsRoot) -> dict[str, object]: ...
def validate_source_catalog(payload: dict[str, object]) -> dict[str, object]: ...
def validate_pit_registry(payload: dict[str, object], catalog: dict[str, object]) -> dict[str, object]: ...
def forbidden_source_references(config_payloads: list[dict[str, object]], catalog: dict[str, object]) -> list[dict[str, str]]: ...
```

```python
# src/data_analysts/pit.py
class PitError(ValueError): ...

def normalize_date(value: object) -> str | None: ...
def select_latest_pit_rows(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    decision_date: str,
) -> tuple[list[dict[str, object]], dict[str, int]]: ...
```

```python
# src/data_analysts/diagnostics.py
def write_diagnostic(root: DataAnalystsRoot, name: str, payload: dict[str, object]) -> Path: ...
```

---

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

## Task 2: Catalog and Registry Loaders

**Files:**
- Create: `src/data_analysts/source_catalog.py`
- Modify: `src/data_analysts/config.py`
- Test: `tests/test_pit_foundation_config.py`

**Boundary:**
- This task only loads and validates static config.
- It must not implement date selection or mutate pipeline output.

**Consumes:**
- `configs/source_catalog.json`
- `configs/pit_registry.json`
- Existing `DataAnalystsRoot`.

**Produces:**
- `SourceCatalogError`
- `load_source_catalog(root)`
- `load_pit_registry(root)`
- `validate_source_catalog(payload)`
- `validate_pit_registry(payload, catalog)`
- new `RuntimeConfig.source_catalog`
- new `RuntimeConfig.pit_registry`

- [ ] **Step 1: Add failing loader tests**

Append to `tests/test_pit_foundation_config.py`:

```python
import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsRoot
from data_analysts.source_catalog import load_pit_registry, load_source_catalog


def test_load_runtime_config_includes_source_catalog_and_pit_registry():
    config = load_runtime_config(DataAnalystsRoot.from_path(ROOT))
    assert "sources" in config.source_catalog
    assert "families" in config.pit_registry


def test_source_catalog_rejects_forbidden_source_reference(tmp_path):
    root = tmp_path
    (root / "configs").mkdir()
    (root / "configs" / "mongodb_sources.json").write_text((ROOT / "configs" / "mongodb_sources.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "configs" / "universe_specs.json").write_text((ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "configs" / "source_catalog.json").write_text((ROOT / "configs" / "source_catalog.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "configs" / "pit_registry.json").write_text((ROOT / "configs" / "pit_registry.json").read_text(encoding="utf-8"), encoding="utf-8")
    bad_profiles = {
        "schema_version": "1.0",
        "families": [
            {
                "family_id": "bad_financial",
                "enabled": True,
                "connection": "tej",
                "collection": "AINVFQ1",
                "source_profile": "medium_pit_table",
                "primary_key": ["ticker", "source_date"]
            }
        ]
    }
    import json
    (root / "configs" / "source_family_profiles.json").write_text(json.dumps(bad_profiles), encoding="utf-8")

    with pytest.raises(ConfigError, match="forbidden source"):
        load_runtime_config(DataAnalystsRoot.from_path(root))
```

- [ ] **Step 2: Run tests and verify expected failure**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py -q
```

Expected: fails because `data_analysts.source_catalog` does not exist or `RuntimeConfig` has no catalog fields.

- [ ] **Step 3: Implement `src/data_analysts/source_catalog.py`**

Implement:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_analysts.paths import DataAnalystsRoot


class SourceCatalogError(ValueError):
    pass


def load_source_catalog(root: DataAnalystsRoot) -> dict[str, object]:
    return validate_source_catalog(_load_json(root.config_path("source_catalog.json")))


def load_pit_registry(root: DataAnalystsRoot) -> dict[str, object]:
    catalog = load_source_catalog(root)
    return validate_pit_registry(_load_json(root.config_path("pit_registry.json")), catalog)


def validate_source_catalog(payload: dict[str, object]) -> dict[str, object]:
    if payload.get("schema_version") != "1.0":
        raise SourceCatalogError("unsupported schema_version in source_catalog.json")
    sources = payload.get("sources")
    forbidden = payload.get("forbidden_sources")
    if not isinstance(sources, list) or not sources:
        raise SourceCatalogError("source_catalog.json must define sources")
    if not isinstance(forbidden, list) or not forbidden:
        raise SourceCatalogError("source_catalog.json must define forbidden_sources")
    seen: set[str] = set()
    forbidden_pairs = _forbidden_pairs(payload)
    for source in sources:
        if not isinstance(source, dict):
            raise SourceCatalogError("source catalog entries must be objects")
        family_id = str(source.get("family_id") or "")
        if not family_id:
            raise SourceCatalogError("source catalog entry missing family_id")
        if family_id in seen:
            raise SourceCatalogError(f"duplicate source family_id: {family_id}")
        seen.add(family_id)
        _reject_forbidden_source(source, forbidden_pairs)
        if not source.get("pit_field"):
            raise SourceCatalogError(f"source catalog entry missing pit_field: {family_id}")
        if not source.get("logical_key"):
            raise SourceCatalogError(f"source catalog entry missing logical_key: {family_id}")
    return payload


def validate_pit_registry(payload: dict[str, object], catalog: dict[str, object]) -> dict[str, object]:
    if payload.get("schema_version") != "1.0":
        raise SourceCatalogError("unsupported schema_version in pit_registry.json")
    families = payload.get("families")
    if not isinstance(families, dict) or not families:
        raise SourceCatalogError("pit_registry.json must define families")
    catalog_ids = {str(item.get("family_id")) for item in catalog.get("sources", []) if isinstance(item, dict)}
    forbidden_pairs = _forbidden_pairs(catalog)
    for family_id, rule in families.items():
        if family_id not in catalog_ids:
            raise SourceCatalogError(f"pit registry family not in source catalog: {family_id}")
        if not isinstance(rule, dict):
            raise SourceCatalogError(f"pit registry rule must be object: {family_id}")
        _reject_forbidden_source(rule, forbidden_pairs)
        if not rule.get("availability_field"):
            raise SourceCatalogError(f"pit registry missing availability_field: {family_id}")
        if not rule.get("logical_key"):
            raise SourceCatalogError(f"pit registry missing logical_key: {family_id}")
    return payload


def forbidden_source_references(config_payloads: list[dict[str, object]], catalog: dict[str, object]) -> list[dict[str, str]]:
    forbidden = _forbidden_pairs(catalog)
    hits: list[dict[str, str]] = []
    for payload in config_payloads:
        for family in payload.get("families", []) if isinstance(payload.get("families"), list) else []:
            if not isinstance(family, dict):
                continue
            database = _database_from_connection(str(family.get("connection") or ""))
            collection = str(family.get("collection") or "")
            if collection and (database, collection) in forbidden:
                hits.append({"database": database, "collection": collection, "family_id": str(family.get("family_id") or "")})
    return hits


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SourceCatalogError(f"missing config: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SourceCatalogError(f"config must be a JSON object: {path.name}")
    return payload


def _forbidden_pairs(catalog: dict[str, object]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for item in catalog.get("forbidden_sources", []):
        if isinstance(item, dict):
            pairs.add((str(item.get("database") or ""), str(item.get("collection") or "")))
    return pairs


def _reject_forbidden_source(source: dict[str, object], forbidden_pairs: set[tuple[str, str]]) -> None:
    database = str(source.get("database") or "")
    collection = str(source.get("collection") or "")
    if (database, collection) in forbidden_pairs:
        raise SourceCatalogError(f"forbidden source referenced: {database}.{collection}")


def _database_from_connection(connection: str) -> str:
    return {"tej": "TEJ", "apiprcd": "APIPRCD"}.get(connection, connection)
```

- [ ] **Step 4: Extend `RuntimeConfig` and `load_runtime_config`**

Modify `src/data_analysts/config.py`:

```python
from data_analysts.source_catalog import SourceCatalogError, forbidden_source_references, load_pit_registry, load_source_catalog
```

Add dataclass fields:

```python
source_catalog: dict[str, Any]
pit_registry: dict[str, Any]
```

In `load_runtime_config`, load catalog and registry after existing config JSON files:

```python
source_catalog = load_source_catalog(root)
pit_registry = load_pit_registry(root)
forbidden_hits = forbidden_source_references([source_family_profiles], source_catalog)
if forbidden_hits:
    first = forbidden_hits[0]
    raise ConfigError(f"forbidden source referenced: {first['database']}.{first['collection']}")
```

Return the new fields in `RuntimeConfig`.

- [ ] **Step 5: Convert catalog errors to config errors**

Add `SourceCatalogError` to the existing CLI/config exception flow by catching it where `ConfigError` is caught, or wrap it in `ConfigError` inside `load_runtime_config`.

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py -q
```

Expected: all tests pass.

## Task 3: PIT Date Normalization and Revision Selection

**Files:**
- Create: `src/data_analysts/pit.py`
- Test: `tests/test_pit_selection.py`

**Boundary:**
- This task implements pure functions only.
- It must not read MongoDB, parquet, or runtime manifests.

**Produces:**
- `PitError`
- `normalize_date(value)`
- `select_latest_pit_rows(...)`

- [ ] **Step 1: Create failing tests for date normalization**

Create `tests/test_pit_selection.py`:

```python
from datetime import date, datetime

import pytest

from data_analysts.pit import PitError, normalize_date, select_latest_pit_rows


def test_normalize_date_strips_time_component():
    assert normalize_date("2025-03-31 00:00:00") == "2025-03-31"
    assert normalize_date("2025-03-31T13:45:01") == "2025-03-31"
    assert normalize_date(datetime(2025, 3, 31, 13, 45, 1)) == "2025-03-31"
    assert normalize_date(date(2025, 3, 31)) == "2025-03-31"


def test_normalize_date_rejects_unparseable_text():
    with pytest.raises(PitError, match="unsupported date value"):
        normalize_date("not-a-date")
```

- [ ] **Step 2: Add failing tests for `AINVFINB` selected PIT behavior**

Append:

```python
def test_select_latest_pit_rows_excludes_future_key3_and_uses_latest_mdate_for_same_key3():
    rows = [
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-01",
            "eps": 10,
        },
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-02",
            "eps": 11,
        },
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-09-01",
            "revision_date": "2025-06-03",
            "eps": 12,
        },
    ]

    selected, diagnostics = select_latest_pit_rows(
        rows,
        logical_key=["ticker", "no", "sem", "curr", "merg", "period_end_date"],
        availability_field="source_available_date",
        revision_field="revision_date",
        decision_date="2025-08-31",
    )

    assert len(selected) == 1
    assert selected[0]["eps"] == 11
    assert diagnostics["input_row_count"] == 3
    assert diagnostics["eligible_row_count"] == 2
    assert diagnostics["future_row_count"] == 1
    assert diagnostics["resolved_duplicate_count"] == 1
    assert diagnostics["unresolved_duplicate_count"] == 0


def test_select_latest_pit_rows_fails_when_latest_revision_is_still_ambiguous():
    rows = [
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-02",
            "source_row_id": "a",
        },
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-02",
            "source_row_id": "b",
        },
    ]

    with pytest.raises(PitError, match="unresolved duplicate"):
        select_latest_pit_rows(
            rows,
            logical_key=["ticker", "no", "sem", "curr", "merg", "period_end_date"],
            availability_field="source_available_date",
            revision_field="revision_date",
            decision_date="2025-08-31",
        )
```

- [ ] **Step 3: Run tests and verify expected failure**

Run:

```powershell
python -m pytest tests/test_pit_selection.py -q
```

Expected: fails because `pit.py` does not exist or functions are not implemented.

- [ ] **Step 4: Implement `src/data_analysts/pit.py`**

Implement:

```python
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any


class PitError(ValueError):
    pass


def normalize_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        candidate = text[:10]
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError as exc:
            raise PitError(f"unsupported date value: {value!r}") from exc
    if hasattr(value, "date"):
        parsed = value.date()
        if isinstance(parsed, date):
            return parsed.isoformat()
    raise PitError(f"unsupported date value: {value!r}")


def select_latest_pit_rows(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    decision_date: str,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    normalized_decision_date = normalize_date(decision_date)
    if normalized_decision_date is None:
        raise PitError("decision_date is required")

    diagnostics = {
        "input_row_count": len(rows),
        "eligible_row_count": 0,
        "future_row_count": 0,
        "selected_row_count": 0,
        "resolved_duplicate_count": 0,
        "unresolved_duplicate_count": 0,
    }
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        available = normalize_date(row.get(availability_field))
        if available is None:
            continue
        normalized = dict(row)
        normalized[availability_field] = available
        if revision_field:
            revision = normalize_date(row.get(revision_field))
            normalized[revision_field] = revision
        if available > normalized_decision_date:
            diagnostics["future_row_count"] += 1
            continue
        diagnostics["eligible_row_count"] += 1
        grouped[_logical_key(normalized, logical_key)].append(normalized)

    selected: list[dict[str, object]] = []
    for key, candidates in grouped.items():
        latest_available = max(str(row.get(availability_field) or "") for row in candidates)
        available_rows = [row for row in candidates if str(row.get(availability_field) or "") == latest_available]
        if revision_field:
            latest_revision = max(str(row.get(revision_field) or "") for row in available_rows)
            revision_rows = [row for row in available_rows if str(row.get(revision_field) or "") == latest_revision]
        else:
            revision_rows = available_rows
        if len(revision_rows) > 1:
            diagnostics["unresolved_duplicate_count"] += len(revision_rows)
            raise PitError(f"unresolved duplicate PIT rows for key={key}")
        if len(candidates) > 1:
            diagnostics["resolved_duplicate_count"] += len(candidates) - 1
        selected.append(revision_rows[0])
    diagnostics["selected_row_count"] = len(selected)
    return sorted(selected, key=lambda row: tuple(str(row.get(column) or "") for column in logical_key)), diagnostics


def _logical_key(row: dict[str, object], columns: list[str]) -> tuple[str, ...]:
    return tuple(str(row.get(column) or "") for column in columns)
```

- [ ] **Step 5: Run PIT tests**

Run:

```powershell
python -m pytest tests/test_pit_selection.py -q
```

Expected: all PIT selection tests pass.

## Task 4: Diagnostics Writer

**Files:**
- Modify: `src/data_analysts/paths.py`
- Create: `src/data_analysts/diagnostics.py`
- Test: `tests/test_pit_foundation_verify.py`

**Boundary:**
- This task writes JSON diagnostics only.
- It must enforce DataAnalysts root boundaries.

**Produces:**
- `write_diagnostic(root, name, payload)`

- [ ] **Step 1: Create failing diagnostics tests**

Create `tests/test_pit_foundation_verify.py`:

```python
import json

from data_analysts.diagnostics import write_diagnostic
from data_analysts.paths import DataAnalystsRoot


def test_write_diagnostic_stays_under_runtime_diagnostics(tmp_path):
    root = DataAnalystsRoot.from_path(tmp_path)
    path = write_diagnostic(root, "pit_foundation/source_catalog", {"status": "ready", "forbidden_source_usage_count": 0})

    assert path == tmp_path / "runs" / "real_all_products" / "diagnostics" / "pit_foundation" / "source_catalog.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["forbidden_source_usage_count"] == 0
```

- [ ] **Step 2: Run test and verify expected failure**

Run:

```powershell
python -m pytest tests/test_pit_foundation_verify.py -q
```

Expected: fails because `diagnostics.py` does not exist.

- [ ] **Step 3: Implement `src/data_analysts/diagnostics.py`**

First add this method to `DataAnalystsRoot` in `src/data_analysts/paths.py`:

```python
    def diagnostics_path(self, *parts: str) -> Path:
        return self.resolve_output(Path("runs") / "real_all_products" / "diagnostics" / Path(*parts))
```

Then implement `src/data_analysts/diagnostics.py`:

Implement:

```python
from __future__ import annotations

import json
from pathlib import Path

from data_analysts.paths import DataAnalystsRoot


def write_diagnostic(root: DataAnalystsRoot, name: str, payload: dict[str, object]) -> Path:
    safe_parts = [part for part in name.replace("\\", "/").split("/") if part and part not in {".", ".."}]
    if not safe_parts:
        raise ValueError("diagnostic name is required")
    path = root.diagnostics_path(*safe_parts).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run diagnostics test**

Run:

```powershell
python -m pytest tests/test_pit_foundation_verify.py -q
```

Expected: diagnostics test passes.

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

## Task 6: Contract Documentation

**Files:**
- Create: `contracts/PIT_REGISTRY_CONTRACT.md`
- Modify: `contracts/CONFIG_CONTRACT.md`
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`

**Boundary:**
- This task updates docs only.
- It must not change code behavior.

**Produces:**
- Reader-facing PIT Foundation contract.

- [ ] **Step 1: Write `PIT_REGISTRY_CONTRACT.md`**

Include these sections:

```markdown
# PIT Registry Contract

## Purpose

The PIT registry is the fail-closed source of truth for DataAnalysts source availability dates, logical keys, and revision selection.

## Required Date Rule

All PIT fields must be normalized to `YYYY-MM-DD` before filtering. Datetime values and strings with `HH:MM:SS` must lose the time component before comparison.

## Forbidden Sources

`TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden. Any config, catalog, manifest, or runtime output referencing them blocks verification.

## AINVFINB Financial Statement Rule

Raw canonical output preserves every `TEJ.AINVFINB` source row and revision.

Selected PIT views use:

1. `source_available_date = normalize_date(key3)`
2. `source_available_date <= decision_date`
3. group by `ticker, no, sem, curr, merg, period_end_date`
4. choose max `source_available_date`
5. within that date choose max `revision_date = normalize_date(mdate)`
6. if still duplicated, fail closed

## AFESTM1 Rule

`AFESTM1.annd` is the PIT date. `AFESTM1.key3` is a statement form/category field and must not be parsed as a date.
```

- [ ] **Step 2: Update `CONFIG_CONTRACT.md`**

Add:

```markdown
## Source Catalog and PIT Registry

Valid configs must include `configs/source_catalog.json` and `configs/pit_registry.json`.

Validation fails closed when:
- either file is missing
- schema version is unsupported
- a family id is duplicated
- a PIT field is missing
- a logical key is missing
- any config references `TEJ.AINVFQ1`
- any config references `TEJ.APISHRACTW`
```

- [ ] **Step 3: Update `OUTPUT_CONTRACT.md`**

Add:

```markdown
## PIT Foundation Diagnostics

PIT Foundation writes diagnostics to `runs/real_all_products/diagnostics/pit_foundation/source_catalog.json`.

The diagnostic must include:
- `forbidden_source_count`
- `approved_source_count`
- `pit_registry_family_count`
- `forbidden_source_usage_count`
- `missing_pit_field_count`
- `missing_logical_key_count`
```

- [ ] **Step 4: Update `VERIFICATION_CONTRACT.md`**

Add:

```markdown
## PIT Foundation Thresholds

Verification is blocked unless:
- `forbidden_source_usage_count == 0`
- `missing_pit_field_count == 0`
- `missing_logical_key_count == 0`
- `TEJ.AINVFQ1` references are absent
- `TEJ.APISHRACTW` references are absent
```

## Task 7: Final PIT Foundation Verification

**Files:**
- Existing configs, contracts, and source files from prior tasks.

**Boundary:**
- This task only verifies PIT Foundation.
- It must not run a full raw-family rebuild.

- [ ] **Step 1: Run PIT Foundation tests**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py tests/test_pit_selection.py tests/test_pit_foundation_verify.py -q
```

Expected: all PIT Foundation tests pass.

- [ ] **Step 2: Run full DataAnalysts unit tests if test folder exists**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass. Do not remove `tests` in this task; test cleanup requires a separate user confirmation after verification evidence is captured.

- [ ] **Step 3: Run CLI verify against the real product root**

Run:

```powershell
$env:PYTHONPATH='src'; python -m data_analysts.cli verify --root runs\real_all_products
```

Expected: `ready`.

- [ ] **Step 4: Inspect PIT Foundation diagnostic**

Run:

```powershell
Get-Content runs\real_all_products\diagnostics\pit_foundation\source_catalog.json
```

Expected JSON values:

```json
{
  "forbidden_source_count": 2,
  "forbidden_source_usage_count": 0,
  "missing_pit_field_count": 0,
  "missing_logical_key_count": 0
}
```

- [ ] **Step 5: Confirm no output outside DataAnalysts root**

Run:

```powershell
git -C "C:\Users\ChastLai\Documents\ALF" status --short -- "量化積木/DataAnalysts"
```

Expected: only intended DataAnalysts config, contract, source, and temporary test changes appear.

- [ ] **Step 6: Record test cleanup decision**

Write the final response with one explicit cleanup decision:

```text
Temporary tests were kept for regression safety. Test cleanup was not performed because this PIT Foundation plan does not include deletion without a separate confirmation.
```

## Completion Evidence

PIT Foundation is complete only when all are true:

- `configs/source_catalog.json` exists.
- `configs/pit_registry.json` exists.
- `contracts/PIT_REGISTRY_CONTRACT.md` exists.
- `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are rejected by config validation.
- `normalize_date()` strips `HH:MM:SS` and rejects unparseable dates.
- `select_latest_pit_rows()` excludes rows with availability after `decision_date`.
- `select_latest_pit_rows()` chooses latest revision for same logical key and same availability date.
- unresolved selected PIT duplicates raise `PitError`.
- `verify` writes PIT Foundation metrics.
- PIT Foundation diagnostic reports:
  - `forbidden_source_count == 2`
  - `forbidden_source_usage_count == 0`
  - `missing_pit_field_count == 0`
  - `missing_logical_key_count == 0`
- No raw-family expansion behavior is implemented in this PIT Foundation slice.
