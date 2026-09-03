# DataAnalysts Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent, portable DataAnalysts runtime under this folder that supports the three backfill modes plus verify and produces MongoDB-to-PIT-parquet artifacts, adjusted price/events, security panel, and universe membership without writing outside the DataAnalysts root.

**Architecture:** Start with a small Python package in `src/data_analysts` with strict root-boundary path handling, config validation, artifact/manifest publish primitives, verification checks, and an argparse CLI. Mongo extraction and semantic publishers are implemented behind interfaces so tests can use local fixture sources first, then MongoDB can be enabled without changing downstream contracts.

**Tech Stack:** Python 3, standard library validation, `pyarrow` for parquet I/O, `pymongo` for MongoDB extraction, `pytest` for tests.

---

## File Structure

- `pyproject.toml`: package metadata, dependencies, pytest config.
- `configs/mongodb_sources.json`: connection namespace template without credentials.
- `configs/source_family_profiles.json`: source family template and profile rules.
- `configs/universe_specs.json`: universe selector template using only security panel fields.
- `src/data_analysts/paths.py`: root resolution and no-output-outside-root guard.
- `src/data_analysts/config.py`: config loading and fail-closed validation.
- `src/data_analysts/artifacts.py`: parquet/JSON staging, atomic publish, manifest helpers.
- `src/data_analysts/verify.py`: contract checks for config, manifests, artifacts, security panel, universe.
- `src/data_analysts/cli.py`: CLI entrypoint for `run-full-history`, `run-backfill`, `run-daily`, `verify`, `inspect-artifacts`.
- `src/data_analysts/extract.py`: source readers, including fixture reader and MongoDB reader.
- `src/data_analysts/publish.py`: canonical raw publisher orchestration.
- `src/data_analysts/events.py`: dividend/capital event normalization.
- `src/data_analysts/adjusted_prices.py`: adjusted price builder with seed checks.
- `src/data_analysts/security_panel.py`: security panel builder.
- `src/data_analysts/universe.py`: universe membership builder.
- `tests/`: TDD tests for each contract boundary.

## Task 1: Runtime Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `src/data_analysts/__init__.py`
- Create: `src/data_analysts/paths.py`
- Create: `src/data_analysts/config.py`
- Create: `src/data_analysts/cli.py`
- Create: `tests/test_paths.py`
- Create: `tests/test_config.py`
- Create: `tests/test_cli_contract.py`

- [ ] Write failing tests for root-boundary path resolution.
- [ ] Implement `DataAnalystsRoot` and path guards.
- [ ] Write failing tests for config validation.
- [ ] Implement config loaders and validators.
- [ ] Write failing tests for CLI argument rejection.
- [ ] Implement argparse command skeleton and fail-closed exits.
- [ ] Run `python -m pytest tests/test_paths.py tests/test_config.py tests/test_cli_contract.py -q`.

## Task 2: Artifact and Manifest Publisher

**Files:**
- Create: `src/data_analysts/artifacts.py`
- Create: `tests/test_artifacts.py`

- [ ] Write failing tests for JSON manifest atomic publish under `runtime/manifests`.
- [ ] Write failing tests rejecting artifact paths outside root.
- [ ] Write failing tests for parquet publish with required schema columns.
- [ ] Implement staging write and replace.
- [ ] Run `python -m pytest tests/test_artifacts.py -q`.

## Task 3: Verify Contract

**Files:**
- Create: `src/data_analysts/verify.py`
- Create: `tests/test_verify.py`

- [ ] Write failing tests for missing config files producing `blocked`.
- [ ] Write failing tests for manifest path escaping root producing `blocked`.
- [ ] Write failing tests for leakage columns in security panel producing `blocked`.
- [ ] Write failing tests for universe membership duplicate keys producing `blocked`.
- [ ] Implement verifier and JSON result writer.
- [ ] Run `python -m pytest tests/test_verify.py -q`.

## Task 4: Fixture-Based Pipeline

**Files:**
- Create: `src/data_analysts/extract.py`
- Create: `src/data_analysts/publish.py`
- Create: `src/data_analysts/events.py`
- Create: `src/data_analysts/adjusted_prices.py`
- Create: `src/data_analysts/security_panel.py`
- Create: `src/data_analysts/universe.py`
- Create: `tests/fixtures/*.json`
- Create: `tests/test_fixture_pipeline.py`

- [ ] Write failing end-to-end test using local fixture rows instead of MongoDB.
- [ ] Publish canonical raw parquet and manifests.
- [ ] Build dividend/capital event parquet.
- [ ] Build adjusted price columns.
- [ ] Build security panel.
- [ ] Build universe membership.
- [ ] Run `python -m pytest tests/test_fixture_pipeline.py -q`.

## Task 5: MongoDB Reader

**Files:**
- Modify: `src/data_analysts/extract.py`
- Create: `tests/test_mongo_reader.py`

- [ ] Write failing tests against a fake Mongo collection object for bounded queries.
- [ ] Implement source profiles: `small_snapshot`, `medium_pit_table`, `large_daily_panel`.
- [ ] Ensure small snapshots are not split into tiny partitions.
- [ ] Ensure large daily panels require bounded date windows.
- [ ] Run `python -m pytest tests/test_mongo_reader.py -q`.

## Task 6: Full CLI Integration

**Files:**
- Modify: `src/data_analysts/cli.py`
- Create: `tests/test_cli_pipeline.py`

- [ ] Write failing tests for `run-full-history`.
- [ ] Write failing tests for `run-backfill --families`.
- [ ] Write failing tests for `run-backfill --start-date --end-date`.
- [ ] Write failing tests for `verify`.
- [ ] Wire CLI commands to pipeline and verifier.
- [ ] Run `python -m pytest -q`.

## Completion Evidence

The goal is not complete until all of the following are true:

- `python -m pytest -q` passes from the DataAnalysts root.
- `python -m data_analysts.cli run-full-history --root .` can produce fixture-backed artifacts in `runtime/`.
- `python -m data_analysts.cli run-backfill --root . --families daily_price_volume` works.
- `python -m data_analysts.cli run-backfill --root . --start-date YYYY-MM-DD --end-date YYYY-MM-DD` works.
- `python -m data_analysts.cli verify --root .` returns `ready` for a complete fixture runtime and `blocked` for missing/invalid artifacts.
- Search confirms there are no writes or generated products outside the DataAnalysts root.
- Search confirms no `python -m alf`, `alf.cli`, `from alf`, or `import alf` runtime dependency exists.

