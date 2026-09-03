# Historical Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace latest-only universe publishing with PIT-safe historical security panels and year-partitioned historical universe membership.

**Architecture:** Historical Universe consumes already-published DataAnalysts canonical inputs; it does not add new MongoDB source families. The flow is `trading_calendar + daily_price_volume + security_master + optional daily_tradability -> security_panel_history -> universe membership_by_year -> verify`. `as_of_date` is the observation date after close; `effective_date` is the next trading day and is the earliest date a downstream system may trade the membership.

**Tech Stack:** Python 3, standard library collections/datetime, `pyarrow` parquet, JSON configs/contracts, existing `ArtifactPublisher`, existing DataAnalysts CLI and verify gates.

## Global Constraints

- All generated and edited DataAnalysts artifacts must stay under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not use ALF main-flow modules as runtime adapters.
- Do not write raw/generated data artifacts to git.
- Fail closed on missing data, unsupported schema, forbidden source usage, ambiguous PIT dates, unresolved duplicate logical rows, invalid universe membership, and universe small-file regressions.
- Historical Universe must not add new raw source families.
- Historical Universe must consume `trading_calendar` where `date_rmk` blank after trimming means the date is a trading day.
- Universe membership must distinguish `as_of_date` from `effective_date`.
- A universe built with same-day close, market cap, volume, or ADV is effective no earlier than the next trading day.
- Historical universe membership must be year-partitioned, not one parquet file per day.
- Universe selectors may use only security panel fields; no alpha feature, realized return, strategy signal, portfolio weight, or external feature store field is allowed.
- Top-N universe row counts must be quantitatively checked.

---

## File Structure

- Modify `contracts/OUTPUT_CONTRACT.md`: define `security_panel_history`, historical `membership_by_year`, diagnostics, and latest convenience output boundaries.
- Modify `contracts/VERIFICATION_CONTRACT.md`: add historical universe checks for effective date, coverage, duplicate keys, top-N row counts, and small-file limits.
- Modify `contracts/CONFIG_CONTRACT.md`: add historical universe config fields and allowed selector fields `effective_date` and `source_max_date` semantics.
- Modify `configs/universe_specs.json`: add baseline universes and explicit historical selector metadata.
- Modify `src/data_analysts/config.py`: validate new universe fields and selector operators.
- Modify `src/data_analysts/security_panel.py`: keep existing latest `build_security_panel()` and add `build_historical_security_panel()`.
- Modify `src/data_analysts/universe.py`: keep existing latest `build_universe_memberships()` and add `build_historical_universe_memberships()`.
- Modify `src/data_analysts/pipeline.py`: publish `security_panel_history`, `membership_by_year`, universe diagnostics, and optional latest convenience artifacts.
- Modify `src/data_analysts/verify.py`: enforce historical universe manifest/artifact rules.
- Test `tests/test_historical_security_panel.py`: calendar/effective-date/security-panel behavior.
- Test `tests/test_historical_universe.py`: universe selection, ranking, year partitioning, top-N counts.
- Test `tests/test_historical_universe_pipeline.py`: pipeline publishing and manifest shape.
- Test `tests/test_historical_universe_verify.py`: fail-closed verification.

## Design Boundary

Historical Universe should be implemented in this slice. The following are out of scope:

- New MongoDB families.
- New factor/feature tables.
- Strategy, backtest, or performance feedback.
- Redesigning price adjustment or corporate action logic.
- Replacing the existing latest-only helper functions; keep them as compatibility helpers unless a task explicitly wraps them.

---

### Task 1: Historical Universe Contracts and Config

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\OUTPUT_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\VERIFICATION_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\CONFIG_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\configs\universe_specs.json`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\config.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_config.py`

**Interfaces:**
- Consumes: existing `RuntimeConfig.universe_specs`.
- Produces: validated universe specs with baseline universe ids and historical-safe selector fields.

- [ ] **Step 1: Write failing config tests**

Create `tests/test_historical_universe_config.py`:

```python
import json
from pathlib import Path

import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsRoot


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        (tmp_path / "configs" / name).write_text(
            (ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_universe_specs_define_baseline_historical_universes():
    payload = json.loads((ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8"))
    universe_ids = {item["universe_id"] for item in payload["universes"]}
    assert {
        "tw_equity_all_listed",
        "tw_common_stock_all",
        "tw_common_stock_tradable",
        "tw_equity_liquid_top100",
        "tw_equity_liquid_top300",
        "tw_equity_liquid_top500",
        "twse_common_stock",
        "tpex_common_stock",
    }.issubset(universe_ids)


def test_universe_config_allows_effective_date_but_rejects_realized_return(tmp_path):
    _copy_configs(tmp_path)
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["universes"][0]["filters"].append({"field": "effective_date", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    load_runtime_config(DataAnalystsRoot.from_path(tmp_path))

    payload["universes"][0]["filters"].append({"field": "realized_return_20d", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported field"):
        load_runtime_config(DataAnalystsRoot.from_path(tmp_path))
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py -q
```

Expected: FAIL because baseline universes and `effective_date` selector field are not yet configured.

- [ ] **Step 3: Update config validator**

Modify `src/data_analysts/config.py`:

```python
SECURITY_PANEL_FIELDS = {
    "as_of_date",
    "effective_date",
    "source_max_date",
    "ticker",
    "stock_name",
    "market",
    "security_type",
    "listed",
    "tradable",
    "close",
    "adj_close",
    "traded_value",
    "market_cap",
    "adv20",
    "data_cutoff_at",
}

SUPPORTED_UNIVERSE_OPERATORS = {"eq", "gte", "not_null"}
```

Then extend `_validate_universe_fields()` so every filter has a supported `op`:

```python
def _validate_universe_fields(universe_id: str, universe: dict[str, Any]) -> None:
    for filter_rule in universe.get("filters", []):
        field = filter_rule.get("field") if isinstance(filter_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
        op = filter_rule.get("op") if isinstance(filter_rule, dict) else None
        if op not in SUPPORTED_UNIVERSE_OPERATORS:
            raise ConfigError(f"universe {universe_id} uses unsupported operator: {op}")
    for rank_rule in universe.get("rank_by", []):
        field = rank_rule.get("field") if isinstance(rank_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
```

- [ ] **Step 4: Expand universe specs**

Modify `configs/universe_specs.json` to include exactly these enabled universes:

```json
{
  "schema_version": "1.0",
  "universes": [
    {
      "universe_id": "tw_equity_all_listed",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_common_stock_all",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_common_stock_tradable",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_equity_liquid_top100",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 100
    },
    {
      "universe_id": "tw_equity_liquid_top300",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 300
    },
    {
      "universe_id": "tw_equity_liquid_top500",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 500
    },
    {
      "universe_id": "twse_common_stock",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market", "op": "eq", "value": "TWSE"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tpex_common_stock",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market", "op": "eq", "value": "TPEX"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    }
  ]
}
```

- [ ] **Step 5: Update contracts**

In `OUTPUT_CONTRACT.md`, add historical paths:

```text
runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/diagnostics.parquet
```

State that `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` is a latest-date convenience output only, not the historical canonical surface.

In `VERIFICATION_CONTRACT.md`, add hard checks:

```text
effective_date > as_of_date
duplicate (effective_date, universe_id, ticker) count == 0
duplicate (effective_date, universe_id, rank) count == 0
small_file_daily_partition_count == 0
top-N row_count <= N for every effective_date
top-N row_count == N when eligible_count >= N
```

- [ ] **Step 6: Run config tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py tests\test_pit_foundation_config.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add configs\universe_specs.json contracts\OUTPUT_CONTRACT.md contracts\VERIFICATION_CONTRACT.md contracts\CONFIG_CONTRACT.md src\data_analysts\config.py tests\test_historical_universe_config.py
git commit -m "spec: define historical universe contracts"
```

---

### Task 2: Historical Security Panel Builder

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`

**Interfaces:**
- Consumes:
  - `daily_prices: list[dict[str, Any]]`
  - `security_master: list[dict[str, Any]]`
  - `trading_calendar: list[dict[str, Any]]`
  - `daily_tradability: list[dict[str, Any]] | None`
- Produces:
  - `build_historical_security_panel(...) -> tuple[list[dict[str, Any]], dict[str, Any]]`
  - Rows keyed by `(as_of_date, ticker)`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_historical_security_panel.py`:

```python
from data_analysts.security_panel import build_historical_security_panel


def test_historical_security_panel_uses_next_trading_day_as_effective_date():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {"date": "2025-01-02", "ticker": "2330", "close": 100, "adj_close": 100, "volume": 10, "traded_value": 1000, "market_cap": 10000, "data_cutoff_at": "2025-01-02T00:00:00Z"},
            {"date": "2025-01-03", "ticker": "2330", "close": 101, "adj_close": 101, "volume": 11, "traded_value": 1111, "market_cap": 10100, "data_cutoff_at": "2025-01-03T00:00:00Z"},
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True, "security_type": "common_stock", "data_cutoff_at": "2025-01-01T00:00:00Z"}
        ],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["effective_date"] == "2025-01-03"
    assert by_date["2025-01-03"]["effective_date"] == "2025-01-06"
    assert by_date["2025-01-03"]["source_max_date"] == "2025-01-03"
    assert diag["as_of_date_count"] == 2
    assert diag["duplicate_as_of_ticker_count"] == 0


def test_historical_security_panel_adv20_uses_only_past_and_current_values():
    rows, _ = build_historical_security_panel(
        daily_prices=[
            {"date": "2025-01-02", "ticker": "2330", "close": 100, "adj_close": 100, "volume": 1, "traded_value": 10, "market_cap": 10000},
            {"date": "2025-01-03", "ticker": "2330", "close": 101, "adj_close": 101, "volume": 1, "traded_value": 30, "market_cap": 10100},
        ],
        security_master=[{"ticker": "2330", "market": "TWSE", "listed": True, "security_type": "common_stock"}],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["adv20"] == 10
    assert by_date["2025-01-03"]["adv20"] == 20
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q
```

Expected: FAIL because `build_historical_security_panel` does not exist.

- [ ] **Step 3: Implement helper functions**

Add to `src/data_analysts/security_panel.py`:

```python
def build_historical_security_panel(
    daily_prices: list[dict[str, Any]],
    security_master: list[dict[str, Any]],
    trading_calendar: list[dict[str, Any]],
    daily_tradability: list[dict[str, Any]] | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trading_dates = _trading_dates(trading_calendar, start_date=start_date, end_date=end_date)
    next_trading_date = _next_trading_date_map(trading_calendar)
    master_by_ticker = {row["ticker"]: row for row in security_master if row.get("ticker")}
    tradability_by_key = {
        (str(row.get("date")), str(row.get("ticker"))): row
        for row in daily_tradability or []
        if row.get("date") and row.get("ticker")
    }
    price_by_key = {
        (str(row.get("date")), str(row.get("ticker"))): row
        for row in daily_prices
        if row.get("date") and row.get("ticker")
    }
    tickers = sorted({key[1] for key in price_by_key} | set(master_by_ticker))
    adv_by_key = _rolling_adv20(daily_prices)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    duplicate_count = 0
    missing_effective_count = 0
    for as_of_date in trading_dates:
        effective_date = next_trading_date.get(as_of_date)
        if effective_date is None:
            missing_effective_count += len(tickers)
        for ticker in tickers:
            price = price_by_key.get((as_of_date, ticker))
            if price is None:
                continue
            key = (as_of_date, ticker)
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            master = master_by_ticker.get(ticker, {})
            tradability = tradability_by_key.get((as_of_date, ticker), {})
            volume = float(price.get("volume") or 0.0)
            listed = bool(master.get("listed", True))
            row = {
                "as_of_date": as_of_date,
                "effective_date": effective_date,
                "source_max_date": price.get("date"),
                "ticker": ticker,
                "stock_name": master.get("stock_name"),
                "market": master.get("market"),
                "security_type": _security_type(master),
                "listed": listed,
                "tradable": bool(tradability.get("tradable", listed and volume > 0)),
                "close": price.get("close"),
                "adj_close": price.get("adj_close"),
                "traded_value": float(price.get("traded_value") or 0.0),
                "market_cap": price.get("market_cap"),
                "adv20": adv_by_key.get((as_of_date, ticker)),
                "data_cutoff_at": price.get("data_cutoff_at") or master.get("data_cutoff_at"),
            }
            rows.append(row)
    diagnostics = {
        "as_of_date_count": len(trading_dates),
        "panel_row_count": len(rows),
        "duplicate_as_of_ticker_count": duplicate_count,
        "effective_date_null_count": missing_effective_count,
        "date_min": min(trading_dates) if trading_dates else None,
        "date_max": max(trading_dates) if trading_dates else None,
    }
    return rows, diagnostics
```

Also add `_trading_dates()`, `_next_trading_date_map()`, and `_rolling_adv20()` with deterministic sorted-date behavior.

- [ ] **Step 4: Run tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src\data_analysts\security_panel.py tests\test_historical_security_panel.py
git commit -m "feat: build historical security panel"
```

---

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

---

### Task 4: Pipeline Publishing and Year Partitions

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\pipeline.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_pipeline.py`

**Interfaces:**
- Consumes:
  - existing `run_pipeline(...)`
  - `build_historical_security_panel(...)`
  - `build_historical_universe_memberships(...)`
- Produces:
  - `runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet`
  - `runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet`
  - `runtime/data_canonical/derived/universes/<universe_id>/diagnostics/diagnostics.parquet`

- [ ] **Step 1: Write failing pipeline tests**

Create `tests/test_historical_universe_pipeline.py` with fixture configs and run `run_pipeline()` using fixture rows for `daily_price_volume`, `security_master`, and `trading_calendar`. Assert:

```python
assert (tmp_path / "runtime/data_canonical/derived/security_panel_history/as_of_year=2025/part.parquet").exists()
assert (tmp_path / "runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet").exists()
assert not list((tmp_path / "runtime/data_canonical/derived/universes/tw_equity_liquid_top500").glob("membership_by_date/as_of_date=*/membership.parquet"))
```

Also assert manifest fields:

```python
manifest = json.loads((tmp_path / "runtime/manifests/universe_tw_equity_liquid_top500.json").read_text(encoding="utf-8"))
assert manifest["partitioning"] == ["as_of_year"]
assert manifest["pit_policy"] == "effective_next_trading_day_membership"
assert manifest["date_range"] == ["2025-01-02", "2025-01-03"]
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q
```

Expected: FAIL because the pipeline only publishes latest `security_panel` and `membership_by_date`.

- [ ] **Step 3: Add historical publish branch**

Modify `run_pipeline()` after `daily_prices` are built:

```python
trading_calendar_rows = family_rows.get("trading_calendar", [])
daily_tradability_rows = family_rows.get("daily_tradability", [])
security_panel_history, security_panel_diagnostics = ([], {})
if daily_prices and security_master and trading_calendar_rows:
    security_panel_history, security_panel_diagnostics = build_historical_security_panel(
        daily_prices,
        security_master,
        trading_calendar_rows,
        daily_tradability_rows,
        start_date=start_date,
        end_date=end_date,
    )
    if security_panel_history:
        _publish_dataset(
            root,
            publisher,
            "security_panel_history",
            "derived",
            security_panel_history,
            "runtime/data_canonical/derived/security_panel_history",
            ["as_of_date", "effective_date", "source_max_date", "ticker", "tradable", "adj_close", "market_cap", "adv20", "data_cutoff_at"],
            date_field="as_of_date",
            partition_name="as_of_year",
            pit_policy="effective_next_trading_day_panel",
        )
        write_diagnostic(root, "historical_universe/security_panel_history", security_panel_diagnostics)
```

Then publish universes:

```python
if security_panel_history:
    historical_memberships, universe_diagnostics = build_historical_universe_memberships(
        security_panel_history,
        config.universe_specs,
    )
    for universe_id, rows in historical_memberships.items():
        if not rows:
            continue
        _publish_dataset(
            root,
            publisher,
            f"universe_{universe_id}",
            "derived",
            rows,
            f"runtime/data_canonical/derived/universes/{universe_id}/membership_by_year",
            ["as_of_date", "effective_date", "universe_id", "ticker", "rank", "included", "reason"],
            date_field="as_of_date",
            availability_field="effective_date",
            partition_name="as_of_year",
            pit_policy="effective_next_trading_day_membership",
        )
        diagnostic_rows = [universe_diagnostics[universe_id]]
        publisher.publish_parquet(
            f"runtime/data_canonical/derived/universes/{universe_id}/diagnostics/diagnostics.parquet",
            rows=diagnostic_rows,
            required_columns=["universe_id", "candidate_count", "included_count", "excluded_count"],
        )
```

Keep existing latest `membership_by_date` only when `as_of_date` is explicitly provided and `security_panel_history` is empty. Do not publish one file per historical date during range backfill.

- [ ] **Step 4: Run pipeline tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py tests\test_raw_family_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src\data_analysts\pipeline.py tests\test_historical_universe_pipeline.py
git commit -m "feat: publish historical universes by year"
```

---

### Task 5: Historical Universe Verification

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\verify.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_verify.py`

**Interfaces:**
- Consumes: universe manifests and parquet artifacts.
- Produces: fail-closed `blocked_step="historical_universe"` when historical universe rules fail.

- [ ] **Step 1: Write failing verification tests**

Create `tests/test_historical_universe_verify.py`:

```python
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.paths import DataAnalystsRoot
from data_analysts.verify import verify_runtime


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in ["mongodb_sources.json", "source_family_profiles.json", "universe_specs.json", "source_catalog.json", "pit_registry.json"]:
        (tmp_path / "configs" / name).write_text((ROOT / "configs" / name).read_text(encoding="utf-8"), encoding="utf-8")


def _write_universe_artifact(tmp_path: Path, rows: list[dict[str, object]]) -> None:
    artifact = tmp_path / "runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact)
    manifests = tmp_path / "runtime/manifests"
    manifests.mkdir(parents=True)
    (manifests / "universe_tw_equity_liquid_top500.json").write_text(json.dumps({
        "artifact_id": "universe_tw_equity_liquid_top500",
        "schema_version": "1.0",
        "layer": "derived",
        "source_families": ["security_panel_history"],
        "source_collections": [],
        "row_count": len(rows),
        "date_range": ["2025-01-02", "2025-01-02"],
        "availability_date_range": ["2025-01-03", "2025-01-03"],
        "columns": list(rows[0].keys()),
        "partitioning": ["as_of_year"],
        "artifact_paths": ["runtime/data_canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"],
        "pit_policy": "effective_next_trading_day_membership",
        "data_cutoff_at": "2025-01-02T00:00:00Z",
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": "2026-07-07T00:00:00Z"
    }), encoding="utf-8")


def test_verify_blocks_historical_universe_same_day_effective_date(tmp_path):
    _copy_configs(tmp_path)
    _write_universe_artifact(tmp_path, [{
        "as_of_date": "2025-01-02",
        "effective_date": "2025-01-02",
        "universe_id": "tw_equity_liquid_top500",
        "ticker": "2330",
        "rank": 1,
        "included": True,
        "reason": "selected",
    }])
    result = verify_runtime(DataAnalystsRoot.from_path(tmp_path))
    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "effective_date" in result["message"]
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_verify.py -q
```

Expected: FAIL because verify does not enforce historical effective-date rules.

- [ ] **Step 3: Implement verification**

Modify `_check_universe_manifest()` in `verify.py`:

```python
def _check_universe_manifest(root: DataAnalystsRoot, manifest: dict[str, Any]) -> str | None:
    artifact_id = manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("universe_"):
        return None
    is_historical = manifest.get("partitioning") == ["as_of_year"] or manifest.get("pit_policy") == "effective_next_trading_day_membership"
    for artifact_path in manifest.get("artifact_paths", []):
        rows = pq.ParquetFile(root.resolve_output(artifact_path)).read().to_pylist()
        if is_historical:
            error = _check_historical_universe_rows(artifact_id, rows)
            if error:
                return error
        else:
            error = _check_latest_universe_rows(rows)
            if error:
                return error
    return None
```

Add:

```python
def _check_historical_universe_rows(artifact_id: str, rows: list[dict[str, Any]]) -> str | None:
    required = {"as_of_date", "effective_date", "universe_id", "ticker", "rank"}
    seen_memberships: set[tuple[Any, Any, Any]] = set()
    seen_ranks: set[tuple[Any, Any, Any]] = set()
    for row in rows:
        missing = [field for field in required if row.get(field) in {None, ""}]
        if missing:
            return f"historical universe {artifact_id} missing required fields: {', '.join(missing)}"
        if str(row["effective_date"]) <= str(row["as_of_date"]):
            return f"historical universe {artifact_id} has effective_date <= as_of_date"
        membership_key = (row.get("effective_date"), row.get("universe_id"), row.get("ticker"))
        if membership_key in seen_memberships:
            return "duplicate historical universe effective membership key"
        seen_memberships.add(membership_key)
        rank_key = (row.get("effective_date"), row.get("universe_id"), row.get("rank"))
        if rank_key in seen_ranks:
            return "duplicate historical universe rank"
        seen_ranks.add(rank_key)
    return None
```

In `verify_runtime()`, when `_check_universe_manifest()` returns a string for a historical manifest, block with `blocked_step="historical_universe"` instead of `"universe"`.

- [ ] **Step 4: Add small-file and top-N diagnostics check**

Extend historical universe verification to count paths matching `membership_by_date/as_of_date=` under historical manifests. If any exist during a historical range run, block:

```text
small_file_daily_partition_count > 0
```

Use universe specs to check top-N manifests:

```text
row_count_by_effective_date <= limit
```

For `eligible_count >= limit`, enforce included count equals limit through diagnostics parquet if present.

- [ ] **Step 5: Run verification tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_verify.py tests\test_raw_family_verify.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src\data_analysts\verify.py tests\test_historical_universe_verify.py
git commit -m "test: verify historical universe gates"
```

---

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

---

### Task 7: Real Data Smoke and Final Verification

**Files:**
- Modify only if verification exposes a real defect:
  - `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
  - `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\universe.py`
  - `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\pipeline.py`
  - `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\verify.py`
- Runtime output: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\runs\real_all_products`

**Interfaces:**
- Consumes: real MongoDB via existing localhost default URI and DataAnalysts configs.
- Produces: historical security panel, historical universes, diagnostics, and verification result.

- [ ] **Step 1: Run full tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run bounded real smoke**

Use a short but multi-day period with at least two trading days:

```powershell
$env:PYTHONPATH='src'; python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31
```

Expected:

```text
ready
```

If current CLI family names differ for price/master, inspect `configs/source_family_profiles.json` and use the exact enabled ids. Do not add ad hoc source names in code.

- [ ] **Step 3: Verify historical artifacts quantitatively**

Run:

```powershell
$env:PYTHONPATH='src'; python -m data_analysts.cli verify --root runs\real_all_products
```

Expected:

```text
ready
```

Run a compact artifact check:

```powershell
@'
from pathlib import Path
import pyarrow.parquet as pq

root = Path("runs/real_all_products/runtime/data_canonical/derived/universes")
files = list(root.glob("*/membership_by_year/as_of_year=*/part.parquet"))
rows = 0
bad_effective = 0
for path in files:
    table = pq.read_table(path, columns=["as_of_date", "effective_date", "universe_id", "ticker", "rank"])
    for row in table.to_pylist():
        rows += 1
        bad_effective += int(str(row["effective_date"]) <= str(row["as_of_date"]))
print({"files": len(files), "rows": rows, "bad_effective": bad_effective})
'@ | python -
```

Expected:

```text
bad_effective == 0
files <= universe_count * year_count
```

- [ ] **Step 4: Run inspect**

```powershell
$env:PYTHONPATH='src'; python -m data_analysts.cli inspect-artifacts --root runs\real_all_products
```

Expected:

```text
historical_universe.status == ready
small_file_daily_partition_count == 0
```

- [ ] **Step 5: Update progress ledger**

Create or update:

```text
plans/sdd/historical-universe/progress.md
```

Record each task result, test command, verify command, real smoke source families, and whether every subagent was closed.

- [ ] **Step 6: Final review**

Dispatch a read-only final reviewer with these exact questions:

```text
1. Does Historical Universe preserve as_of_date/effective_date semantics without same-day trading leakage?
2. Are historical memberships year-partitioned, not one parquet per day?
3. Are top-N row counts and duplicate keys verified quantitatively?
4. Did the implementation stay fully inside DataAnalysts and avoid ALF main-flow adapters?
```

Fix any Critical/Important findings and re-review until clean.

- [ ] **Step 7: Commit**

```powershell
git add src\data_analysts tests contracts configs README.md plans\sdd\historical-universe\progress.md
git commit -m "feat: add historical universe publishing"
```

---

## Completion Evidence

Historical Universe is complete only when all of the following are true:

- `security_panel_history` exists and is partitioned by `as_of_year`.
- Every historical panel row has `as_of_date`; every universe membership row has `effective_date`.
- `effective_date > as_of_date` for every membership row.
- Membership is published under `membership_by_year/as_of_year=YYYY/part.parquet`.
- No historical range run creates daily parquet files under `membership_by_date/as_of_date=...`.
- Baseline universes include at least:
  - `tw_equity_all_listed`
  - `tw_common_stock_all`
  - `tw_common_stock_tradable`
  - `tw_equity_liquid_top100`
  - `tw_equity_liquid_top300`
  - `tw_equity_liquid_top500`
  - `twse_common_stock`
  - `tpex_common_stock`
- Duplicate `(effective_date, universe_id, ticker)` count is zero.
- Duplicate `(effective_date, universe_id, rank)` count is zero.
- Top-N universes never exceed N rows per effective date.
- Top-N universes have exactly N rows when eligible count is at least N.
- `python -m pytest -q` passes.
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --root runs\real_all_products` returns `ready`.
- `inspect-artifacts` reports historical universe status ready and `small_file_daily_partition_count == 0`.

## Self-Review Notes

- Scope coverage: this plan covers contracts/config, historical security panel, historical membership, pipeline publish, verify, inspect/docs, and real smoke.
- Boundary check: no task adds new MongoDB source families or strategy/feature logic.
- Leakage check: every membership uses `effective_date`, and verify blocks `effective_date <= as_of_date`.
- Small-file check: canonical historical universe output is year-partitioned; latest daily output is explicitly convenience-only.
- Quantitative checks: duplicate counts, top-N row counts, effective-date violations, coverage, and file-count checks are all required verification surfaces.
