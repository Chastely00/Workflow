# Task 1 Report: Historical Universe Contracts and Config

## STATUS

DONE

## Files changed

- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_config.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\config.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\configs\universe_specs.json`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\CONFIG_CONTRACT.md`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\OUTPUT_CONTRACT.md`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\VERIFICATION_CONTRACT.md`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-1-report.md`

## RED test command/result

Command:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py -q
```

Result: failed as expected.

- `test_universe_specs_define_baseline_historical_universes` failed because `configs/universe_specs.json` only contained `tw_equity_liquid_top500`.
- `test_universe_config_allows_effective_date_but_rejects_realized_return` failed because `load_runtime_config()` rejected `effective_date` as an unsupported field.

## GREEN test command/result

Command:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py tests\test_pit_foundation_config.py -q
```

Result: passed.

```text
10 passed in 0.15s
```

## Self-review notes

- Scope stayed inside `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Followed the brief's TDD order: added the failing test first, captured RED, then implemented the minimum validator/config/contract updates needed for GREEN.
- Did not implement security panel history publishing, universe builder behavior, pipeline publish, or verify gates.
- Config validation remains fail-closed: unsupported universe fields and unsupported operators still raise `ConfigError`.
- `effective_date` was added only as an allowed selector/control field; realized-return fields remain rejected by config validation.

## Concerns

None for Task 1. Contracts now describe historical canonical paths before runtime implementation exists, which is intentional for this contract/config-only slice.

## 2026-07-07 Reviewer Fix Append

- Fixed `contracts/OUTPUT_CONTRACT.md` so historical canonical `security_panel_history` explicitly requires `effective_date` in the required-column surface.
- Fixed `contracts/OUTPUT_CONTRACT.md` so historical canonical `membership_by_year` is no longer described by the 4-column convenience schema; it now requires at least `as_of_date`, `effective_date`, `universe_id`, `ticker`, `rank`, `included`, `reason`, `market`, `security_type`, `listed`, `tradable`, `close`, `adj_close`, `market_cap`, `adv20`, and `data_cutoff_at`.
- Kept `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` explicitly scoped as a latest-date convenience output and clarified that it must not be confused with the historical canonical schema.
- Tightened `tests/test_historical_universe_config.py` so it asserts the exact enabled baseline universe set instead of a subset.
- Added a fail-closed regression proving unsupported universe filter operators still raise `ConfigError`.

Verification:

```text
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py tests\test_pit_foundation_config.py -q
11 passed in 0.13s
```
