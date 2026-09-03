# Raw Family Expansion Task 6 Report

## Status

Completed.

## Scope

Implemented and verified only Task 6 inside `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.

## Changed Files

- `src/data_analysts/raw_families.py`
- `tests/test_raw_family_normalization.py`
- `plans/sdd/raw-family-expansion/task-6-report.md`

## TDD Evidence

1. Added generic governance `mdate` normalization coverage.
2. Added governance diagnostic coverage for required raw-family counters.
3. Added futures near-month normalization coverage using Chinese source fields `日期` and `契約`.
4. Added futures duplicate diagnostic coverage for `duplicate_date_contract_count`.
5. Ran `python -m pytest tests/test_raw_family_normalization.py -q`.
6. Observed RED: `test_futures_near_month_reports_duplicate_date_contract_count` failed with `KeyError: 'duplicate_date_contract_count'`.
7. Implemented the minimal production change: `_normalize_futures_near_month` now returns `duplicate_date_contract_count` computed from normalized `date` + `contract`.
8. Re-ran `python -m pytest tests/test_raw_family_normalization.py -q`.
9. Observed GREEN: `12 passed in 0.02s`.

## Registration Notes

`taiwan_index_futures_near_month` is registered in `_NORMALIZERS` with `_normalize_futures_near_month`.

No generic governance source-family list was added. The excluded families `AINVFQ1`, `APISHRACTW`, `AINVFINB`, and `AFESTM1` were not registered into generic governance logic.

## Quantitative Verification

- Generic governance families report:
  - `source_row_count`
  - `published_row_count`
  - `pit_null_count`
  - `pit_parse_failure_count`
  - `duplicate_logical_key_count`
  - `unresolved_duplicate_count`
- Futures near-month reports:
  - `duplicate_date_contract_count`

## Concerns

`git status` from `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts` did not show these files, and `git ls-files` did not list the modified task files. The files were still updated on disk and the required pytest command passed.

## Reviewer Fix: Explicit Generic Governance Allowlist

- Root cause: `normalize_raw_family()` used `_NORMALIZERS.get(family_id, _normalize_generic_mdate_family)`, so any registered family without a dedicated normalizer could be accepted by generic `mdate` logic.
- Fix: replaced the broad fallback with an explicit `_GENERIC_MDATE_FAMILY_IDS` allowlist containing only:
  - `director_supervisor_holdings`
  - `board_reelection_statistics`
  - `executive_change_events`
  - `merger_acquisition_events`
  - `private_placement_relation_events`
  - `insider_transfer_completed`
  - `insider_transfer_declared_not_completed`
  - `treasury_stock_events`
- Unsupported registered family ids now raise `RawFamilyError`.
- Unknown/unregistered family ids still raise `RawFamilyError` through registry lookup.
- Added regression coverage proving `AINVFINB`, `AFESTM1`, `AINVFQ1`, and `APISHRACTW` are not accepted by generic `mdate` normalization.
- Added coverage that forbidden `AINVFQ1`, forbidden `APISHRACTW`, and arbitrary unknown family ids fail closed.
- Added coverage that canonical `financial_statement_raw` and `self_reported_numbers_raw` still use dedicated behavior.
- Added explicit daily panel normalizer coverage for `daily_tradability` and `daily_chip` so existing daily panel pipeline behavior does not depend on broad fallback.

### RED Evidence

```text
python -m pytest tests/test_raw_family_normalization.py -q
..........F....
FAILED tests/test_raw_family_normalization.py::test_source_collection_ids_are_not_accepted_by_generic_mdate_normalizer
1 failed, 14 passed in 0.08s
```

```text
python -m pytest tests/test_raw_family_normalization.py::test_daily_panel_families_use_mdate_as_source_available_date -q
FF
FAILED tests/test_raw_family_normalization.py::test_daily_panel_families_use_mdate_as_source_available_date[daily_tradability]
FAILED tests/test_raw_family_normalization.py::test_daily_panel_families_use_mdate_as_source_available_date[daily_chip]
2 failed in 0.10s
```

### GREEN Evidence

```text
python -m pytest tests/test_raw_family_normalization.py -q
.................                                                        [100%]
17 passed in 0.03s
```

```text
python -m pytest tests/test_raw_family_normalization.py tests/test_raw_family_config.py tests/test_raw_family_pipeline.py -q
.........................                                                [100%]
25 passed in 0.52s
```
