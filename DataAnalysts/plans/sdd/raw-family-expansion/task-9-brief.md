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
