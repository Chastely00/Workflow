# Task 7 Report: Run-root Diagnostics Path Fix

## Status

Completed.

## Changed Files

- `src/data_analysts/paths.py`
- `tests/test_pit_foundation_verify.py`
- `plans/sdd/task-7-report.md`

## Fix Summary

- Added a regression test for `DataAnalystsRoot.from_path(tmp_path / "runs" / "real_all_products")` with `write_diagnostic(root, "pit_foundation/source_catalog", ...)`.
- Updated `DataAnalystsRoot.diagnostics_path()` so project-root mode writes to `<root>/runs/real_all_products/diagnostics/...` and run-root mode writes to `<root>/diagnostics/...`.
- Did not modify `verify.py`, `configs`, or `contracts`.
- Did not delete generated runtime artifacts.

## Command Outputs

Red test before fix:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_pit_foundation_verify.py -q
.F....                                                                   [100%]
================================== FAILURES ===================================
____________ test_write_diagnostic_uses_diagnostics_under_run_root ____________

E       AssertionError: assert WindowsPath('.../runs/real_all_products/runs/real_all_products/diagnostics/pit_foundation/source_catalog.json') == ... / 'runs' / 'real_all_products' / 'diagnostics' / 'pit_foundation' / 'source_catalog.json'

FAILED tests/test_pit_foundation_verify.py::test_write_diagnostic_uses_diagnostics_under_run_root
1 failed, 5 passed in 0.19s
```

Final verification:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_pit_foundation_verify.py -q
......                                                                   [100%]
6 passed in 0.15s
```

## Concerns

None.
