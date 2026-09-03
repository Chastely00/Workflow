## Task 6: Contract Documentation

**Files:**
- Create: `contracts/PIT_REGISTRY_CONTRACT.md`
- Modify: `contracts/CONFIG_CONTRACT.md`
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`

**Boundary:**
- This task updates docs only.
- It must not change code behavior.

**Produces:**
- Reader-facing PIT Foundation contract.

- [ ] **Step 1: Write `PIT_REGISTRY_CONTRACT.md`**

Include these sections:

```markdown
# PIT Registry Contract

## Purpose

The PIT registry is the fail-closed source of truth for DataAnalysts source availability dates, logical keys, and revision selection.

## Required Date Rule

All PIT fields must be normalized to `YYYY-MM-DD` before filtering. Datetime values and strings with `HH:MM:SS` must lose the time component before comparison.

## Forbidden Sources

`TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden. Any config, catalog, manifest, or runtime output referencing them blocks verification.

## AINVFINB Financial Statement Rule

Raw canonical output preserves every `TEJ.AINVFINB` source row and revision.

Selected PIT views use:

1. `source_available_date = normalize_date(key3)`
2. `source_available_date <= decision_date`
3. group by `ticker, no, sem, curr, merg, period_end_date`
4. choose max `source_available_date`
5. within that date choose max `revision_date = normalize_date(mdate)`
6. if still duplicated, fail closed

## AFESTM1 Rule

`AFESTM1.annd` is the PIT date. `AFESTM1.key3` is a statement form/category field and must not be parsed as a date.
```

- [ ] **Step 2: Update `CONFIG_CONTRACT.md`**

Add:

```markdown
## Source Catalog and PIT Registry

Valid configs must include `configs/source_catalog.json` and `configs/pit_registry.json`.

Validation fails closed when:
- either file is missing
- schema version is unsupported
- a family id is duplicated
- a PIT field is missing
- a logical key is missing
- any config references `TEJ.AINVFQ1`
- any config references `TEJ.APISHRACTW`
```

- [ ] **Step 3: Update `OUTPUT_CONTRACT.md`**

Add:

```markdown
## PIT Foundation Diagnostics

PIT Foundation writes diagnostics to `runs/real_all_products/diagnostics/pit_foundation/source_catalog.json`.

The diagnostic must include:
- `forbidden_source_count`
- `approved_source_count`
- `pit_registry_family_count`
- `forbidden_source_usage_count`
- `missing_pit_field_count`
- `missing_logical_key_count`
```

- [ ] **Step 4: Update `VERIFICATION_CONTRACT.md`**

Add:

```markdown
## PIT Foundation Thresholds

Verification is blocked unless:
- `forbidden_source_usage_count == 0`
- `missing_pit_field_count == 0`
- `missing_logical_key_count == 0`
- `TEJ.AINVFQ1` references are absent
- `TEJ.APISHRACTW` references are absent
```

