# AGENTS.md - DataAnalysts Subagent Contract

DataAnalysts is an independent quant data processing subagent. Its job is to turn MongoDB source data into PIT-safe parquet artifacts that downstream agents can consume without knowing MongoDB schemas or rebuilding data logic.

This file is intentionally short and directive. Use `README.md`, `contracts/*.md`, and `AGENT_DATA_USAGE.md` for details.

## Role

DataAnalysts owns the data product layer:

```text
MongoDB
-> canonical raw parquet
-> PIT-normalized parquet
-> adjusted price parquet
-> event parquet
-> security panel
-> universe membership
```

DataAnalysts is not a feature research, strategy, backtest, portfolio construction, or execution agent.

## Mission

Produce a portable `data_store` that any downstream system can read directly:

```text
data_store/
  canonical/
  manifests/
  metadata/
  diagnostics/
  jobs/
  output/
```

The output must be reproducible from this folder's source code, configs, contracts, and MongoDB source data. Missing data, ambiguous schema, unsupported PIT logic, and failed verification must fail closed.

## Workflow

Preferred workspace environment:

```powershell
cd C:\Users\ChastLai\Documents\量化交易積木
.\.venv\Scripts\Activate.ps1
cd .\DataAnalysts
```

`DataAnalysts/.venv` is a legacy local runtime residue. Do not create it, update it, or use it for tests, CLI, build, or verification. After root `.venv` passes the DataAnalysts acceptance checks, `DataAnalysts/.venv` must be removed.

Full historical build:

```powershell
python -m data_analysts.cli run-full-history
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

Partial backfill:

```powershell
python -m data_analysts.cli run-backfill --families daily_price_volume,daily_tradability --start-date 2025-01-01 --end-date 2026-07-02
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

Daily refresh:

```powershell
python -m data_analysts.cli run-daily
python -m data_analysts.cli run-daily --to-date YYYY-MM-DD
python -m data_analysts.cli run-daily --from-date YYYY-MM-DD --to-date YYYY-MM-DD
python -m data_analysts.cli run-daily --as-of-date YYYY-MM-DD
python -m data_analysts.cli verify --as-of-date YYYY-MM-DD
python -m data_analysts.cli inspect-artifacts --as-of-date YYYY-MM-DD
```

Use `run-daily` without date for scheduled production refresh. Use `--to-date` or `--from-date/--to-date` for catch-up after missed runs. Use `--as-of-date` only for exact single-day reruns or debugging.

Monitor active runs:

```powershell
Get-Content .\data_store\jobs\daily_state.json
Get-Content .\data_store\jobs\current_run.json
```

## Hard Boundaries

- Do not call ALF CLI as a runtime adapter.
- Do not depend on `alf.*` modules at runtime.
- Do not write formal artifacts outside `data_store`.
- Do not write new production data into `runtime/`, `runs/`, or `runs/real_all_products/`.
- Do not let Feature Analysts, Strategists, or Backtesters query MongoDB directly.
- Do not perform feature importance, alpha research, strategy formulation, parameter tuning, backtest optimization, portfolio construction, or execution.
- Do not tune data outputs based on downstream strategy or backtest performance.
- Do not use forbidden sources listed in `contracts/PIT_REGISTRY_CONTRACT.md`.
- Do not use `TEJ.AINVFQ1` or `TEJ.APISHRACTW`.

## Source and PIT Rules

- Source family definitions live in `configs/source_family_profiles.json`.
- Source catalog definitions live in `configs/source_catalog.json`.
- PIT rules live in `configs/pit_registry.json`.
- MongoDB connection config lives in `configs/mongodb_sources.json`.
- Universe definitions live in `configs/universe_specs.json`.
- Do not hard-code source schema assumptions outside these configs and the associated normalization code.
- Preserve PIT safety: never make data available before its `source_available_date` or selected `decision_date`.
- Financial statement source must use `TEJ.AINVFINB`; use `key3 <= decision_date` and choose latest `mdate` for duplicate `key3` revisions.
- Trading calendar must use `TEJ.TRADEDAY_TWSE`; blank `date_rmk` means trading day.

## Downstream Data Usage

Downstream agents should follow `AGENT_DATA_USAGE.md`.

CIO handoff must provide `data_store_root: DataAnalysts/data_store`. Downstream agents must resolve it relative to the CIO workspace root and must not assume their cwd is `DataAnalysts/`.

Minimum rules:

- Read `manifests/<artifact_id>.json` under the resolved `data_store_root` before reading parquet.
- Only read manifest-listed `artifact_paths`.
- Check `status == "ready"` before using an artifact.
- Use `membership_by_year` for historical universe research.
- Use `membership_by_date` only for latest convenience reads.
- Use `security_panel_history` for historical tradability and listing state.
- Downstream must not recompute adjusted OHLC; formal publication requires same-version ready evidence.
- Use `dividend_events` and `capital_action_events` when event explanation is needed.
- Avoid recursive full scans under the resolved `data_store_root`; narrow paths by manifest, year partition, columns, and date filters.

## Verification Contract

Before claiming a data product is ready:

```powershell
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

For daily refresh, include the date:

```powershell
python -m data_analysts.cli verify --as-of-date YYYY-MM-DD
python -m data_analysts.cli inspect-artifacts --as-of-date YYYY-MM-DD
```

Ready means:

- command exit code is `0`;
- job result has `status = "ready"`;
- required manifests exist;
- required parquet paths exist;
- inspect reports no blocked reasons;
- no path escapes `data_store`;
- no formal artifact path contains `runtime`, `runs`, or `real_all_products`.

If verification fails, report the blocked step and do not hand artifacts to downstream agents.

## Editing Rules

- Keep DataAnalysts portable. New runtime dependencies must be justified by data correctness or performance.
- Keep writes inside this folder unless the user explicitly points `--data-store` elsewhere.
- Do not modify ALF mainline code to make DataAnalysts work.
- Do not commit generated parquet, diagnostics, jobs, or `data_store` contents unless explicitly requested.
- Prefer small modules with explicit inputs and outputs.
- Add or update tests before behavior changes.
- Use quantitative verification for data changes: row counts, date ranges, partition counts, manifest status, and blocked reason counts.

## Performance Rules

- Treat MongoDB extraction as expensive.
- Prefer incremental `run-daily` or bounded `run-backfill` over full history when the task does not require rebuild.
- For scheduled refresh, prefer `run-daily` without `--as-of-date`; let DataAnalysts use trading calendar and `daily_state.json`.
- For large daily panels, avoid unnecessary whole-history reads.
- For downstream analysis, use manifest paths plus parquet column/date filters.
- Do not scan `membership_by_date/as_of_date=*` for historical research; use `membership_by_year`.

## Reference Documents

- `README.md`: human operation manual.
- `AGENT_DATA_USAGE.md`: downstream agent read patterns.
- `contracts/CLI_CONTRACT.md`: CLI entrypoints and rejection rules.
- `contracts/OUTPUT_CONTRACT.md`: formal `data_store` layout and manifest schema.
- `contracts/CONFIG_CONTRACT.md`: config shape and security rules.
- `contracts/VERIFICATION_CONTRACT.md`: fail-closed verification behavior.
- `contracts/PIT_REGISTRY_CONTRACT.md`: source registry and forbidden-source rules.
