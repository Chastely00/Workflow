# PIT Foundation Final Review Fix Report

## Changed Files

- `src/data_analysts/pit.py`
- `src/data_analysts/source_catalog.py`
- `src/data_analysts/verify.py`
- `tests/test_pit_selection.py`
- `tests/test_pit_foundation_config.py`
- `tests/test_pit_foundation_verify.py`
- `plans/sdd/final-review-fix-report.md`

## Fix Summary

- `select_latest_pit_rows()` now fails closed with `PitError` for missing or blank availability fields, logical key fields, and revision fields when revision selection is enabled.
- `validate_pit_registry()` now requires catalog and PIT registry family ids to match exactly, and validates non-empty logical keys plus `date_normalization == "date_only"` for catalog and registry rules.
- `verify_runtime()` now scans runtime manifest JSON dictionaries/lists for forbidden source references and blocks PIT foundation verification when usage is found.

## Tests Run

- `python -m pytest tests/test_pit_selection.py tests/test_pit_foundation_config.py tests/test_pit_foundation_verify.py -q`
  - Result: `23 passed in 0.17s`

## Concerns

- Runtime manifest source-reference schema is still implicit; the scanner is intentionally conservative for `database`, `collection`, `source_collection`, and `source_collections` shapes.
