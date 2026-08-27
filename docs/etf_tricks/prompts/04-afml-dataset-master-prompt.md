# Authoritative Prompt - ETF Tricks AFML Dataset Foundation

Goal reference: `GOAL-ETF-AFML-DATASET-001`

Status: Approved authority as of 2026-08-27.

## 1. Role, authority, and source boundary

你是 Taiwan Equity ETF Tricks 下游 AFML research-dataset layer 的量化研究與實作負責人。工作目錄為 `C:\Users\ChastLai\Documents\量化交易Workflow`。

開始前完整閱讀：

1. repository `AGENTS.md` 與更高層 runtime instructions；
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`；
3. `docs/etf_tricks/prompts/README.md`、`00-goal-prompt.md`、`01-master-prompt.md`；
4. 本 Prompt 與 `03-afml-dataset-goal-prompt.md`；
5. `docs/Marcos Lopez de Prado - Advances in Financial Machine Learning-Wiley (2018).pdf` 的第 2、3、5、17、19 章；
6. 經核准後才建立的 implementation plan。

上游 ETF Tricks 契約仍由已核准設計控制，本層只能消費已驗證輸出，不得改寫 NAV、return、holdings、cost、selection 或 `etf_amount`。AFML 書籍是方法來源；本 Prompt 負責把方法縮限到目前真實資料能力。若書中方法需要 tick、aggressor side、volume buckets 或真實 OHLC，而現有資料沒有，就必須標示 unavailable，不得用近似值偷換名稱。

## 2. Required outcome and explicit scope

建立一個共用、importable、manifest-backed 的下游研究層，針對 13 個 ETF Tricks 產生：

- 日資料衍生、不可虛構 intraday path 的 Dollar bars；
- 每檔 ETF 專屬的 AFML fixed-width FFD 與完整 `d*` 搜尋證據；
- 一般 ADF stationarity diagnostics；
- rolling/expanding SADF、QADF、CADF 結構轉換 features；
- 價格記憶、趨勢、分配、流動性與大盤環境 features；
- 可配置 triple-barrier directional labels 與事件區間；
- Notebook-facing API、canonical long-form tables、manifest、hash、diagnostics 與 fail-closed readiness report。

本輪可實作 feature/label dataset generation，但不含模型訓練、超參數搜尋、feature selection、交叉驗證、績效比較、部位 sizing、meta-label model 或券商送單。成功產出 labels 不代表可預測或可獲利。

## 3. Governing scientific decisions

### 3.1 Pipeline order

標準路徑固定為：

```text
validated ETF Trick Daily NAV + amount
  -> close-path Dollar bars
  -> FFD on Dollar-bar log NAV
  -> stationarity and structural-break series
  -> feature matrix
  -> triple-barrier event labels
```

理由：Dollar bars 定義資訊時間軸，FFD 的 lag 與記憶長度必須在該時間軸上解讀。FFD 與非線性 threshold sampling 不具交換律。禁止把 daily FFD 值加總、平均或 OHLC 聚合成 Dollar bars。若保留 daily FFD，只能作為獨立描述性診斷，名稱與 artifacts 必須和正式 event-time FFD 分開。

### 3.2 Stationarity gate versus regime features

- `d*` 的選擇使用一般、左尾 ADF unit-root test，符合 AFML Chapter 5。
- SADF、QADF、CADF 是 Chapter 17 的右尾 explosiveness/structural-break statistics，用作環境或 regime features。
- 不得用 SADF/QADF/CADF 是否極端來宣稱 FFD 已平穩，也不得把本 Prompt 的 CADF 誤解為 cointegration ADF；此處 CADF 指書中的 Conditional ADF。

### 3.3 Point-in-time modes

每次 run 必須指定下列一種 mode：

1. `train` - 預設且可供 ML。只在明確 training interval 內 fit/select 共同 Dollar threshold `q*`、每檔 ETF 的 `d*`、FFD width 與其他 preprocessing parameters，之後以同一版本 transform validation/test。公開介面使用 `train`；artifact metadata 必須保存 `calibration_scope=train_only`、`calibration_fit_end`、`parameters_frozen_at` 與 `calibration_effective_at`。訓練區間內以整段 training fit 後回建的 bars/features 只能是 `CALIBRATION_HISTORY`，不得透過 `for_trading(as_of)` 冒充當時已在線可得。
2. `walk_forward` - 只在預先設定的 retrain dates 使用當時以前資料重選；每個版本分段保存，不得把不同 `q*` 或 `d*` 的 level 無標記拼成單一同質序列。新的 Dollar threshold version 只能套用到 effective time 後新開始的 bar；已開啟的 bar 必須用舊 threshold 完成，或以顯式 `ABANDONED_ON_RECALIBRATION` 結束且不輸出為 completed bar，禁止用新參數改寫其歷史 membership。
3. `research_full_history` - 使用完整樣本選擇 `d*` 或其他 preprocessing parameters，只做描述性診斷；artifact 必須標記 `DESCRIPTIVE_ONLY`，禁止流入 ML validation、test 或績效結論。

未提供 training boundary 時，不得默認用完整歷史建立 ML dataset。

固定 `d`、tolerance、width 及其他參數後，fixed-width FFD 是單邊、因果轉換：時間 `t` 的輸出只讀取 `t` 與更早的值。因此可以一次對完整可用時段執行 transform，也可在 validation/test 起點使用更早、已發生的 training bars 作為 warm-up history；這些本身不構成洩漏。禁止的是用 split 後資料反向 fit/select `d*`、window、threshold、scaling 或其他 preprocessing parameters。禁止在 split 邊界重置歷史造成不必要缺口。

`train` mode 只保證對明確 held-out validation/test 的隔離。若後續在 train 內做 purged CV 或其他 fold evaluation，`q*`、`d*` 與所有 data-dependent preprocessing 必須在各 fold 的 training side 重 fit，或使用更早且完全獨立的 calibration interval；不得把整段 train 預先 fit 的參數誤稱為 fold-local evidence。

## 4. Source and lineage contract

### 4.1 Required inputs

- 一個通過上游 readiness gates 的 `ETFTrickResult` 或其 `result_manifest.json`；
- `daily_etf` 至少含 `date, etf_id, nav, daily_return, etf_amount, missing_traded_value_count, has_data_quality_flag, cash_weight, invested_weight, holdings_count, target_completion_ratio`；
- 若建立組合狀態 features，讀取同一 result identity 的 `daily_holdings`、`trades` 與 `monthly_targets`；
- DataAnalysts manifest-declared `TRADEDAY_TWSE` 與 IX0001 source `close, amt`（若 gateway 正規化則欄位為 `close, traded_value`），並保留 manifest 宣告的 `source_available_date`、`effective_date`、`data_cutoff_at` 或等價 availability/lineage 欄位；
- VIX 或其他環境資料只有 manifest、schema、PIT availability 與 coverage 全部成立時才能加入。

### 4.2 Identity and quality

保存並驗證上游 result table hashes、ETF spec hash、DataAnalysts manifest hashes、run config、日期範圍與所有下游 config hash。禁止跨兩個不同 result identity 拼接 NAV、amount 或 holdings。

只有 `daily_etf.date` 不足以證明 PIT。下游必須由同一 result identity 的 constituent market rows、前一期 holdings/targets、交易日曆與各自 availability contract 推導每列 synthetic NAV/`etf_amount` 的 `source_available_at`；不得用目前檔案 mtime、這次 batch 的完成時間或 observation date 本身冒充歷史可得時間。`data_cutoff_at` 是 extraction/lineage cutoff，不得在未經 artifact `availability_field`／`pit_policy` 證明時直接當成 source publication time。若現有 gateway 在正規化時捨棄 availability 欄位，AFML source adapter 必須從 manifest-declared canonical artifact 一併讀取並驗證，或 fail closed／標記 `PIT_REVISION_UNVERIFIED`，不得靜默猜測。

`etf_amount` 是依前一日經濟權重加權的成分股成交金額 proxy，不是真實上市 ETF 的成交額，也不等同可執行容量。若 `missing_traded_value_count > 0` 或 amount quality flag 成立，預設 `quality_policy="fail"`；可選的寬鬆模式必須顯式設定、保留缺失日期，且不得稱為 production-ready。

一根 Dollar bar 只有在 `bar_end_date` 的 NAV、`etf_amount`、IX0001 與品質資料全部到達且通過驗證後才成立。`bar_available_at = max(source_available_at)`；來源只有日期、沒有可驗證 timestamp 時，保守標記為該交易日 `after_close`。所有 bar-derived features 都繼承或晚於 `bar_available_at`，任何交易訊號最早只能作用於 `TRADEDAY_TWSE` 上資料已在 decision cutoff 前可用的下一個可執行 session。禁止假設能以觸發 bar 的同一收盤價交易。Triple-barrier 的 `t0` close 是研究 label reference，不是 execution price，未來回測仍須另行處理 execution lag、cost 與 slippage。

### 4.3 Source capability is phase zero

在實作 Dollar bar、FFD 或 features 前，先產出 `source_capabilities` artifact，至少逐項檢查 VPIN、Kyle lambda、ATR、ADX 與 VIX 所需的：來源名稱與路徑、manifest/hash、schema、資料粒度、PIT availability、日期／商品 coverage、修訂政策與 quality flags。狀態只能是 `AVAILABLE_VERIFIED`、`PARTIAL_COVERAGE` 或 `UNAVAILABLE_SOURCE_GRAIN`，並附精確證據與缺失資料契約。

- VPIN 必須有 tick/trade grain、等量 volume buckets 所需欄位及 buy/sell classification；
- Kyle lambda 必須有 signed order flow 或可驗證 aggressor side；
- ATR/ADX 必須有同一 ETF Trick 在同一持倉與時間語意下的真實 open/high/low/close；逐成分股 high/low 的加權和不得冒充組合 OHLC；
- VIX 必須是 manifest-declared、PIT-safe 的台股隱含波動率序列。

來源能力不足不阻止其餘有效資料層開發，但正式 feature 保持 unavailable，不能以 proxy 偷換名稱。capability audit 必須優先完成，讓後續來源補齊可依明確 schema/coverage contract 進行。

## 5. Configuration contract and defaults

所有參數必須是 versioned dataclass/config fields，可由 Notebook 覆寫；不可散落為 magic numbers。

| Group | Parameter | Default |
|---|---|---:|
| Dollar bar | `threshold_mode` | `lagged_market_fraction` |
| Dollar bar | `market_amount_lookback_days` | 60 |
| Dollar bar | `min_market_amount_observations` | 20 |
| Dollar bar | `market_fraction` | `auto_train_calibrated` |
| Dollar bar | `candidate_quantile_min, candidate_quantile_max, candidate_quantile_count` | `0.01, 0.99, 99` |
| Dollar bar | `min_completed_bars` | 120 |
| Dollar bar | `max_bar_duration_trading_days` | 60 |
| Dollar bar | `max_bars_per_day` | 1 |
| Dollar bar | `emit_incomplete_terminal_bar` | `False` |
| FFD | `input` | `log_close_nav` |
| FFD | `d_search_policy` | `autonomous_governed` |
| FFD | `d_initial_min, d_initial_max` | `0.0, 1.0` |
| FFD | `d_first_escalation_max` | `2.0` |
| FFD | `d_expansion_span` | `1.0` |
| FFD | `autonomous_max_d` | `5.0` |
| FFD | `coarse_step, refine_step` | `0.05, 0.01` |
| FFD | `weight_tolerance` | `1e-5` |
| ADF | `alpha, regression, maxlag, autolag` | `0.05, "c", 1, None` |
| ADF | `min_adf_observations` | 120 |
| Structural | `min_sample_length, lags` | `60, 1` |
| QADF/CADF | `q, v` | `0.95, 0.025` |
| QADF/CADF | `quantile_method, conditional_std_ddof` | `"linear", 0` |
| PIT join | `max_environment_staleness_trading_days` | 1 |
| Features | `ffd_ma_window` | 20 |
| Features | `ffd_vol_windows` | `(14, 60)` |
| Features | `shape_window, min_shape_obs` | `60, 30` |
| Barrier target | `volatility_method` | `ewma_log_return` |
| Barrier target | `volatility_span, min_obs` | `60, 20` |
| Triple barrier | `pt_mult, sl_mult` | `2.0, 2.0` |
| Triple barrier | `vertical_bars` | 60 |
| Triple barrier | `vertical_touch_policy` | `sign_return` |

預設不是宣稱最佳參數。參數比較只能在 training/validation 治理範圍進行，不能看 test 後回填。

bar duration 是資訊到達速度的結果，不是預先鎖定的兩日頻率。60-bar vertical horizon 的實際交易日／日曆日長度會依 ETF 與 regime 改變；每次 run 必須報告其分布，不得把 60 bars 翻譯成固定天數。

## 6. Daily-derived Dollar bar contract

### 6.1 Default threshold

對新 bar `b`，先使用 bar 開始日前、已完成的最近 `L=60` 個 `TRADEDAY_TWSE` 正值 IX0001 `amt` 建立市場基準：

```text
market_amount_baseline[j,b] = median(IX0001_amt[t]), t < bar_start[j,b]
theta[j,b] = q_star * market_amount_baseline[j,b]
```

至少需要 20 個有效市場觀察；研究起點前可載入純 warm-up IX0001 rows，但不得把 warm-up 輸出成研究 bars。`theta[j,b]` 在 bar 開始時凍結，13 個 ETF 在同一 calibration version 使用共同 `q_star`，直到該 bar 關閉都不因形成期間的新資料變動。這使低相對成交 ETF 自然累積更多交易日，而不是用自身低均量把門檻同步調低。

`q_star` 只能在 `train` interval，以不讀取 labels、returns performance 或 validation/test 的規則校準。先計算每個有效 ETF-day 的 `etf_amount / lagged_market_amount_baseline`，再由 pooled training-only 正值比例分布的 0.01 到 0.99 共 99 個固定 empirical quantile levels 產生有限候選集合；quantile level 規則與 method 必須版本化。逐一形成 training bars，在全部 13 ETF 都至少有 120 根 completed bars、completed-bar duration 不超過 60 個交易日且通過 amount/quality gates 的候選中選最大共同 `q`，以最大化每根 bar 的累積資訊。若無候選通過，回報 `bar_threshold_not_calibrated`；不得為單一低量 ETF 私下降低 `q`、改回自身均量或直接延伸到完整歷史找過關值。

選股使用的 0.05%/0.1%/0.2% 是 universe liquidity gates，不是 bar threshold，不能直接指定為 `q_star`。保留一個非預設研究模式：

- `fixed_nominal`：使用者明確提供固定 TWD threshold，適合可重現敏感度比較；

`own_lagged_median` 不再是 canonical mode，因為它會把各 ETF 的低量狀態自我正規化成近似固定形成頻率。禁止使用當日 IX0001 `amt` 改動正在形成的 threshold，因為 bar 的目標資訊量會成為 moving target。

### 6.2 Formation with daily data

- 依交易日順序累加 `etf_amount`，第一次使累積值 `>= theta[j,b]` 的日期關閉 bar。
- 每個 ETF 每個交易日最多關閉一根 bar。即使單日 amount 超過 threshold 多倍，也不得複製同一 NAV 形成多根假 bars。
- 關閉後 reset amount accumulator；不得把無法定位到 intraday price path 的 overshoot 偽裝成下一根 bar 的先驗成交。
- `bar_amount` 必須精確等於 membership 中 `etf_amount` 的總和；它既是 sampling reconciliation 欄位，也是正式、未縮放的 ML feature。
- 未達 threshold 的尾端 incomplete bar 不得進入 completed bars/features；只保存為 `OPEN_PROVISIONAL` checkpoint，供下一批資料接續累積。
- `open/close` 是 bar 中第一／最後一個 daily closing NAV；`high/low` 是 daily-close path extrema，欄位必須命名 `close_path_open_nav, close_path_high_nav, close_path_low_nav, close_nav`，不得稱為真實 ETF OHLC。
- 每根 bar 保存其所有 daily membership，讓 amount reconciliation、first-touch labels 與人工驗算可重建。

### 6.3 PIT lifecycle and trading alignment

每根 bar 必須經過單向狀態轉移：

```text
OPEN_PROVISIONAL
  -> threshold crossed on source observation date
  -> all required daily sources and quality checks available
  -> FINALIZED at bar_available_at
  -> FEATURE_READY at feature_available_at
  -> TRADABLE only at the first eligible execution session after decision cutoff
```

完整規則：

1. bar 開始時，以 `threshold_asof_date < bar_start_date` 的 IX0001 歷史計算並凍結 threshold；該 `q_star` calibration version 必須在 bar 開始前已 `effective`，否則該 bar 只能標記 `CALIBRATION_HISTORY`、不得進 trading-facing output。threshold、membership 或 `q_star` 不得被未來資料回寫。
2. 觸發日的 NAV、`etf_amount` 與 IX0001 只能在來源實際可得後加入；bar 的 close、return、`bar_amount`、duration、overshoot、FFD 與其他 features 在此之前一律不可見。
3. `feature_available_at >= bar_available_at = max(required source_available_at)`。若來源只有交易日期，預設 conservative availability 為 `after_close`；同日 close execution 一律禁止。`earliest_execution_session` 由 `TRADEDAY_TWSE` 與實際 data-refresh/decision cutoff 推導，不得硬編碼 `bar_end_date + 1 calendar day`。
4. `bar_amount[t]` 可用於 bar `t` 完成後的決策；`amount_ratio_20[t]` 的分母只能使用 `t` 以前 20 根 finalized bars。不得把尚未完成 bar 的 partial amount 當成正式 feature，除非未來建立另版、明確標記的 live partial-bar feature contract。
5. 不同 ETF 的 bar 非同步。跨 ETF、IX0001 或 VIX 特徵必須以 `available_at <= decision_time` 做 backward as-of join；禁止按 `bar_id`、未來最近日期或 forward-fill 無 staleness 地對齊。每個 carried value 保存 `source_bar_end_date, source_available_at, staleness_trading_days`。
6. feature/event split 歸屬依 `feature_available_at`，不是 bar start。FFD/rolling features 可讀取較早、已發生且 finalized 的 training bars 作 causal warm-up；但第一根 validation/test 或 live-eligible Dollar bar 不得包含 `calibration_effective_at` 以前的 daily member rows，必須從 effective time 後第一個可用交易日重新開始累積。training label 必須同時滿足 `t1 <= train_end` 與 `label_available_at <= train_decision_cutoff` 才能進 training；purging/embargo 是在此前提成立後，額外移除與 validation/test information interval 重疊或過度接近的 training events，不能使尚未實現或尚未到達的標籤合法化。
7. batch build 必須與逐交易日 replay 完全一致：append future rows 不得改變任何既有 `FINALIZED` bar 或其 features；先前的 open bar 可以在新資料到達後完成。若歷史來源被修訂，只能產生新的 source/result identity 與 versioned artifacts，不得靜默覆寫原 PIT evidence。若來源沒有 publication timestamp 或 vintage/revision history，就標記 `PIT_REVISION_UNVERIFIED`，不得僅憑 observation date 宣稱已證明嚴格歷史 PIT。
8. trading-facing API 只能回傳截至 `as_of` 已 finalized 且 feature-ready 的資料，不得包含 labels、future `t1`、未完成 bar 或 decision cutoff 後才到達的來源。

### 6.4 Required bar fields

`etf_id, bar_id, bar_status, bar_role, bar_start_date, bar_end_date, threshold_asof_date, threshold_mode, market_amount_baseline, market_fraction_q, threshold_amount, bar_amount, overshoot_amount, overshoot_ratio, trading_day_count, close_path_open_nav, close_path_high_nav, close_path_low_nav, close_nav, previous_close_nav, log_return, ix0001_amount_sum, etf_market_share, source_observation_max_date, calibration_fit_end, parameters_frozen_at, calibration_effective_at, bar_available_at, feature_available_at, live_eligible, crosses_split_boundary, source_quality_flag, source_revision_status, calibration_version, config_version`。

completed bar 主鍵為 `(etf_id, bar_id)`；`(etf_id, bar_end_date)` 必須唯一。`bar_daily_membership` 主鍵為 `(etf_id, bar_id, date)`，並保存每個 member row 的 source identity/availability。`OPEN_PROVISIONAL` state 使用獨立 checkpoint table，不得混入 completed `dollar_bars`。

`decision_time` 與 `earliest_execution_session` 不是 bar 的固有屬性，不得回寫進 canonical bar 來假裝只有一種下單時點；它們屬於 `for_trading` 產生的 snapshot。snapshot 至少保存 `etf_id, decision_time, decision_cutoff, source_bar_id, bar_available_at, feature_available_at, calibration_version, source_revision_status, staleness fields, earliest_execution_session, snapshot_status`。所有 timestamp 使用 timezone-aware `Asia/Taipei`；只有交易日期的來源另存 availability assumption，不可偽造成精確發布時間。

### 6.5 Trading-facing PIT clock and replay contract

每個來源、bar、feature 與交易 snapshot 必須明確區分以下時鐘，不得只保留一個 `date`：

1. `observation_date`：經濟事件所屬交易日；
2. `source_available_at`：該來源值在當時第一次可被系統取得的時間；
3. `ingested_at` 與 `source_revision_id`：本系統實際接收時間及來源版本；
4. `bar_available_at`：最後必要 member/source 全部到齊且 bar 通過品質檢查的時間；
5. `feature_available_at`：FFD、rolling、跨資產 as-of join 全部完成且可供決策的時間；
6. `decision_time`：模型 snapshot 的知識截止點；
7. `earliest_execution_session`：依交易日曆、decision cutoff 與上游 execution policy 推導的第一個可執行 session。

`for_trading(as_of, decision_cutoff)` 必須以 knowledge time 過濾，而非只用 observation date：只允許 `live_eligible=True`、`bar_status=FEATURE_READY`、`calibration_effective_at <= source_available_at of the first membership row`、`bar_available_at <= decision_time` 且 `feature_available_at <= decision_time` 的最後一列。所有跨 ETF、IX0001、VIX 或其他環境欄位分別做 backward as-of，回傳各自的 `source_available_at` 與 staleness；超過 config 上限就回傳 missing/quality flag，不得無限 carry-forward。

`bar_daily_membership` 每列至少保存 `observation_date, source_available_at, ingested_at, source_revision_id, source_manifest_hash`，使 `bar_amount`、NAV path、threshold crossing 與 quality flags 能依任意 `decision_time` 重播。若歷史來源只有目前最新版、沒有 publication/vintage history，資料可用於保守 after-close 研究，但整個 trading snapshot 必須帶 `PIT_REVISION_UNVERIFIED`，不得宣稱已證明 revision-safe PIT。

open bar 可以保存 checkpoint 供未來日接續，但任何 partial `bar_amount`、partial NAV path 或「接近 threshold」狀態預設不進正式 feature table。bar 完成後也不得把其 feature 回填到 `bar_end_date` 日內；事件的知識時間是 `feature_available_at`。labels 與 `t1` 永遠位於獨立 research table，`for_trading` 在 schema 層就不得載入它們。

走向實際交易時，模型輸出只是一個在 `decision_time` 成立的 ETF allocation decision。執行層必須在 `earliest_execution_session` 重新以 PIT 方式查詢當時有效的 ETF constituent targets/weights、原始未還原價格、可交易狀態與成本規則；Dollar bar 的歷史 holdings、bar NAV、`adj_close` 或尚未完成的 execution-session close 都不能拿來拆解下單股數。若執行日成分或價格尚未可得，必須延後或 fail closed，不能回用未來補齊值。

## 7. Fixed-width FFD and `d*` selection

### 7.1 Mathematical contract

對 Dollar-bar `log(close_nav)` 使用：

```text
w[0] = 1
w[k] = -w[k-1] * (d-k+1) / k
FFD_d[x_t] = sum(k=0..width, w[k] * x[t-k])
```

從 `k=1` 逐步建立權重，固定寬度在第一個 `abs(w[k]) < tolerance` 前停止，保存最後一個納入的權重與完整 weight vector。只輸出擁有完整 width 的 `valid` observations；禁止 partial-window、zero padding、backfill 或 forward-fill。

`fracdiff-modern==1.0.0` 可作計算工具，但其 `window` 必須由 governed tolerance 推導，使用 `mode="valid"`，並與遞迴公式、手算 fixture 及 `d=0/1` 邊界做 parity test。library behavior 不是權威定義，也不可安裝 `mlfinlab`、`mlfinpy` 或其他替代品。

### 7.2 Search and selection

每檔 ETF 分開搜尋：

1. 在 `[0,1]` 以 0.05 coarse grid 計算 FFD 與 ADF；
2. 找到第一個通過點後，在前一 coarse point 到該點間以 0.01 全格 refine；
3. `pass = p_value < 0.05 AND adf_stat < critical_value_5pct`；
4. 選最小通過的 `d`，以較小 `d` 最大化記憶保留；
5. 保存 aligned raw-log/FFD correlation、width、nobs、lags、p-value、statistic、critical values、regression 與 failure reason。

`[0,1]` 是起始搜尋區間，不是硬上限。AFML Chapter 5 明確指出 `d` 可為任意正分數，且爆炸性序列可能需要 `d*>1`。若起始區間沒有通過，Model 不需等待使用者核准，按下列治理流程自主處理：

1. 先驗證輸入排序／重複／缺失、Dollar-bar coverage、FFD width、post-FFD `nobs`、數值有限性與 ADF regression residual diagnostics，區分資料失敗、樣本不足、檢定規格問題與真正未達平穩；
2. 在不改 alpha、不指定 fallback `d` 的前提下，自動以相同 coarse/refine 規則搜尋 `(1,2]`；
3. 若到 `2.0` 仍未通過，Model 可依 training-only evidence 自主建立下一個有限搜尋區間，不需向使用者取得個別核准。每次擴張前必須先寫入新的 versioned config：區間、理論或實證理由、預期檢定規格、最低有效樣本及停止條件；預設每次只增加 `d_expansion_span=1.0`，本版 hard stop 為 `autonomous_max_d=5.0`，禁止無界或一次跳到任意大值；
4. 可用 Phillips-Perron、KPSS、Zivot-Andrews 或 Fractional Dickey-Fuller 作 corroborative diagnostics，判斷序列相關、確定性趨勢、結構斷裂或 fractional integration。這些結果不能在看到 p-value 後被事後挑選成新的通過門檻；若要改 primary gate 或 deterministic/lag specification，必須先依預先定義、可重現的 decision rule 建立新 calibration version，再重跑完整候選區間；
5. 只有找到最小有效通過值、資料／樣本／數值條件失敗，或已沒有文獻與診斷支持的下一個合理區間時才能停止。最後一種情況回報 `stationarity_not_reached`，不是要求使用者替 Model 猜下一個 `d_max`。

不得靜默選 `d=1`、放寬 alpha、降低最低樣本、把不同檢定中最有利的結果拼成通過，或只因更高 `d` 能拒絕單根就忽略記憶損失與過度差分。所有嘗試、被拒 config 與停止理由都要寫入 `ffd_search`/diagnostics。

ADF 是必要但非充分證據。可額外保存 KPSS diagnostic；若啟用 `strict_dual_gate`，其規則必須在 run 前固定，不能事後挑選。

### 7.3 Full-history FFD and leakage boundary

必須嚴格區分 `fit/select` 與 `transform`：

- 權重只由已凍結的 `d` 與 tolerance 決定，且 `FFD_d[x_t]` 只使用 `x_t, x_{t-1}, ...`。因此對完整歷史一次執行 causal transform，不會讓未來價格直接進入過去的 FFD 值；append future rows 時，既有 prefix 必須完全不變。
- 若用包含 validation/test 的完整序列跑 ADF 並選出 `d*`，過去 feature 雖沒有直接使用 future price，其 preprocessing parameter 已受到未來分布影響。這是 data-dependent preprocessing leakage，不能用於乾淨的 OOS/CV 績效主張。
- `research_full_history` 可依 AFML Chapter 5 的示範用全樣本研究 `d*`，但只能 `DESCRIPTIVE_ONLY`。`train` mode 必須在 training-only 選 `d*` 後凍結，再以同一參數 transform training/validation/test；`walk_forward` 則只能在預定 retrain date 用當時已知資料重選。
- 最終模型、特徵規格與評估流程全部凍結後，可在部署時用截至部署日的所有可得歷史重新 fit `d*` 與模型；但原 test set 從此已參與最終 fit，不能再被宣稱為 untouched test evidence。

這個 leakage 界線是一般 ML evaluation governance，不是 AFML Chapter 5 明文禁止「使用完整歷史」。書中以合約自 inception 的 E-mini 歷史搜尋 `d*` 作方法示範，並未在該節討論 train/validation/test。方法依據包含 [Hosking (1981)](https://doi.org/10.1093/biomet/68.1.165) 的 fractional differencing 定義、[Dolado, Gonzalo & Mayoral (2002)](https://www.econometricsociety.org/publications/econometrica/2002/09/01/fractional-dickey%E2%80%93fuller-test-unit-roots) 的 Fractional Dickey-Fuller 檢定，以及 [Moscovich & Rosset (2022)](https://doi.org/10.1111/rssb.12537) 對 data-dependent unsupervised preprocessing 在 cross-validation 中造成偏誤的證明；實作上的 train-only fit 原則另見 [scikit-learn data leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)。

## 8. SADF, QADF, and CADF features

在每個 event time `t`，只用 `<=t` 的 raw Dollar-bar log NAV 建立 backwards-expanding ADF 集合：

```text
s_t = {ADF[t0,t] for all governed start points t0}
SADF_t = max(s_t)
QADF_t(q) = quantile(s_t, q)
QADF_dispersion_t(q,v) = quantile(s_t, q+v) - quantile(s_t, q-v)
CADF_t(q) = mean(x in s_t where x >= QADF_t(q))
CADF_dispersion_t(q) = std(x in s_t where x >= QADF_t(q))
SADF_CADF_z_t = (SADF_t - CADF_t) / CADF_dispersion_t
```

使用 log prices、固定 `min_sample_length`、lags 與 deterministic regression specification。empirical quantile 的 interpolation/method 固定為 config 的 `quantile_method`；Conditional ADF 尾端包含 `x >= QADF`，標準差使用書中條件母體矩的 `ddof=0`。`QADF_dispersion` 是書中定義的 quantile spread；`CADF_dispersion` 是右尾條件分布的標準差，兩者不得混名或互相替代。零 dispersion 時 z-score 為 missing 並附原因，不得補 0。

同一組 ADF regressions 必須一次重用來計算 SADF/QADF/CADF，避免三次重跑。這些序列同時針對各 ETF 及 IX0001 建立；IX0001 feature 對齊各 ETF bar end 時只能 backward as-of join。完整 `s_t` 可選擇壓縮保存，但每個 `t` 至少保留 statistic、`q`、`v`、`qadf_dispersion`、`cadf_dispersion`、window count、min start、maximizing start、config/hash 與 quality flag。

SADF 是 `q=1` 的極端值且容易受 outlier 影響；QADF/CADF 預設一併保存，不允許只留下最後一個全樣本 statistic。

## 9. Feature contract

每個 feature row 必須保存 `feature_available_at`；任何 decision row 只能包含 `feature_available_at <= decision_time` 的值，不得包含 label end、future barrier path、open partial bar 或全樣本 scaling。輸出 raw values、必要 observation counts、source bar timestamps、staleness 與 missingness flags；imputation、winsorization、standardization 只可由下游 training fold fit。

### Tier 1 - Auto-FFD memory features

- `ffd_level`；
- `ffd_ma_distance_20 = ffd_level - rolling_mean(ffd_level,20)`；
- `ffd_ma_distance_z_20`，分母只用截至當期的 rolling sample std；
- `ffd_level_std_14`, `ffd_level_std_60`；
- `ffd_change_vol_14`, `ffd_change_vol_60`，明確與 level std 分開；
- FFD width、selected `d*` 與 calibration-version categorical/audit fields。

### Tier 2 - Close-path trend and distribution features

- Dollar-bar log return、realized volatility、downside volatility；
- `efficiency_ratio = abs(close_t-close_t-n) / sum(abs(delta_close))`；
- rolling drawdown、`close_path_range = log(close_path_high_nav / close_path_low_nav)`；
- `ffd_level_skew_60`, `ffd_level_excess_kurtosis_60`，至少 30 個觀察；
- bar duration 與 `duration_surprise = trading_day_count / median(previous 20 bar durations)`。

目前沒有真實且同步的 ETF open/high/low。`close_path_high/low` 只是日收盤路徑極值，因此不得輸出名為 ATR 或 ADX 的欄位。未來若新增通過 PIT/schema/coverage 驗證的真實 ETF OHLC，再以獨立 feature version 啟用。

### Tier 3 - Honest liquidity and activity features

- 未縮放的當期 `bar_amount` 必須直接進入正式 feature table；另計算 `log1p_bar_amount`、`amount_ratio_20 = bar_amount[t] / mean(bar_amount[t-20:t-1])`，以及只以前期 finalized bars 的 EWMA mean/std 計算的 amount z-score；
- `overshoot_ratio`, `trading_day_count`, `etf_market_share`；
- Amihud illiquidity：`abs(log_return) / bar_amount` 的 rolling mean 與 regression/t-statistics；
- Roll close-price spread estimator僅在其 covariance domain 成立時輸出，否則 missing + reason；
- bar end 已知的 portfolio HHI、cash/invested weight、holdings count、target completion，以及上一個已完成月份的 realized constituent replacement/turnover。

VPIN 需要等量 volume buckets 內的 buy/sell volume；Kyle lambda 需要 aggressor flags 或 signed order flow。現有日資料不具備這些條件，兩者正式狀態為 `UNAVAILABLE_SOURCE_GRAIN`。若未來研究 close-only proxy，名稱必須含 `_proxy`，不可進入正式 VPIN/Kyle 欄位，也不可引用書中理論值替它背書。

### Tier 4 - Global environment features

- IX0001 log return、rolling volatility 20/60、drawdown；
- IX0001 SADF、QADF、QADF quantile dispersion、CADF、CADF conditional dispersion 與 `SADF_CADF_z`；
- ETF 對 IX0001 rolling beta/correlation，全部 backward-looking；
- VIX 只有在 manifest-declared PIT artifact 存在時可啟用，否則輸出 capability diagnostic，不建立假欄位。

## 10. Triple-barrier directional-label contract

### 10.1 Event and target

每個 feature-ready Dollar-bar close 都可成為 event。必須分開保存 `t0_bar_id`、`t0_observation_date=bar_end_date` 與 `event_available_at=feature_available_at`；模型的 knowledge time 不得倒填成 `t0_observation_date`。entry reference price 是 `close_nav[t0]`；barrier search 從 `t0` 之後第一個 daily close 開始，features 不得讀取該路徑。

風險尺度預設為截至 `t0` 的 60-bar EWMA log-return standard deviation，至少 20 個有效 observations。水平屏障採 log-price 對稱定義：

```text
upper = log(P_t0) + pt_mult * sigma_t0
lower = log(P_t0) - sl_mult * sigma_t0
```

預設 `pt_mult=2`, `sl_mult=2`。ATR target 只有在真實 OHLC capability 成立後才可選。

### 10.2 Path and vertical barrier

vertical barrier 是 `t0` 後第 60 根完成的 Dollar bar。為利用仍可觀察的日資料，每根未來 bar 內依原始 daily NAV close sequence 判斷第一個 close-touch 日期；這是 daily-close first touch，不是 intraday high/low touch。若 horizontal barrier 未先觸碰，事件在第 60 根 bar end 結束。

預設 label：upper first=`+1`、lower first=`-1`、vertical first=`sign(log(P_t1/P_t0))`；恰為零者標記 `zero_vertical_return` 並從二元 target 移除。可配置 `zero_class` 或 `drop`，但不得在看到 test class balance 後修改。

本 target 是 primary directional label `{-1,+1}`。AFML meta-label 的 `{0,1}` 是未來第二層模型，兩者不得混稱。

`2x/2x` 搭配 60 bars 只是依使用者要求建立的 baseline，不預設它在數學上已校準。當 `sigma_t0` 是一根 bar 的 volatility 時，水平屏障可能遠早於第 60 根觸發；每次 run 必須報告 upper/lower/vertical 比例、median/p90 time-to-touch 與 realized holding days。若 vertical touch 幾乎不存在或 holding time 遠短於 60 bars，readiness 必須提出 `barrier_horizon_mismatch`，後續只能在 training/validation 內比較預先登記的 multiplier/horizon grid，不能依 test outcome 調參。

### 10.3 Event output

保存 `etf_id, event_id, t0_bar_id, t0_observation_date, event_available_at, entry_reference_price, sigma, upper, lower, vertical_bar_id, vertical_date, first_touch_type, first_touch_date, t1, label_available_at, realized_log_return, label, label_status, pt_mult, sl_mult, vertical_bars, source_path_kind, config_version`。`label_available_at` 是 first-touch/vertical outcome 所需來源實際到齊且 label 完成的最晚時間；horizontal touch 可在 future bar 尚未關閉時由已到達 daily-close path 確認，但不能早於該 touch day 的來源 availability。最後不足 60 根 future bars 的 events 必須 `unresolved_tail`，不得提前縮短 horizon。

另存 event concurrency、average uniqueness 與 `t1`，讓後續 purged/embargo CV、sequential bootstrap 與 sample weights 不必重建路徑；本輪不使用它們調模型。

## 11. Public API and artifacts

Notebook 使用方式應保持薄層：

```python
from etf_tricks import ETFTrickResult
from etf_tricks.afml import AFMLConfig, ETFAFMLLab

base = ETFTrickResult.read(ETF_TRICK_RESULT_DIR)
lab = ETFAFMLLab.from_data_analysts(DATA_ANALYSTS_ROOT)
dataset = lab.build_all(
    base,
    config=AFMLConfig(),
    mode="train",
    train_start=TRAIN_START,
    train_end=TRAIN_END,
    validation_end=VALIDATION_END,
    test_end=TEST_END,
)

dataset.train
dataset.validation
dataset.test
dataset.dollar_bars
dataset.ffd_search
dataset.ffd_series
dataset.structural_features
dataset.features
dataset.events
dataset.labels
dataset.diagnostics
dataset.for_ml("momentum", split="train")
dataset.for_trading(as_of=AS_OF, decision_cutoff=DECISION_CUTOFF)
```

研究邊界必須由使用者或 governed run config 明確提供。`dataset.train/validation/test` 是 split views；公開 API 的 mode 只使用 `train`。參數凍結語意只存在 manifest 的 `calibration_scope=train_only`、`parameters_frozen_at` 與 calibration version。

Canonical tables：

1. `source_capabilities`
2. `dollar_bars`
3. `open_bar_checkpoints`
4. `bar_daily_membership`
5. `ffd_weights`
6. `ffd_search`
7. `ffd_series`
8. `structural_features`
9. `features`
10. `events`
11. `labels`
12. `diagnostics`

`for_ml(etf_id, split)` 回傳一列一個 event 的 feature table，label 以獨立欄位／table join，並附 `t0_observation_date,event_available_at,t1,label_available_at`。不得默默刪除 missing feature rows，也不得把 split cutoff 後才可得的 label join 回較早 split。`for_trading(as_of, decision_cutoff)` 只能 backward as-of 取得 13 ETF 最後一根 feature-ready bar，回傳來源時間、staleness、calibration version 與 `earliest_execution_session`，且絕不包含 labels、`t1` 或 future path；若 `as_of` 早於 calibration effective time、PIT 條件不成立或沒有合格 completed bar，應回傳明確 unavailable status，而不是取未來第一列或 calibration-history row。

`for_trading` 是 PIT-safe feature snapshot，不是訂單或 sizing API。未來模型分配某 ETF 資金後，必須把 `etf_id, allocated_capital, earliest_execution_session` 交給上游既有 allocation/execution 介面，使用該 execution session 當時有效的 constituent targets/weights 與原始未還原可執行價格拆解股數；不得使用 bar NAV、`adj_close`、bar 結束日已不再有效的 holdings，或尚未可得的 execution-session close。台灣最少 1 股、最低手續費與滑價仍由上游 execution contract 處理，本層不得重寫。

Runtime artifacts 寫入 git-ignored `.artifacts/etf_tricks/afml/<run_id>/`，每張 parquet 具 row count、schema、key、SHA-256；manifest 保存來源與 config identities。正式策略升級時才依 repository mainline 將經核准的 research summary/spec 投影到 `output/research/...`；不得把衍生市場資料提交 Git。

## 12. Engineering and performance

- 先寫三到五根 bars 的手算 amount/threshold/overshoot fixture，再向量化。
- FFD 可使用 convolution/matrix operation；相同 `d,width` 的 ETF 可批次處理，但 NaN mask、valid window 與 output index 必須完全一致。
- SADF/QADF/CADF 共用每個 endpoint 的 ADF vector；優先 sufficient-statistics、NumPy/SciPy 線性代數與可重用 matrix slices，禁止三套巢狀 pandas loops。
- 只優化 profiler 證明的瓶頸；數學等價前不得 JIT/GPU/多程序化。
- 測試順序固定為：手算／synthetic fixtures；1 至 2 個 ETF 的 `2024-01-01` 至 `2026-07-07`；13 ETF 的同一短區間。只有因有效 bar/FFD/window observations 不足的測試，才可把所需範圍延伸到 `2020-01-01` 至 `2026-07-07`，並記錄延伸原因；不得直接跳至全歷史。
- 所有短區間 correctness、PIT replay、schema/key/hash、數學 parity、效能與 peak-memory gates 通過後，才能以顯式 `full_history_acceptance=True` 執行一次 13 ETF 完整歷史驗收。一般 unit/integration/profile command 必須拒絕 13 ETF 全歷史 scope。
- profiling 先 warm-up，逐 stage 報 wall time、peak memory、rows 與 hashes；短區間未證明等價前不得以全歷史測試發現基本錯誤。
- 不新增依賴，除非使用者另行核准；沿用 project `.venv`。禁止安裝 `mlfinlab`、`mlfinpy` 或 fork。

## 13. Required tests

### 13.1 Dollar bars

- 手算 lagged IX0001 baseline、共同 `q_star` threshold、跨日 accumulation、bar-start threshold freeze、overshoot、terminal incomplete bar；
- 超大單日 amount 仍只關閉一根；
- 日成員 amount sum 與 `bar_amount` 完全對帳，且未縮放 `bar_amount` 存在於正式 feature table；
- 低 `etf_amount / IX0001_amt` regime 必須產生較長 duration，而不是被自身均量正規化回固定頻率；
- 依 `source_available_at` 而非僅 observation date 進行的逐筆／逐日 replay，必須與相同 as-of cutoff 的 batch prefix finalized bars/features 完全一致；延遲任一必要來源時 bar 不得提前 final；append future rows 不改變既有 finalized bars；
- open checkpoint 可在新資料到達後完成，但在完成前不存在於 completed bars/features；
- `bar_available_at`、`feature_available_at`、decision cutoff 與 `earliest_execution_session` 手算一致，同日 close execution 被拒絕；
- missing/quality-flagged amount 按 policy fail closed。

### 13.2 FFD and stationarity

- recurrence weights 與手算 convolution；
- `d=0`, fractional `d`, `d=1`；
- tolerance-to-width 邊界、valid-window index、無 partial boundary；
- `fracdiff-modern` parity；
- known stationary/unit-root synthetic series 的 ADF gate；
- 選出的 `d*` 必須是 grid 上第一個 pass；
- `[0,1]` 無 pass 時，必須自動進入 `(1,2]`，且 escalation config/audit 可重現；
- 任何超過 `2.0` 的搜尋都必須有先行 diagnostics、有限區間與明確停止條件；
- future append 不改變 `train` mode 已凍結的 `d*` 或歷史 FFD 值。
- 固定 `d` 的 full-period transform 與分段 transform 在相同 warm-up 下輸出一致；full-history 選 `d*` 則必須是 `DESCRIPTIVE_ONLY`。

### 13.3 Structural features

- 小型 ADF vectors 分別手算 SADF、QADF、`Q(q+v)-Q(q-v)`、CADF、右尾條件標準差與 `SADF_CADF_z`；
- 每個 feature time 不讀 future end points；
- QADF `q=1` 與 SADF 關係；
- zero-dispersion 明確 missing；
- IX0001 backward as-of alignment 無 forward match。

### 13.4 Features and labels

- rolling windows、min observations、skew/kurtosis convention；
- ATR/ADX/VPIN/Kyle capability flags，禁止錯名 proxy；
- triple-barrier upper/lower/vertical first-touch、60-bar tail、zero-return policy；
- event path 從 `t0` 後開始，features 與 label path 完全隔離；
- 已結束 event 在 append 更晚資料後不變；
- input shuffle 不改變 deterministic output。

### 13.5 Integration and leakage

- 13 個 ETF 全部存在且 keys/schema 唯一；
- 每根 bar、FFD、feature、event 可回溯到同一 source manifest/hash；
- source-capability matrix 對 VPIN、Kyle、ATR、ADX、VIX 的 schema/grain/PIT/coverage 狀態具 fresh evidence；
- 跨 ETF/IX0001/VIX join 只可 backward as-of；在相同 decision time 注入未來完成 bar 不得改變輸出；
- 人工延遲某個 source 的 `source_available_at` 時，該 bar 與其 `bar_amount`/features 在延遲前不可見，延遲後才一次變成 finalized/feature-ready；
- q/calibration version 在 effective time 前建立的 bars 只能是 `CALIBRATION_HISTORY`；新的 walk-forward q 不得重切已開啟或已完成 bar；
- `for_trading` 在 calibration effective time 前、無 completed bar、staleness 超標或只剩 provisional bar 時回傳明確 unavailable，不得 forward lookup；
- 以每個歷史 `decision_time` 做逐日 replay，其 trading snapshot 必須等於一次 batch build 後以同一 knowledge-time filter 截出的 prefix；
- 同一 observation date 的新 revision 必須產生新的 source/result identity；舊 artifact 保持不可變，缺少 vintage 時明確標記 `PIT_REVISION_UNVERIFIED`；
- split 歸屬以 `feature_available_at` 決定；任何跨界 bar 都有 flag 且不得成為 validation/test/live-eligible 起始 bar，training labels 必須同時通過 `t1` 與 `label_available_at` cutoff；
- `for_trading(as_of)` 不含 labels、future path、open bar 或 decision cutoff 後來源；
- full-history parameter selection 不可誤標為 ML-ready；已凍結參數的 full-period causal transform 不可被誤判為 leakage；
- scaling/imputation 不在本層 fit；
- Notebook API 可一次取得全部 tables 與單一 ETF ML view；
- 無 `full_history_acceptance=True` 時，測試入口拒絕 13 ETF 全歷史 scope；
- `python -O` 下 validation 仍有效，不依賴 `assert`。

## 14. Fail-closed readiness

下列任一成立即 `NOT READY`：

- 上游 result identity/readiness 無法驗證，或不是完整 13 ETF；
- Dollar-bar threshold 使用 future/current moving target、各 ETF 自身均量或不同 `q` 破壞共同資訊尺度、一天生成多根假 bars、amount 無法對帳；
- finalized bar/features 的 availability 早於任一必要來源、同日 close 被當成可執行價、跨 ETF 使用 forward join、或 open bar 混入正式 features；
- FFD 使用 partial window、權重或 index parity 失敗；
- ML mode 的 `d*` 使用 validation/test/full-history 資料；
- ADF grid 沒有 pass 卻靜默選值，或 autonomous escalation 透過放寬 alpha、降樣本、事後挑檢定取得通過；
- structural feature 讀到未來資料；
- proxy 被命名為 VPIN、Kyle、ATR 或 ADX；
- VPIN、Kyle、ATR、ADX、VIX 沒有 source-capability evidence 卻被標為 available；
- label path、touch ordering、vertical horizon 或 event end 不可重建；
- 任一 ETF 無法產出足以通過 config 最低觀察要求的 bars/FFD/features/labels；
- manifest/schema/key/hash 或 Notebook API 驗證失敗。

Warnings 必須保留：bar count 過少、overshoot 過高、duration drift、class imbalance、unresolved tail、zero return、stationarity marginal pass、FFD memory correlation 下降、structural statistic zero dispersion、VIX unavailable、daily-close path limitation、synthetic amount limitation。

## 15. Final acceptance evidence

完成前必須提供 fresh、可重現 evidence：

- 13 ETF 各自的 daily input range、共同 `q_star` calibration evidence、bar count/frequency/duration/threshold/`bar_amount`/overshoot；
- `d*`, width, ADF statistic/p-value/critical value/nobs、memory correlation 與 calibration mode；
- SADF/QADF/CADF coverage 與 missing causes；
- feature/label row counts、class balance、unresolved events；
- source-capability matrix 與 PIT availability evidence；
- replay/prefix invariance、backward as-of、split/label containment、execution timing、hand fixture、schema/key/hash 與 Notebook smoke tests；
- 2024-2026 各階段 timings；若有延伸，另列 2020-2026 的原因與 timings；
- full-history acceptance command/config 與證明其只在 bounded gates 通過後執行；
- 所有 canonical artifact paths/hashes 與 code/config identity；
- 明確標題 `目前可用` 與 `目前缺失／限制`。

這些證據只證明研究資料層可用，不證明 alpha、因果性、交易容量、模型泛化或投資績效。
