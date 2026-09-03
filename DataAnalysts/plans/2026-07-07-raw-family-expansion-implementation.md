# Raw Family Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand DataAnalysts from current price/event/security outputs into registry-driven raw family parquet outputs for trading calendar, daily tradability, daily chip, monthly sales, financial statements, self-reported numbers, governance/event tables, and TX near-month futures.

**Architecture:** PIT Foundation remains the contract owner: `source_catalog.json`, `pit_registry.json`, `source_catalog.py`, `pit.py`, and `verify.py` are consumed, not redesigned. Raw Family Expansion adds source-family profiles, a focused `raw_families.py` normalizer, pipeline orchestration, per-family diagnostics, and manifest verification. Universe history, historical security panel, feature engineering, and strategy logic remain out of scope.

**Tech Stack:** Python 3 standard library, existing `pyarrow` parquet publisher, existing `pymongo` extraction path, existing `DataAnalystsRoot`, existing `ArtifactPublisher`, existing PIT helpers, temporary `pytest` tests, no `alf.*` runtime imports.

## Global Constraints

- All generated and edited artifacts must stay under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not use ALF main-flow modules as runtime adapters.
- Do not write raw/generated data artifacts to git.
- Do not implement Historical Universe or Historical Security Panel in this slice.
- Do not change adjusted-price, corporate-action, or universe ranking behavior in this slice.
- Fail closed on missing required PIT fields, unparseable PIT dates, missing logical keys, unresolved duplicate selected PIT rows, forbidden source usage, artifact paths outside root, and unsupported raw family schemas.
- `TEJ.AINVFQ1` is forbidden and must not be used.
- `TEJ.APISHRACTW` is forbidden and must not be used.
- `TEJ.AINVFINB` is the only financial statement source.
- `TEJ.AINVFINB.source_available_date = normalize_date(key3)`.
- `TEJ.AINVFINB` selected PIT views must enforce `source_available_date <= decision_date`.
- `TEJ.AINVFINB` raw canonical output must preserve all revisions.
- For `TEJ.AINVFINB`, selected PIT views choose max `source_available_date`, then max `revision_date = normalize_date(mdate)`, then fail closed if still duplicated.
- `TEJ.AFESTM1.annd` is the PIT date.
- `TEJ.AFESTM1.key3` is a statement form/category field, not a date.
- `TEJ.TRADEDAY_TWSE.date_rmk` blank after trimming means the date is a trading day.
- Small source tables must be published as single parquet files or coarse partitions; do not create daily tiny parquet files.
- Per-ticker large daily panels may use collection-pattern extraction; small TEJ tables must use single-collection extraction.
- Do not commit unless the user explicitly asks.

---

## Existing Interfaces to Preserve

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

```python
# src/data_analysts/artifacts.py
class ArtifactPublisher:
    def publish_parquet(self, path: str | Path, *, rows: list[dict[str, Any]], required_columns: list[str]) -> Path: ...
    def publish_manifest(...): ...
```

## File Structure

- Modify `configs/mongodb_sources.json`: add `apistkattr`, `apishract`, and `futures_taifex_tx` connections with localhost defaults only.
- Modify `configs/source_family_profiles.json`: add approved raw family profiles from the PIT registry.
- Mirror config changes into `runs/real_all_products/configs/*.json` during final real verification only.
- Create `src/data_analysts/raw_families.py`: family-specific raw canonical normalization and diagnostics.
- Modify `src/data_analysts/extract.py`: add projection/query support and source collection count diagnostics without changing public CLI.
- Modify `src/data_analysts/pipeline.py`: orchestrate raw family publishing in explicit phases.
- Modify `src/data_analysts/verify.py`: verify raw-family manifests and diagnostics thresholds.
- Modify `contracts/OUTPUT_CONTRACT.md`: document raw-family artifact surfaces.
- Modify `contracts/VERIFICATION_CONTRACT.md`: document raw-family quantitative checks.
- Add temporary tests:
  - `tests/test_raw_family_config.py`
  - `tests/test_raw_family_normalization.py`
  - `tests/test_raw_family_pipeline.py`
  - `tests/test_raw_family_verify.py`

## Runtime Output Contract for This Slice

Raw canonical outputs:

```text
runtime/data_canonical/raw/trading_calendar/trading_calendar.parquet
runtime/data_canonical/raw/daily_tradability/year=YYYY/part.parquet
runtime/data_canonical/raw/daily_chip/year=YYYY/part.parquet
runtime/data_canonical/raw/monthly_sales/available_year=YYYY/part.parquet
runtime/data_canonical/raw/financial_statement_raw/available_year=YYYY/part.parquet
runtime/data_canonical/derived/pit/financial_statement_pit_selected/decision_year=YYYY/part.parquet
runtime/data_canonical/raw/self_reported_numbers_raw/available_year=YYYY/part.parquet
runtime/data_canonical/derived/pit/self_reported_numbers_pit_selected/decision_year=YYYY/part.parquet
runtime/data_canonical/raw/<governance_family>/available_year=YYYY/part.parquet
runtime/data_canonical/raw/taiwan_index_futures_near_month/year=YYYY/part.parquet
```

Per-family diagnostics:

```text
runs/real_all_products/diagnostics/raw_families/<family_id>.json
```

Manifest ids:

```text
trading_calendar
daily_tradability
daily_chip
monthly_sales
financial_statement_raw
financial_statement_pit_selected
self_reported_numbers_raw
self_reported_numbers_pit_selected
director_supervisor_holdings
board_reelection_statistics
executive_change_events
merger_acquisition_events
private_placement_relation_events
insider_transfer_completed
insider_transfer_declared_not_completed
treasury_stock_events
taiwan_index_futures_near_month
```

---

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

## Task 2: Raw Family Normalization Module

**Files:**
- Create: `src/data_analysts/raw_families.py`
- Test: `tests/test_raw_family_normalization.py`

**Boundary:**
- This task implements pure normalization only.
- It must not read MongoDB, write parquet, write diagnostics files, or mutate pipeline output.

**Interfaces:**
- Consumes:
  - raw source rows from `extract_family_rows_from_database()`
  - PIT rules from `RuntimeConfig.pit_registry`
  - `normalize_date()` and `select_latest_pit_rows()` from `pit.py`
- Produces:

```python
class RawFamilyError(ValueError): ...

def normalize_raw_family(
    family_id: str,
    rows: list[dict[str, object]],
    pit_registry: dict[str, object],
    *,
    decision_dates: list[str] | None = None,
) -> dict[str, object]: ...
```

Return shape:

```python
{
    "family_id": "financial_statement_raw",
    "raw_rows": [dict(...), ...],
    "selected_rows": [dict(...), ...],
    "diagnostics": {
        "source_row_count": 2,
        "published_row_count": 2,
        "omitted_row_count": 0,
        "pit_null_count": 0,
        "pit_parse_failure_count": 0,
        "duplicate_logical_key_count": 0,
        "resolved_duplicate_count": 0,
        "unresolved_duplicate_count": 0,
        "date_min": "2025-01-01",
        "date_max": "2025-12-31"
    }
}
```

- [ ] **Step 1: Write failing normalization tests**

Create `tests/test_raw_family_normalization.py`:

```python
import pytest

from data_analysts.raw_families import RawFamilyError, normalize_raw_family


def _registry() -> dict:
    return {
        "schema_version": "1.0",
        "families": {
            "trading_calendar": {
                "availability_field": "zdate",
                "date_normalization": "date_only",
                "logical_key": ["date", "market"],
                "revision_field": None,
                "selected_view": False,
            },
            "monthly_sales": {
                "availability_field": "annd_s",
                "date_normalization": "date_only",
                "logical_key": ["ticker", "source_period_date"],
                "revision_field": "mdate",
                "selected_view": False,
            },
            "financial_statement_raw": {
                "availability_field": "key3",
                "date_normalization": "date_only",
                "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
                "revision_field": "mdate",
                "selected_view": False,
            },
            "financial_statement_pit_selected": {
                "availability_field": "source_available_date",
                "date_normalization": "date_only",
                "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date"],
                "revision_field": "revision_date",
                "selected_view": True,
            },
            "self_reported_numbers_raw": {
                "availability_field": "annd",
                "date_normalization": "date_only",
                "logical_key": ["ticker", "key3", "sem", "curr", "merg", "period_end_date", "source_available_date", "revision_date"],
                "revision_field": "mdate",
                "selected_view": False,
            },
        },
    }


def test_trading_calendar_uses_blank_date_rmk_as_trading_day():
    result = normalize_raw_family(
        "trading_calendar",
        [
            {"zdate": "2025-01-02 00:00:00", "mkt": "TWSE", "date_rmk": "", "source_row_id": "a"},
            {"zdate": "2025-01-03", "mkt": "TWSE", "date_rmk": "休市", "source_row_id": "b"},
        ],
        _registry(),
    )
    rows = result["raw_rows"]
    assert rows[0]["date"] == "2025-01-02"
    assert rows[0]["market"] == "TWSE"
    assert rows[0]["is_trading_day"] is True
    assert rows[1]["is_trading_day"] is False
    assert result["diagnostics"]["published_row_count"] == 2
    assert result["diagnostics"]["pit_parse_failure_count"] == 0


def test_monthly_sales_normalizes_period_and_availability_dates():
    result = normalize_raw_family(
        "monthly_sales",
        [
            {
                "coid": "2330",
                "mdate": "2025-06-01",
                "annd_s": "2025-07-10 13:30:00",
                "sales": 100,
                "source_row_id": "a",
            }
        ],
        _registry(),
    )
    row = result["raw_rows"][0]
    assert row["ticker"] == "2330"
    assert row["source_period_date"] == "2025-06-01"
    assert row["source_available_date"] == "2025-07-10"
    assert row["sales"] == 100


def test_financial_statement_preserves_raw_revisions_and_selects_latest_revision():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-15", "eps": 10, "source_row_id": "a"},
            {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-20", "eps": 11, "source_row_id": "b"},
            {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-09-01", "mdate": "2025-09-02", "eps": 12, "source_row_id": "c"},
        ],
        _registry(),
        decision_dates=["2025-08-31"],
    )
    assert len(result["raw_rows"]) == 3
    selected = result["selected_rows"]
    assert len(selected) == 1
    assert selected[0]["decision_date"] == "2025-08-31"
    assert selected[0]["eps"] == 11
    assert result["diagnostics"]["future_row_count"] == 1
    assert result["diagnostics"]["resolved_duplicate_count"] == 1


def test_self_reported_numbers_keeps_key3_as_category():
    result = normalize_raw_family(
        "self_reported_numbers_raw",
        [
            {"coid": "2330", "key3": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "annd": "2025-07-20", "mdate": "2025-07-21", "value": 1, "source_row_id": "a"}
        ],
        _registry(),
        decision_dates=["2025-07-31"],
    )
    assert result["raw_rows"][0]["key3"] == "Q"
    assert result["raw_rows"][0]["source_available_date"] == "2025-07-20"
    assert result["selected_rows"][0]["key3"] == "Q"


def test_missing_required_pit_field_fails_closed():
    with pytest.raises(RawFamilyError, match="missing required PIT field"):
        normalize_raw_family("monthly_sales", [{"coid": "2330", "mdate": "2025-06-01"}], _registry())
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_raw_family_normalization.py -q
```

Expected: fails because `data_analysts.raw_families` does not exist.

- [ ] **Step 3: Implement `src/data_analysts/raw_families.py`**

Implement these exact public names:

```python
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from data_analysts.pit import PitError, normalize_date, select_latest_pit_rows


class RawFamilyError(ValueError):
    """Raised when a raw family cannot be normalized without data leakage or ambiguity."""


def normalize_raw_family(
    family_id: str,
    rows: list[dict[str, object]],
    pit_registry: dict[str, object],
    *,
    decision_dates: list[str] | None = None,
) -> dict[str, object]:
    rule = _rule_for(family_id, pit_registry)
    normalizer = _NORMALIZERS.get(family_id, _normalize_generic_mdate_family)
    raw_rows, diagnostics = normalizer(family_id, rows, rule)
    selected_rows: list[dict[str, object]] = []
    if family_id == "financial_statement_raw":
        selected_rows, selected_diag = _selected_rows(
            raw_rows,
            selected_family_id="financial_statement_pit_selected",
            pit_registry=pit_registry,
            decision_dates=decision_dates,
        )
        diagnostics.update(selected_diag)
    elif family_id == "self_reported_numbers_raw":
        selected_rows, selected_diag = _selected_rows(
            raw_rows,
            selected_family_id="self_reported_numbers_pit_selected",
            pit_registry=pit_registry,
            decision_dates=decision_dates,
        )
        diagnostics.update(selected_diag)
    diagnostics.setdefault("source_row_count", len(rows))
    diagnostics.setdefault("published_row_count", len(raw_rows))
    diagnostics.setdefault("omitted_row_count", len(rows) - len(raw_rows))
    diagnostics.setdefault("pit_null_count", 0)
    diagnostics.setdefault("pit_parse_failure_count", 0)
    diagnostics.setdefault("duplicate_logical_key_count", _duplicate_count(raw_rows, list(rule["logical_key"])))
    diagnostics.setdefault("resolved_duplicate_count", 0)
    diagnostics.setdefault("unresolved_duplicate_count", 0)
    _add_date_range(diagnostics, raw_rows, "source_available_date")
    return {"family_id": family_id, "raw_rows": raw_rows, "selected_rows": selected_rows, "diagnostics": diagnostics}
```

Implement family normalizers with these required mappings:

```python
def _normalize_trading_calendar(family_id: str, rows: list[dict[str, object]], rule: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        date_value = _required_date(row, "zdate", family_id)
        market = str(row.get("mkt") or row.get("market") or "TWSE").strip()
        if not market:
            raise RawFamilyError("missing market for trading_calendar")
        date_rmk = "" if row.get("date_rmk") is None else str(row.get("date_rmk")).strip()
        output.append(_with_source_metadata(row, {
            "date": date_value,
            "market": market,
            "is_trading_day": date_rmk == "",
            "date_rmk": date_rmk,
            "source_available_date": date_value,
        }))
    return output, {"trading_day_count": sum(1 for row in output if row["is_trading_day"]), "non_trading_day_count": sum(1 for row in output if not row["is_trading_day"])}
```

```python
def _normalize_monthly_sales(family_id: str, rows: list[dict[str, object]], rule: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        output.append(_with_source_metadata(row, {
            "ticker": _required_text(row, "coid", family_id),
            "source_period_date": _required_date(row, "mdate", family_id),
            "source_available_date": _required_date(row, "annd_s", family_id),
        }))
    return output, {}
```

```python
def _normalize_financial_statement(family_id: str, rows: list[dict[str, object]], rule: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    by_no = Counter()
    for row in rows:
        no = _required_text(row, "no", family_id)
        by_no[no] += 1
        output.append(_with_source_metadata(row, {
            "ticker": _required_text(row, "coid", family_id),
            "no": no,
            "sem": _required_text(row, "sem", family_id),
            "curr": _required_text(row, "curr", family_id),
            "merg": _required_text(row, "merg", family_id),
            "period_start_date": normalize_date(row.get("begd")),
            "period_end_date": _required_date(row, "endd", family_id),
            "source_period_date": normalize_date(row.get("mdate")),
            "source_available_date": _required_date(row, "key3", family_id),
            "revision_date": _required_date(row, "mdate", family_id),
        }))
    return output, {"rows_by_no": dict(by_no)}
```

```python
def _normalize_self_reported_numbers(family_id: str, rows: list[dict[str, object]], rule: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    by_key3 = Counter()
    for row in rows:
        key3 = _required_text(row, "key3", family_id)
        by_key3[key3] += 1
        output.append(_with_source_metadata(row, {
            "ticker": _required_text(row, "coid", family_id),
            "key3": key3,
            "sem": _required_text(row, "sem", family_id),
            "curr": _required_text(row, "curr", family_id),
            "merg": _required_text(row, "merg", family_id),
            "period_end_date": _required_date(row, "endd", family_id),
            "source_available_date": _required_date(row, "annd", family_id),
            "revision_date": _required_date(row, "mdate", family_id),
        }))
    return output, {"rows_by_key3": dict(by_key3)}
```

```python
def _normalize_generic_mdate_family(family_id: str, rows: list[dict[str, object]], rule: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        output.append(_with_source_metadata(row, {
            "ticker": str(row.get("coid") or row.get("ticker") or "").strip() or None,
            "source_date": _required_date(row, "mdate", family_id),
            "source_available_date": _required_date(row, "mdate", family_id),
        }))
    return output, {}
```

```python
def _normalize_futures_near_month(family_id: str, rows: list[dict[str, object]], rule: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        date_value = _required_date(row, "日期", family_id)
        output.append(_with_source_metadata(row, {
            "date": date_value,
            "source_available_date": date_value,
            "contract": str(row.get("contract") or row.get("契約") or row.get("symbol") or "TX_1").strip(),
        }))
    return output, {}
```

Helper functions must:

```python
def _required_date(row: dict[str, object], field: str, family_id: str) -> str:
    if field not in row:
        raise RawFamilyError(f"missing required PIT field for {family_id}: {field}")
    try:
        value = normalize_date(row[field])
    except PitError as exc:
        raise RawFamilyError(f"invalid PIT date for {family_id}.{field}: {row[field]!r}") from exc
    if value is None:
        raise RawFamilyError(f"blank required PIT field for {family_id}: {field}")
    return value


def _required_text(row: dict[str, object], field: str, family_id: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise RawFamilyError(f"missing required field for {family_id}: {field}")
    return value
```

`_with_source_metadata()` must preserve raw source fields after canonical fields, and must keep:

```python
source_dataset_id
source_collection
source_row_id
data_cutoff_at
```

- [ ] **Step 4: Run normalization tests**

Run:

```powershell
python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- `pit_parse_failure_count == 0` in clean fixtures.
- `published_row_count == source_row_count` in clean fixtures.
- financial raw row count stays `3` while selected row count is `1`.
- AFESTM1 `key3` remains a category string and is never parsed as a date.

---

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

## Task 5: Financial and Self-Reported Selected PIT Views

**Files:**
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`
- Test: `tests/test_raw_family_normalization.py`
- Test: `tests/test_raw_family_pipeline.py`

**Boundary:**
- This task hardens selected PIT surfaces only.
- It must not filter raw canonical rows to `no = Q`; raw output preserves `A`, `Q`, and `TTM`.
- It must not use selected PIT output to tune strategy or universe behavior.

**Interfaces:**
- Produces selected view rows with columns:

```text
decision_date
ticker
no or key3
sem
curr
merg
period_end_date
source_available_date
revision_date
source_dataset_id
source_collection
source_row_id
data_cutoff_at
```

- [ ] **Step 1: Add tests for A/Q/TTM preservation and selected Q convenience counts**

Append to `tests/test_raw_family_normalization.py`:

```python
def test_financial_statement_raw_preserves_a_q_ttm_and_reports_selected_q_count():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {"coid": "2330", "no": "A", "sem": "4", "curr": "TWD", "merg": "Y", "endd": "2024-12-31", "key3": "2025-03-31", "mdate": "2025-04-01", "eps": 40, "source_row_id": "a"},
            {"coid": "2330", "no": "Q", "sem": "1", "curr": "TWD", "merg": "Y", "endd": "2025-03-31", "key3": "2025-05-15", "mdate": "2025-05-16", "eps": 10, "source_row_id": "q"},
            {"coid": "2330", "no": "TTM", "sem": "1", "curr": "TWD", "merg": "Y", "endd": "2025-03-31", "key3": "2025-05-15", "mdate": "2025-05-16", "eps": 42, "source_row_id": "t"},
        ],
        _registry(),
        decision_dates=["2025-05-31"],
    )
    assert {row["no"] for row in result["raw_rows"]} == {"A", "Q", "TTM"}
    assert result["diagnostics"]["rows_by_no"] == {"A": 1, "Q": 1, "TTM": 1}
    assert result["diagnostics"]["selected_no_q_row_count"] == 1
```

- [ ] **Step 2: Add unresolved duplicate selected PIT test**

Append:

```python
def test_financial_statement_selected_pit_unresolved_duplicate_fails_closed():
    with pytest.raises(RawFamilyError, match="unresolved duplicate"):
        normalize_raw_family(
            "financial_statement_raw",
            [
                {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-20", "eps": 10, "source_row_id": "a"},
                {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-20", "eps": 11, "source_row_id": "b"},
            ],
            _registry(),
            decision_dates=["2025-08-31"],
        )
```

- [ ] **Step 3: Implement selected diagnostics**

In `_selected_rows()`, accumulate:

```python
{
    "decision_date_count": len(decision_dates or []),
    "eligible_row_count": sum(selected_diag["eligible_row_count"] for each decision_date),
    "future_row_count": sum(selected_diag["future_row_count"] for each decision_date),
    "selected_row_count": total_selected_rows,
    "selected_no_q_row_count": sum(1 for row in selected_rows if row.get("no") == "Q"),
    "selected_key3_category_counts": dict(Counter(row["key3"] for row in selected_rows if "key3" in row)),
}
```

On `PitError`, raise:

```python
raise RawFamilyError(str(exc)) from exc
```

- [ ] **Step 4: Run selected PIT tests**

Run:

```powershell
python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- raw rows by `no` include all observed `A`, `Q`, and `TTM`.
- `selected_no_q_row_count` is reported.
- unresolved duplicate selected PIT rows raise `RawFamilyError`.

---

## Task 6: Governance/Event and Futures Raw Families

**Files:**
- Modify: `src/data_analysts/raw_families.py`
- Modify: `tests/test_raw_family_normalization.py`
- Modify: `tests/test_raw_family_pipeline.py`

**Boundary:**
- This task handles generic `mdate` governance/event tables and `Futures_TAIFEX_TX.TX_1`.
- It must not include `AINVFQ1`, `APISHRACTW`, `AINVFINB`, or `AFESTM1` in generic governance logic.

**Interfaces:**
- Produces raw canonical artifacts for:
  - `director_supervisor_holdings`
  - `board_reelection_statistics`
  - `executive_change_events`
  - `merger_acquisition_events`
  - `private_placement_relation_events`
  - `insider_transfer_completed`
  - `insider_transfer_declared_not_completed`
  - `treasury_stock_events`
  - `taiwan_index_futures_near_month`

- [ ] **Step 1: Add generic mdate family normalization tests**

Append to `tests/test_raw_family_normalization.py`:

```python
def test_generic_governance_family_uses_mdate_as_source_available_date():
    registry = _registry()
    registry["families"]["director_supervisor_holdings"] = {
        "availability_field": "mdate",
        "date_normalization": "date_only",
        "logical_key": ["ticker", "source_date"],
        "revision_field": "mdate",
        "selected_view": False,
    }
    result = normalize_raw_family(
        "director_supervisor_holdings",
        [{"coid": "2330", "mdate": "2025-01-15 00:00:00", "shares": 10, "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["ticker"] == "2330"
    assert row["source_date"] == "2025-01-15"
    assert row["source_available_date"] == "2025-01-15"
    assert row["shares"] == 10
```

- [ ] **Step 2: Add futures normalization test**

Append:

```python
def test_futures_near_month_uses_chinese_date_field():
    registry = _registry()
    registry["families"]["taiwan_index_futures_near_month"] = {
        "availability_field": "日期",
        "date_normalization": "date_only",
        "logical_key": ["date", "contract"],
        "revision_field": None,
        "selected_view": False,
    }
    result = normalize_raw_family(
        "taiwan_index_futures_near_month",
        [{"日期": "2025-01-02", "契約": "TXF202501", "收盤價": 23000, "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["date"] == "2025-01-02"
    assert row["source_available_date"] == "2025-01-02"
    assert row["contract"] == "TXF202501"
```

- [ ] **Step 3: Register normalizers**

In `raw_families.py`, set:

```python
_NORMALIZERS = {
    "trading_calendar": _normalize_trading_calendar,
    "monthly_sales": _normalize_monthly_sales,
    "financial_statement_raw": _normalize_financial_statement,
    "self_reported_numbers_raw": _normalize_self_reported_numbers,
    "taiwan_index_futures_near_month": _normalize_futures_near_month,
}
```

Do not register `AINVFQ1` or `APISHRACTW`.

- [ ] **Step 4: Run governance/futures tests**

Run:

```powershell
python -m pytest tests/test_raw_family_normalization.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- Every governance family reports `source_row_count`, `published_row_count`, `pit_null_count`, `pit_parse_failure_count`, `duplicate_logical_key_count`, and `unresolved_duplicate_count`.
- Futures reports `duplicate_date_contract_count`.

---

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

## Task 8: Contracts and Reader Documentation

**Files:**
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`
- Modify: `README.md`

**Boundary:**
- This task updates documentation only.
- It must not change code behavior.

**Interfaces:**
- Documents artifact paths, schemas, and verification metrics produced by Tasks 1-7.

- [ ] **Step 1: Update `OUTPUT_CONTRACT.md` raw family section**

Add:

```markdown
## Raw Family Expansion Outputs

Raw family artifacts are registry-driven canonical parquet surfaces. They are not feature tables and they are not strategy inputs until Feature Analysts consume them.

Required raw outputs:

| artifact_id | layer | partitioning | PIT field |
|---|---|---|---|
| trading_calendar | raw | single_file | zdate |
| daily_tradability | raw | year | mdate |
| daily_chip | raw | year | mdate |
| monthly_sales | raw | available_year | annd_s |
| financial_statement_raw | raw | available_year | key3 |
| financial_statement_pit_selected | derived | decision_year | source_available_date |
| self_reported_numbers_raw | raw | available_year | annd |
| self_reported_numbers_pit_selected | derived | decision_year | source_available_date |
| taiwan_index_futures_near_month | raw | year | 日期 |

Governance and event raw families use `mdate` as `source_available_date` and publish by `available_year`.
```

- [ ] **Step 2: Update `VERIFICATION_CONTRACT.md` raw thresholds**

Add:

```markdown
## Raw Family Thresholds

Verification blocks unless:

- `pit_parse_failure_count_total == 0`
- `unresolved_duplicate_count_total == 0`
- `forbidden_source_usage_count_total == 0`
- every manifest artifact path stays under DataAnalysts root
- every selected PIT view has `source_available_date <= decision_date`

Every raw family diagnostic must report:

- `source_row_count`
- `published_row_count`
- `omitted_row_count`
- `pit_null_count`
- `pit_parse_failure_count`
- `duplicate_logical_key_count`
- `resolved_duplicate_count`
- `unresolved_duplicate_count`
- `date_min`
- `date_max`
- `artifact_file_count`
```

- [ ] **Step 3: Update `README.md` source coverage**

Add a short section:

```markdown
## Raw Family Coverage

Raw Family Expansion publishes trading calendar, daily tradability, daily chip, monthly sales, financial statements from `TEJ.AINVFINB`, self-reported numbers from `TEJ.AFESTM1`, governance/event tables, and TX near-month futures. `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden and fail verification.
```

- [ ] **Step 4: Verify docs**

Run:

```powershell
rg -n "Raw Family Expansion|AINVFQ1|APISHRACTW|AINVFINB|AFESTM1|pit_parse_failure_count_total" README.md contracts
```

Expected: command exits `0` and prints the updated sections.

---

## Task 9: Real Data Smoke and Full-History Readiness

**Files:**
- Modify runtime files only under `runs/real_all_products` during execution.
- Do not modify source code unless verification fails and a fix task is created.

**Boundary:**
- This task verifies Raw Family Expansion on real MongoDB data.
- It must not implement Historical Universe or Historical Security Panel.
- It must not tune strategy logic.

**Interfaces:**
- Consumes CLI:

```powershell
$env:PYTHONPATH='src'
python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_tradability,daily_chip --start-date 2026-01-01 --end-date 2026-01-31
python -m data_analysts.cli run-backfill --root runs\real_all_products --families monthly_sales,financial_statement_raw,self_reported_numbers_raw --start-date 2025-01-01 --end-date 2026-07-02
python -m data_analysts.cli run-backfill --root runs\real_all_products --families director_supervisor_holdings,board_reelection_statistics,executive_change_events,merger_acquisition_events,private_placement_relation_events,insider_transfer_completed,insider_transfer_declared_not_completed,treasury_stock_events,taiwan_index_futures_near_month --start-date 2025-01-01 --end-date 2026-07-02
python -m data_analysts.cli verify --root runs\real_all_products
```

- [ ] **Step 1: Copy config files into run root**

Run:

```powershell
Copy-Item configs\mongodb_sources.json runs\real_all_products\configs\mongodb_sources.json -Force
Copy-Item configs\source_family_profiles.json runs\real_all_products\configs\source_family_profiles.json -Force
Copy-Item configs\source_catalog.json runs\real_all_products\configs\source_catalog.json -Force
Copy-Item configs\pit_registry.json runs\real_all_products\configs\pit_registry.json -Force
```

Expected: all four files exist under `runs\real_all_products\configs`.

- [ ] **Step 2: Run short smoke by phase**

Run:

```powershell
$env:PYTHONPATH='src'
python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_tradability,daily_chip --start-date 2026-01-01 --end-date 2026-01-31
```

Expected: `ready`.

- [ ] **Step 3: Run financial smoke**

Run:

```powershell
$env:PYTHONPATH='src'
python -m data_analysts.cli run-backfill --root runs\real_all_products --families monthly_sales,financial_statement_raw,self_reported_numbers_raw --start-date 2025-01-01 --end-date 2026-07-02
```

Expected: `ready`.

- [ ] **Step 4: Run governance/futures smoke**

Run:

```powershell
$env:PYTHONPATH='src'
python -m data_analysts.cli run-backfill --root runs\real_all_products --families director_supervisor_holdings,board_reelection_statistics,executive_change_events,merger_acquisition_events,private_placement_relation_events,insider_transfer_completed,insider_transfer_declared_not_completed,treasury_stock_events,taiwan_index_futures_near_month --start-date 2025-01-01 --end-date 2026-07-02
```

Expected: `ready`.

- [ ] **Step 5: Inspect diagnostics**

Run:

```powershell
Get-ChildItem runs\real_all_products\diagnostics\raw_families\*.json | Select-Object Name,Length
python - <<'PY'
import json
from pathlib import Path
base = Path('runs/real_all_products/diagnostics/raw_families')
for path in sorted(base.glob('*.json')):
    p = json.loads(path.read_text(encoding='utf-8'))
    print(path.name, p.get('source_row_count'), p.get('published_row_count'), p.get('pit_parse_failure_count'), p.get('unresolved_duplicate_count'))
PY
```

Expected:

```text
pit_parse_failure_count == 0 for every required family
unresolved_duplicate_count == 0 for every family
source_row_count > 0 for families present in MongoDB
published_row_count > 0 for families present in MongoDB
```

- [ ] **Step 6: Run verify**

Run:

```powershell
$env:PYTHONPATH='src'
python -m data_analysts.cli verify --root runs\real_all_products
```

Expected: `ready`.

- [ ] **Step 7: Confirm no nested bad runtime directory**

Run:

```powershell
Get-ChildItem -Recurse -Directory runs\real_all_products | Where-Object { $_.FullName -match '\\runs\\real_all_products\\runs($|\\)' }
```

Expected: no output.

**Quantitative Verification:**
- `raw_family_diagnostic_count >= 15`.
- `pit_parse_failure_count_total == 0`.
- `unresolved_duplicate_count_total == 0`.
- `forbidden_source_usage_count_total == 0`.
- `artifact_path_outside_root_count == 0`.
- `verify_status == ready`.

---

## Completion Evidence

Raw Family Expansion is complete only when all are true:

- `configs/source_family_profiles.json` contains all approved raw family profiles from PIT registry non-derived families.
- `configs/mongodb_sources.json` contains localhost-default connections for `apistkattr`, `apishract`, and `futures_taifex_tx`.
- `TEJ.AINVFQ1` and `TEJ.APISHRACTW` remain forbidden and unused.
- `trading_calendar` publishes a single parquet file and treats blank `date_rmk` as trading day.
- `daily_tradability` and `daily_chip` publish year-partitioned raw panels.
- `monthly_sales` uses `annd_s` as `source_available_date`.
- `financial_statement_raw` uses only `TEJ.AINVFINB`, normalizes `key3`, preserves raw revisions, and reports rows by `no`.
- `financial_statement_pit_selected` enforces `source_available_date <= decision_date` and resolves same availability by latest `mdate`.
- `self_reported_numbers_raw` uses `AFESTM1.annd` as availability and preserves `AFESTM1.key3` as category.
- Generic governance/event tables use `mdate` as PIT availability.
- `taiwan_index_futures_near_month` uses `日期` as PIT date.
- Raw family diagnostics exist under `runs/real_all_products/diagnostics/raw_families`.
- `python -m pytest tests/test_raw_family_config.py tests/test_raw_family_normalization.py tests/test_raw_family_pipeline.py tests/test_raw_family_verify.py -q` passes.
- `python -m pytest -q` passes.
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --root runs\real_all_products` returns `ready`.
- No output is written outside `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.

## Self-Review Checklist

- Spec coverage: tasks cover config profiles, pure normalization, pipeline publishing, extraction diagnostics, selected PIT hardening, governance/futures, verification, docs, and real data smoke.
- Scope boundary: no task implements Historical Universe, Historical Security Panel, adjusted-price semantics, strategy logic, or feature analysis.
- PIT safety: missing PIT fields, missing logical keys, forbidden sources, and unresolved selected PIT duplicates all fail closed.
- Small-table read efficiency: small TEJ tables use single-collection reads; only per-ticker daily panels use collection-pattern fanout.
- Quantitative verification: every task has explicit numeric diagnostics or pass/fail thresholds.
