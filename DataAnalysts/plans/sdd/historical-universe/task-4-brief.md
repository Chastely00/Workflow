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

