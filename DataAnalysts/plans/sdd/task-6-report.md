# Task 6 Report: Contract Documentation

## Status

Completed.

## Scope

Docs only. No `src/`, `tests/`, or `configs/` files were modified.

## Changed Files

- `contracts/PIT_REGISTRY_CONTRACT.md`
- `contracts/CONFIG_CONTRACT.md`
- `contracts/OUTPUT_CONTRACT.md`
- `contracts/VERIFICATION_CONTRACT.md`
- `plans/sdd/task-6-report.md`

## Contract Updates

- Added `PIT_REGISTRY_CONTRACT.md` as the reader-facing PIT Foundation registry contract.
- Declared `TEJ.AINVFQ1` and `TEJ.APISHRACTW` forbidden; any config, catalog, manifest, or runtime output reference blocks verification.
- Documented required PIT date normalization to `YYYY-MM-DD` before filtering.
- Documented `TEJ.AINVFINB` selected-view revision rule:
  1. preserve all raw rows and revisions in canonical output.
  2. use `key3` as `source_available_date`.
  3. require `source_available_date <= decision_date`.
  4. group by `ticker, no, sem, curr, merg, period_end_date`.
  5. choose max `source_available_date`.
  6. within that date choose max `revision_date = normalize_date(mdate)`.
  7. fail closed if duplicates remain.
- Documented `AFESTM1.annd` as the PIT date and `AFESTM1.key3` as a statement form/category field, not a date.
- Updated config contract to require `configs/source_catalog.json` and `configs/pit_registry.json`.
- Updated output contract to require PIT Foundation diagnostics at `runs/real_all_products/diagnostics/pit_foundation/source_catalog.json`.
- Documented diagnostics metrics:
  - `forbidden_source_count`
  - `approved_source_count`
  - `pit_registry_family_count`
  - `forbidden_source_usage_count`
  - `missing_pit_field_count`
  - `missing_logical_key_count`
- Updated verification contract thresholds:
  - `forbidden_source_usage_count == 0`
  - `missing_pit_field_count == 0`
  - `missing_logical_key_count == 0`
  - `TEJ.AINVFQ1` references are absent
  - `TEJ.APISHRACTW` references are absent

## Validation

Command:

```powershell
rg -n "AINVFQ1|APISHRACTW|AINVFINB|AFESTM1|PIT Foundation" contracts
```

Result: passed. The command found the forbidden source rules, `AINVFINB` rule, `AFESTM1` rule, PIT Foundation diagnostics, and PIT Foundation verification thresholds in `contracts/`.

## Concerns

None for Task 6. This task intentionally did not verify runtime behavior because the brief is docs only and forbids `src/`, `tests/`, and `configs/` changes.
