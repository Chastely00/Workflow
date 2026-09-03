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

