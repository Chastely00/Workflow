# Task 3 Report: PIT Date Normalization and Revision Selection

## Scope

Implemented Task 3 only inside `DataAnalysts`:

- Added `src/data_analysts/pit.py`
- Added `tests/test_pit_selection.py`
- Did not read MongoDB, parquet files, runtime manifests, configs, verify, or pipeline modules.

## TDD Evidence

Red step:

- Added `tests/test_pit_selection.py` before creating production code.
- Ran `python -m pytest tests/test_pit_selection.py -q`.
- Expected failure observed: `ModuleNotFoundError: No module named 'data_analysts.pit'`.

Green step:

- Implemented `PitError`, `normalize_date`, and `select_latest_pit_rows`.
- Re-ran `python -m pytest tests/test_pit_selection.py -q`.
- Result: `4 passed in 0.02s`.

## Implemented Semantics

`normalize_date(value)`:

- Returns ISO `YYYY-MM-DD` strings.
- Accepts `date`, `datetime`, plain ISO dates, ISO datetimes, and `YYYY-MM-DD HH:MM:SS`.
- Strips datetime components by using the parsed date portion.
- Returns `None` for `None` and blank text.
- Raises `PitError("unsupported date value: ...")` for unparseable values.

`select_latest_pit_rows(...)`:

- Normalizes `decision_date`; missing decision date raises `PitError`.
- Normalizes availability and revision fields on selected output rows.
- Excludes rows with `availability_field > decision_date`.
- Groups by `logical_key`.
- Within each logical key, selects max availability date first.
- Within max availability rows, selects max revision date when `revision_field` is provided.
- If latest availability and latest revision still leave multiple rows, raises `PitError` with `unresolved duplicate`.
- Returns deterministic selected row ordering by logical key.
- Returns diagnostics for input, eligible, future, selected, resolved duplicate, and unresolved duplicate row counts.

## Verification

Command:

```powershell
python -m pytest tests/test_pit_selection.py -q
```

Output:

```text
....                                                                     [100%]
4 passed in 0.02s
```

## Concerns

None for Task 3 scope.
