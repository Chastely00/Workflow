STATUS: DONE

Files changed:
- src/data_analysts/paths.py
- tests/test_historical_universe_config.py
- tests/test_raw_family_config.py

Commands run:
- `rg -n "DataAnalystsRoot|runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests`
- `$env:PYTHONPATH='src'; python -m pytest -q tests/test_historical_universe_config.py tests/test_raw_family_config.py`
- `rg -n "DataAnalystsRoot|runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests`
- `$env:PYTHONPATH='src'; python -m pytest -q`
- `rg -n "runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests README.md contracts`

Results:
- Initial legacy scan found remaining `DataAnalystsRoot` imports/usages in `tests/test_historical_universe_config.py` and `tests/test_raw_family_config.py`, plus the temporary `DataAnalystsRoot` class in `src/data_analysts/paths.py`.
- Migrated both tests to `DataAnalystsContext.from_paths(...)` and removed the unused `DataAnalystsRoot` compatibility class from `src/data_analysts/paths.py`.
- Focused regression run passed: `8 passed in 0.09s`.
- Full test suite passed: `129 passed in 3.44s`.
- Final scan found no remaining source/test references to `DataAnalystsRoot`, `runtime/data_canonical`, `runtime/manifests`, `runtime/jobs`, or `runs/real_all_products`.
- Remaining `--root` matches are limited to explicit legacy rejection handling and documentation in `src/data_analysts/cli.py`, `tests/test_data_store_cli.py`, `README.md`, and `contracts/CLI_CONTRACT.md`.
- `contracts/OUTPUT_CONTRACT.md` retains `runs/real_all_products/` only inside the explicit `Legacy Layout Warning` section.

Concerns:
- None.
## Stability Fix 1
STATUS: DONE

Files changed:
- src/data_analysts/metadata.py
- tests/test_data_store_metadata.py

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`
- `$env:PYTHONPATH='src'; 1..20 | ForEach-Object { python -m pytest tests\test_data_store_metadata.py -q; if ($LASTEXITCODE -ne 0) { Write-Host "FAILED_ITER=$_"; exit $LASTEXITCODE } }; Write-Host 'metadata-file-loop-ok'`
- `$env:PYTHONPATH='src'; python -m pytest -q`

Results:
- Added a regression test that forces directory `Path.rename(...)` to raise `PermissionError` and verified metadata publish still succeeds, preserves the active versioned snapshot, refreshes the convenience snapshot, and keeps snapshot hash verification clean.
- Reworked config snapshot publishing to create the unique versioned snapshot directory directly and write files in place, with cleanup on failure, so manifest publication never depends on directory rename.
- Reworked convenience snapshot publishing to remove any stale temporary directory, replace the existing convenience directory, and copy from the active versioned snapshot without directory rename.
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q` -> `8 passed in 0.43s`
- `$env:PYTHONPATH='src'; 1..20 | ForEach-Object { python -m pytest tests\test_data_store_metadata.py -q; if ($LASTEXITCODE -ne 0) { Write-Host "FAILED_ITER=$_"; exit $LASTEXITCODE } }; Write-Host 'metadata-file-loop-ok'` -> 20 consecutive passes, final line `metadata-file-loop-ok`
- `$env:PYTHONPATH='src'; python -m pytest -q` -> `130 passed in 3.48s`

Concerns:
- None.
