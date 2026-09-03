# ETF AFML Dataset Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use \`superpowers:executing-plans\` to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Produce PIT-safe, manifest-backed AFML research datasets from validated ETF Trick results without changing the upstream ETF accounting contract.

**Architecture:** \`ETFAFMLLab\` consumes one identified \`ETFTrickResult\` plus manifest-declared IX0001/calendar rows. It builds close-path Dollar bars, train-fitted fixed-width FFD, structural features, causal features, and isolated triple-barrier labels. Every table carries source availability, calibration version, and hash lineage; the trading API only exposes feature-ready information by knowledge time.

**Tech Stack:** Python 3.12, pandas, NumPy, SciPy/statsmodels, PyArrow/Parquet, pytest. No new dependency, \`mlfinlab\`, \`mlfinpy\`, broker code, or raw-market artifact.

**Spec:** \`docs/etf_tricks/prompts/04-afml-dataset-master-prompt.md\`, \`docs/superpowers/specs/2026-08-26-etf-tricks-design.md\`

## Global Constraints

- Upstream \`ETFTrickResult\` NAV, returns, holdings, costs, selection, and \`etf_amount\` are immutable inputs.
- Normal validation is 2024-01-01 to 2026-07-07; extend to 2020-01-01 only for recorded observation insufficiency. Full 13-ETF history requires \`full_history_acceptance=True\`.
- Daily data permits at most one close-path bar per ETF/day. It is not tick, intraday OHLC, VPIN, or signed-order-flow data.
- Fit \`q*\`, \`d*\`, FFD width and all data-dependent preprocessing only on training data. Causal transforms may use earlier finalized bars as warm-up.
- Preserve timestamps, identities, revision evidence and quality flags. Date-only availability is conservative \`after_close\`; missing vintage evidence is \`PIT_REVISION_UNVERIFIED\`.
- \`daily_market_state\`/DMS coverage is an upstream readiness gate. Do not weaken AFML to hide incomplete inputs.
- Runtime artifacts are git-ignored under \`.artifacts/etf_tricks/afml/<run_id>/\`.

## File Structure

| File | Responsibility |
|---|---|
| \`pit.py\` | Validate upstream result identity/readiness, source availability, calendar, and IX0001. |
| \`capabilities.py\` | Evidence-backed VPIN/Kyle/ATR/ADX/VIX capability states. |
| \`dollar_bars.py\` | Common train-only q calibration, close-path state machine, membership/checkpoints. |
| \`ffd.py\` | Fixed-width weights, governed d-star selection, diagnostics. |
| \`structural.py\`, \`features.py\` | Causal SADF/QADF/CADF and honest feature matrix. |
| \`labels.py\`, \`dataset.py\`, \`lab.py\` | Labels, canonical schemas/manifests/views, public orchestration/profiling. |
| \`tests/etf_tricks/afml/\` | Hand fixtures, replay, parity, integration and performance gates. |

### Task 1: Gate upstream input identity and coverage

**Files:**
- Modify: \`etf_tricks/afml/pit.py\`, \`etf_tricks/afml/lab.py\`
- Test: \`tests/etf_tricks/afml/test_pit.py\`, \`tests/etf_tricks/afml/test_integration.py\`

**Produces:** \`PreparedAFMLInputs\` only for one table/result identity with a valid upstream readiness report, unique keys, required manifests, and availability fields. Constituent-level DMS coverage remains an upstream producer responsibility.

- [ ] **Step 1: Write failing tests**

\`\`\`python
def test_prepare_rejects_not_ready_upstream_result(base, gateway):
    base.readiness["headline"] = "NOT READY"
    with pytest.raises(AFMLContractError, match="upstream.*NOT READY"):
        PITSourceAdapter(gateway).prepare(base, BOUNDARIES, AFMLConfig())

def test_prepare_rejects_mixed_result_identity(base, gateway):
    with pytest.raises(AFMLContractError, match="result identity"):
        PITSourceAdapter(gateway).prepare(MIXED_RESULT, BOUNDARIES, AFMLConfig())
\`\`\`

- [ ] **Step 2: Run RED**

Run: \`python -m pytest tests/etf_tricks/afml/test_pit.py -q\`

Expected: each invalid upstream fixture reaches the adapter and fails before the guard is implemented.

- [ ] **Step 3: Implement minimal fail-closed check**

\`\`\`python
headline = base.readiness["headline"].iloc[0]
if headline != "READY":
    raise AFMLContractError(f"upstream ETF result is not READY: {headline}")
\`\`\`

Validate manifest/table hash consistency and upstream readiness/coverage evidence before normalization; reject duplicates instead of deduplicating. DMS repairs happen in the upstream DMS/ETF pipeline, never in AFML.

- [ ] **Step 4: Run regression and commit**

Run: \`python -m pytest tests/etf_tricks/afml/test_pit.py tests/etf_tricks/afml/test_integration.py -q\`

\`\`\`powershell
git add etf_tricks/afml/pit.py etf_tricks/afml/lab.py tests/etf_tricks/afml/test_pit.py tests/etf_tricks/afml/test_integration.py
git commit -m "fix: gate afml on validated upstream coverage"
\`\`\`

### Task 2: Publish source capability evidence before features

**Files:**
- Modify: \`etf_tricks/afml/capabilities.py\`, \`etf_tricks/afml/pit.py\`
- Test: \`tests/etf_tricks/afml/test_capabilities.py\`

**Produces:** \`source_capabilities\` with only \`AVAILABLE_VERIFIED\`, \`PARTIAL_COVERAGE\`, or \`UNAVAILABLE_SOURCE_GRAIN\`.

- [ ] **Step 1: Write RED tests**

\`\`\`python
def test_daily_data_cannot_claim_microstructure(gateway):
    states = CapabilityAuditor(gateway).audit().set_index("feature")["status"]
    assert states["vpin"] == "UNAVAILABLE_SOURCE_GRAIN"
    assert states["kyle_lambda"] == "UNAVAILABLE_SOURCE_GRAIN"

def test_missing_true_ohlc_keeps_atr_adx_unavailable(gateway):
    states = CapabilityAuditor(gateway).audit().set_index("feature")["status"]
    assert states["atr"] == states["adx"] == "UNAVAILABLE_SOURCE_GRAIN"
\`\`\`

- [ ] **Step 2: Run RED** — \`python -m pytest tests/etf_tricks/afml/test_capabilities.py -q\`.
- [ ] **Step 3: Implement evidence checks** — require tick/trade plus aggressor side for VPIN/Kyle, true synchronized ETF OHLC for ATR/ADX, and manifest/PIT/coverage validated VIX. Preserve exact absent fields/source paths; do not create misnamed proxies.
- [ ] **Step 4: Verify and commit**

Run: \`python -m pytest tests/etf_tricks/afml/test_capabilities.py tests/etf_tricks/afml/test_features.py -q\`

\`\`\`powershell
git add etf_tricks/afml/capabilities.py etf_tricks/afml/pit.py tests/etf_tricks/afml/test_capabilities.py
git commit -m "feat: publish afml source capability evidence"
\`\`\`

### Task 3: Lock Dollar-bar math and PIT lifecycle

**Files:**
- Modify: \`etf_tricks/afml/dollar_bars.py\`
- Test: \`tests/etf_tricks/afml/test_dollar_bars.py\`, \`tests/etf_tricks/afml/test_pit.py\`

**Produces:** finalized \`dollar_bars\`, \`bar_daily_membership\`, and separate \`open_bar_checkpoints\`.

- [ ] **Step 1: Write three-to-five-day RED fixtures**

\`\`\`python
def test_threshold_is_frozen_and_membership_reconciles():
    bars = DollarBarBuilder(CONFIG).build(DAYS, IX0001, CALIBRATION)
    assert bars.dollar_bars.loc[0, ["threshold_amount", "bar_amount", "overshoot_amount"]].tolist() == [100., 115., 15.]
    assert bars.bar_daily_membership["etf_amount"].sum() == 115.

def test_one_large_daily_amount_forms_one_bar():
    assert len(DollarBarBuilder(CONFIG).build(ONE_LARGE_DAY, IX0001, CALIBRATION).dollar_bars) == 1
\`\`\`

Also test delayed member source, terminal incomplete tail, low-activity longer duration, append-prefix immutability, and same-close execution denial.

- [ ] **Step 2: Run RED** — \`python -m pytest tests/etf_tricks/afml/test_dollar_bars.py tests/etf_tricks/afml/test_pit.py -q\`.
- [ ] **Step 3: Implement canonical state machine**

\`\`\`python
if accumulated_amount >= frozen_threshold:
    finalize(members, bar_available_at=max(members.member_available_at))
else:
    checkpoint_open_bar(members)
\`\`\`

Use strictly prior 60 IX0001 observations (minimum 20), fixed train-only common \`q*\`, one bar/day, no overshoot carry, and date-only \`after_close\` availability.

- [ ] **Step 4: Verify and commit**

Run: \`python -m pytest tests/etf_tricks/afml/test_dollar_bars.py tests/etf_tricks/afml/test_pit.py -q\`

\`\`\`powershell
git add etf_tricks/afml/dollar_bars.py tests/etf_tricks/afml/test_dollar_bars.py tests/etf_tricks/afml/test_pit.py
git commit -m "feat: enforce pit-safe close-path dollar bars"
\`\`\`

### Task 4: Govern train-only fixed-width FFD selection

**Files:**
- Modify: \`etf_tricks/afml/ffd.py\`, \`etf_tricks/afml/config.py\`
- Test: \`tests/etf_tricks/afml/test_ffd.py\`

**Produces:** \`ffd_weights\`, full search audit, per-ETF d-star and causal \`ffd_series\`.

- [ ] **Step 1: Write RED tests**

\`\`\`python
def test_recurrence_matches_hand_convolution():
    assert np.allclose(apply_ffd(X, fixed_width_weights(.4, 1e-3)), EXPECTED)

def test_search_escalates_without_alpha_change():
    result = FFDSelector(CONFIG).select(LOG_NAV, TRAIN_END)
    assert "(1.00,2.00]" in set(result.search["searched_interval"])
    assert result.search["alpha"].eq(.05).all()
\`\`\`

Also test \`d=0\`, \`d=1\`, valid-only window, first passing grid point, finite \`>2\` expansion, full-transform prefix stability, and \`DESCRIPTIVE_ONLY\` full-history fit.

- [ ] **Step 2: Run RED** — \`python -m pytest tests/etf_tricks/afml/test_ffd.py -q\`.
- [ ] **Step 3: Implement recurrence and search**

\`\`\`python
w = [1.0]
while abs(w[-1]) >= tolerance:
    k = len(w)
    w.append(-w[-1] * (d - k + 1.0) / k)
valid = np.convolve(log_nav.to_numpy(), np.asarray(w), mode="valid")
\`\`\`

Require both fixed ADF p-value and 5% critical gates. Search \`[0,1]\`, refine the first coarse pass, then use audited finite expansions of width 1 through maximum 5; never change alpha/minimum samples or choose \`d=1\` as a fallback.

- [ ] **Step 4: Verify and commit**

Run: \`python -m pytest tests/etf_tricks/afml/test_ffd.py tests/etf_tricks/afml/test_config.py -q\`

\`\`\`powershell
git add etf_tricks/afml/ffd.py etf_tricks/afml/config.py tests/etf_tricks/afml/test_ffd.py
git commit -m "feat: govern train-only fixed-width ffd selection"
\`\`\`

### Task 5: Build causal structural and feature tables

**Files:**
- Modify: \`etf_tricks/afml/structural.py\`, \`etf_tricks/afml/features.py\`
- Test: \`tests/etf_tricks/afml/test_structural.py\`, \`tests/etf_tricks/afml/test_features.py\`

**Produces:** event-time features with availability, source timestamps, staleness, observation counts and missingness flags.

- [ ] **Step 1: Write RED tests**

\`\`\`python
def test_sadf_qadf_cadf_reuse_one_adf_vector():
    row = StructuralFeatureEngine(CONFIG).build(LOG_NAV).iloc[-1]
    assert row["sadf"] == max(HAND_ADF_VECTOR)
    assert row["cadf"] == np.mean([x for x in HAND_ADF_VECTOR if x >= np.quantile(HAND_ADF_VECTOR, .95)])

def test_ix_join_never_forward_matches():
    assert feature["ix_source_bar_end_date"] <= feature["bar_end_date"]
\`\`\`

- [ ] **Step 2: Run RED** — \`python -m pytest tests/etf_tricks/afml/test_structural.py tests/etf_tricks/afml/test_features.py -q\`.
- [ ] **Step 3: Implement** — use one endpoint ADF vector for SADF/QADF/QADF spread/CADF/CADF dispersion; build FFD distance/volatility/shape, close-path range, duration surprise, exact \`bar_amount\`, Amihud, domain-validated Roll, portfolio state, and bounded backward IX0001 as-of features. No ATR/ADX/VPIN/Kyle aliases.
- [ ] **Step 4: Verify and commit**

Run: \`python -m pytest tests/etf_tricks/afml/test_structural.py tests/etf_tricks/afml/test_features.py -q\`

\`\`\`powershell
git add etf_tricks/afml/structural.py etf_tricks/afml/features.py tests/etf_tricks/afml/test_structural.py tests/etf_tricks/afml/test_features.py
git commit -m "feat: add causal afml structural features"
\`\`\`

### Task 6: Isolate triple-barrier labels and public views

**Files:**
- Modify: \`etf_tricks/afml/labels.py\`, \`etf_tricks/afml/dataset.py\`, \`etf_tricks/afml/lab.py\`, \`etf_tricks/afml/__init__.py\`
- Test: \`tests/etf_tricks/afml/test_labels.py\`, \`test_dataset.py\`, \`test_lab.py\`, \`test_notebook_quickstart.py\`

**Produces:** isolated events/labels and PIT-safe \`for_ml\`/ \`for_trading\` views with canonical-table hashes.

- [ ] **Step 1: Write RED tests**

\`\`\`python
def test_touch_starts_after_t0_and_tail_is_unresolved():
    event = TripleBarrierLabeler(CONFIG).build(FEATURES, BARS, MEMBERS).events.iloc[0]
    assert event["first_touch_date"] > event["t0_observation_date"]
    assert TAIL_LABEL["label_status"] == "unresolved_tail"

def test_for_trading_never_exposes_labels(dataset):
    snapshot = dataset.for_trading(as_of="2026-06-30", decision_cutoff="after_close")
    assert {"label", "t1", "realized_log_return"}.isdisjoint(snapshot.columns)
\`\`\`

- [ ] **Step 2: Run RED** — \`python -m pytest tests/etf_tricks/afml/test_labels.py tests/etf_tricks/afml/test_dataset.py tests/etf_tricks/afml/test_lab.py -q\`.
- [ ] **Step 3: Implement** — 60-bar EWMA log-return sigma (minimum 20), 2x/2x log barriers, daily-close first touch after \`t0\`, separate future outcome availability, concurrency/average uniqueness, manifest row/key/schema/SHA-256, and knowledge-time snapshot filtering. Reject 13-ETF full history without \`full_history_acceptance=True\`.
- [ ] **Step 4: Verify and commit**

Run: \`python -m pytest tests/etf_tricks/afml -q\`

\`\`\`powershell
git add etf_tricks/afml tests/etf_tricks/afml
git commit -m "feat: expose pit-safe afml dataset api"
\`\`\`

### Task 7: Short-window acceptance, measured optimization, then full history

**Files:**
- Modify only profiler-proven files in \`etf_tricks/afml/\`
- Modify: \`tests/etf_tricks/afml/test_integration.py\`
- Create: \`docs/etf_tricks/afml/2026-09-03-short-window-acceptance.md\`

**Produces:** reproducible short-window evidence and, only after it passes, one full-history readiness record.

- [ ] **Step 1: Capture warmed short-window diagnostics**

\`\`\`python
dataset = lab.build_all(base, config=config, mode="train", **SHORT_BOUNDARIES,
                        etf_ids=("momentum", "market_cap"))
baseline = dataset.diagnostics[["stage", "elapsed_seconds", "peak_rss_observed_bytes", "row_count"]]
\`\`\`

- [ ] **Step 2: Write equivalence RED before optimization**

\`\`\`python
def test_vectorized_structural_equals_reference_fixture():
    assert_frame_equal(vectorized_output, reference_output, check_exact=True)
\`\`\`

- [ ] **Step 3: Optimize only a measured hotspot** — FFD uses convolution for equal kernels; structural statistics reuse endpoint ADF vectors. No GPU/JIT/multiprocessing before exact parity and profiler evidence.
- [ ] **Step 4: Run 1–2 ETF then 13 ETF short acceptance**

Run: \`python -m pytest tests/etf_tricks/afml -q\`

Use public \`build_all\` for 2024-01-01 through 2026-07-07. Extend to 2020 only if recorded bar/FFD gates prove insufficient. Record stage time, peak RSS, rows, artifact hashes, q-star, d-star, capability gaps, unresolved tails, and readiness headline. Do not claim profitability.

- [ ] **Step 5: One full-history run only after all short gates pass**

\`\`\`python
dataset = lab.build_all(base, config=config, mode="train", **FULL_BOUNDARIES,
                        full_history_acceptance=True)
\`\`\`

If the upstream coverage gate fails, report exact keys/count and do not mutate AFML semantics.

- [ ] **Step 6: Commit evidence**

\`\`\`powershell
git add etf_tricks/afml tests/etf_tricks/afml docs/etf_tricks/afml/2026-09-03-short-window-acceptance.md
git commit -m "perf: validate afml short-window execution"
\`\`\`

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 cover lineage/capabilities; 3 Dollar bars/PIT replay; 4 FFD; 5 structural/features; 6 labels/API; 7 profiling and acceptance.
- **Leakage:** every fit is training-only; transform is causal; joins are backward as-of; labels are schema-isolated from trading snapshots; append/revision behavior is tested.
- **Scope:** DMS is an explicit input prerequisite, not silently repaired in AFML. No outcome implies alpha, capacity, or profitability.
