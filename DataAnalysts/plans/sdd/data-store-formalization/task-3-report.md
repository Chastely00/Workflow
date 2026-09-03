STATUS: DONE

Files changed:
- src/data_analysts/metadata.py
- tests/test_data_store_metadata.py
- plans/sdd/data-store-formalization/task-3-report.md

Commands run:
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\data-store-formalization\task-3-brief.md`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\paths.py`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\config.py`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_data_store_context.py`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`

Results:
- Added `tests/test_data_store_metadata.py` first, following the task brief contract for metadata publish and config snapshot hash verification.
- Confirmed RED on the first pytest run with `ModuleNotFoundError: No module named 'data_analysts.metadata'`.
- Implemented `src/data_analysts/metadata.py` with required config snapshot copying, SHA-256 hashing, manifest publishing, manifest loading, and hash verification.
- Used temp-file plus `os.replace()` for manifest JSON writes.
- Final targeted verification passed: `2 passed in 0.12s`.

Concerns:
- Verification stayed task-scoped to `tests/test_data_store_metadata.py`; no broader suite was run in this slice.

---

STATUS: DONE

Files changed:
- `src/data_analysts/metadata.py`
- `tests/test_data_store_metadata.py`
- `plans/sdd/data-store-formalization/task-3-report.md`

Commands run:
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\metadata.py`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_data_store_metadata.py`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\data-store-formalization\task-3-report.md`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\paths.py`
- `Get-Content -Raw C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\config.py`
- `rg -n "load_data_store_metadata|verify_config_snapshot_hashes|publish_data_store_metadata|config_snapshot_file_count" C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`

Results:
- Added coverage that each manifest `config_hashes[name]` equals the SHA-256 of `metadata/config_snapshot/<name>` bytes.
- Added coverage that each snapshot file byte-for-byte matches `project_root/configs/<name>`.
- Added direct coverage for `load_data_store_metadata(context)`.
- Added coverage that republishing replaces the entire `config_snapshot` directory and clears stale temp siblings.
- Changed snapshot publishing to stage a complete sibling temp directory, delete any previous temp, remove the old final directory, and rename the finished temp directory into place before manifest write.
- Clarified `verify_config_snapshot_hashes()` so `config_snapshot_file_count` reports the number of existing required snapshot files, while missing files increment `config_snapshot_missing_count`.
- Targeted verification passed: `5 passed in 0.21s`.

Concerns:
- Verification remained scoped to `tests/test_data_store_metadata.py`; no broader suite was run in this task slice.

---

STATUS: DONE

Files changed:
- `src/data_analysts/metadata.py`
- `tests/test_data_store_metadata.py`
- `plans/sdd/data-store-formalization/task-3-report.md`

Commands run:
- `Get-Content C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\metadata.py`
- `Get-Content C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_data_store_metadata.py`
- `Get-Content C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\data-store-formalization\task-3-report.md`
- `Get-Content src\data_analysts\paths.py | Select-Object -First 120`
- `Get-Content configs\universe_specs.json`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_metadata.py -q`

Results:
- Reproduced RED before the fix: new manifest-driven tests failed with missing `config_snapshot_path`, confirming publish/verify still depended on the legacy convenience directory.
- Changed metadata publish to stage a full versioned snapshot under `metadata/config_snapshots/<snapshot_id>`, keep previous versioned snapshots in place, and record the active relative path in manifest field `config_snapshot_path`.
- Kept compatibility publishing of `metadata/config_snapshot`, but only after the versioned snapshot completed; publish no longer deletes the previously active versioned snapshot directory.
- Updated `verify_config_snapshot_hashes()` to resolve snapshot files from manifest `config_snapshot_path`, with fallback to `metadata/config_snapshot` when the manifest field is absent.
- Added regression coverage that two publishes keep the previous versioned snapshot directory intact, manifest load points to the latest active snapshot, and verify still passes after deleting the convenience `metadata/config_snapshot` directory.
- Final targeted verification passed: `7 passed in 0.42s`.

Concerns:
- Verification remained scoped to `tests/test_data_store_metadata.py`; no broader suite was run in this task slice.
