# DataAnalysts Source Catalog, PIT Registry, Raw Expansion, and Historical Universe Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend DataAnalysts from price/events/latest-universe coverage into a broader PIT-safe portable data product with explicit source contracts, richer raw families, and historical universe membership.

**Architecture:** Split the work into three bounded specs. First, build a machine-readable Source Catalog and PIT Registry that fail closed before any new extractor runs. Second, expand raw families in small source groups while preserving raw revisions and publishing quantitative diagnostics. Third, build historical universes from trading-calendar dates using an explicit `as_of_date` / `effective_date` contract and year-partitioned membership files.

**Tech Stack:** Python 3, `pyarrow` parquet, `pymongo` MongoDB extraction, JSON configs/contracts, CLI verification, artifact diagnostics. Temporary tests may be recreated during implementation; if the final product should remain test-folder-free, remove tests only after CLI verification and diagnostic scans pass.

## Global Constraints

- All generated and edited DataAnalysts artifacts must stay under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not use ALF main-flow modules as runtime adapters.
- Do not write raw/generated data artifacts to git.
- Fail closed on missing data, unsupported schema, forbidden source usage, ambiguous PIT dates, unresolved duplicate logical rows, invalid universe membership, and universe small-file regressions.
- `TEJ.AINVFQ1` is forbidden and must not be used for financial statements.
- `TEJ.APISHRACTW` is deprecated/forbidden and must not be used.
- `TEJ.AINVFINB` is the only financial statement source.
- `TEJ.AINVFINB.source_available_date = normalize_date(key3)`.
- `TEJ.AINVFINB` PIT selection must enforce `source_available_date <= decision_date`.
- `TEJ.AINVFINB` raw canonical output must preserve all revisions; selected PIT views may choose latest valid revisions.
- For `TEJ.AINVFINB`, if multiple rows share the same logical statement identity and the same normalized `key3`, selected PIT views choose latest normalized `mdate`.
- For `TEJ.TRADEDAY_TWSE`, `date_rmk` blank after trimming means the date is a trading day.
- Universe membership must distinguish `as_of_date` from `effective_date`.
- A universe built with same-day close, market cap, volume, or ADV is effective no earlier than the next trading day.

---

## Spec Decomposition

### Spec A: PIT Foundation

**Purpose:** Establish the source catalog, forbidden-source rules, PIT field rules, date normalization, and revision-selection helpers before expanding data coverage.

**Owns:**
- `configs/source_catalog.json`
- `configs/pit_registry.json`
- `contracts/PIT_REGISTRY_CONTRACT.md`
- validation in `config.py`, `verify.py`, `source_catalog.py`, and `pit.py`

**Does Not Own:**
- Mongo extraction for new raw families.
- Security panel generation.
- Universe membership generation.

### Spec B: Raw Family Expansion

**Purpose:** Add approved raw families in small groups after PIT foundation exists.

**Owns:**
- `trading_calendar`
- `daily_tradability`
- `daily_chip`
- `monthly_sales`
- `financial_statement`
- `company_self_reported_numbers`
- generic mdate governance/event tables
- futures near-month, if still in DataAnalysts scope

**Does Not Own:**
- Historical universe definitions, membership ranking, or effective dating.
- Feature-specific transformations beyond canonical raw and selected PIT surfaces.

### Spec C: Historical Universe

**Purpose:** Replace latest-only universe publishing with trading-calendar-driven, year-partitioned historical membership.

**Owns:**
- Universe definitions.
- `as_of_date` / `effective_date` semantics.
- Historical security panel snapshots.
- Year-partitioned universe membership.
- Small-file verification.

**Does Not Own:**
- Adding new raw source families.
- PIT registry semantics, except consuming them.

---

## Target PIT Registry

| family_id | DB | collection pattern | PIT field | normalize | logical key | revision key | include phase |
|---|---|---|---|---|---|---|---|
| trading_calendar | TEJ | TRADEDAY_TWSE | zdate | date only | date, market | none | 1 |
| daily_tradability | APISTKATTR | per ticker collection | mdate | date only | date, ticker | none | 1 |
| daily_chip | APISHRACT | per ticker collection | mdate | date only | date, ticker | none | 1 |
| monthly_sales | TEJ | APISALE | annd_s | date only | ticker, source_period_date | source_available_date, mdate | 2 |
| financial_statement_raw | TEJ | AINVFINB | key3 | date only | ticker, no, sem, curr, merg, period_end_date, source_available_date, revision_date | none; preserve all revisions | 2 |
| financial_statement_pit_selected | derived | financial_statement_raw | source_available_date | date only | ticker, no, sem, curr, merg, period_end_date, decision_date | source_available_date desc, revision_date desc | 2 |
| self_reported_numbers_raw | TEJ | AFESTM1 | annd | date only | ticker, key3, sem, curr, merg, period_end_date, source_available_date, revision_date | none; preserve all revisions | 3 |
| self_reported_numbers_pit_selected | derived | self_reported_numbers_raw | source_available_date | date only | ticker, key3, sem, curr, merg, period_end_date, decision_date | source_available_date desc, revision_date desc | 3 |
| director_supervisor_holdings | TEJ | APIBSTN1 | mdate | date only | ticker, source_date | mdate | 3 |
| board_reelection_statistics | TEJ | APICHGSTAT | mdate | date only | ticker, source_date | mdate | 3 |
| executive_change_events | TEJ | APIDIRCHG | mdate | date only | ticker, source_date | mdate | 3 |
| merger_acquisition_events | TEJ | APIMA | mdate | date only | ticker, source_date | mdate | 3 |
| private_placement_relation_events | TEJ | APISTKPRV | mdate | date only | ticker, source_date | mdate | 3 |
| insider_transfer_completed | TEJ | APITRANS1 | mdate | date only | ticker, source_date | mdate | 3 |
| insider_transfer_declared_not_completed | TEJ | APITRANS2 | mdate | date only | ticker, source_date | mdate | 3 |
| treasury_stock_events | TEJ | APITRS | mdate | date only | ticker, source_date | mdate | 3 |
| taiwan_index_futures_near_month | Futures_TAIFEX_TX | TX_1 | 日期 | date only | date, contract | none | 4 |

Forbidden registry entries:

| DB | collection | reason |
|---|---|---|
| TEJ | AINVFQ1 | deprecated financial source; use AINVFINB only |
| TEJ | APISHRACTW | deprecated; do not use |

## Quantitative Verification Standards

Every task must write or update diagnostics under `runs/real_all_products/diagnostics` when run on real data.

Minimum metrics:

- `source_row_count`
- `published_row_count`
- `omitted_row_count`
- `pit_null_count`
- `pit_parse_failure_count`
- `duplicate_logical_key_count`
- `resolved_duplicate_count`
- `unresolved_duplicate_count`
- `forbidden_source_usage_count`
- `date_min`
- `date_max`
- `partition_count`
- `artifact_file_count`

Required zero thresholds:

- `forbidden_source_usage_count == 0`
- `pit_parse_failure_count == 0` unless the source family contract explicitly allows omission and reports `omitted_row_count`
- `unresolved_duplicate_count == 0`
- `artifact_path_outside_root_count == 0`

---

## File Structure

- Create `configs/source_catalog.json`: complete source list, forbidden sources, row-count metadata, PIT fields, date normalization, logical keys, revision keys, and inclusion phase.
- Create `configs/pit_registry.json`: executable PIT rules consumed by config validation and extractors.
- Modify `configs/source_family_profiles.json`: add approved families only after the registry exists.
- Modify `configs/mongodb_sources.json`: add explicit connections for `apistkattr`, `apishract`, and `futures_taifex_tx`.
- Create `contracts/PIT_REGISTRY_CONTRACT.md`: reader-facing PIT field and selection rules.
- Modify `contracts/CONFIG_CONTRACT.md`: require source catalog and PIT registry; forbid AINVFQ1/APISHRACTW.
- Modify `contracts/OUTPUT_CONTRACT.md`: add raw outputs, selected PIT outputs, and historical universe outputs.
- Modify `contracts/VERIFICATION_CONTRACT.md`: add fail-closed checks and quantitative thresholds.
- Create `src/data_analysts/pit.py`: date normalization and PIT row selection helpers.
- Create `src/data_analysts/source_catalog.py`: catalog loading, forbidden-source validation, and registry validation.
- Create `src/data_analysts/raw_families.py`: family-specific raw normalization.
- Create `src/data_analysts/diagnostics.py`: metric collection and JSON diagnostic writers.
- Modify `src/data_analysts/config.py`: load and validate the new catalog files.
- Modify `src/data_analysts/extract.py`: support registry-driven bounded extraction.
- Modify `src/data_analysts/pipeline.py`: orchestrate new families in phases.
- Modify `src/data_analysts/security_panel.py`: consume trading calendar and tradability when available.
- Modify `src/data_analysts/universe.py`: publish historical, year-partitioned universe membership.
- Modify `src/data_analysts/verify.py`: enforce new contracts.

---

## Task 1: PIT Foundation Contracts

**Files:**
- Create: `configs/source_catalog.json`
- Create: `configs/pit_registry.json`
- Create: `contracts/PIT_REGISTRY_CONTRACT.md`
- Modify: `contracts/CONFIG_CONTRACT.md`
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`

**Boundary:**
- This task defines contracts only. It must not add new extraction behavior.
- It may describe future artifacts but must not create runtime parquet files.

**Interfaces:**
- Produces: `source_catalog.json`, `pit_registry.json`, and contract documentation.
- Consumes: user-approved PIT field rules.

- [ ] Add `configs/source_catalog.json` with `schema_version`, `sources`, and `forbidden_sources`.
- [ ] Add `configs/pit_registry.json` with one PIT rule per approved family.
- [ ] In `PIT_REGISTRY_CONTRACT.md`, document all PIT dates must be normalized to `YYYY-MM-DD` before filtering.
- [ ] In `PIT_REGISTRY_CONTRACT.md`, document raw vs selected PIT surfaces:
  - raw canonical preserves source rows and revisions
  - selected PIT views apply `decision_date`
  - selected PIT views must not overwrite raw revisions
- [ ] In `PIT_REGISTRY_CONTRACT.md`, document `AINVFINB` exact selected-view rule:
  - filter `normalize_date(key3) <= decision_date`
  - group by `ticker, no, sem, curr, merg, period_end_date`
  - choose max `source_available_date`
  - within same `source_available_date`, choose max `revision_date`
  - if still duplicated, fail closed and write diagnostics
- [ ] In `CONFIG_CONTRACT.md`, state that any config referencing `TEJ.AINVFQ1` or `TEJ.APISHRACTW` is invalid.
- [ ] In `OUTPUT_CONTRACT.md`, define all new raw, selected, and universe artifact paths.
- [ ] In `VERIFICATION_CONTRACT.md`, add numeric thresholds listed in this plan.

**Quantitative Verification:**
- `forbidden_source_count` in catalog equals `2`.
- `approved_source_count` is at least `15`.
- `pit_registry_family_count` equals approved source families plus derived selected surfaces.
- `forbidden_source_usage_count == 0`.
- `missing_pit_field_count == 0`.
- `missing_logical_key_count == 0`.

## Task 2: PIT Foundation Loader and Validation

**Files:**
- Create: `src/data_analysts/source_catalog.py`
- Create: `src/data_analysts/pit.py`
- Create: `src/data_analysts/diagnostics.py`
- Modify: `src/data_analysts/config.py`
- Modify: `src/data_analysts/verify.py`

**Boundary:**
- This task validates configs and implements reusable PIT helpers.
- It must not publish new raw family artifacts.

**Interfaces:**
- Produces:
  - `load_source_catalog(root) -> dict`
  - `load_pit_registry(root) -> dict`
  - `normalize_date(value) -> str | None`
  - `select_latest_pit_rows(rows, rule, decision_date) -> tuple[list[dict], dict]`
  - `write_diagnostic(root, name, payload) -> Path`
- Consumes: `configs/source_catalog.json`, `configs/pit_registry.json`.

- [ ] Add loader for `source_catalog.json`.
- [ ] Add loader for `pit_registry.json`.
- [ ] Reject missing catalog files.
- [ ] Reject unsupported `schema_version`.
- [ ] Reject duplicate `family_id`.
- [ ] Reject catalog entries referencing forbidden sources.
- [ ] Reject source family profiles referencing forbidden sources.
- [ ] Implement `normalize_date()` for `datetime`, `date`, ISO strings, and strings with `HH:MM:SS`.
- [ ] Implement `select_latest_pit_rows()` for selected PIT surfaces.
- [ ] Return diagnostics from selected PIT helpers:
  - `input_row_count`
  - `eligible_row_count`
  - `selected_row_count`
  - `future_row_count`
  - `resolved_duplicate_count`
  - `unresolved_duplicate_count`
- [ ] Add verification diagnostics under `runtime/jobs/verification_result.json`.

**Quantitative Verification:**
- Invalid config referencing `TEJ.AINVFQ1` returns `blocked`.
- Invalid config referencing `TEJ.APISHRACTW` returns `blocked`.
- `normalize_date("2025-03-31 00:00:00") == "2025-03-31"`.
- AINVFINB fixture with two rows sharing same `key3` selects the max `mdate`.
- AINVFINB fixture with later `key3 > decision_date` excludes that row.
- `unresolved_duplicate_count == 0` for clean fixtures.

## Task 3: Trading Calendar

**Files:**
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`
- Modify: `src/data_analysts/verify.py`

**Boundary:**
- This task only creates `trading_calendar`.
- It must not change universe behavior yet.

**Interfaces:**
- Produces artifact `trading_calendar`.
- Schema:
  - `date`
  - `market`
  - `is_trading_day`
  - `date_rmk`
  - `source_available_date`
  - `data_cutoff_at`

- [ ] Add `trading_calendar` family using `TEJ.TRADEDAY_TWSE`.
- [ ] Normalize `zdate` to `date`.
- [ ] Set `source_available_date = date`.
- [ ] Set `market = mkt`.
- [ ] Set `is_trading_day = true` only when `date_rmk` is blank after trimming.
- [ ] Publish as single-file parquet because row count is small.
- [ ] Reject duplicate `(date, market)`.

**Quantitative Verification:**
- `source_row_count > 0`.
- `published_row_count > 0`.
- `trading_day_count > 0`.
- `non_trading_day_count > 0`.
- `duplicate_date_market_count == 0`.
- `date_min <= "2005-01-01"`.
- `date_max >= run end date` when source contains it.

## Task 4: Daily Tradability and Daily Chip

**Files:**
- Modify: `configs/mongodb_sources.json`
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/extract.py`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`

**Boundary:**
- This task adds only per-ticker daily raw panels from `APISTKATTR` and `APISHRACT`.
- It must not create security panel or universe behavior changes.

**Interfaces:**
- Produces:
  - `daily_tradability` from `APISTKATTR.{ticker}`
  - `daily_chip` from `APISHRACT.{ticker}`
- Both use `mdate` as `date` and `source_available_date`.
- Both are partitioned by `year`.

- [ ] Add Mongo connections for `APISTKATTR` and `APISHRACT`.
- [ ] Add `daily_tradability` profile with collection pattern `{ticker}`.
- [ ] Add `daily_chip` profile with collection pattern `{ticker}`.
- [ ] Normalize `mdate` to `date` and `source_available_date`.
- [ ] Reject rows without ticker or mdate.
- [ ] Publish year partitions.
- [ ] Add duplicate diagnostics for `(date, ticker)` per family.

**Quantitative Verification:**
- For each family:
  - `source_collection_count > 0`
  - `source_row_count > 0`
  - `published_row_count > 0`
  - `pit_null_count == 0`
  - `pit_parse_failure_count == 0`
  - `duplicate_date_ticker_count == 0` or `resolved_duplicate_count > 0` with `unresolved_duplicate_count == 0`
  - `date_min` and `date_max` are reported
  - `partition_count` equals number of years with rows

## Task 5: Monthly Sales

**Files:**
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`

**Boundary:**
- This task only adds `monthly_sales`.
- It must not mix monthly sales into financial statements or universe rules.

**Interfaces:**
- Produces artifact `monthly_sales`.
- Source: `TEJ.APISALE`.
- PIT field: `annd_s`.
- Schema prefix:
  - `ticker`
  - `source_period_date`
  - `source_available_date`
  - `data_cutoff_at`

- [ ] Add source family `monthly_sales`.
- [ ] Normalize `mdate` as `source_period_date`.
- [ ] Normalize `annd_s` as `source_available_date`.
- [ ] Reject rows where `annd_s` is missing or unparsable.
- [ ] Preserve all non-metadata source fields after canonical prefix.
- [ ] Partition by `available_year`.

**Quantitative Verification:**
- `source_row_count > 0`.
- `published_row_count > 0`.
- `pit_null_count == 0`.
- `pit_parse_failure_count == 0`.
- `source_available_date_min` and `source_available_date_max` are reported.
- `period_date_min` and `period_date_max` are reported.
- `duplicate_logical_key_count` is reported.
- `unresolved_duplicate_count == 0`.

## Task 6: Financial Statement Raw and PIT Selected Views

**Files:**
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pit.py`
- Modify: `src/data_analysts/pipeline.py`
- Modify: `src/data_analysts/verify.py`

**Boundary:**
- This task handles only `TEJ.AINVFINB`.
- It must not read `TEJ.AINVFQ1`.
- It must not discard `A` or `TTM` from raw canonical output.
- It must separate raw revisions from selected PIT views.

**Interfaces:**
- Produces raw artifact `financial_statement_raw`.
- Produces selected artifact `financial_statement_pit_selected` only for requested `decision_date` or requested date range.
- Source: `TEJ.AINVFINB` only.
- Forbidden: `TEJ.AINVFQ1`.
- Raw PIT field: `key3`.
- Revision field: `mdate`.
- Logical statement identity: `ticker, no, sem, curr, merg, period_end_date`.

- [ ] Add `financial_statement_raw` source family using `TEJ.AINVFINB`.
- [ ] Normalize `begd` to `period_start_date`.
- [ ] Normalize `endd` to `period_end_date`.
- [ ] Normalize `mdate` to `source_period_date` and `revision_date`.
- [ ] Normalize `key3` to `source_available_date`.
- [ ] Preserve `no` values `A`, `Q`, and `TTM` in raw canonical output.
- [ ] Preserve all raw revisions; do not collapse rows in raw output.
- [ ] Add selected PIT logic:
  - filter `source_available_date <= decision_date`
  - group by `ticker, no, sem, curr, merg, period_end_date`
  - choose max `source_available_date`
  - within same `source_available_date`, choose max `revision_date`
  - block if still duplicated
- [ ] Add a default reader-facing selector for `no = Q`; keep `A` and `TTM` available but not default.
- [ ] Add verification that no `AINVFQ1` source appears in manifests or catalog references.

**Quantitative Verification:**
- `forbidden_source_usage_count == 0`.
- `raw_source_row_count > 0`.
- `raw_published_row_count > 0`.
- Rows by `no` are reported for `A`, `Q`, `TTM`, and other values.
- `key3_null_count == 0`.
- `key3_parse_failure_count == 0`.
- `same_identity_same_key3_duplicate_count` is reported.
- `resolved_by_latest_mdate_count` is reported.
- `unresolved_after_latest_mdate_count == 0`.
- Selected PIT diagnostics report:
  - `decision_date_count`
  - `eligible_row_count`
  - `future_row_excluded_count`
  - `selected_row_count`
  - `selected_no_q_row_count`

## Task 7: Self-Reported Numbers from AFESTM1

**Files:**
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pit.py`
- Modify: `src/data_analysts/pipeline.py`
- Modify: `src/data_analysts/verify.py`

**Boundary:**
- This task handles only `TEJ.AFESTM1`.
- It must not be bundled with generic governance tables because it is revision-aware and has A/Q/TTM-like categories.
- `AFESTM1.key3` is a form/category field, not a date.

**Interfaces:**
- Produces raw artifact `self_reported_numbers_raw`.
- Produces selected artifact `self_reported_numbers_pit_selected` only for requested decision dates.
- PIT field: `annd`.
- Revision field: `mdate`.

- [ ] Add `self_reported_numbers_raw` source family.
- [ ] Normalize `annd` to `source_available_date`.
- [ ] Normalize `mdate` to `revision_date`.
- [ ] Preserve `key3` as statement form/category.
- [ ] Preserve raw revisions.
- [ ] Add selected PIT rule equivalent to financial statements but using `annd` as availability.

**Quantitative Verification:**
- `source_row_count > 0`.
- `pit_null_count == 0`.
- `pit_parse_failure_count == 0`.
- Rows by `key3` category are reported.
- `resolved_by_latest_mdate_count` is reported.
- `unresolved_duplicate_count == 0`.

## Task 8: Generic Governance and Event Tables

**Files:**
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`

**Boundary:**
- This task handles only generic mdate-PIT governance/event tables.
- It must not include `AFESTM1`, `AINVFINB`, `APISHRACTW`, or `AINVFQ1`.

**Interfaces:**
- Produces raw canonical artifacts:
  - `director_supervisor_holdings` from `TEJ.APIBSTN1`, PIT `mdate`
  - `board_reelection_statistics` from `TEJ.APICHGSTAT`, PIT `mdate`
  - `executive_change_events` from `TEJ.APIDIRCHG`, PIT `mdate`
  - `merger_acquisition_events` from `TEJ.APIMA`, PIT `mdate`
  - `private_placement_relation_events` from `TEJ.APISTKPRV`, PIT `mdate`
  - `insider_transfer_completed` from `TEJ.APITRANS1`, PIT `mdate`
  - `insider_transfer_declared_not_completed` from `TEJ.APITRANS2`, PIT `mdate`
  - `treasury_stock_events` from `TEJ.APITRS`, PIT `mdate`

- [ ] Add one source family profile per table.
- [ ] Normalize `mdate` to `source_available_date`.
- [ ] Preserve full source fields after canonical prefix.
- [ ] Partition by `available_year`.
- [ ] Reject rows missing ticker where ticker is required.
- [ ] Report per-family diagnostics independently.

**Quantitative Verification:**
- For each family:
  - `source_row_count` is reported
  - `published_row_count` is reported
  - `pit_null_count == 0`
  - `pit_parse_failure_count == 0`
  - `duplicate_logical_key_count` is reported
  - `unresolved_duplicate_count == 0`

## Task 9: Futures Near-Month

**Files:**
- Modify: `configs/mongodb_sources.json`
- Modify: `configs/source_family_profiles.json`
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`

**Boundary:**
- This task is optional unless DataAnalysts scope explicitly includes multi-asset data.
- It must not affect equity universe or equity security panel logic.

**Interfaces:**
- Produces artifact `taiwan_index_futures_near_month`.
- Source: `Futures_TAIFEX_TX.TX_1`.
- PIT field: `日期`.
- Logical key: `date, contract`.

- [ ] Add Mongo connection `futures_taifex_tx`.
- [ ] Add source family `taiwan_index_futures_near_month`.
- [ ] Normalize `日期` to `date` and `source_available_date`.
- [ ] Preserve contract identifier.
- [ ] Reject duplicate `(date, contract)`.

**Quantitative Verification:**
- `source_row_count > 0`.
- `published_row_count > 0`.
- `pit_null_count == 0`.
- `pit_parse_failure_count == 0`.
- `duplicate_date_contract_count == 0`.

## Task 10: Historical Security Panel

**Files:**
- Modify: `src/data_analysts/security_panel.py`
- Modify: `src/data_analysts/pipeline.py`
- Modify: `src/data_analysts/verify.py`

**Boundary:**
- This task creates historical security panel snapshots.
- It must not publish universe membership yet.

**Interfaces:**
- Consumes:
  - `daily_price_volume`
  - `security_master`
  - `trading_calendar`
  - `daily_tradability`, when available
- Produces year-partitioned `security_panel_history`.
- Required date semantics:
  - `as_of_date`: market observation date after close
  - `effective_date`: next trading day from `trading_calendar`

- [ ] Generate panel dates from `trading_calendar.is_trading_day == true`.
- [ ] For each `as_of_date`, compute latest known price and tradability fields at or before `as_of_date`.
- [ ] Compute `effective_date` as the next trading day after `as_of_date`.
- [ ] Publish year-partitioned panel rows by `as_of_year`.
- [ ] Reject duplicate `(as_of_date, ticker)`.

**Quantitative Verification:**
- `as_of_date_count > 0`.
- `effective_date_null_count == 0` except for the last calendar date when no next trading day exists; that count must be reported.
- `duplicate_as_of_ticker_count == 0`.
- `calendar_coverage_ratio >= 0.99` for dates within data availability.
- `panel_row_count_by_year` is reported.

## Task 11: Historical Universe Publisher

**Files:**
- Modify: `configs/universe_specs.json`
- Modify: `src/data_analysts/universe.py`
- Modify: `src/data_analysts/pipeline.py`
- Modify: `src/data_analysts/verify.py`

**Boundary:**
- This task consumes historical security panel and publishes universe membership.
- It must not create one parquet file per day.
- It must not use same-day membership for same-day trading.

**Interfaces:**
- Produces year-partitioned universe membership:
  - `runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet`
- Schema:
  - `as_of_date`
  - `effective_date`
  - `universe_id`
  - `ticker`
  - `rank`
  - `included`
  - `reason`
  - `market`
  - `security_type`
  - `listed`
  - `tradable`
  - `close`
  - `adj_close`
  - `market_cap`
  - `adv20`
  - `data_cutoff_at`

- [ ] Add baseline universes:
  - `tw_equity_all_listed`
  - `tw_common_stock_all`
  - `tw_common_stock_tradable`
  - `tw_equity_liquid_top100`
  - `tw_equity_liquid_top300`
  - `tw_equity_liquid_top500`
  - `twse_common_stock`
  - `tpex_common_stock`
- [ ] Build membership from `security_panel_history`.
- [ ] Use `effective_date` from panel history.
- [ ] Publish many dates per yearly parquet partition.
- [ ] Keep latest-date convenience output only if explicitly marked as derived convenience.
- [ ] Reject duplicate `(universe_id, effective_date, ticker)`.

**Quantitative Verification:**
- `universe_count >= 8`.
- `artifact_file_count <= universe_count * year_count + convenience_file_count`.
- `small_file_daily_partition_count == 0`.
- `duplicate_universe_effective_ticker_count == 0`.
- For top-N universes, each full eligible date has row count `<= N`.
- For top-N universes, dates with eligible count `>= N` have row count exactly `N`.
- `calendar_trading_day_coverage_ratio >= 0.99` for dates with security panel data.

## Task 12: Full Product Verification and Refresh

**Files:**
- Modify: `src/data_analysts/verify.py`
- Create diagnostics under `runs/real_all_products/diagnostics`.

**Boundary:**
- This task runs final verification only.
- It must not introduce new source-family behavior.

**Interfaces:**
- Produces final complete portable DataAnalysts runtime.

- [ ] Run full bounded rebuild through the requested current date.
- [ ] Run `python -m data_analysts.cli verify --root runs\real_all_products`.
- [ ] Run `python -m data_analysts.cli inspect-artifacts --root runs\real_all_products`.
- [ ] Write diagnostics:
  - source catalog coverage
  - forbidden source scan
  - PIT missing field scan
  - AINVFINB duplicate/revision scan
  - raw family row counts
  - universe small-file count
  - universe historical coverage
  - effective-date coverage
- [ ] Confirm no output is outside DataAnalysts root.

**Quantitative Verification:**
- `verify_status == ready`.
- `inspect_status == ready`.
- `forbidden_source_usage_count == 0`.
- `artifact_path_outside_root_count == 0`.
- `pit_parse_failure_count_total == 0` for required PIT fields.
- `unresolved_duplicate_count_total == 0`.
- `historical_universe_file_count` is year-partition scaled, not trading-day scaled.
- `effective_date_null_count` is reported and justified by calendar end only.

## Completion Evidence

The expansion is complete only when all of the following are true:

- `source_catalog.json` and `pit_registry.json` exist and validate.
- `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are rejected by config validation.
- `trading_calendar` exists and uses `date_rmk` blank as trading day.
- `daily_tradability`, `daily_chip`, `monthly_sales`, and `financial_statement_raw` publish ready manifests.
- `financial_statement_raw` uses only `TEJ.AINVFINB`.
- `financial_statement_raw` preserves raw revisions.
- `financial_statement_pit_selected` normalizes `key3`, enforces `key3 <= decision_date`, and resolves same-key3 revisions by latest `mdate`.
- `self_reported_numbers_raw` and `self_reported_numbers_pit_selected` treat `AFESTM1.key3` as category, not date.
- Historical security panel includes `as_of_date` and `effective_date`.
- Historical universes are year-partitioned, not one tiny file per day.
- Top-N universe row counts are quantitatively checked.
- `verify` returns `ready`.
- `inspect-artifacts` returns `ready`.
- Diagnostics show zero forbidden source usage and zero unresolved PIT ambiguity.
