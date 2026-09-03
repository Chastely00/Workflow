# Task 4 Review Package

Rebuilt after lineage fix.

## FILE: plans\sdd\historical-universe\task-4-brief.md
```
# Task 4 Brief

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


```


## FILE: plans\sdd\historical-universe\task-4-report.md
```
STATUS: GREEN

changed files
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\pipeline.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_pipeline.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-4-report.md`

RED test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q`
- Result: FAIL with `assert security_panel_path.exists()` because the pipeline only published latest `security_panel` / `membership_by_date`, not historical year-partition outputs.

GREEN test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py tests\test_raw_family_pipeline.py -q`
- Result: PASS, `6 passed in 0.62s`

self-review
- Followed the brief's TDD order: added the historical pipeline test first, captured RED, then implemented the minimum publishing changes for GREEN.
- Historical `security_panel_history` now publishes to `runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet` with `effective_date` as availability range and a diagnostic JSON written under `historical_universe/security_panel_history`.
- Historical universe memberships now publish to `runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet` plus `diagnostics/diagnostics.parquet`; range backfill no longer emits `membership_by_date/as_of_date=*/membership.parquet`.
- Kept latest `membership_by_date` publishing only for explicit `as_of_date` runs when no historical history was produced, so existing latest-only path remains available without reintroducing one-day-per-file backfill output.
- To preserve next-trading-day PIT semantics for the range end, the historical branch rebuilds a calendar view without the `end_date` cap for `build_historical_security_panel()` only; raw `trading_calendar` publishing still uses the originally requested range.
- Scope stayed inside the allowed `DataAnalysts` files only; verify/inspect/docs gates were not changed.

concerns
- `src/data_analysts/verify.py` still validates universe uniqueness on `(as_of_date, universe_id, ticker/rank)` and does not yet know about `membership_by_year` historical publishing. The brief explicitly deferred verify-gate updates to a later task.
- Latest `security_panel` publishing still runs alongside the new historical branch. This preserves current consumers, but downstream tasks should decide whether range backfills should continue emitting that latest-only convenience artifact.

---

STATUS: GREEN

fix summary
- Root cause: `_publish_dataset()` hardcoded `source_families=[artifact_id]`, so any historical manifest published through the helper recorded itself as its own lineage.
- Fix: added optional `_publish_dataset(..., source_families=...)` with default fallback to `[artifact_id]` to preserve existing behavior.
- Historical `security_panel_history` manifest now publishes canonical upstream lineage:
  - `daily_price_volume`
  - `security_master`
  - `trading_calendar`
  - `daily_tradability`
- Historical universe membership manifests now publish `source_families=["security_panel_history"]`.
- Added regression assertions in `tests/test_historical_universe_pipeline.py` for both historical manifest lineage cases.

verification
- RED: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q`
  - failed at `assert manifest["source_families"] == ["security_panel_history"]`
- GREEN: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py tests\test_raw_family_pipeline.py -q`
  - passed: `6 passed in 0.66s`

concerns
- This change intentionally leaves non-historical `_publish_dataset()` call sites on the default lineage path; only the two historical call sites override `source_families`.

```


## FILE: src\data_analysts\pipeline.py
```
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import math
import re
from typing import Any

from data_analysts.adjusted_prices import build_adjusted_daily_prices
from data_analysts.artifacts import ArtifactPublisher
from data_analysts.config import RuntimeConfig
from data_analysts.diagnostics import write_diagnostic
from data_analysts.events import (
    build_capital_action_events,
    build_corporate_actions,
    build_dividend_events,
)
from data_analysts.extract import DatabaseLike, ExtractError, extract_family_rows_from_database, open_mongo_databases
from data_analysts.paths import DataAnalystsRoot
from data_analysts.raw_families import normalize_raw_family
from data_analysts.security_panel import build_historical_security_panel, build_security_panel
from data_analysts.universe import build_historical_universe_memberships, build_universe_memberships


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


def run_pipeline(
    root: DataAnalystsRoot,
    config: RuntimeConfig,
    *,
    families: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
    mongo_databases: dict[str, DatabaseLike] | None = None,
    allow_full_history: bool = False,
) -> dict[str, Any]:
    publisher = ArtifactPublisher(root)
    if as_of_date:
        start_date = start_date or as_of_date
        end_date = end_date or as_of_date
    family_rows = _rows_by_family(config, families, start_date, end_date, mongo_databases, allow_full_history)

    raw_expansion_ids = RAW_EXPANSION_FAMILIES.intersection(family_rows)
    for family_id in sorted(raw_expansion_ids):
        normalized = normalize_raw_family(
            family_id,
            family_rows[family_id],
            config.pit_registry,
            decision_dates=_decision_dates(start_date=start_date, end_date=end_date, as_of_date=as_of_date),
        )
        source_collections = sorted(
            {str(row.get("source_collection")) for row in family_rows[family_id] if row.get("source_collection")}
        )
        normalized["diagnostics"]["source_collection_count"] = len(source_collections)  # type: ignore[index]
        normalized["diagnostics"]["source_collections"] = source_collections[:200]  # type: ignore[index]
        normalized["diagnostics"]["source_collection_sample_truncated"] = len(source_collections) > 200  # type: ignore[index]
        _publish_raw_family_outputs(root, publisher, family_id, normalized)

    security_master = family_rows.get("security_master", [])
    if security_master:
        _publish_dataset(
            root,
            publisher,
            "security_master",
            "raw",
            security_master,
            "runtime/data_canonical/raw/security_master",
            ["ticker", "data_cutoff_at"],
            date_field=None,
            partition_name=None,
            pit_policy="snapshot_cutoff",
        )

    dividend_events = _filter_event_rows_by_date(
        build_dividend_events(family_rows.get("dividend_policy", [])),
        start_date=start_date,
        end_date=end_date,
    )
    if dividend_events:
        _publish_dataset(
            root,
            publisher,
            "dividend_events",
            "derived",
            dividend_events,
            "runtime/data_canonical/derived/events/dividend_events",
            ["event_date", "ex_date", "ticker", "cash_dividend_per_share", "stock_dividend_ratio", "source_dataset_id", "source_row_id", "data_cutoff_at"],
            date_field="event_date",
            partition_name="event_year",
            pit_policy="event_date",
        )

    capital_action_events = _filter_event_rows_by_date(
        build_capital_action_events(family_rows.get("capital_formation", [])),
        start_date=start_date,
        end_date=end_date,
    )
    if capital_action_events:
        _publish_dataset(
            root,
            publisher,
            "capital_action_events",
            "derived",
            capital_action_events,
            "runtime/data_canonical/derived/events/capital_action_events",
            ["event_date", "ex_date", "ticker", "action_type", "share_multiplier", "cash_return_per_share", "source_dataset_id", "source_row_id", "data_cutoff_at"],
            date_field="event_date",
            partition_name="event_year",
            pit_policy="event_date",
        )

    daily_prices = build_adjusted_daily_prices(
        family_rows.get("daily_price_volume", []),
        dividend_events=dividend_events,
        capital_action_events=capital_action_events,
    )
    if daily_prices:
        _publish_dataset(
            root,
            publisher,
            "daily_price_volume",
            "raw",
            daily_prices,
            "runtime/data_canonical/raw/daily_price_volume",
            ["date", "ticker", "open", "high", "low", "close", "volume", "traded_value", "adj_factor", "adj_close", "data_cutoff_at"],
            date_field="date",
            partition_name="year",
            pit_policy="source_date_lagged_to_decision_date",
        )

    trading_calendar_rows = family_rows.get("trading_calendar", [])
    daily_tradability_rows = family_rows.get("daily_tradability", [])
    security_panel_history: list[dict[str, Any]] = []
    if daily_prices and security_master and trading_calendar_rows:
        panel_trading_calendar_rows = trading_calendar_rows
        if end_date:
            panel_trading_calendar_rows = _rows_by_family(
                config,
                {"trading_calendar"},
                start_date,
                None,
                mongo_databases,
                allow_full_history,
            ).get("trading_calendar", trading_calendar_rows)
        security_panel_history, security_panel_diagnostics = build_historical_security_panel(
            daily_prices,
            security_master,
            panel_trading_calendar_rows,
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
                [
                    "as_of_date",
                    "effective_date",
                    "source_max_date",
                    "ticker",
                    "tradable",
                    "adj_close",
                    "market_cap",
                    "adv20",
                    "data_cutoff_at",
                ],
                date_field="as_of_date",
                partition_name="as_of_year",
                pit_policy="effective_next_trading_day_panel",
                availability_field="effective_date",
                source_families=[
                    "daily_price_volume",
                    "security_master",
                    "trading_calendar",
                    "daily_tradability",
                ],
            )
            write_diagnostic(root, "historical_universe/security_panel_history", security_panel_diagnostics)

    corporate_actions = build_corporate_actions(dividend_events, capital_action_events)
    if corporate_actions:
        _publish_dataset(
            root,
            publisher,
            "corporate_actions",
            "raw",
            corporate_actions,
            "runtime/data_canonical/raw/corporate_actions",
            ["event_date", "ticker", "action_type", "cash_amount", "share_multiplier", "source_dataset_id", "source_row_id", "data_cutoff_at"],
            date_field="event_date",
            partition_name="year",
            pit_policy="event_date",
        )

    effective_as_of_date, panel = build_security_panel(daily_prices, security_master, as_of_date=as_of_date)
    if panel:
        panel_path = f"runtime/data_canonical/derived/security_panel/as_of_date={effective_as_of_date}/security_panel.parquet"
        publisher.publish_parquet(
            panel_path,
            rows=panel,
            required_columns=["as_of_date", "source_max_date", "ticker", "tradable", "adj_close", "market_cap", "adv20", "data_cutoff_at"],
        )
        publisher.publish_manifest(
            artifact_id="security_panel",
            layer="derived",
            source_families=["daily_price_volume", "security_master"],
            source_collections=[],
            columns=list(panel[0].keys()),
            artifact_paths=[panel_path],
            row_count=len(panel),
            date_range=[effective_as_of_date, effective_as_of_date],
            availability_date_range=[effective_as_of_date, effective_as_of_date],
            partitioning=["as_of_date"],
            pit_policy="decision_date_panel",
            data_cutoff_at=_max_cutoff(panel),
            duplicate_count=0,
            omitted_row_count=0,
            status="ready",
        )

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
                source_families=["security_panel_history"],
            )
            publisher.publish_parquet(
                f"runtime/data_canonical/derived/universes/{universe_id}/diagnostics/diagnostics.parquet",
                rows=[universe_diagnostics[universe_id]],
                required_columns=["universe_id", "candidate_count", "included_count", "excluded_count"],
            )
    elif as_of_date:
        memberships = build_universe_memberships(panel, config.universe_specs)
        for universe_id, rows in memberships.items():
            if not rows:
                continue
            membership_path = f"runtime/data_canonical/derived/universes/{universe_id}/membership_by_date/as_of_date={effective_as_of_date}/membership.parquet"
            publisher.publish_parquet(
                membership_path,
                rows=rows,
                required_columns=["as_of_date", "universe_id", "ticker", "rank"],
            )
            publisher.publish_manifest(
                artifact_id=f"universe_{universe_id}",
                layer="derived",
                source_families=["security_panel"],
                source_collections=[],
                columns=["as_of_date", "universe_id", "ticker", "rank"],
                artifact_paths=[membership_path],
                row_count=len(rows),
                date_range=[effective_as_of_date, effective_as_of_date],
                availability_date_range=[effective_as_of_date, effective_as_of_date],
                partitioning=["as_of_date"],
                pit_policy="decision_date_membership",
                data_cutoff_at=_max_cutoff(panel),
                duplicate_count=0,
                omitted_row_count=0,
                status="ready",
            )

    result = {"status": "ready", "as_of_date": effective_as_of_date, "families": sorted(family_rows)}
    result_path = root.runtime_path("jobs", "pipeline_result.json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(__import__("json").dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def _decision_dates(*, start_date: str | None, end_date: str | None, as_of_date: str | None) -> list[str] | None:
    if as_of_date:
        return [as_of_date]
    if start_date and end_date and start_date == end_date:
        return [start_date]
    if start_date and end_date:
        return [end_date]
    return None


def publish_raw_family_outputs(
    root: DataAnalystsRoot,
    publisher: ArtifactPublisher,
    family_id: str,
    normalized: dict[str, object],
) -> list[str]:
    return _publish_raw_family_outputs(root, publisher, family_id, normalized)


def _publish_raw_family_outputs(
    root: DataAnalystsRoot,
    publisher: ArtifactPublisher,
    family_id: str,
    normalized: dict[str, object],
) -> list[str]:
    raw_rows = list(normalized["raw_rows"])  # type: ignore[arg-type]
    selected_rows = list(normalized.get("selected_rows") or [])
    diagnostics = dict(normalized["diagnostics"])  # type: ignore[arg-type]
    published: list[str] = []
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
        published.append(family_id)
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
            availability_field="source_available_date",
            partition_name="decision_year",
            pit_policy="selected_pit_decision_date",
        )
        published.append(selected_family_id)
    return published


def _raw_output_contract(family_id: str) -> tuple[str | None, str | None, str, list[str]]:
    if family_id == "trading_calendar":
        return None, None, "runtime/data_canonical/raw/trading_calendar", [
            "date",
            "market",
            "is_trading_day",
            "source_available_date",
            "data_cutoff_at",
        ]
    if family_id in {"daily_tradability", "daily_chip", "taiwan_index_futures_near_month"}:
        return "date", "year", f"runtime/data_canonical/raw/{family_id}", [
            "date",
            "source_available_date",
            "data_cutoff_at",
        ]
    if family_id == "financial_statement_raw":
        return "source_available_date", "available_year", "runtime/data_canonical/raw/financial_statement_raw", [
            "ticker",
            "no",
            "period_end_date",
            "source_available_date",
            "revision_date",
            "data_cutoff_at",
        ]
    if family_id == "self_reported_numbers_raw":
        return "source_available_date", "available_year", "runtime/data_canonical/raw/self_reported_numbers_raw", [
            "ticker",
            "key3",
            "period_end_date",
            "source_available_date",
            "revision_date",
            "data_cutoff_at",
        ]
    return "source_available_date", "available_year", f"runtime/data_canonical/raw/{family_id}", [
        "source_available_date",
        "data_cutoff_at",
    ]


def _rows_by_family(
    config: RuntimeConfig,
    families: set[str] | None,
    start_date: str | None,
    end_date: str | None,
    mongo_databases: dict[str, DatabaseLike] | None,
    allow_full_history: bool,
) -> dict[str, list[dict[str, Any]]]:
    if mongo_databases is None and _needs_mongo(config, families):
        mongo_databases = open_mongo_databases(config.mongodb_sources)

    output: dict[str, list[dict[str, Any]]] = {}
    for family in config.source_family_profiles.get("families", []):
        family_id = family["family_id"]
        if family.get("enabled", True) is False:
            continue
        if families and family_id not in families:
            continue
        rows = [
            _apply_field_map(family, row)
            for row in _rows_for_family(family, mongo_databases, start_date, end_date, allow_full_history)
        ]
        rows = [_normalize_source_row(family_id, row, index) for index, row in enumerate(rows)]
        rows = [_filter_date(row, start_date, end_date) for row in rows]
        output[family_id] = [row for row in rows if row is not None]
    return output


def _needs_mongo(config: RuntimeConfig, families: set[str] | None) -> bool:
    for family in config.source_family_profiles.get("families", []):
        if family.get("enabled", True) is False:
            continue
        if families and family["family_id"] not in families:
            continue
        if "fixture_rows" not in family:
            return True
    return False


def _rows_for_family(
    family: dict[str, Any],
    mongo_databases: dict[str, DatabaseLike] | None,
    start_date: str | None,
    end_date: str | None,
    allow_full_history: bool,
) -> list[dict[str, Any]]:
    if "fixture_rows" in family:
        return [dict(row) for row in family.get("fixture_rows", [])]

    if mongo_databases is None:
        raise ExtractError(f"{family['family_id']} has no fixture_rows and no MongoDB database")
    connection = family.get("connection")
    database = mongo_databases.get(connection)
    if database is None:
        raise ExtractError(f"missing MongoDB database for connection: {connection}")
    return extract_family_rows_from_database(
        database,
        family,
        start_date=start_date,
        end_date=end_date,
        allow_full_history=allow_full_history,
    )


def _normalize_source_row(family_id: str, row: dict[str, Any], index: int) -> dict[str, Any]:
    row.setdefault("source_dataset_id", family_id)
    row.setdefault("source_collection", f"fixture.{family_id}")
    row.setdefault("source_row_id", f"{family_id}:{index}")
    row.setdefault("data_cutoff_at", "1970-01-01T00:00:00Z")
    return row


def _apply_field_map(family: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    field_map = family.get("field_map")
    if not isinstance(field_map, dict) or not field_map:
        return dict(row)
    mapped = {
        key: row[key]
        for key in ["source_collection", "source_row_id", "source_dataset_id"]
        if key in row
    }
    for canonical_field, source_field in field_map.items():
        if source_field in row:
            mapped[canonical_field] = _normalize_source_value(row[source_field])
    return mapped


def _normalize_source_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value):
        return value[:10]
    return value


def _filter_date(row: dict[str, Any], start_date: str | None, end_date: str | None) -> dict[str, Any] | None:
    row_date = row.get("date") or row.get("event_date") or row.get("ex_date")
    if row_date is None:
        return row
    row_date = str(row_date)
    if start_date and row_date < start_date:
        return None
    if end_date and row_date > end_date:
        return None
    return row


def _filter_event_rows_by_date(
    rows: list[dict[str, Any]],
    *,
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        event_date = row.get("event_date")
        if event_date is None:
            output.append(row)
            continue
        event_date_text = str(event_date)
        if start_date and event_date_text < start_date:
            continue
        if end_date and event_date_text > end_date:
            continue
        output.append(row)
    return output


def _publish_dataset(
    root: DataAnalystsRoot,
    publisher: ArtifactPublisher,
    artifact_id: str,
    layer: str,
    rows: list[dict[str, Any]],
    base_path: str,
    required_columns: list[str],
    *,
    date_field: str | None,
    partition_name: str | None,
    pit_policy: str,
    availability_field: str | None = None,
    source_families: list[str] | None = None,
) -> None:
    partitions = _partition_rows(rows, date_field, partition_name)
    artifact_paths: list[str] = []
    for partition_value, partition_rows in partitions.items():
        if partition_name is None:
            path = f"{base_path}/{artifact_id}.parquet"
        else:
            path = f"{base_path}/{partition_name}={partition_value}/part.parquet"
        publisher.publish_parquet(path, rows=partition_rows, required_columns=required_columns)
        artifact_paths.append(path)

    dates = [str(row[date_field]) for row in rows if date_field and row.get(date_field)]
    availability_dates = [
        str(row[availability_field])
        for row in rows
        if availability_field and row.get(availability_field)
    ]
    publisher.publish_manifest(
        artifact_id=artifact_id,
        layer=layer,
        source_families=[artifact_id] if source_families is None else source_families,
        source_collections=sorted({str(row.get("source_collection")) for row in rows if row.get("source_collection")}),
        columns=list(rows[0].keys()),
        artifact_paths=artifact_paths,
        row_count=len(rows),
        date_range=[min(dates), max(dates)] if dates else None,
        availability_date_range=(
            [min(availability_dates), max(availability_dates)]
            if availability_dates
            else [min(dates), max(dates)] if dates else None
        ),
        partitioning=[partition_name] if partition_name else ["single_file"],
        pit_policy=pit_policy,
        data_cutoff_at=_max_cutoff(rows),
        duplicate_count=0,
        omitted_row_count=0,
        status="ready",
    )


def _partition_rows(
    rows: list[dict[str, Any]],
    date_field: str | None,
    partition_name: str | None,
) -> dict[str, list[dict[str, Any]]]:
    if partition_name is None or date_field is None:
        return {"single": rows}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[date_field])[:4]].append(row)
    return dict(grouped)


def _max_cutoff(rows: list[dict[str, Any]]) -> str:
    cutoffs = [str(row.get("data_cutoff_at")) for row in rows if row.get("data_cutoff_at")]
    return max(cutoffs) if cutoffs else "1970-01-01T00:00:00Z"

```


## FILE: tests\test_historical_universe_pipeline.py
```
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
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        payload = json.loads((source / name).read_text(encoding="utf-8"))
        if name == "source_family_profiles.json":
            payload["families"] = [
                {
                    "family_id": "daily_price_volume",
                    "enabled": True,
                    "connection": "apiprcd",
                    "collection_pattern": "{ticker}",
                    "source_profile": "large_daily_panel",
                    "primary_key": ["date", "ticker"],
                    "date_fields": {"source_date": "mdate"},
                    "availability": {"type": "same_day_after_close", "field": "mdate"},
                    "partitioning": ["year"],
                    "pit_policy": "source_date_lagged_to_decision_date",
                    "field_map": {
                        "date": "mdate",
                        "ticker": "coid",
                        "open": "open_d",
                        "high": "high_d",
                        "low": "low_d",
                        "close": "close_d",
                        "volume": "vol",
                        "traded_value": "amt",
                        "market_cap": "mktcap",
                        "data_cutoff_at": "data_cutoff_at",
                    },
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "mdate": "2025-01-02",
                            "open_d": 100,
                            "high_d": 101,
                            "low_d": 99,
                            "close_d": 100,
                            "vol": 10,
                            "amt": 20000000,
                            "mktcap": 500000000,
                            "data_cutoff_at": "2025-01-02T00:00:00Z",
                        },
                        {
                            "coid": "2330",
                            "mdate": "2025-01-03",
                            "open_d": 101,
                            "high_d": 102,
                            "low_d": 100,
                            "close_d": 101,
                            "vol": 11,
                            "amt": 22000000,
                            "mktcap": 510000000,
                            "data_cutoff_at": "2025-01-03T00:00:00Z",
                        },
                    ],
                },
                {
                    "family_id": "security_master",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "APISTOCK",
                    "source_profile": "small_snapshot",
                    "primary_key": ["ticker"],
                    "date_fields": {},
                    "availability": {"type": "snapshot_as_of_cutoff"},
                    "partitioning": ["single_file"],
                    "pit_policy": "snapshot_cutoff",
                    "field_map": {
                        "ticker": "coid",
                        "stock_name": "stk_name",
                        "market": "mkt",
                        "security_type": "stktp_e",
                        "data_cutoff_at": "data_cutoff_at",
                    },
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "stk_name": "TSMC",
                            "mkt": "TWSE",
                            "stktp_e": "common_stock",
                            "data_cutoff_at": "2025-01-01T00:00:00Z",
                        }
                    ],
                },
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
                        {
                            "zdate": "2025-01-02",
                            "mkt": "TWSE",
                            "date_rmk": "",
                            "date": "2025-01-02",
                            "market": "TWSE",
                            "is_trading_day": True,
                        },
                        {
                            "zdate": "2025-01-03",
                            "mkt": "TWSE",
                            "date_rmk": "",
                            "date": "2025-01-03",
                            "market": "TWSE",
                            "is_trading_day": True,
                        },
                        {
                            "zdate": "2025-01-06",
                            "mkt": "TWSE",
                            "date_rmk": "",
                            "date": "2025-01-06",
                            "market": "TWSE",
                            "is_trading_day": True,
                        },
                    ],
                },
            ]
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_pipeline_publishes_historical_universe_memberships_by_year(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)

    result = run_pipeline(
        root,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    assert result["status"] == "ready"

    security_panel_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "security_panel_history"
        / "as_of_year=2025"
        / "part.parquet"
    )
    membership_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "membership_by_year"
        / "as_of_year=2025"
        / "part.parquet"
    )
    assert security_panel_path.exists()
    assert membership_path.exists()
    assert not list(
        (
            tmp_path
            / "runtime"
            / "data_canonical"
            / "derived"
            / "universes"
            / "tw_equity_liquid_top500"
        ).glob("membership_by_date/as_of_date=*/membership.parquet")
    )

    membership_rows = pq.read_table(membership_path).to_pylist()
    assert {(row["as_of_date"], row["effective_date"], row["ticker"]) for row in membership_rows} == {
        ("2025-01-02", "2025-01-03", "2330"),
        ("2025-01-03", "2025-01-06", "2330"),
    }

    diagnostics_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "diagnostics"
        / "diagnostics.parquet"
    )
    assert diagnostics_path.exists()

    manifest = json.loads(
        (
            tmp_path
            / "runtime"
            / "manifests"
            / "universe_tw_equity_liquid_top500.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["partitioning"] == ["as_of_year"]
    assert manifest["pit_policy"] == "effective_next_trading_day_membership"
    assert manifest["date_range"] == ["2025-01-02", "2025-01-03"]
    assert manifest["source_families"] == ["security_panel_history"]

    security_panel_manifest = json.loads(
        (
            tmp_path
            / "runtime"
            / "manifests"
            / "security_panel_history.json"
        ).read_text(encoding="utf-8")
    )
    assert security_panel_manifest["source_families"] == [
        "daily_price_volume",
        "security_master",
        "trading_calendar",
        "daily_tradability",
    ]

```


## FILE: tests\test_raw_family_pipeline.py
```
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from data_analysts.artifacts import ArtifactPublisher
from data_analysts.config import load_runtime_config
from data_analysts.paths import DataAnalystsRoot
from data_analysts.pipeline import run_pipeline


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


def _write_configs(root: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = root / "configs"
    target.mkdir(parents=True)
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
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
                    "primary_key": [
                        "ticker",
                        "no",
                        "sem",
                        "curr",
                        "merg",
                        "period_end_date",
                        "source_available_date",
                        "revision_date",
                    ],
                    "date_fields": {"source_date": "key3"},
                    "availability": {"type": "source_available_date", "field": "key3"},
                    "partitioning": ["available_year"],
                    "pit_policy": "source_available_date",
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "no": "Q",
                            "sem": "2",
                            "curr": "TWD",
                            "merg": "Y",
                            "endd": "2025-06-30",
                            "key3": "2025-08-14",
                            "mdate": "2025-08-15",
                            "eps": 10,
                            "source_row_id": "a",
                        },
                        {
                            "coid": "2330",
                            "no": "Q",
                            "sem": "2",
                            "curr": "TWD",
                            "merg": "Y",
                            "endd": "2025-06-30",
                            "key3": "2025-08-14",
                            "mdate": "2025-08-20",
                            "eps": 11,
                            "source_row_id": "b",
                        },
                    ],
                },
            ]
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_pipeline_publishes_raw_family_artifacts_and_diagnostics(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)

    result = run_pipeline(
        root,
        config,
        families={"trading_calendar", "financial_statement_raw"},
        as_of_date="2025-08-31",
    )

    assert result["status"] == "ready"
    calendar_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "raw"
        / "trading_calendar"
        / "trading_calendar.parquet"
    )
    assert calendar_path.exists()
    calendar_rows = pq.read_table(calendar_path).to_pylist()
    assert calendar_rows[0]["is_trading_day"] is True

    raw_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "raw"
        / "financial_statement_raw"
        / "available_year=2025"
        / "part.parquet"
    )
    assert raw_path.exists()
    raw_rows = pq.read_table(raw_path).to_pylist()
    assert len(raw_rows) == 2

    selected_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "pit"
        / "financial_statement_pit_selected"
        / "decision_year=2025"
        / "part.parquet"
    )
    assert selected_path.exists()
    selected_rows = pq.read_table(selected_path).to_pylist()
    assert selected_rows[0]["eps"] == 11
    assert len(raw_rows) > len(selected_rows)
    selected_manifest = json.loads(
        (tmp_path / "runtime" / "manifests" / "financial_statement_pit_selected.json").read_text(
            encoding="utf-8"
        )
    )
    assert selected_manifest["date_range"] == ["2025-08-31", "2025-08-31"]
    assert selected_manifest["availability_date_range"] == ["2025-08-14", "2025-08-14"]

    diagnostic = json.loads(
        (
            tmp_path
            / "runs"
            / "real_all_products"
            / "diagnostics"
            / "raw_families"
            / "financial_statement_raw.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["source_row_count"] == 2
    assert diagnostic["unresolved_duplicate_count"] == 0


def test_artifact_publisher_normalizes_mixed_integer_scalars_before_parquet(tmp_path):
    root = DataAnalystsRoot.from_path(tmp_path)
    publisher = ArtifactPublisher(root)

    target = publisher.publish_parquet(
        "runtime/data_canonical/raw/monthly_sales/available_year=2025/part.parquet",
        rows=[
            {"ticker": "2330", "source_available_date": "2025-07-10", "d0005": 1, "raw_note": b"alpha"},
            {
                "ticker": "2317",
                "source_available_date": "2025-07-10",
                "d0005": np.int64(2),
                "raw_note": np.nan,
            },
            {
                "ticker": "2454",
                "source_available_date": "2025-07-10",
                "d0005": pd.Int64Dtype().type(3),
                "raw_note": bytearray(b"beta"),
            },
        ],
        required_columns=["ticker", "source_available_date", "d0005"],
    )

    rows = pq.read_table(target).to_pylist()
    assert [row["d0005"] for row in rows] == [1, 2, 3]
    assert [row["raw_note"] for row in rows] == [b"alpha", None, b"beta"]


def test_financial_statement_range_backfill_publishes_only_end_date_selected_snapshot(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)

    run_pipeline(
        root,
        config,
        families={"financial_statement_raw"},
        start_date="2025-08-01",
        end_date="2025-08-31",
    )

    raw_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "raw"
        / "financial_statement_raw"
        / "available_year=2025"
        / "part.parquet"
    )
    raw_rows = pq.read_table(raw_path).to_pylist()
    assert len(raw_rows) == 2
    assert {row["source_row_id"] for row in raw_rows} == {"a", "b"}

    selected_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "pit"
        / "financial_statement_pit_selected"
        / "decision_year=2025"
        / "part.parquet"
    )
    selected_rows = pq.read_table(selected_path).to_pylist()
    assert len(selected_rows) == 1
    assert selected_rows[0]["decision_date"] == "2025-08-31"
    assert selected_rows[0]["eps"] == 11


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
        "2330": FakeCollection([{"coid": "2330", "date": "2025-01-02", "mdate": "2025-01-02", "source_row_id": "a"}]),
        "2317": FakeCollection([{"coid": "2317", "date": "2025-01-02", "mdate": "2025-01-02", "source_row_id": "b"}]),
    })

    run_pipeline(
        root,
        config,
        families={"daily_tradability"},
        start_date="2025-01-01",
        end_date="2025-01-31",
        mongo_databases={"apistkattr": fake_db},
    )

    diagnostic = json.loads(
        (
            tmp_path
            / "runs"
            / "real_all_products"
            / "diagnostics"
            / "raw_families"
            / "daily_tradability.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["source_collection_count"] == 2
    assert diagnostic["source_collections"] == ["2317", "2330"]
    assert diagnostic["source_collection_sample_truncated"] is False
    assert diagnostic["published_row_count"] == 2


def test_small_snapshot_uses_single_collection_for_trading_calendar(tmp_path):
    _write_configs(tmp_path)
    config_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for family in payload["families"]:
        if family["family_id"] == "trading_calendar":
            family.pop("fixture_rows", None)
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)
    fake_collection = FakeCollection([{"zdate": "2025-01-02", "mkt": "TWSE", "date_rmk": ""}])
    fake_db = FakeDatabase({"TRADEDAY_TWSE": fake_collection})

    run_pipeline(root, config, families={"trading_calendar"}, mongo_databases={"tej": fake_db})

    diagnostic = json.loads(
        (
            tmp_path
            / "runs"
            / "real_all_products"
            / "diagnostics"
            / "raw_families"
            / "trading_calendar.json"
        ).read_text(encoding="utf-8")
    )
    assert fake_collection.queries == [{}]
    assert diagnostic["source_collection_count"] == 1
    assert diagnostic["source_collections"] == ["TRADEDAY_TWSE"]

```

