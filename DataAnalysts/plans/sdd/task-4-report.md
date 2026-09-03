# Task 4 Report: Diagnostics Writer

## Status

Task 4 completed.

## Scope

Implemented JSON diagnostic writing only. No verify integration logic was added or modified.

## Changed Files

- `src/data_analysts/paths.py`
  - Added `DataAnalystsRoot.diagnostics_path(*parts)`.
  - Diagnostic output resolves through `resolve_output`, so paths must stay under the DataAnalysts root.
- `src/data_analysts/diagnostics.py`
  - Added `write_diagnostic(root, name, payload)`.
  - Normalizes slash and backslash-separated diagnostic names.
  - Rejects empty diagnostic names.
  - Writes sorted, indented UTF-8 JSON to `runs/real_all_products/diagnostics/...json`.
- `tests/test_pit_foundation_verify.py`
  - Added diagnostics writer test for the expected PIT foundation diagnostic path and JSON payload.

## TDD Evidence

Red:

```text
python -m pytest tests/test_pit_foundation_verify.py -q
ModuleNotFoundError: No module named 'data_analysts.diagnostics'
```

Green:

```text
python -m pytest tests/test_pit_foundation_verify.py -q
1 passed in 0.03s
```

## Boundary Check

Diagnostic paths are rooted at:

```text
runs/real_all_products/diagnostics
```

The path is resolved through `DataAnalystsRoot.resolve_output`, which raises `PathBoundaryError` if a resolved path escapes the DataAnalysts root.

## Concerns

None for Task 4. Verify integration remains intentionally untouched for Task 5.

## Reviewer Fix: Diagnostic Namespace Bypass

Fixed `write_diagnostic(root, r"C:\outside\x", payload)` bypassing the required
`runs/real_all_products/diagnostics` namespace. Diagnostic names are now parsed
as semantic relative ids only; root-like names, parent traversal, and drive-like
parts raise `ValueError` before path construction.

Regression test added:

```text
test_write_diagnostic_rejects_absolute_like_names
```

Red:

```text
python -m pytest tests/test_pit_foundation_verify.py -q
3 failed, 1 passed in 0.07s
Failed: DID NOT RAISE <class 'ValueError'>
```

Green:

```text
python -m pytest tests/test_pit_foundation_verify.py -q
4 passed in 0.03s
```
