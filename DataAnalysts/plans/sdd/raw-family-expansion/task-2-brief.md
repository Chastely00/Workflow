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

