# Task 3 Review Package


## FILE: plans\sdd\historical-universe\task-3-brief.md
```
# Task 3 Brief

### Task 3: Historical Universe Builder

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\universe.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe.py`

**Interfaces:**
- Consumes: `security_panel_history: list[dict[str, Any]]`, `universe_specs: dict[str, Any]`.
- Produces: `build_historical_universe_memberships(...) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]`.
- Membership uniqueness key: `(effective_date, universe_id, ticker)`.
- Rank uniqueness key: `(effective_date, universe_id, rank)`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_historical_universe.py`:

```python
from data_analysts.universe import build_historical_universe_memberships


def test_historical_universe_uses_effective_date_and_year_safe_rows():
    specs = {
        "universes": [
            {
                "universe_id": "tw_equity_liquid_top2",
                "enabled": True,
                "source": "security_panel",
                "filters": [
                    {"field": "listed", "op": "eq", "value": True},
                    {"field": "tradable", "op": "eq", "value": True},
                    {"field": "market_cap", "op": "not_null"},
                ],
                "rank_by": [
                    {"field": "market_cap", "direction": "desc"},
                    {"field": "ticker", "direction": "asc"},
                ],
                "limit": 2,
            }
        ]
    }
    panel = [
        {"as_of_date": "2025-01-02", "effective_date": "2025-01-03", "ticker": "2330", "listed": True, "tradable": True, "market_cap": 30, "market": "TWSE", "security_type": "common_stock"},
        {"as_of_date": "2025-01-02", "effective_date": "2025-01-03", "ticker": "2317", "listed": True, "tradable": True, "market_cap": 20, "market": "TWSE", "security_type": "common_stock"},
        {"as_of_date": "2025-01-02", "effective_date": "2025-01-03", "ticker": "9999", "listed": True, "tradable": True, "market_cap": 10, "market": "TPEX", "security_type": "common_stock"},
    ]

    memberships, diagnostics = build_historical_universe_memberships(panel, specs)

    rows = memberships["tw_equity_liquid_top2"]
    assert [(row["ticker"], row["rank"]) for row in rows] == [("2330", 1), ("2317", 2)]
    assert rows[0]["as_of_date"] == "2025-01-02"
    assert rows[0]["effective_date"] == "2025-01-03"
    assert rows[0]["included"] is True
    assert diagnostics["tw_equity_liquid_top2"]["top_n_limit"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["max_included_count"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_ticker_count"] == 0
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe.py -q
```

Expected: FAIL because `build_historical_universe_memberships` does not exist.

- [ ] **Step 3: Implement historical builder**

Add to `src/data_analysts/universe.py`:

```python
def build_historical_universe_memberships(
    security_panel_history: list[dict[str, Any]],
    universe_specs: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    memberships: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, Any] = {}
    rows_by_as_of: dict[str, list[dict[str, Any]]] = {}
    for row in security_panel_history:
        if not row.get("effective_date"):
            continue
        rows_by_as_of.setdefault(str(row["as_of_date"]), []).append(row)

    for spec in universe_specs.get("universes", []):
        if spec.get("enabled", True) is False:
            continue
        universe_id = spec["universe_id"]
        output: list[dict[str, Any]] = []
        included_counts: list[int] = []
        eligible_counts: list[int] = []
        duplicate_membership_count = 0
        seen_memberships: set[tuple[str, str, str]] = set()
        for as_of_date in sorted(rows_by_as_of):
            candidates = [row for row in rows_by_as_of[as_of_date] if _passes_filters(row, spec.get("filters", []))]
            eligible_counts.append(len(candidates))
            candidates = _sort_candidates(candidates, spec.get("rank_by", []))
            limit = spec.get("limit")
            if isinstance(limit, int):
                candidates = candidates[:limit]
            included_counts.append(len(candidates))
            for index, row in enumerate(candidates, start=1):
                membership_key = (str(row["effective_date"]), universe_id, str(row["ticker"]))
                if membership_key in seen_memberships:
                    duplicate_membership_count += 1
                    continue
                seen_memberships.add(membership_key)
                output.append(_membership_row(row, universe_id, index))
        memberships[universe_id] = output
        diagnostics[universe_id] = {
            "universe_id": universe_id,
            "as_of_date_count": len(rows_by_as_of),
            "candidate_count": sum(eligible_counts),
            "included_count": len(output),
            "excluded_count": sum(eligible_counts) - len(output),
            "top_n_limit": spec.get("limit"),
            "max_included_count": max(included_counts) if included_counts else 0,
            "duplicate_universe_effective_ticker_count": duplicate_membership_count,
        }
    return memberships, diagnostics
```

Add `_membership_row(row, universe_id, rank)`:

```python
def _membership_row(row: dict[str, Any], universe_id: str, rank: int) -> dict[str, Any]:
    return {
        "as_of_date": row["as_of_date"],
        "effective_date": row["effective_date"],
        "universe_id": universe_id,
        "ticker": row["ticker"],
        "rank": rank,
        "included": True,
        "reason": "selected",
        "market": row.get("market"),
        "security_type": row.get("security_type"),
        "listed": row.get("listed"),
        "tradable": row.get("tradable"),
        "close": row.get("close"),
        "adj_close": row.get("adj_close"),
        "market_cap": row.get("market_cap"),
        "adv20": row.get("adv20"),
        "data_cutoff_at": row.get("data_cutoff_at"),
    }
```

- [ ] **Step 4: Run tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src\data_analysts\universe.py tests\test_historical_universe.py
git commit -m "feat: build historical universe membership"
```


```


## FILE: plans\sdd\historical-universe\task-3-report.md
```
STATUS: GREEN

changed files
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\universe.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-3-report.md`

RED test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe.py -q`
- Result: FAIL during collection with `ImportError: cannot import name 'build_historical_universe_memberships' from 'data_analysts.universe'`

GREEN test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe.py -q`
- Result: PASS, `2 passed in 0.02s`

self-review
- Kept existing `build_universe_memberships()` behavior unchanged.
- Historical builder emits membership rows with both `as_of_date` and `effective_date`, but uniqueness and rank de-duplication are keyed on `effective_date`.
- Rows with null `effective_date` are skipped, so same-day `as_of_date` is not promoted into a tradable historical membership.
- Added regression coverage for baseline historical selection and duplicate `(effective_date, universe_id, ticker)` / `(effective_date, universe_id, rank)` handling.
- Scope stayed inside the allowed `DataAnalysts` files only; no pipeline publish, verify gate, inspect, or docs flow changes.

concerns
- `src/data_analysts/verify.py` still validates universe artifact uniqueness using `(as_of_date, universe_id, ticker)` and `(as_of_date, universe_id, rank)`; Task 3 explicitly excluded verify-gate changes, so historical builder and downstream verifier are not yet aligned.
- Historical diagnostics now include `duplicate_universe_effective_rank_count` in addition to the brief's required ticker-duplicate count. No current tests outside this slice assert diagnostics shape, but downstream consumers should tolerate the extra field.

```


## FILE: src\data_analysts\universe.py
```
from __future__ import annotations

from typing import Any


def build_universe_memberships(
    security_panel: list[dict[str, Any]],
    universe_specs: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    memberships: dict[str, list[dict[str, Any]]] = {}
    for spec in universe_specs.get("universes", []):
        if spec.get("enabled", True) is False:
            continue
        universe_id = spec["universe_id"]
        candidates = [row for row in security_panel if _passes_filters(row, spec.get("filters", []))]
        candidates = _sort_candidates(candidates, spec.get("rank_by", []))
        limit = spec.get("limit")
        if isinstance(limit, int):
            candidates = candidates[:limit]
        memberships[universe_id] = [
            {
                "as_of_date": row["as_of_date"],
                "universe_id": universe_id,
                "ticker": row["ticker"],
                "rank": index + 1,
            }
            for index, row in enumerate(candidates)
        ]
    return memberships


def build_historical_universe_memberships(
    security_panel_history: list[dict[str, Any]],
    universe_specs: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    memberships: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, Any] = {}
    rows_by_as_of: dict[str, list[dict[str, Any]]] = {}
    for row in security_panel_history:
        effective_date = row.get("effective_date")
        if not effective_date:
            continue
        rows_by_as_of.setdefault(str(row["as_of_date"]), []).append(row)

    for spec in universe_specs.get("universes", []):
        if spec.get("enabled", True) is False:
            continue
        universe_id = spec["universe_id"]
        output: list[dict[str, Any]] = []
        included_counts: list[int] = []
        eligible_counts: list[int] = []
        duplicate_membership_count = 0
        duplicate_rank_count = 0
        seen_memberships: set[tuple[str, str, str]] = set()
        seen_ranks: set[tuple[str, str, int]] = set()
        for as_of_date in sorted(rows_by_as_of):
            candidates = [
                row for row in rows_by_as_of[as_of_date] if _passes_filters(row, spec.get("filters", []))
            ]
            eligible_counts.append(len(candidates))
            candidates = _sort_candidates(candidates, spec.get("rank_by", []))
            limit = spec.get("limit")
            if isinstance(limit, int):
                candidates = candidates[:limit]
            included_counts.append(len(candidates))
            for index, row in enumerate(candidates, start=1):
                effective_date = str(row["effective_date"])
                membership_key = (effective_date, universe_id, str(row["ticker"]))
                rank_key = (effective_date, universe_id, index)
                membership_seen = membership_key in seen_memberships
                rank_seen = rank_key in seen_ranks
                if membership_seen:
                    duplicate_membership_count += 1
                if rank_seen:
                    duplicate_rank_count += 1
                if membership_seen or rank_seen:
                    continue
                seen_memberships.add(membership_key)
                seen_ranks.add(rank_key)
                output.append(_membership_row(row, universe_id, index))
        memberships[universe_id] = output
        diagnostics[universe_id] = {
            "universe_id": universe_id,
            "as_of_date_count": len(rows_by_as_of),
            "candidate_count": sum(eligible_counts),
            "included_count": len(output),
            "excluded_count": sum(eligible_counts) - len(output),
            "top_n_limit": spec.get("limit"),
            "max_included_count": max(included_counts) if included_counts else 0,
            "duplicate_universe_effective_ticker_count": duplicate_membership_count,
            "duplicate_universe_effective_rank_count": duplicate_rank_count,
        }
    return memberships, diagnostics


def _passes_filters(row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
    for rule in filters:
        field = rule["field"]
        op = rule["op"]
        value = rule.get("value")
        row_value = row.get(field)
        if op == "eq" and row_value != value:
            return False
        if op == "gte" and (row_value is None or row_value < value):
            return False
        if op == "not_null" and row_value is None:
            return False
    return True


def _sort_candidates(rows: list[dict[str, Any]], rank_by: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = list(rows)
    for rule in reversed(rank_by):
        field = rule["field"]
        reverse = rule.get("direction") == "desc"
        sorted_rows.sort(key=lambda row: (row.get(field) is None, row.get(field)), reverse=reverse)
    return sorted_rows


def _membership_row(row: dict[str, Any], universe_id: str, rank: int) -> dict[str, Any]:
    return {
        "as_of_date": row["as_of_date"],
        "effective_date": row["effective_date"],
        "universe_id": universe_id,
        "ticker": row["ticker"],
        "rank": rank,
        "included": True,
        "reason": "selected",
        "market": row.get("market"),
        "security_type": row.get("security_type"),
        "listed": row.get("listed"),
        "tradable": row.get("tradable"),
        "close": row.get("close"),
        "adj_close": row.get("adj_close"),
        "market_cap": row.get("market_cap"),
        "adv20": row.get("adv20"),
        "data_cutoff_at": row.get("data_cutoff_at"),
    }

```


## FILE: tests\test_historical_universe.py
```
from data_analysts.universe import build_historical_universe_memberships


def test_historical_universe_uses_effective_date_and_year_safe_rows():
    specs = {
        "universes": [
            {
                "universe_id": "tw_equity_liquid_top2",
                "enabled": True,
                "source": "security_panel",
                "filters": [
                    {"field": "listed", "op": "eq", "value": True},
                    {"field": "tradable", "op": "eq", "value": True},
                    {"field": "market_cap", "op": "not_null"},
                ],
                "rank_by": [
                    {"field": "market_cap", "direction": "desc"},
                    {"field": "ticker", "direction": "asc"},
                ],
                "limit": 2,
            }
        ]
    }
    panel = [
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-03",
            "ticker": "2330",
            "listed": True,
            "tradable": True,
            "market_cap": 30,
            "market": "TWSE",
            "security_type": "common_stock",
        },
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-03",
            "ticker": "2317",
            "listed": True,
            "tradable": True,
            "market_cap": 20,
            "market": "TWSE",
            "security_type": "common_stock",
        },
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-03",
            "ticker": "9999",
            "listed": True,
            "tradable": True,
            "market_cap": 10,
            "market": "TPEX",
            "security_type": "common_stock",
        },
    ]

    memberships, diagnostics = build_historical_universe_memberships(panel, specs)

    rows = memberships["tw_equity_liquid_top2"]
    assert [(row["ticker"], row["rank"]) for row in rows] == [("2330", 1), ("2317", 2)]
    assert rows[0]["as_of_date"] == "2025-01-02"
    assert rows[0]["effective_date"] == "2025-01-03"
    assert rows[0]["included"] is True
    assert diagnostics["tw_equity_liquid_top2"]["top_n_limit"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["max_included_count"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_ticker_count"] == 0


def test_historical_universe_deduplicates_on_effective_date_keys():
    specs = {
        "universes": [
            {
                "universe_id": "tw_equity_liquid_top2",
                "enabled": True,
                "source": "security_panel",
                "filters": [
                    {"field": "listed", "op": "eq", "value": True},
                    {"field": "tradable", "op": "eq", "value": True},
                    {"field": "market_cap", "op": "not_null"},
                ],
                "rank_by": [
                    {"field": "market_cap", "direction": "desc"},
                    {"field": "ticker", "direction": "asc"},
                ],
                "limit": 2,
            }
        ]
    }
    panel = [
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-06",
            "ticker": "2330",
            "listed": True,
            "tradable": True,
            "market_cap": 30,
        },
        {
            "as_of_date": "2025-01-03",
            "effective_date": "2025-01-06",
            "ticker": "2330",
            "listed": True,
            "tradable": True,
            "market_cap": 30,
        },
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-06",
            "ticker": "2317",
            "listed": True,
            "tradable": True,
            "market_cap": 20,
        },
        {
            "as_of_date": "2025-01-03",
            "effective_date": "2025-01-06",
            "ticker": "2317",
            "listed": True,
            "tradable": True,
            "market_cap": 20,
        },
    ]

    memberships, diagnostics = build_historical_universe_memberships(panel, specs)

    rows = memberships["tw_equity_liquid_top2"]
    assert [(row["effective_date"], row["ticker"], row["rank"]) for row in rows] == [
        ("2025-01-06", "2330", 1),
        ("2025-01-06", "2317", 2),
    ]
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_ticker_count"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_rank_count"] == 2

```

