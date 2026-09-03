# Task 7 Brief

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

