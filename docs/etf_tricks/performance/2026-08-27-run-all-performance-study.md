# `ETFTrickLab.run_all()` Performance Study

Authority: `docs/etf_tricks/prompts/02-performance-optimization-prompt.md`

Research window: `2024-01-01` through `2026-07-07`

Final acceptance window, not used for iterative profiling: `2005-01-01` through `2026-07-07`

## Conclusion first

`run_all()` 的慢點不是 `ETFTrickLab.from_data_analysts()`，也不是單純 parquet I/O。2024–2026 的 fresh baseline 花費 `286.996` 秒，前三大 bottleneck 是：

1. `PITFeatureEngine.compute()`：`180.871` 秒，63.02%；
2. 13 個 ETF execution：合計 `67.037` 秒，23.36%；
3. `UniverseEngine.select()`：`15.557` 秒，5.42%。

`attach_etf_amount()` 另耗 `11.494` 秒。資料讀取只有 `6.454` 秒，但現行 gateway 將 21 年共 972 萬列價格與 917 萬列籌碼全部 materialize，再於 pandas 內過濾，profiling process 觀察到約 4.27 GB working set。這不是主要 wall-time bottleneck，卻是主要 memory bottleneck。

最有效的架構不是把整套流程盲目改成 GPU/3D tensor，而是：

- 特徵層改為同質 `date × ticker` 2D arrays，一次處理所有 formation dates；
- execution 保留時間軸 state machine，但共用 prepared market arrays，只處理正持股與當月 schedule tickers；
- Universe 共用 master、IX0001 denominator 與 base eligibility；
- ETF amount 使用一次 merge/alignment，移除逐日逐持股 MultiIndex lookup；
- 最後才處理 partition pruning 與其他低占比項目。

代表區間的初始完成預算為 `<=30` 秒（相對 baseline 至少 9.5x）；stretch goal 為 `<=15` 秒。這是實作 gate，不是目前已達成的結果。

## Baseline environment

| Item | Value |
|---|---|
| Python | 3.12.2 |
| pandas | 3.0.3 |
| NumPy | 2.4.6 |
| pyarrow | 25.0.0 |
| OS | Windows 11 |
| Run window | 2024-01-01..2026-07-07 |
| Initial capital | NT$10,000,000 |
| Trading dates emitted | 606 per established ETF |
| Formation dates | 31 |
| ETF selections | 31 × 13 = 403 |
| Observed process working set | approximately 4.27 GB; sampled, not a certified peak |

`ETFTrickLab.from_data_analysts()` took approximately `0.000331` seconds. It only binds paths; all material work occurs inside `run_all()`.

## End-to-end stage timing

The baseline used light method wrappers, not cProfile, around one fresh public-API run.

| Stage | Seconds | Share | Calls / output |
|---|---:|---:|---|
| PIT feature computation | 180.871 | 63.02% | 31 calls; 80,142 formation-ticker rows |
| Portfolio execution | 67.037 | 23.36% | 13 calls |
| Universe selection | 15.557 | 5.42% | 403 calls |
| ETF amount attachment | 11.494 | 4.01% | 1 call |
| Six canonical artifact reads | 6.454 | 2.25% | 6 calls |
| Feature initialization, concat, result construction, metadata | 5.583 | 1.95% | residual |
| **Total** | **286.996** | **100%** | |

`PITFeatureEngine` initialization was separately measured at `4.487` seconds. It builds per-ticker DataFrames and therefore accounts for most of the residual stage.

### Execution by ETF

| ETF | Seconds |
|---|---:|
| volume_ratio | 9.601 |
| sortino_60d | 8.357 |
| sharpe_60d | 7.931 |
| chip | 6.039 |
| turnover | 5.976 |
| momentum | 5.175 |
| monthly_sales | 4.632 |
| low_volatility | 4.472 |
| traded_amount | 4.169 |
| roe | 2.843 |
| shipping | 2.717 |
| financial | 2.654 |
| market_cap | 2.471 |

The difference is driven mainly by historical ticker accumulation and lookup count, not by a mathematically harder ETF formula. Signal computation is already outside the execution loop.

## Output materialization size

| Table | Rows from fresh 2024–2026 run |
|---|---:|
| daily_etf | 7,878 |
| daily_holdings | 106,926 |
| trades | 106,840 |
| monthly_targets | 3,967 |
| candidate_audit | 1,041,846 |
| diagnostics | 1 |

The candidate audit is intentionally large: every formation-ticker row is repeated for 13 ETF-specific eligibility and selection decisions. It cannot be silently removed or sampled to gain speed.

## Function-level evidence

The following cProfile runs have instrumentation overhead and are used for call-count and hotspot ranking, not as the baseline wall time.

### Feature computation

One formation date with 2,730 rows produced:

- `25,663,908` function calls;
- 2,730 calls to `_daily_signals()`;
- 21,840 pandas `reindex()` calls;
- `_daily_signals()` consumed 16.225 of 16.935 profiled seconds;
- `reindex()` paths alone accumulated 5.705 seconds.

The current complexity is effectively `formation × ticker × several pandas window objects`. Each ticker creates separate 20-, 61-, and 80-date DataFrames or Series and repeats dtype conversion, missing-value checks and reductions.

### Execution

One `volume_ratio` execution produced:

- `28,139,975` function calls;
- 89,220 `_market_row()` calls;
- `_market_row()` and pandas MultiIndex `.loc` consumed 9.187 of 12.294 profiled seconds;
- 181,081 `transaction_cost()` calls consumed only 0.474 seconds.

The primary root cause is `current_tickers = set(shares) | ...`: zero-share tickers remain forever in `shares`, so every previously held ticker continues to receive daily price lookups. The path then performs a slow pandas MultiIndex row extraction for each ticker. Decimal accounting is not the main bottleneck and should not be weakened.

### ETF amount

The full representative result produced:

- `45,200,237` function calls;
- 210,180 pandas index operations;
- 7,865 DataFrame `itertuples()` constructions;
- pandas lookup paths consumed 10.173 of 16.201 profiled seconds.

The formula is a relational alignment problem and does not require nested Python lookup loops.

### Universe selection

One formation across all 13 ETFs took 0.495 profiled seconds. Of this:

- repeated `_ix0001_sum20()` consumed 0.151 seconds;
- `TradingCalendar.days` repeatedly rebuilt tuples and iterated 241,007 timestamps;
- 13 repeated master validations and merges consumed about 0.107 seconds.

The 13 ETF-specific rankings remain valid loops; the shared preparation around them should be removed from those loops.

## Artifact I/O ledger

| Artifact | Current rows returned | Paths | Stored bytes, all columns | Baseline read seconds | Main consumers |
|---|---:|---:|---:|---:|---|
| trading_calendar | 23,376 | 1 | 1.09 MB | 0.414 | calendar, formation, execution |
| daily_price_volume | 9,729,801 | 22 | 634.18 MB | 2.648 | features, IX0001, execution, ETF amount |
| daily_chip | 9,169,271 | 22 | 717.86 MB | 2.502 | chip feature |
| monthly_sales | 390,808 | 22 | 37.33 MB | 0.348 | PIT r18 |
| financial_statement_raw | 651,242 | 22 | 156.77 MB | 0.525 | PIT r103 |
| security_master | 3,444 | 1 | 0.14 MB | 0.017 | universe, delisting |

`read_artifact()` checks every declared path, concatenates all years, verifies total physical row count and duplicates, converts dates, and only then applies `start/end`. `run_all()` also passes no lower bound for daily and sparse sources. Therefore a 2024 start still materializes 2005 onward.

For the representative run, a 252-trading-day feature warm-up starts on `2022-12-13`. Reading only the needed yearly partitions would reduce the main inputs to:

| Artifact | Needed paths | Needed rows | Stored bytes, all columns |
|---|---:|---:|---:|
| daily_price_volume, 2022–2026 | 5 | 2,710,977 | 174.86 MB |
| daily_chip, 2022–2026 | 5 | 2,415,171 | 224.74 MB |
| monthly_sales, 2023–2026 | 4 | 77,932 | 9.66 MB |
| financial_statement_raw, 2023–2026 | 4 | 168,337 | 41.79 MB |

This is a 72–80% row reduction. However, current manifests do not contain per-partition row counts or hashes. Partition pruning must not silently drop the existing physical row-count/duplicate validation. It is therefore a memory-focused `benchmark first` task that needs an explicit validation design.

There are no result writes inside `run_all()`. The performance issue is repeated reads, copies, sorts, index construction and in-memory materialization, not repeated parquet output.

## Repeated-computation matrix

| Data or operation | Current repetition | Correct shared form |
|---|---|---|
| Daily rolling windows | Per formation × per ticker × multiple Series | One prepared 2D panel; vector reductions over all tickers |
| Sales/ROE PIT selection | Full sparse frame copy/filter/sort per formation | Pre-sorted events plus bounded as-of lookup |
| Security master normalization | 13 times per formation plus 13 executions | One immutable prepared master |
| IX0001 20-day denominator | 13 times per formation | One value per formation |
| Calendar tuple/month slices | Rebuilt across compute/select/execution | Cached day array and date→month/k/N map |
| Market copy/sort/index | Once per ETF | One immutable prepared execution market |
| Market row lookup | Per active and every historically seen ticker | Dense integer lookup for positive holdings and schedule tickers only |
| Target month filtering | Per ETF × per day | Pre-group target rows by month |
| ETF amount market index | Rebuilt after execution | Reuse prepared market amount array or one merge |
| Manifest JSON | Reopened for reads, freshness and metadata hashes | Per-gateway immutable manifest cache, invalidated by file identity |

## Mathematical audit

### Daily signals: implement now

Use a homogeneous 2D representation with global trading dates on one axis and ticker integer codes on the other. A dict of 2D arrays is preferred to one mixed-dtype 3D tensor.

| Signal | Equivalent fast operation |
|---|---|
| ADV20 / traded-value sum20 | 20-row slice; finite count, sum and mean across ticker axis |
| Turnover20 | same 20-row reduction |
| Chip20 | daily three-field completeness mask, then 20-row count and sum |
| Volume ratio | numerator `T-19:T`; denominator `T-79:T-20`; disjoint reductions |
| Momentum | direct rows `T-21` and `T-252` |
| 60-day returns | compute adjacent-calendar return matrix once; reduce `T-59:T` |
| Volatility | finite count plus sample standard deviation; valid when count >=20 |
| Sharpe | mean/sample std only when count is exactly 60 |
| Sortino | mean and `sqrt(mean(min(return,0)^2))` only when count is exactly 60 |

A throwaway matrix probe used a shape of `854 × 2,828` and computed all 31 formation dates:

| Probe stage | Seconds |
|---|---:|
| Read required yearly partitions | 0.751 |
| Build matrices | 0.132 |
| Compute all daily signals | 0.156 |

Key coverage was exact: 80,142 rows, zero key differences. ADV, traded-value sum, volume ratio, chip and momentum were bit-for-bit equal to the certified v5 feature evidence. Turnover and risk statistics had only floating reduction-order differences:

| Field | Maximum absolute difference |
|---|---:|
| turnover_20d | 1.42e-14 |
| vol_60d | 7.11e-15 |
| sharpe_60d | 6.22e-15 |
| downside deviation | 5.55e-17 |
| sortino_60d | 1.99e-13 |

All 3,967 monthly target rows, ticker ranks and weights remained exactly identical across all 403 selections. This proves the architecture is viable, but not that ULP differences are automatically acceptable. Production implementation must either preserve the existing reduction order or explicitly pass the governed float-equivalence gate while keeping all targets and ledgers exact.

The first probe accidentally mapped five non-calendar raw dates to array index `-1`; the corrected probe excludes any raw date not present in `TRADEDAY_TWSE`. This must be an explicit test because silent negative-index assignment would be a correctness bug.

### Sparse PIT fundamentals: benchmark after daily features

Current monthly sales and ROE logic correctly filters by `source_available_date`, revision date and age, but copies and sorts the whole table per formation. Pre-sort by ticker and availability, then use `searchsorted` or a governed as-of join. Preserve source-row tie-breaks and audit dates. This is lower priority because `_daily_signals()` occupied more than 95% of the profiled feature call.

### Selection: implement shared context, keep deterministic rank loop

Prepare master fields, common-stock/listing masks, formation-date liquidity ratios and IX0001 denominators once. Keep the 13 spec loops because industry rules and signal direction differ. `argpartition` is optional and low-value for only about 2,730 rows; stable `sort_values` should remain until shared preparation is removed and re-profiled.

### Execution: keep the state machine, replace lookup plumbing

Time cannot be parallelized across dates because cash, shares, fees, corporate actions, backlog and variable-N schedules are path-dependent. The safe acceleration is:

1. build one prepared date/ticker code map and raw/adjusted-close arrays;
2. share it across all 13 engine runs;
3. define current tickers as positive holdings plus schedule start/target and current targets;
4. remove or ignore zero-share dictionary entries;
5. precompute date→month, k and N;
6. pre-group targets by month;
7. retain `Decimal`, sell-before-buy, cost rounding and cash allocation loops.

Cost-model JIT or float conversion is rejected: cost calculation is under 4% of the profiled execution call and changing it risks accounting semantics.

### ETF amount: implement after execution market preparation

Map each `(current date, ETF)` to its previous ETF date, join previous holdings once, join current traded value once, then aggregate `traded_value × actual_weight`. The initial vector probe ran in 0.204 seconds but changed floating summation order; the relative difference was at most `4.76e-16` and missing counts were exact. The first date in a sliced full-history artifact also differs because that artifact carries pre-2024 holdings, while a fresh 2024 run starts new capital. Production comparison must use a fresh 2024 baseline, preserve summation semantics where required, and never compare stateful execution to a filtered full-history ledger.

## Optimization decision table

| Candidate | Decision | Reason |
|---|---|---|
| Multi-formation 2D feature arrays | `implement now` | 63% bottleneck; probe demonstrates >100x conservative stage potential and exact selections |
| Warm-up slice before feature preparation | `implement now` | removes irrelevant 2005–2022 rows from feature memory without changing reads or coverage checks |
| Positive-holding ticker pruning | `implement now` | direct root cause of 89,220 lookup calls in one ETF |
| Shared prepared market arrays | `implement now` | pandas MultiIndex lookup is 75% of profiled execution |
| Vectorized ETF amount alignment | `implement now`, after exact oracle | 4% total; probe reduces seconds to sub-second territory |
| Cached calendar/month metadata | `implement now` | low risk; repeated tuple/month work is directly measured |
| Shared Universe context | `implement now` | repeated IX/master preparation is measurable and deterministic |
| Sparse PIT as-of index | `benchmark first` | correct opportunity, but low share after daily-signal hotspot |
| Physical partition pruning | `benchmark first` | major memory benefit; current manifest lacks per-partition validation evidence |
| Candidate-audit categorical/dtype compression | `benchmark first` | could reduce memory but may change public dtypes/schema |
| `argpartition` top-10 | `defer` | selection sort is not a top bottleneck and tie rules are strict |
| Parallelize 13 executions | `defer` | first remove shared copies/lookups; multiprocessing may duplicate GB-scale state |
| Numba/Polars/Dask | `defer` | no dependency is justified before NumPy/pandas structural fixes |
| GPU / one 3D mixed tensor | `reject` | transfer/mixed dtype/Decimal/state-machine costs exceed likely benefit |
| Disable candidate audit or validation | `reject` | changes the governed output and fail-closed contract |
| Replace Decimal accounting with float | `reject` | correctness risk; not the measured bottleneck |

## Implementation roadmap

No production code has been changed by this study. Each slice below requires a failing test first, one scoped commit, fresh representative-window evidence and rollback if equivalence fails.

### Slice 0 — Freeze the fresh 2024 execution oracle

Files: ignored `.artifacts/etf_tricks/performance/`, test helpers only if needed.

- Run the current code once for 2024-01-01..2026-07-07 and save all six canonical tables plus manifest identity and timing.
- This additional run is necessary because the earlier timing baseline was not persisted.
- Use certified v5 only for feature/selection comparison. Do not use its 2024 slice for cash/share/trade/NAV equivalence because it contains pre-2024 portfolio state.

### Slice 1 — Prepared multi-formation feature panel

Files: `etf_tricks/features.py`, `etf_tricks/lab.py`, `tests/etf_tricks/test_features.py`, `tests/etf_tricks/test_integration.py`.

- Add a multi-formation API while retaining `compute(formation)` compatibility.
- Factorize ticker/date once and explicitly reject or exclude non-calendar dates according to current semantics.
- Calculate all daily numeric signals with 2D arrays.
- Keep sparse PIT rows and audit columns unchanged initially.
- Require identical keys, masks, target ranks/weights and downstream fresh 2024 execution oracle.
- Performance gate: feature preparation plus all 31 formations `<=5` seconds on the baseline host.

### Slice 2 — Prepared execution market and active-ticker state

Files: `etf_tricks/execution.py`, `etf_tricks/lab.py`, `tests/etf_tricks/test_execution.py`, `tests/etf_tricks/test_integration.py`.

- Prepare market normalization/date-ticker codes once in `run_all()`.
- Reuse prepared close/adjusted-close lookup across 13 ETFs.
- Filter `shares` to positive positions when building current ticker scope; preserve schedule/backlog tickers.
- Cache month/k/N and target-month groups.
- Require exact shares, trades, cash, costs, corporate actions, stale flags, holdings and NAV.
- Performance gate: all 13 executions `<=15` seconds.

### Slice 3 — Universe shared context

Files: `etf_tricks/universe.py`, `etf_tricks/calendar.py`, `etf_tricks/lab.py`, `tests/etf_tricks/test_universe.py`, `tests/etf_tricks/test_data_gateway.py`.

- Cache calendar tuples and month boundaries.
- Normalize security master once.
- Compute IX0001 aligned sum20 and common eligibility once per formation.
- Preserve 403 ETF-specific candidate audits and deterministic ranking.
- Performance gate: all 403 selections `<=5` seconds with exact targets.

### Slice 4 — ETF amount alignment

Files: `etf_tricks/result.py`, `tests/etf_tricks/test_result.py`, `tests/etf_tricks/test_integration.py`.

- Reuse the prepared market amount view.
- Replace repeated DataFrame/MultiIndex lookups with one previous-date alignment and merge.
- Preserve first-date zero behavior for a fresh run, missing counts, flags and governed floating tolerance.
- Performance gate: representative attachment `<=1` second.

### Slice 5 — Bounded artifact reads and memory

Files: `etf_tricks/data_gateway.py`, `etf_tricks/lab.py`, `tests/etf_tricks/test_data_gateway.py`, DataAnalysts manifest contract only if formally authorized.

- First design a physical validation method that retains row-count, duplicate-key and coverage fail-closed behavior.
- Prefer per-partition row count/hash evidence or cheap parquet metadata validation over trusting path names alone.
- Read only warm-up and run partitions after the contract is proven.
- Performance gate: representative working set materially below the observed 4.27 GB; initial target `<=3 GB`, without dtype/schema changes.

### Slice 6 — Re-profile and optional low-priority work

- Re-profile the same 2024–2026 window once.
- Only then consider sparse PIT as-of indexing, categorical compression or parallel ETF execution.
- Reject any change whose improvement is below measurement noise.

### Slice 7 — One-time final full-history acceptance

- Only after all representative gates pass, run 2005-01-01..2026-07-07 once.
- Validate 13 ETF coverage, targets, ledgers, NAV, amount, costs and readiness.
- If it fails, derive the shortest reproducing window; do not repeatedly debug with full history.

## Equivalence oracle and gates

The existing v5 full-history artifact currently has manifest hashes and ETF spec hash identical to the live governed inputs. It is valid for stateless feature and selection evidence.

Before production edits, persist a fresh 2024-run oracle. For every slice, sort canonical tables by governed unique keys and compare:

- schema, dtype, row count, keys and NaN/flag masks;
- candidate rows, target tickers, order and weights;
- exact integer shares, trades, fees, tax, cash and backlog;
- exact reconciliation of cash + holdings = assets and continuous Daily NAV;
- ETF amount alignment and missing-value diagnostics;
- source/spec identities and public Notebook API.

Run focused tests, all `tests/etf_tricks/`, `tests/test_verify_environment.py`, `pip check`, and a 2024–2026 public-API benchmark. Do not run full history during normal iterations.

## Current status

### 目前可用

- Complete authoritative optimization Prompt.
- Fresh 2024–2026 wall-time baseline and stage timings.
- Function-level root causes for features, execution, amount and selection.
- Artifact read ledger and warm-up partition estimate.
- Throwaway 2D feature prototype with exact 3,967 selection rows and ranks.
- Prioritized, rollback-safe implementation roadmap.

### 目前缺失／限制

- No production optimization has been implemented.
- The fresh 2024 canonical result tables were not persisted during the timing run; one final pre-change oracle run is required.
- Peak memory is a sampled working-set observation, not a formal peak-RSS trace.
- Float reductions in risk features and ETF amount need a governed exact/tolerance decision even though selections were exact.
- Partition pruning is not authorized until physical validation semantics are preserved.
- The final 2005–2026 run has deliberately not been repeated during this study.

## Recommended next action

Approve Slices 0–2 as the first implementation batch. They address 86.38% of measured wall time and have the strongest evidence. Re-profile before authorizing Slices 3–5; the bottleneck order will change substantially after feature and execution fixes.
