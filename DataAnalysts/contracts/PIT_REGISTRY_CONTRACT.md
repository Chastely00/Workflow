# PIT Registry Contract

## Purpose

The PIT registry is the fail-closed source of truth for DataAnalysts source availability dates, logical keys, and revision selection.

It binds source catalog entries to point-in-time availability semantics. A source is not usable for research, canonical publish, selected views, diagnostics, or verification unless the PIT registry declares its availability field and logical key.

## Required Date Rule

All PIT fields must be normalized to `YYYY-MM-DD` before filtering. Datetime values and strings with `HH:MM:SS` must lose the time component before comparison.

The normalized date is the value used for PIT eligibility comparisons. Raw timestamp values may only be used when an explicit source-specific tie-breaker is declared.

## Forbidden Sources

`TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden. Any config, catalog, manifest, or runtime output referencing them blocks verification.

Forbidden source references include exact collection names, source family ids, manifest `source_collections`, diagnostics, and runtime output metadata. A forbidden source count greater than zero is not a warning; it is a blocked contract state.

## AINVFINB Financial Statement Rule

Raw canonical output preserves every `TEJ.AINVFINB` source row and revision.

Selected PIT views use:

1. `source_available_date = normalize_date(key3)`
2. `source_available_date <= decision_date`
3. group by `ticker, no, sem, curr, merg, period_end_date`
4. choose max `source_available_date`
5. within the same normalized `source_available_date`, choose the latest raw `key3` timestamp when the day has multiple `HH:MM:SS` publications or corrections
6. within that surviving same-day timestamp choose max `revision_date = normalize_date(mdate)`
7. if still duplicated, fail closed

`key3` is the availability date for the selected view, not a future leakage source. For `AINVFINB`, a later raw `key3` timestamp on the same normalized date is treated as a same-day publication/correction tie-breaker; it is not allowed to make any row eligible after `decision_date`. `mdate` is only the revision tie-breaker after the latest available `key3` date and same-day timestamp have already been selected.

## AFESTM1 Rule

`AFESTM1.annd` is the PIT date. `AFESTM1.key3` is a statement form/category field and must not be parsed as a date.

Any selected view, manifest, diagnostic, or verification implementation that treats `AFESTM1.key3` as the PIT date is invalid and must fail closed.
