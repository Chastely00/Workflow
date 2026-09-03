STATUS: DONE_WITH_CONCERNS

Files changed:
- plans/sdd/data-store-formalization/progress.md
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:DATA_ANALYSTS_MONGODB_URI = "mongodb://localhost:27017/"; $env:PYTHONPATH='src'; python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store`
- `@'`
  `from pathlib import Path`
  `store = Path("data_store")`
  `print({`
  `    "canonical_exists": (store / "canonical").exists(),`
  `    "manifests_exists": (store / "manifests").exists(),`
  `    "metadata_exists": (store / "metadata" / "data_store_manifest.json").exists(),`
  `    "jobs_exists": (store / "jobs").exists(),`
  `    "formal_runtime_under_store": (store / "runtime").exists(),`
  `    "formal_runs_under_store": (store / "runs").exists(),`
  `})`
  `'@ | python -`
- `$env:DATA_ANALYSTS_MONGODB_URI = "mongodb://localhost:27017/"; $env:PYTHONPATH='src'; python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --start-date 2026-01-01 --end-date 2026-01-31`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store`

Results:
- Initial bounded 4-family smoke returned `ready`.
- Initial verify returned `missing required manifest` with `status=blocked`, `manifest_count=12`, `required_manifest_missing_count=15`, `absolute_artifact_path_count=0`, `artifact_path_escape_count=0`, `forbidden_path_segment_count=0`, `config_snapshot_hash_mismatch_count=0`, `legacy_project_runtime_exists=true`, `legacy_project_runs_exists=true`.
- Initial missing enabled-family manifests were `dividend_policy`, `capital_formation`, `daily_chip`, `monthly_sales`, `financial_statement_raw`, `self_reported_numbers_raw`, `taiwan_index_futures_near_month`, `director_supervisor_holdings`, `board_reelection_statistics`, `executive_change_events`, `merger_acquisition_events`, `private_placement_relation_events`, `insider_transfer_completed`, `insider_transfer_declared_not_completed`, `treasury_stock_events`.
- Initial inspect returned `status=ready` with `manifest_count=12`, `artifact_path_count=12`, `required_manifest_missing_count=15`, `forbidden_path_segment_count=0`, `absolute_artifact_path_count=0`, `artifact_path_escape_count=0`, `config_snapshot_hash_mismatch_count=0`, `legacy_layout_detected=true`, `raw_family_diagnostics.status=ready`, `historical_universe.status=ready`.
- Filesystem guard output was `{'canonical_exists': True, 'manifests_exists': True, 'metadata_exists': True, 'jobs_exists': True, 'formal_runtime_under_store': False, 'formal_runs_under_store': False}`.
- Additional bounded all-enabled smoke for `2026-01-01` through `2026-01-31` also returned `ready`.
- Post-expansion verify still returned `missing required manifest`, but blocked metrics improved to `manifest_count=28`, `required_manifest_missing_count=4`, `absolute_artifact_path_count=0`, `artifact_path_escape_count=0`, `forbidden_path_segment_count=0`, `config_snapshot_hash_mismatch_count=0`, `raw_family_diagnostic_count=15`, `pit_parse_failure_count_total=0`, `unresolved_duplicate_count_total=0`.
- Remaining missing enabled-family manifests after the all-enabled bounded smoke are `dividend_policy`, `capital_formation`, `director_supervisor_holdings`, and `insider_transfer_declared_not_completed`.
- Post-expansion inspect returned `status=ready` with `manifest_count=28`, `artifact_path_count=28`, `required_manifest_missing_count=4`, `forbidden_path_segment_count=0`, `absolute_artifact_path_count=0`, `artifact_path_escape_count=0`, `config_snapshot_hash_mismatch_count=0`, `legacy_layout_detected=true`, `raw_family_diagnostics.status=ready`, and `historical_universe.status=ready`.

Concerns:
- `verify --project-root . --data-store .\data_store` is still blocked at `required_manifest_missing_count=4`, so Task 9 cannot claim full verify success.
- The remaining blocked manifest ids are exact contract mismatches between enabled family ids and produced formal manifests: `dividend_policy`, `capital_formation`, `director_supervisor_holdings`, and `insider_transfer_declared_not_completed`.
- Legacy `runtime/` and `runs/` still exist at project root and are correctly detected as legacy (`legacy_layout_detected=true`), but they are not being read as formal artifacts and no forbidden formal paths were created under `data_store/`.

## Controller Re-Verification After Verify Blocker Fix 1
STATUS: DONE

Files changed:
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m pytest -q`

Results:
- Live verify returned `ready`.
- Live inspect returned `status=ready`, `manifest_count=28`, `artifact_path_count=28`, `required_manifest_missing_count=0`, `zero_row_required_family_count=2`, `absolute_artifact_path_count=0`, `artifact_path_escape_count=0`, `forbidden_path_segment_count=0`, and `config_snapshot_hash_mismatch_count=0`.
- Full test suite returned `134 passed in 3.65s`.

Concerns:
- The required-manifest semantics changed after the first Task 9 smoke, so this fix still needs read-only review before Task 9 can be marked complete.

## Verify Blocker Fix 1
STATUS: DONE_WITH_CONCERNS

Files changed:
- src/data_analysts/inspect.py
- src/data_analysts/verify.py
- tests/test_raw_family_verify.py
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py tests\test_pit_foundation_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest -q`

Results:
- Added regression coverage for formal event replacement (`dividend_policy -> dividend_events`, `capital_formation -> capital_action_events`).
- Added regression coverage proving zero-row raw-family diagnostics can satisfy bounded verify without publishing a raw manifest.
- Added regression coverage proving missing manifest still blocks when the raw-family diagnostic is missing or reports nonzero `source_row_count`.
- `tests\test_raw_family_verify.py` passed (`14 passed`).
- Targeted verify suite passed (`41 passed`).
- Full suite passed (`134 passed`).
- Verify/inspect now compute required manifests from the formal product contract, and record `zero_row_required_family_count` while preserving fail-closed `required_manifest_missing_count`.

Concerns:
- I did not rerun the real bounded Mongo smoke or `verify --project-root . --data-store .\data_store` in this fix pass; the change is validated by regression tests rather than fresh live store evidence.

## Verify Blocker Fix 2
STATUS: DONE

Files changed:
- src/data_analysts/inspect.py
- src/data_analysts/verify.py
- tests/test_raw_family_verify.py
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest tests\test_raw_family_verify.py tests\test_pit_foundation_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest -q`

Results:
- Added regression coverage proving malformed zero-row raw-family diagnostics with missing/null/bool/string/float counters do not satisfy missing-manifest checks.
- Added regression coverage proving verify uses published data-store snapshot semantics for required family ids even when live project config drifts after metadata publish, and inspect/verify stay aligned.
- Tightened `_diagnostic_proves_zero_rows()` so zero-row proof now requires explicit integer `0` for `source_row_count`, `pit_parse_failure_count`, and `unresolved_duplicate_count`.
- Changed verify required-manifest counting to use the same metadata snapshot source as inspect instead of current live project config.
- `tests\test_raw_family_verify.py` passed (`16 passed`).
- Targeted verify suite passed (`43 passed`).
- Full suite passed (`136 passed`).

Concerns:
- No additional concerns.

## Controller Re-Verification After Verify Blocker Fix 2
STATUS: DONE

Files changed:
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m pytest -q`

Results:
- Live verify returned `ready`.
- Live inspect returned `status=ready`, `manifest_count=28`, `artifact_path_count=28`, `required_manifest_missing_count=0`, `zero_row_required_family_count=2`, `absolute_artifact_path_count=0`, `artifact_path_escape_count=0`, `forbidden_path_segment_count=0`, `config_snapshot_file_count=5`, and `config_snapshot_hash_mismatch_count=0`.
- Full test suite returned `136 passed in 3.84s`.
- Read-only re-review approved the stricter zero-row diagnostic proof and snapshot-based required-family semantics.

Concerns:
- `legacy_layout_detected=true` because old project-root `runtime/` and `runs/` directories still exist; they are detected as legacy only and are not formal artifacts.

## Final Review Fix 1
STATUS: DONE

Files changed:
- src/data_analysts/cli.py
- src/data_analysts/config.py
- src/data_analysts/metadata.py
- src/data_analysts/verify.py
- tests/test_data_store_cli.py
- tests/test_historical_universe_verify.py
- tests/test_pit_foundation_verify.py
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py tests\test_raw_family_verify.py tests\test_pit_foundation_verify.py tests\test_historical_universe_verify.py -q`
- `$env:PYTHONPATH='src'; python -m pytest -q`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --project-root . --data-store .\data_store`

Results:
- Added CLI regression proving both `--root` and `--root=...` return the same removed-argument message.
- Added drift regressions proving verify uses the published config snapshot rather than live `project_root/configs` after metadata publish for `source_catalog`, `pit_registry`, and `universe_specs`.
- Added a fail-closed regression proving verify blocks at `metadata` when the active config snapshot exists but cannot be loaded, instead of falling back to live configs.
- Targeted verify suite passed (`51 passed`).
- Full suite passed (`141 passed in 3.91s`).
- Live verify returned `ready`.

Concerns:
- None.

## Controller Re-Verification After Final Review Fix 1
STATUS: DONE

Files changed:
- plans/sdd/data-store-formalization/task-9-report.md

Commands run:
- `$env:PYTHONPATH='src'; python -m pytest -q`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli verify --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store`
- `$env:PYTHONPATH='src'; python -c "from data_analysts.cli import main; print('split', main(['verify','--root','.'])); print('equals', main(['verify','--root=.']))"`

Results:
- Full test suite returned `141 passed in 4.06s`.
- Live verify returned `ready`.
- Live inspect returned `status=ready`, `manifest_count=28`, `artifact_path_count=28`, `required_manifest_missing_count=0`, `zero_row_required_family_count=2`, and all path/hash guard counts at `0`.
- Both `--root` and `--root=...` printed `--root has been removed. Use --project-root and --data-store.` and returned `1`.

Concerns:
- `legacy_layout_detected=true` remains expected because project-root legacy `runtime/` and `runs/` directories still exist; `data_store/runtime` and `data_store/runs` remain absent.
