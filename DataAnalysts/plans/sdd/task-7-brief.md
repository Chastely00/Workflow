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
