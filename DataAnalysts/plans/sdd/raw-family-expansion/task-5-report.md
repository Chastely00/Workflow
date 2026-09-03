# Task 5 Report: Financial and Self-Reported Selected PIT Views

## Scope

- Implemented selected PIT diagnostics only.
- Did not filter `financial_statement_raw` raw rows to `no = Q`; raw rows preserve observed `A`, `Q`, and `TTM`.
- Did not use selected PIT output to tune strategy or universe behavior.
- No `pipeline.py` change was required.

## TDD Evidence

1. Added failing tests in `tests/test_raw_family_normalization.py`.
   - `test_financial_statement_raw_preserves_a_q_ttm_and_reports_selected_q_count`
   - `test_financial_statement_selected_pit_unresolved_duplicate_fails_closed`
   - `test_self_reported_numbers_selected_pit_reports_key3_category_counts`
2. Red run:
   - Command: `python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q`
   - Result: `3 failed, 15 passed`
   - Expected failures:
     - missing `selected_no_q_row_count`
     - unresolved duplicate was wrapped in a generic selected PIT message
     - missing `selected_key3_category_counts`
3. Green run:
   - Command: `python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q`
   - Result: `18 passed in 0.03s`

## Implementation

- Updated `src/data_analysts/raw_families.py`.
- `_selected_rows()` now reports:
  - `decision_date_count`
  - summed `eligible_row_count`
  - summed `future_row_count`
  - total `selected_row_count`
  - `selected_no_q_row_count`
  - `selected_key3_category_counts`
- Existing selected PIT counters remain available:
  - `input_row_count`
  - `resolved_duplicate_count`
  - `unresolved_duplicate_count`
- `PitError` is now converted to `RawFamilyError(str(exc))`, so unresolved selected PIT duplicates fail closed with the source ambiguity message.

## Quantitative Verification

- Raw `financial_statement_raw` no distribution keeps all observed `A`, `Q`, and `TTM`.
- AINVFINB selected PIT diagnostics report `selected_no_q_row_count`.
- AFESTM1 selected PIT diagnostics treat `key3` as a category and report `selected_key3_category_counts`.
- Unresolved duplicate selected PIT rows raise `RawFamilyError` with `unresolved duplicate` in the message.

## Concerns

- None for Task 5.
