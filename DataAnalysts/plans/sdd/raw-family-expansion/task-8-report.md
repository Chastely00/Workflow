# Task 8 Report: Contracts and Reader Documentation

## Status

Completed.

## Boundary

- Docs-only update.
- Modified files stayed under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- No `src/`, `tests/`, `configs/`, or `runtime/` files were modified.

## Changed Files

- `contracts/OUTPUT_CONTRACT.md`
- `contracts/VERIFICATION_CONTRACT.md`
- `README.md`
- `plans/sdd/raw-family-expansion/task-8-report.md`

## Implemented Changes

- Added `Raw Family Expansion Outputs` to `contracts/OUTPUT_CONTRACT.md`, including required artifact ids, layers, partitioning, PIT fields, and governance/event publishing semantics.
- Added `Raw Family Thresholds` to `contracts/VERIFICATION_CONTRACT.md`, including hard blocking totals and required raw-family diagnostic metrics.
- Added `Raw Family Coverage` to `README.md`, including approved `TEJ.AINVFINB` and `TEJ.AFESTM1` coverage and forbidden `TEJ.AINVFQ1` / `TEJ.APISHRACTW` sources.

## Validation

Command:

```powershell
rg -n "Raw Family Expansion|AINVFQ1|APISHRACTW|AINVFINB|AFESTM1|pit_parse_failure_count_total" README.md contracts
```

Result: exit code `0`.

Observed matches:

```text
contracts\CONFIG_CONTRACT.md:221:- any config references `TEJ.AINVFQ1`.
contracts\CONFIG_CONTRACT.md:222:- any config references `TEJ.APISHRACTW`.
README.md:150:Raw Family Expansion publishes trading calendar, daily tradability, daily chip, monthly sales, financial statements from `TEJ.AINVFINB`, self-reported numbers from `TEJ.AFESTM1`, governance/event tables, and TX near-month futures. `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden and fail verification.
contracts\PIT_REGISTRY_CONTRACT.md:17:`TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden. Any config, catalog, manifest, or runtime output referencing them blocks verification.
contracts\PIT_REGISTRY_CONTRACT.md:21:## AINVFINB Financial Statement Rule
contracts\PIT_REGISTRY_CONTRACT.md:23:Raw canonical output preserves every `TEJ.AINVFINB` source row and revision.
contracts\PIT_REGISTRY_CONTRACT.md:36:## AFESTM1 Rule
contracts\PIT_REGISTRY_CONTRACT.md:38:`AFESTM1.annd` is the PIT date. `AFESTM1.key3` is a statement form/category field and must not be parsed as a date.
contracts\PIT_REGISTRY_CONTRACT.md:40:Any selected view, manifest, diagnostic, or verification implementation that treats `AFESTM1.key3` as the PIT date is invalid and must fail closed.
contracts\OUTPUT_CONTRACT.md:66:## Raw Family Expansion Outputs
contracts\VERIFICATION_CONTRACT.md:79:- `pit_parse_failure_count_total == 0`
contracts\VERIFICATION_CONTRACT.md:191:- `TEJ.AINVFQ1` references are absent
contracts\VERIFICATION_CONTRACT.md:192:- `TEJ.APISHRACTW` references are absent
```

## Concerns

- The validation pattern also matches existing `CONFIG_CONTRACT.md` and `PIT_REGISTRY_CONTRACT.md` text. These files were not modified by this task.
