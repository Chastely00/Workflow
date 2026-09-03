STATUS: DONE

Files changed:
- src/data_analysts/cli.py
- tests/test_data_store_cli.py
- plans/sdd/data-store-formalization/task-6-report.md

Commands run:
- $env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py -q
- $env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py -q
- $env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py tests\test_raw_family_pipeline.py tests\test_historical_universe_pipeline.py -q

Results:
- RED: 3 failed in `tests/test_data_store_cli.py` before implementation; failures confirmed missing `--project-root` / `--data-store` support and stale `DataAnalystsRoot` CLI wiring.
- GREEN: `tests/test_data_store_cli.py` passed with `3 passed in 0.13s`.
- Regression: combined suite passed with `14 passed in 1.13s`.

Concerns:
- None.
