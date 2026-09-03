# ETF Tricks AFML Dataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a downstream, importable, manifest-backed research layer that transforms the validated 13 ETF Tricks Daily NAV and `etf_amount` into PIT-safe Dollar bars, FFD and stationarity evidence, structural features, ML features, and triple-barrier directional labels.

**Architecture:** Add a focused `etf_tricks.afml` package without changing upstream ETF selection, accounting, or execution semantics. The package separates source/PIT normalization, sampling, transforms, features, labels, persistence, and orchestration; `ETFAFMLLab` is the thin Notebook facade and `AFMLDataset` is the immutable result surface. Fit/select operations are training-only and versioned, while transforms and trading snapshots are causal and replayable.

**Tech Stack:** Python 3.12, pandas 3, NumPy 2, PyArrow 25, SciPy 1.18, statsmodels 0.14, `fracdiff-modern==1.0.0` for parity only, pytest 9, project-local `.venv`.

**Spec:** `docs/afml/prompts/02-dataset-master-prompt.md`

## Global Constraints

- Read repository `AGENTS.md`, `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`, `docs/etf_tricks/prompts/01-master-prompt.md`, and the complete AFML master prompt before each implementation batch.
- Treat the approved AFML master prompt as authority. Upstream `ETFTrickResult` NAV, return, holdings, selection, costs, and `etf_amount` semantics are immutable inputs.
- Standard order is Daily NAV/amount -> Dollar bars -> FFD/structural series -> features -> labels. Never aggregate daily FFD into Dollar bars.
- `bar_amount` is exactly the sum of member-day `etf_amount` and is an unscaled formal feature.
- Fit `q*`, `d*`, FFD width, and any data-dependent preprocessing on training data only. Full-history fits are `DESCRIPTIVE_ONLY`.
- Preserve observation, source availability, bar availability, feature availability, label availability, decision, and execution clocks as separate fields.
- Use only backward as-of joins. Never expose open bars, future labels, future `t1`, or calibration-history rows through `for_trading`.
- No new dependency without explicit user approval. Do not install `mlfinlab`, `mlfinpy`, or a fork. Do not modify `requirements.txt` in this plan.
- Core logic stays in Python modules. The Notebook is a thin, output-free quickstart committed at repository root.
- Formal runtime artifacts go only under git-ignored `.artifacts/etf_tricks/afml/<run_id>/`. Do not write canonical parquet under `reports/` or commit market data.
- This is a research-dataset layer, not a strategy or backtest CLI stage; do not add one-off top-level runners or modify `scripts/quant.py`.
- Use TDD for every production behavior: write one failing test, observe the expected failure, implement the minimum code, then run focused and broader tests.
- Correctness tests use hand fixtures first, then one or two ETFs over `2024-01-01..2026-07-07`, then all 13 over the same bounded range. Extend only observation-limited checks to `2020-01-01..2026-07-07`. Full history requires `full_history_acceptance=True` after all bounded gates pass.
- Use `.venv\Scripts\python.exe -m pytest` and preserve deterministic ordering, config hashes, table hashes, and fixed random seeds where randomness is unavoidable.

---

## Planned File Structure

```text
etf_tricks/
  afml/
    __init__.py          public AFML exports only
    config.py            immutable nested configs, modes, hashes, validation
    capabilities.py      source-capability audit and evidence rows
    pit.py               availability normalization, lineage, replay clocks
    dollar_bars.py       q calibration, bar formation, memberships, checkpoints
    ffd.py               fixed-width weights, transform, ADF d search
    structural.py        shared ADF vectors and SADF/QADF/CADF series
    features.py          feasible Tier 1-4 raw features and as-of joins
    labels.py            triple-barrier paths, label clocks, uniqueness
    dataset.py           canonical tables, hashes, read/write, views
    lab.py               ETFAFMLLab orchestration and scope guards
tests/etf_tricks/afml/
  conftest.py
  test_config.py
  test_capabilities.py
  test_pit.py
  test_dollar_bars.py
  test_ffd.py
  test_structural.py
  test_features.py
  test_labels.py
  test_dataset.py
  test_integration.py
ETF_Tricks_AFML_Quickstart.ipynb
docs/etf_tricks/AFML_NOTEBOOK_QUICKSTART.md
```

## Task 1: Public configuration and scope guards

**Files:**
- Create: `etf_tricks/afml/__init__.py`
- Create: `etf_tricks/afml/config.py`
- Modify: `etf_tricks/__init__.py`
- Test: `tests/etf_tricks/afml/test_config.py`

**Interfaces:**
- Produces immutable `DollarBarConfig`, `FFDConfig`, `StructuralConfig`, `FeatureConfig`, `LabelConfig`, `PITConfig`, `AFMLConfig`, `AFMLRunMode`, `AFMLBoundaries`, and `config_sha256(config) -> str`.
- Produces public `AFMLContractError` and `AFMLScopeError` exceptions; specialized modules subclass `AFMLContractError` rather than silently coercing invalid inputs.
- `AFMLConfig()` must be Notebook-usable without hidden globals.
- Candidate q values are training-data-derived from 99 fixed empirical quantile levels between 0.01 and 0.99; the finite candidate-generation rule, not realized q values, is versioned.

- [ ] **Step 1: Write failing configuration tests**

```python
from dataclasses import FrozenInstanceError
import pytest

from etf_tricks.afml import AFMLBoundaries, AFMLConfig, config_sha256


def test_default_config_matches_approved_contract():
    config = AFMLConfig()
    assert config.dollar_bar.market_amount_lookback_days == 60
    assert config.dollar_bar.min_market_amount_observations == 20
    assert config.dollar_bar.candidate_quantile_count == 99
    assert config.dollar_bar.min_completed_bars == 120
    assert config.dollar_bar.max_bar_duration_trading_days == 60
    assert config.ffd.weight_tolerance == 1e-5
    assert config.ffd.coarse_step == 0.05
    assert config.ffd.refine_step == 0.01
    assert config.ffd.autonomous_max_d == 5.0
    assert config.structural.q == 0.95
    assert config.structural.v == 0.025
    assert config.labels.pt_mult == config.labels.sl_mult == 2.0
    assert config.labels.vertical_bars == 60
    assert len(config_sha256(config)) == 64
    with pytest.raises(FrozenInstanceError):
        config.labels.vertical_bars = 5


def test_train_boundaries_are_explicit_and_ordered():
    boundaries = AFMLBoundaries(
        train_start="2020-01-01",
        train_end="2023-12-31",
        validation_end="2024-12-31",
        test_end="2026-07-07",
    )
    assert boundaries.train_end < boundaries.validation_end < boundaries.test_end
    with pytest.raises(ValueError, match="ordered"):
        AFMLBoundaries("2024-01-01", "2025-01-01", "2024-12-31", "2026-01-01")
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_config.py -q`

Expected: collection fails because `etf_tricks.afml` does not exist.

- [ ] **Step 3: Implement immutable configs and deterministic hashing**

Use nested frozen dataclasses with exact defaults from the master prompt. Add explicit validation for positive windows, `0 < q < 1`, `0 < v <= min(q, 1-q)`, ordered boundaries, supported modes `train|walk_forward|research_full_history`, `max_bars_per_day == 1`, and `emit_incomplete_terminal_bar is False`. Serialize dataclasses with `dataclasses.asdict`, JSON `sort_keys=True`, and SHA-256.

- [ ] **Step 4: Run focused and upstream tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_config.py -q
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks -q
```

Expected: config tests pass and the original 94-test suite remains green.

- [ ] **Step 5: Commit only Task 1 files**

```powershell
git add etf_tricks/afml/__init__.py etf_tricks/afml/config.py etf_tricks/__init__.py tests/etf_tricks/afml/test_config.py
git commit -m "feat: add AFML dataset configuration contracts"
```

## Task 2: Manifest source capability and filtered canonical reads

**Files:**
- Modify: `etf_tricks/data_gateway.py`
- Create: `etf_tricks/afml/capabilities.py`
- Test: `tests/etf_tricks/afml/test_capabilities.py`
- Modify: `tests/etf_tricks/test_data_gateway.py`

**Interfaces:**
- Adds `DataGateway.scan_artifact(artifact_id, *, columns, filters, start, end, date_column) -> pd.DataFrame` using manifest-declared paths and PyArrow predicate pushdown without loading all 9.7M daily rows.
- Produces `SourceCapabilityAuditor(gateway).audit() -> pd.DataFrame` with one row each for `IX0001`, `VPIN`, `KYLE_LAMBDA`, `ATR`, `ADX`, and `VIX`.
- Capability status is exactly `AVAILABLE_VERIFIED`, `PARTIAL_COVERAGE`, or `UNAVAILABLE_SOURCE_GRAIN`.

- [ ] **Step 1: Write failing filtered-read and capability tests**

```python
def test_filtered_scan_reads_only_ix0001_rows(manifest_fixture):
    gateway = DataGateway.from_data_analysts(manifest_fixture)
    frame = gateway.scan_artifact(
        "daily_price_volume",
        columns=["date", "ticker", "close", "traded_value"],
        filters=[("ticker", "==", "IX0001")],
        start="2024-01-01",
        end="2024-01-31",
    )
    assert frame["ticker"].unique().tolist() == ["IX0001"]
    assert frame["date"].between("2024-01-01", "2024-01-31").all()


def test_filtered_scan_binds_dates_to_physical_string_schema(manifest_fixture):
    gateway = DataGateway.from_data_analysts(manifest_fixture)
    frame = gateway.scan_artifact(
        "daily_price_volume",
        columns=["date", "ticker"],
        filters=[("ticker", "==", "IX0001")],
        start=pd.Timestamp("2024-01-01"),
        end=pd.Timestamp("2024-01-31"),
    )
    assert pd.api.types.is_datetime64_any_dtype(frame["date"])
    assert frame["date"].min() >= pd.Timestamp("2024-01-01")


def test_capability_audit_does_not_invent_unavailable_features(manifest_fixture):
    table = SourceCapabilityAuditor(
        DataGateway.from_data_analysts(manifest_fixture)
    ).audit()
    status = table.set_index("feature_id")["status"].to_dict()
    assert status["IX0001"] == "AVAILABLE_VERIFIED"
    assert status["VPIN"] == "UNAVAILABLE_SOURCE_GRAIN"
    assert status["KYLE_LAMBDA"] == "UNAVAILABLE_SOURCE_GRAIN"
    assert status["ATR"] == "UNAVAILABLE_SOURCE_GRAIN"
    assert status["ADX"] == "UNAVAILABLE_SOURCE_GRAIN"
    assert status["VIX"] == "UNAVAILABLE_SOURCE_GRAIN"
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_capabilities.py tests\etf_tricks\test_data_gateway.py -q`

Expected: failures name the missing `scan_artifact` and `SourceCapabilityAuditor` interfaces.

- [ ] **Step 3: Implement manifest-first predicate scans and capability evidence**

`scan_artifact` must validate requested columns, coverage metadata, resolved paths inside `data_store`, and filtered logical-key uniqueness. It inspects each physical Arrow schema before constructing predicates: the current daily-price `date` field is an ISO string, so bounded predicates bind ISO strings at scan time and normalize/revalidate timestamps after scan. It must never use undeclared files or silently fall back to a different store. The capability audit records required fields, observed artifact/columns, manifest hash, selected-row content hash, coverage, PIT policy, revision status, reason, and evidence timestamp; constituent daily OHLC must not qualify as true synthetic ETF Trick OHLC.

- [ ] **Step 4: Run tests and measure the real IX0001 bounded scan**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_capabilities.py tests\etf_tricks\test_data_gateway.py -q
.\.venv\Scripts\python.exe -c "from etf_tricks.data_gateway import DataGateway; g=DataGateway.from_data_analysts('DataAnalysts'); x=g.scan_artifact('daily_price_volume', columns=['date','ticker','close','traded_value'], filters=[('ticker','==','IX0001')], start='2024-01-01', end='2026-07-07'); print(len(x), x.ticker.unique().tolist(), x.date.min(), x.date.max())"
```

Expected: tests pass; the live scan returns only IX0001 over the bounded interval and does not materialize the full universe.

- [ ] **Step 5: Commit Task 2 files**

```powershell
git add etf_tricks/data_gateway.py etf_tricks/afml/capabilities.py tests/etf_tricks/afml/test_capabilities.py tests/etf_tricks/test_data_gateway.py
git commit -m "feat: audit AFML source capabilities"
```

## Task 3: PIT source adapter and knowledge clocks

**Files:**
- Create: `etf_tricks/afml/pit.py`
- Test: `tests/etf_tricks/afml/test_pit.py`
- Create: `tests/etf_tricks/afml/conftest.py`

**Interfaces:**
- Produces frozen `PITDailyInputs(daily_etf, ix0001, trading_calendar, source_capabilities, source_identity)`.
- `PITSourceAdapter(gateway).prepare(base, boundaries, config) -> PITDailyInputs` verifies the upstream result identity and derives date-only after-close availability without pretending to know a publication timestamp.
- `next_execution_session(calendar, feature_available_at, decision_cutoff) -> pd.Timestamp` is the only execution-calendar mapper used downstream.
- Produces `PITContractError(AFMLContractError)` for identity, availability, calendar, and replay violations.

- [ ] **Step 1: Write failing clock tests**

```python
def test_date_only_market_data_is_after_close_and_next_session_executable(pit_fixture):
    inputs = pit_fixture.inputs
    row = inputs.daily_etf.query("etf_id == 'momentum'").iloc[0]
    assert row["availability_assumption"] == "AFTER_CLOSE_DATE_ONLY"
    assert str(row["source_available_at"].tz) == "Asia/Taipei"
    assert row["source_revision_status"] == "PIT_REVISION_UNVERIFIED"
    execution = next_execution_session(
        inputs.trading_calendar,
        row["source_available_at"],
        decision_cutoff="after_close",
    )
    assert execution > row["date"]


def test_current_manifest_hash_mismatch_fails_closed(pit_fixture):
    stale = copy.deepcopy(pit_fixture.base)
    stale.metadata["manifest_hashes"]["daily_price_volume"] = "stale"
    with pytest.raises(PITContractError, match="daily_price_volume"):
        pit_fixture.adapter.prepare(stale, pit_fixture.boundaries, AFMLConfig())
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_pit.py -q`

Expected: import or interface failure for the absent PIT adapter.

- [ ] **Step 3: Implement source identity, availability assumptions, and execution calendar mapping**

Derive synthetic row availability from the daily-price manifest PIT policy plus prior holdings/targets already validated by the upstream result. For `source_date_lagged_to_decision_date`, store the observation-day end as a timezone-aware conservative sentinel, preserve `availability_assumption`, and make the next trading session the earliest execution. Treat `data_cutoff_at` as lineage only. Missing vintage remains `PIT_REVISION_UNVERIFIED`.

- [ ] **Step 4: Run PIT and upstream regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_pit.py -q
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\test_result.py tests\etf_tricks\test_integration.py -q
```

Expected: all pass with no upstream semantic change.

- [ ] **Step 5: Commit Task 3 files**

```powershell
git add etf_tricks/afml/pit.py tests/etf_tricks/afml/conftest.py tests/etf_tricks/afml/test_pit.py
git commit -m "feat: preserve AFML point-in-time clocks"
```

## Task 4: Common q calibration and daily-derived Dollar bars

**Files:**
- Create: `etf_tricks/afml/dollar_bars.py`
- Test: `tests/etf_tricks/afml/test_dollar_bars.py`

**Interfaces:**
- `DollarBarCalibrator(config).fit(daily_etf, ix0001, boundaries, etf_ids) -> QCalibration`.
- `DollarBarBuilder(config).transform(daily_etf, ix0001, calendar, calibration, role) -> DollarBarTables`.
- `DollarBarTables` contains `dollar_bars`, `bar_daily_membership`, `open_bar_checkpoints`, and `calibration_evidence`.
- Subset calibration is allowed only for bounded tests and is marked `TEST_ONLY_SUBSET`; production readiness requires one common q calibrated across all 13 ETF IDs.

- [ ] **Step 1: Write failing hand-calculation tests**

```python
def test_threshold_freezes_and_bar_amount_reconciles():
    daily, ix, calendar, calibration = three_bar_fixture()
    tables = DollarBarBuilder(AFMLConfig().dollar_bar).transform(
        daily, ix, calendar, calibration, role="CALIBRATION_HISTORY"
    )
    bars = tables.dollar_bars
    assert bars["bar_amount"].tolist() == pytest.approx([110.0, 130.0, 100.0])
    reconciled = tables.bar_daily_membership.groupby(["etf_id", "bar_id"])["etf_amount"].sum()
    assert reconciled.tolist() == pytest.approx(bars.set_index(["etf_id", "bar_id"])["bar_amount"])
    assert bars.iloc[0]["threshold_asof_date"] < bars.iloc[0]["bar_start_date"]
    assert bars.iloc[0]["threshold_amount"] == bars.iloc[0]["frozen_threshold_amount"]


def test_one_day_can_close_at_most_one_bar_and_overshoot_is_not_carried():
    tables = build_single_day_overshoot_fixture(amount=500.0, threshold=100.0)
    assert len(tables.dollar_bars) == 1
    assert tables.dollar_bars.iloc[0]["overshoot_amount"] == 400.0
    assert tables.open_bar_checkpoints.empty


def test_future_append_preserves_finalized_prefix():
    prefix = build_replay_fixture(end="2024-01-10")
    extended = build_replay_fixture(end="2024-01-31")
    pd.testing.assert_frame_equal(
        prefix.dollar_bars,
        extended.dollar_bars.query("bar_end_date <= '2024-01-10'").reset_index(drop=True),
    )
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_dollar_bars.py -q`

Expected: import failure for the absent bar builder.

- [ ] **Step 3: Implement deterministic q fit and bar state machine**

Compute daily ratios `etf_amount / lagged_60d_IX0001_median`, generate 99 candidate q values from fixed training-only empirical quantiles, deterministically deduplicate/sort them, and choose the largest candidate for which every requested ETF has at least 120 completed training bars and maximum completed-bar duration no greater than 60 trading days. Save every candidate result. Build bars with a frozen threshold at start, one close per day, no overshoot carry, explicit `OPEN_PROVISIONAL`, member-level lineage, and role `CALIBRATION_HISTORY|LIVE_ELIGIBLE`.

- [ ] **Step 4: Run bar tests and bounded replay benchmark**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_dollar_bars.py -q
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_pit.py tests\etf_tricks\afml\test_dollar_bars.py -q
```

Expected: all hand calculations and prefix-invariance checks pass.

- [ ] **Step 5: Commit Task 4 files**

```powershell
git add etf_tricks/afml/dollar_bars.py tests/etf_tricks/afml/test_dollar_bars.py
git commit -m "feat: build PIT-safe ETF Dollar bars"
```

## Task 5: Fixed-width FFD and governed d search

**Files:**
- Create: `etf_tricks/afml/ffd.py`
- Test: `tests/etf_tricks/afml/test_ffd.py`

**Interfaces:**
- `fixed_width_weights(d, tolerance) -> np.ndarray` ordered current-to-oldest.
- `apply_fixed_width_ffd(values, weights) -> np.ndarray` returns valid-only output.
- `FFDSelector(config).fit(log_nav, calibration_version) -> FFDSelection`.
- `FFDSelector.transform(log_nav, selection) -> pd.DataFrame` never refits.

- [ ] **Step 1: Write failing mathematical and leakage tests**

```python
def test_recursive_weights_and_d_boundaries():
    assert fixed_width_weights(0.0, 1e-5).tolist() == [1.0]
    assert fixed_width_weights(1.0, 1e-5).tolist() == pytest.approx([1.0, -1.0])
    weights = fixed_width_weights(0.5, 0.05)
    assert weights.tolist() == pytest.approx([1.0, -0.5, -0.125, -0.0625])


def test_valid_transform_uses_only_current_and_past():
    values = np.log(np.array([100, 101, 102, 104, 103], dtype=float))
    weights = np.array([1.0, -0.5])
    result = apply_fixed_width_ffd(values, weights)
    assert result.tolist() == pytest.approx(values[1:] - 0.5 * values[:-1])
    extended = apply_fixed_width_ffd(np.r_[values, np.log(999.0)], weights)
    assert extended[: len(result)].tolist() == pytest.approx(result)


def test_selector_chooses_minimum_passing_d(monkeypatch):
    monkeypatch.setattr("etf_tricks.afml.ffd._adf", deterministic_adf_gate)
    selection = FFDSelector(AFMLConfig().ffd).fit(random_walk_fixture(), "cal-1")
    assert selection.d == pytest.approx(0.37)
    assert selection.search_evidence.query("passed").iloc[0]["d"] == pytest.approx(0.37)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_ffd.py -q`

Expected: missing FFD interfaces.

- [ ] **Step 3: Implement weights, convolution, ADF search, and evidence**

Use `statsmodels.tsa.stattools.adfuller` with `regression='c', maxlag=1, autolag=None`. Search `[0,1]` by 0.05, refine the first passing bracket by 0.01, then finite spans `(1,2]`, `(2,3]`, `(3,4]`, `(4,5]` only after diagnostics. A pass requires both `p < 0.05` and statistic below the 5% critical value with at least 120 post-FFD observations. Record every attempt, width, observations, correlation, diagnostic status, config, and stop reason. `fracdiff-modern` is used only in a parity test, never as authority.

- [ ] **Step 4: Run FFD tests and statsmodels/library parity**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_ffd.py -q`

Expected: recursive, d=0/1, prefix invariance, minimum passing d, failure status, and `fracdiff-modern` parity tests pass.

- [ ] **Step 5: Commit Task 5 files**

```powershell
git add etf_tricks/afml/ffd.py tests/etf_tricks/afml/test_ffd.py
git commit -m "feat: add governed fixed-width FFD"
```

## Task 6: SADF, QADF, and Conditional ADF series

**Files:**
- Create: `etf_tricks/afml/structural.py`
- Test: `tests/etf_tricks/afml/test_structural.py`

**Interfaces:**
- `adf_start_vector(log_prices, end, min_sample_length, lags) -> tuple[np.ndarray, np.ndarray]` returns start positions and right-tail ADF beta t-statistics.
- `structural_statistics(adf_values, q, v, quantile_method) -> dict[str, float]`.
- `StructuralFeatureEngine(config).transform(frame, value_column, available_at_column) -> pd.DataFrame`.

- [ ] **Step 1: Write failing QADF/CADF definition tests**

```python
def test_structural_statistics_keep_qadf_and_cadf_dispersion_distinct():
    values = np.array([-2.0, -1.0, 0.0, 1.0, 8.0])
    result = structural_statistics(values, q=0.8, v=0.1, quantile_method="linear")
    assert result["sadf"] == 8.0
    assert result["qadf"] == pytest.approx(np.quantile(values, 0.8, method="linear"))
    assert result["qadf_dispersion"] == pytest.approx(
        np.quantile(values, 0.9) - np.quantile(values, 0.7)
    )
    tail = values[values >= result["qadf"]]
    assert result["cadf"] == pytest.approx(tail.mean())
    assert result["cadf_dispersion"] == pytest.approx(tail.std(ddof=0))


def test_structural_prefix_is_unchanged_by_future_append():
    prefix = StructuralFeatureEngine(config).transform(series.iloc[:150], "log_close", "available_at")
    extended = StructuralFeatureEngine(config).transform(series, "log_close", "available_at")
    pd.testing.assert_frame_equal(prefix, extended.iloc[: len(prefix)].reset_index(drop=True))
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_structural.py -q`

Expected: absent structural module/interfaces.

- [ ] **Step 3: Implement shared ADF vectors with sufficient-statistics reuse**

For each endpoint, build lag-1 ADF regression cross-products from prefix sums and evaluate all governed starts in NumPy batches. Reuse the single t-stat vector for SADF, QADF, QADF quantile spread, CADF tail mean, CADF population dispersion, and z-score. Store counts, min/max start, maximizing start, q/v/method, availability, and quality reason. Compute ETF structural features on raw Dollar-bar log NAV and IX0001 structural features on daily log close; align later by backward as-of.

- [ ] **Step 4: Run statsmodels parity and performance checks**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_structural.py -q`

Expected: small-window t-statistics match direct OLS/statsmodels tolerance; definitions and append invariance pass; no three duplicated nested pandas loops exist.

- [ ] **Step 5: Commit Task 6 files**

```powershell
git add etf_tricks/afml/structural.py tests/etf_tricks/afml/test_structural.py
git commit -m "feat: add reusable structural ADF features"
```

## Task 7: Feasible Tier 1-4 feature table

**Files:**
- Create: `etf_tricks/afml/features.py`
- Test: `tests/etf_tricks/afml/test_features.py`

**Interfaces:**
- `AFMLFeatureEngine(config).build(bars, memberships, ffd, structural_etf, structural_ix, base) -> pd.DataFrame`.
- Output key is `(etf_id, bar_id)` and every row has `feature_available_at`, per-source timestamps, staleness, observation counts, missingness flags, and calibration/config versions.

- [ ] **Step 1: Write failing feature tests**

```python
def test_bar_amount_is_raw_and_ratio_excludes_current_bar(feature_fixture):
    features = feature_fixture.features
    row = features.query("bar_id == 21").iloc[0]
    history = feature_fixture.bars.query("1 <= bar_id <= 20")["bar_amount"]
    assert row["bar_amount"] == feature_fixture.bars.query("bar_id == 21").iloc[0]["bar_amount"]
    assert row["amount_ratio_20"] == pytest.approx(row["bar_amount"] / history.mean())


def test_future_market_row_does_not_enter_backward_asof_join(feature_fixture):
    decision = feature_fixture.features.iloc[-1]["feature_available_at"]
    future_ix = feature_fixture.structural_ix.assign(
        feature_available_at=lambda x: x["feature_available_at"] + pd.Timedelta(days=30)
    )
    result = AFMLFeatureEngine(feature_fixture.config).build(
        *feature_fixture.inputs(structural_ix=future_ix)
    )
    assert result.iloc[-1]["ix_sadf"] is pd.NA or pd.isna(result.iloc[-1]["ix_sadf"])


def test_unavailable_microstructure_names_are_absent(feature_fixture):
    assert not {"vpin", "kyle_lambda", "atr", "adx", "vix"}.intersection(
        feature_fixture.features.columns
    )
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_features.py -q`

Expected: missing feature engine.

- [ ] **Step 3: Implement raw, causal features only**

Implement the approved feasible features: FFD level/distance/volatility, raw log returns and distribution shape, close-path range, efficiency, drawdown, duration surprise, raw `bar_amount`, `log1p_bar_amount`, prior-20 amount ratio, prior-only EWMA z-score, overshoot, duration, market share, Amihud, HHI/cash/invested/holdings/target completion, IX0001 daily return/volatility/drawdown/structural statistics, and ETF-vs-IX interval beta/correlation. Preserve missing values and reasons; do no imputation, winsorization, or scaling.

- [ ] **Step 4: Run feature and prefix-invariance tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_features.py -q`

Expected: calculations, staleness gates, as-of joins, `bar_amount`, unavailable-source honesty, and future-append invariance pass.

- [ ] **Step 5: Commit Task 7 files**

```powershell
git add etf_tricks/afml/features.py tests/etf_tricks/afml/test_features.py
git commit -m "feat: build causal AFML feature tables"
```

## Task 8: Triple-barrier labels and overlap evidence

**Files:**
- Create: `etf_tricks/afml/labels.py`
- Test: `tests/etf_tricks/afml/test_labels.py`

**Interfaces:**
- `TripleBarrierLabeler(config).build(features, bars, memberships, split_cutoffs) -> LabelTables`.
- `LabelTables` contains `events` and `labels` with independent availability clocks; event concurrency and average uniqueness are columns in `events`, not a hidden third canonical table.

- [ ] **Step 1: Write failing first-touch and cutoff tests**

```python
def test_daily_close_first_touch_precedes_future_bar_close(label_fixture):
    tables = TripleBarrierLabeler(label_fixture.config).build(*label_fixture.inputs)
    event = tables.labels.query("event_id == 'momentum-1'").iloc[0]
    assert event["first_touch_type"] == "upper"
    assert event["first_touch_date"] < event["vertical_date"]
    assert event["label"] == 1
    assert event["label_available_at"] >= event["first_touch_source_available_at"]


def test_unresolved_tail_is_not_shortened(label_fixture):
    event = TripleBarrierLabeler(label_fixture.config).build(*label_fixture.tail_inputs).labels.iloc[0]
    assert event["label_status"] == "unresolved_tail"
    assert pd.isna(event["label"])


def test_training_label_requires_t1_and_availability_before_cutoff(label_fixture):
    labels = TripleBarrierLabeler(label_fixture.config).build(*label_fixture.delayed_inputs).labels
    row = labels.iloc[0]
    assert row["t1"] <= label_fixture.train_end
    assert row["label_available_at"] > label_fixture.train_cutoff
    assert not bool(row["eligible_for_train"])
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_labels.py -q`

Expected: absent label module.

- [ ] **Step 3: Implement EWMA barriers, daily-close path search, and uniqueness**

Use prior-and-current 60-bar EWMA log-return volatility with 20 observations minimum, symmetric log barriers at 2x, and a vertical barrier exactly 60 completed bars after the event. Search member daily closes after `t0`; never use intraday highs/lows. Preserve first touch, t1, label availability, unresolved tail, zero-return policy, concurrency, and average uniqueness. Features and labels remain separate tables.

- [ ] **Step 4: Run label and leakage tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_labels.py -q`

Expected: upper/lower/vertical/zero/unresolved paths, overlapping events, availability gates, and future-path isolation pass.

- [ ] **Step 5: Commit Task 8 files**

```powershell
git add etf_tricks/afml/labels.py tests/etf_tricks/afml/test_labels.py
git commit -m "feat: add PIT-aware triple-barrier labels"
```

## Task 9: Canonical dataset artifacts and Notebook views

**Files:**
- Create: `etf_tricks/afml/dataset.py`
- Test: `tests/etf_tricks/afml/test_dataset.py`

**Interfaces:**
- `AFMLDataset` owns the 12 canonical tables from the master prompt plus metadata/readiness.
- `AFML_TABLE_NAMES` is the exact ordered tuple `source_capabilities, dollar_bars, open_bar_checkpoints, bar_daily_membership, ffd_weights, ffd_search, ffd_series, structural_features, features, events, labels, diagnostics`.
- `AFMLDataset.write(output_dir) -> dict`, `AFMLDataset.read(output_dir) -> AFMLDataset` use atomic Parquet/JSON writes and SHA-256 verification.
- `for_ml(etf_id, split)` returns event rows with separately joined eligible labels.
- `for_trading(as_of, decision_cutoff)` returns only PIT-qualified snapshots and never loads label tables.
- `train`, `validation`, and `test` are explicit split-view properties over feature/event availability, not duplicated stored tables.

- [ ] **Step 1: Write failing round-trip and schema-isolation tests**

```python
def test_afml_dataset_round_trip_verifies_all_table_hashes(tmp_path, dataset_fixture):
    manifest = dataset_fixture.write(tmp_path / "afml-run")
    assert set(manifest["tables"]) == set(AFML_TABLE_NAMES)
    restored = AFMLDataset.read(tmp_path / "afml-run")
    pd.testing.assert_frame_equal(restored.features, dataset_fixture.features)
    assert restored.metadata["config_sha256"] == dataset_fixture.metadata["config_sha256"]


def test_for_trading_cannot_expose_labels_or_future_rows(dataset_fixture):
    snapshot = dataset_fixture.for_trading(
        as_of="2025-01-31", decision_cutoff="after_close"
    )
    forbidden = {"label", "t1", "label_available_at", "first_touch_date"}
    assert forbidden.isdisjoint(snapshot.columns)
    assert snapshot["feature_available_at"].le(snapshot["decision_time"]).all()
    assert snapshot["live_eligible"].all()


def test_split_views_use_feature_and_label_availability(dataset_fixture):
    assert dataset_fixture.train["feature_available_at"].le(
        dataset_fixture.metadata["train_decision_cutoff"]
    ).all()
    labelled = dataset_fixture.train.dropna(subset=["label"])
    assert labelled["label_available_at"].le(
        dataset_fixture.metadata["train_decision_cutoff"]
    ).all()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_dataset.py -q`

Expected: missing dataset surface.

- [ ] **Step 3: Implement immutable table validation, atomic persistence, and views**

Validate exact keys, monotonicity, table schemas, source/config identities, and hashes. `for_ml` must expose missing reasons instead of silently dropping rows. `for_trading` computes a timezone-aware decision time, applies availability/staleness/calibration/live gates, derives the next execution session, returns explicit unavailable statuses, and references only features/bars/calibration tables.

- [ ] **Step 4: Run corruption, key, and view tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_dataset.py -q`

Expected: round-trip, tamper detection, duplicate rejection, pre-calibration unavailable, provisional-only unavailable, staleness failure, and label schema isolation pass.

- [ ] **Step 5: Commit Task 9 files**

```powershell
git add etf_tricks/afml/dataset.py tests/etf_tricks/afml/test_dataset.py
git commit -m "feat: persist AFML datasets with PIT views"
```

## Task 10: ETFAFMLLab orchestration, readiness, and bounded scope policy

**Files:**
- Create: `etf_tricks/afml/lab.py`
- Modify: `etf_tricks/afml/__init__.py`
- Modify: `etf_tricks/__init__.py`
- Test: `tests/etf_tricks/afml/test_integration.py`

**Interfaces:**
- `ETFAFMLLab.from_data_analysts(root) -> ETFAFMLLab`.
- `build_all(base, *, config, mode, train_start, train_end, validation_end, test_end, etf_ids=None, retrain_dates=(), full_history_acceptance=False) -> AFMLDataset`.
- Pipeline is source audit -> PIT inputs -> q calibration -> bars -> FFD -> structural -> features -> labels -> readiness.

- [ ] **Step 1: Write failing end-to-end and full-history-guard tests**

```python
def test_build_all_returns_importable_tables_for_two_etfs(bounded_base_result, data_root):
    dataset = ETFAFMLLab.from_data_analysts(data_root).build_all(
        bounded_base_result,
        config=AFMLConfig(),
        mode="train",
        train_start="2024-01-01",
        train_end="2025-06-30",
        validation_end="2025-12-31",
        test_end="2026-07-07",
        etf_ids=("momentum", "low_volatility"),
    )
    assert set(dataset.dollar_bars["etf_id"]) == {"momentum", "low_volatility"}
    assert dataset.features["bar_amount"].notna().all()
    assert dataset.metadata["scope"] == "BOUNDED_TEST"


def test_full_history_13_etf_scope_requires_explicit_acceptance(full_base_result, data_root):
    with pytest.raises(AFMLScopeError, match="full_history_acceptance"):
        ETFAFMLLab.from_data_analysts(data_root).build_all(
            full_base_result,
            config=AFMLConfig(),
            mode="train",
            train_start="2005-01-03",
            train_end="2020-12-31",
            validation_end="2023-12-31",
            test_end="2026-07-07",
        )


def test_research_full_history_is_descriptive_only(bounded_base_result, data_root):
    dataset = ETFAFMLLab.from_data_analysts(data_root).build_all(
        bounded_base_result,
        config=AFMLConfig(),
        mode="research_full_history",
        train_start="2024-01-01",
        train_end="2025-06-30",
        validation_end="2025-12-31",
        test_end="2026-07-07",
        etf_ids=("momentum",),
    )
    assert dataset.metadata["readiness_scope"] == "DESCRIPTIVE_ONLY"
    with pytest.raises(AFMLScopeError, match="DESCRIPTIVE_ONLY"):
        dataset.for_ml("momentum", split="test")


def test_walk_forward_versions_do_not_recut_open_bars(bounded_base_result, data_root):
    dataset = ETFAFMLLab.from_data_analysts(data_root).build_all(
        bounded_base_result,
        config=AFMLConfig(),
        mode="walk_forward",
        train_start="2024-01-01",
        train_end="2025-06-30",
        validation_end="2025-12-31",
        test_end="2026-07-07",
        etf_ids=("momentum",),
        retrain_dates=("2025-07-01", "2026-01-02"),
    )
    bars = dataset.dollar_bars.sort_values("bar_start_date")
    assert bars.groupby(["etf_id", "bar_id"])["calibration_version"].nunique().max() == 1
    assert not bars.duplicated(["etf_id", "bar_id"]).any()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_integration.py -q`

Expected: missing lab and scope guard.

- [ ] **Step 3: Implement orchestrator, diagnostics, and fail-closed readiness**

Build each layer once and pass typed tables forward. Record elapsed time and peak RSS per stage, row counts, missing causes, per-ETF d search status, feature coverage, label coverage, and capability statuses. Readiness is `READY` only if every requested ETF passes required bars, FFD, PIT, feature, label, hash, and schema gates; optional unavailable VPIN/Kyle/ATR/ADX/VIX remain explicit limitations rather than fabricated columns.

- [ ] **Step 4: Run all AFML tests, then all upstream tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml -q
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks -q
```

Expected: AFML and original ETF suites pass.

- [ ] **Step 5: Commit Task 10 files**

```powershell
git add etf_tricks/afml/lab.py etf_tricks/afml/__init__.py etf_tricks/__init__.py tests/etf_tricks/afml/test_integration.py
git commit -m "feat: orchestrate ETF AFML datasets"
```

## Task 11: Quickstart Notebook and reader-facing guide

**Files:**
- Create: `ETF_Tricks_AFML_Quickstart.ipynb`
- Create: `docs/etf_tricks/AFML_NOTEBOOK_QUICKSTART.md`
- Test: `tests/etf_tricks/afml/test_notebook_quickstart.py`

**Interfaces:**
- Notebook lives at repository root so `import etf_tricks` works when its kernel uses the repository `.venv`.
- It reads an existing bounded `ETFTrickResult`, builds or reads an `AFMLDataset`, shows `dataset.dollar_bars`, `features`, `labels`, `for_ml`, and `for_trading`, and contains no embedded market-data output.

- [ ] **Step 1: Write a failing notebook contract test**

```python
def test_quickstart_is_output_free_and_uses_public_api():
    notebook = json.loads(Path("ETF_Tricks_AFML_Quickstart.ipynb").read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell.get("source", [])) for cell in notebook["cells"] if cell["cell_type"] == "code"
    )
    assert "from etf_tricks.afml import AFMLConfig, ETFAFMLLab" in source
    assert "ETFTrickResult.read" in source
    assert "dataset.for_ml" in source
    assert "dataset.for_trading" in source
    assert all(not cell.get("outputs") for cell in notebook["cells"] if cell["cell_type"] == "code")
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_notebook_quickstart.py -q`

Expected: missing Notebook file.

- [ ] **Step 3: Create a thin tutorial Notebook and exact setup guide**

Use sections `Goal`, `Setup`, `Build or Read`, `Checks`, and `Next Steps`. Parameters default to the bounded optimized result at `.artifacts/etf_tricks/performance/optimized-final-20240101-20260707`, but the path remains a visible editable cell and supports an `ETF_TRICK_RESULT_DIR` environment override for tests. State that the kernel must be `C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe`. Because `nbformat`, `nbclient`, and `nbconvert` are not installed and new dependencies are forbidden, generate valid notebook JSON with no outputs and execute its code cells in order in the test using a shared namespace plus a synthetic artifact fixture.

- [ ] **Step 4: Validate JSON, cell execution, and no committed outputs**

Run:

```powershell
.\.venv\Scripts\python.exe -m json.tool ETF_Tricks_AFML_Quickstart.ipynb > $null
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_notebook_quickstart.py -q
```

Expected: valid JSON, public API execution succeeds on the fixture, and every code cell has empty outputs/execution count.

- [ ] **Step 5: Commit Task 11 files**

```powershell
git add ETF_Tricks_AFML_Quickstart.ipynb docs/etf_tricks/AFML_NOTEBOOK_QUICKSTART.md tests/etf_tricks/afml/test_notebook_quickstart.py
git commit -m "docs: add ETF AFML Notebook quickstart"
```

## Task 12: Bounded real-artifact acceptance and one-time full-history gate

**Files:**
- Create runtime artifacts only: `.artifacts/etf_tricks/afml/<run_id>/`
- Create: `docs/etf_tricks/afml/2026-08-27-afml-readiness.md`
- Test: existing AFML integration and validation suites

**Interfaces:**
- Bounded input: `.artifacts/etf_tricks/performance/optimized-final-20240101-20260707/result_manifest.json`.
- Full input, only after bounded acceptance: `.artifacts/etf_tricks/full-history-20050103-20260707-v5/result_manifest.json`.
- Evidence report contains `目前可用`, `目前缺失／限制`, per-stage timing/memory, table hashes, and every final gate.

- [ ] **Step 1: Run hand and one/two-ETF bounded tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks\afml\test_dollar_bars.py tests\etf_tricks\afml\test_ffd.py tests\etf_tricks\afml\test_structural.py tests\etf_tricks\afml\test_features.py tests\etf_tricks\afml\test_labels.py -q
```

Expected: all hand and causal-prefix tests pass.

- [ ] **Step 2: Build two ETFs over 2024-2026**

Run this public API from repository root and write `.artifacts/etf_tricks/afml/bounded-2etf-20240101-20260707`:

```python
from etf_tricks import ETFTrickResult
from etf_tricks.afml import AFMLConfig, ETFAFMLLab

base = ETFTrickResult.read(
    ".artifacts/etf_tricks/performance/optimized-final-20240101-20260707"
)
dataset = ETFAFMLLab.from_data_analysts("DataAnalysts").build_all(
    base,
    config=AFMLConfig(),
    mode="train",
    train_start="2024-01-01",
    train_end="2025-06-30",
    validation_end="2025-12-31",
    test_end="2026-07-07",
    etf_ids=("momentum", "low_volatility"),
)
dataset.write(".artifacts/etf_tricks/afml/bounded-2etf-20240101-20260707")
```

If FFD/window observations are insufficient, repeat only this gate with the 2020-2026 bounded input and `train_start="2020-01-01"`; record the exact observation deficit that required extension.

- [ ] **Step 3: Build all 13 ETFs over 2024-2026**

Repeat the preceding call with `etf_ids=None` and output `.artifacts/etf_tricks/afml/bounded-13etf-20240101-20260707`. Require all 13 IDs, unique keys, bar reconciliation, source-capability evidence, q evidence, FFD search evidence, PIT replay, feature/label schema, hashes, and Notebook reads. Do not treat observation-limited stationarity as a code pass.

- [ ] **Step 4: Run one explicit full-history acceptance only after Steps 1-3 pass**

Run the exact acceptance scope below and write `.artifacts/etf_tricks/afml/full-history-20050103-20260707-v1`:

```python
base = ETFTrickResult.read(
    ".artifacts/etf_tricks/full-history-20050103-20260707-v5"
)
dataset = ETFAFMLLab.from_data_analysts("DataAnalysts").build_all(
    base,
    config=AFMLConfig(),
    mode="train",
    train_start="2005-01-03",
    train_end="2018-12-31",
    validation_end="2022-12-31",
    test_end="2026-07-07",
    full_history_acceptance=True,
)
dataset.write(".artifacts/etf_tricks/afml/full-history-20050103-20260707-v1")
```

Record wall time, per-stage time, peak RSS, per-ETF counts/statuses, and hashes. Do not rerun full history to debug a bounded failure.

- [ ] **Step 5: Write the readiness report from fresh artifacts**

The report must state exact usable tables/features and exact missing sources. It must distinguish `READY`, `PARTIAL_COVERAGE`, `PIT_REVISION_UNVERIFIED`, `stationarity_not_reached`, and `UNAVAILABLE_SOURCE_GRAIN`; it may claim the goal complete only when all mandatory gates across all 13 ETFs are proven by the final artifact.

- [ ] **Step 6: Run final verification and commit only source/tests/docs**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\etf_tricks -q
git diff --check
git status --short
```

Expected: all tests pass, no whitespace errors, runtime artifacts remain ignored, and unrelated user files remain untouched.

```powershell
git add etf_tricks tests/etf_tricks ETF_Tricks_AFML_Quickstart.ipynb docs/etf_tricks/AFML_NOTEBOOK_QUICKSTART.md docs/etf_tricks/afml/2026-08-27-afml-readiness.md
git commit -m "feat: complete ETF AFML dataset foundation"
```

## Plan Self-Review Map

- Master sections 3-5 -> Tasks 1-3.
- Master section 6 -> Task 4.
- Master section 7 -> Task 5.
- Master section 8 -> Task 6.
- Master section 9 -> Task 7.
- Master section 10 -> Task 8.
- Master sections 11-12 -> Tasks 9-11.
- Master sections 13-15 -> Tasks 10-12.
- No task trains an ML model, claims profitability, changes upstream ETF accounting, or submits orders.
- No task adds dependencies, broad-scans 13 ETF full history during debugging, or exposes labels through the trading API.
