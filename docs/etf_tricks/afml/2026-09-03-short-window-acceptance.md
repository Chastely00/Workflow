# ETF Tricks AFML Dataset 2026-09-03 驗收

權威目標：`GOAL-ETF-AFML-DATASET-001`<br>
驗證日期：2026-09-03<br>
範圍：資料、PIT 對齊、Dollar bars、FFD、結構特徵與 triple-barrier labels；**不含模型訓練、績效或 production 宣告**。

## 結論

13 個 ETF 的 full-history artifact `full-history-20050103-20260707-v5` 已完成、可讀回且為
`READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS`。所有 ETF 均有 Dollar bars、已通過的 train-only
FFD selection、feature rows 與 resolved labels；因此可以做受限的後續研究與 Notebook 探索。

它**不是 production-ready**。PIT revision identity、交易日曆 manifest coverage 及 VPIN/Kyle/ATR/ADX/VIX
來源仍未補足，這些限制會隨 artifact 一起保留，而不是以代理欄位冒充可用資料。

## 驗收序列與避免重算原則

| 階段 | Artifact | 結果 | 原因 / 用途 |
|---|---|---|---|
| 最短 smoke | `acceptance-2etf-20240201-20260707-r1` | `NOT_READY` | market_cap 可通過；momentum 在 train split 後沒有足夠 FFD observations，正確拒絕產生研究資料。 |
| 延長短窗 | `acceptance-2etf-20200102-20260707-r1` | `READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS` | 2 ETF 都通過；用來確認不足時應延長樣本而非放寬 FFD gate。 |
| 13 ETF bounded | `acceptance-13etf-20200102-20260707-r1` | `READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS` | 13 ETF 的首次全 Universe 短窗驗收。 |
| 唯一採用的全歷史結果 | `full-history-20050103-20260707-v5` | `READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS` | DMS 狀態語意修正後的正式 full-history acceptance。 |

短窗先使用 2024–2026；只有觀察數不足才延伸至 2020–2026。全歷史 v5 僅在 bounded 驗收完成、修正已驗證的資料語意缺陷後執行。先前的 full-history artifact 不得作為目前研究輸入。

## 可重現性與完整性

| Artifact | manifest SHA-256 | metadata SHA-256 | readiness SHA-256 |
|---|---|---|---|
| 2024–2026, 2 ETF | `5d4c960d6bbc126e4984e4e681f540fa1351946ae245031141f4ab3ba85bd197` | `ca5f8c0039ee98f4d0ef1af072deb5e4867a62a78f4ca1843eb973c9a37b0cb1` | `d504a645dbdad354e979ac23dee41bb7159d140402ac38898d6b44878adf12ea` |
| 2020–2026, 2 ETF | `41a2f12c386e637bbdc38d779f709be61c5ee3ce9abdae421322111607fb8175` | `463aa3b537cac8bd781afb1f34d7a4d2ff1980d084df9c9df5ed8feeb4065124` | `981286ce07c87d7e4d7edc122fc07188bde9b2681e60a4adb77f86a2942d5254` |
| 2020–2026, 13 ETF | `bbc65106aecd456d919077c9e3ee81d4abd359ab0c55d703d43c0459c1f86487` | `1a63271aaab0110975137b38ae190ea9ca279327dd77d638b03f3fa8f00dfd6c` | `97474792a63a4416c2f9182fe41987b8f2c65150d87f98b9324ca333e73aff13` |
| 2005–2026, 13 ETF v5 | `88a1ce7f0c572d500b42fc5472f6de817abdea748cd2189fa089d8e37e6b1da5` | `77b2079dd588cd242ee0df287f70ecfa3cda04f093bd0117da06422545a83166` | `36c1dbbdd0f9d443f4fa6b92e017c0102e80c73ab5c358366c1edcfdea00025c` |

v5 的 `etf-afml-dataset-v2` 有 12 張 canonical tables。write 後 read-back 已驗證 schema、key、dtype、table hash 及 PIT cross-table clocks；base ETF artifact 的 daily input 覆蓋 2005-02-01 至 2026-07-07（較晚成立的 ETF 依其實際 inception 開始），IX0001 在要求的 5,287 個交易日都有資料。

## 效能實測

| 驗收 | 量測 stage time | 主要耗時 stages |
|---|---:|---|
| 2024–2026, 2 ETF | 4.90 秒 | labels 1.47s、structural 1.24s |
| 2020–2026, 2 ETF | 10.33 秒 | calibration 3.67s、structural 3.47s |
| 2020–2026, 13 ETF | 53.96 秒 | calibration 24.77s、labels 13.91s、structural 9.98s |
| 2005–2026, 13 ETF v5 | 139.26 秒 | calibration 64.72s、labels 37.13s、structural 20.49s |

v5 產出 18,056 Dollar bars、8,608 FFD rows、18,056 features/events/labels rows，以及 64,995 bar-daily membership rows。耗時集中於需要逐 ETF、train-only 校準的邏輯，不再有逐日 SQLite commit 或對每一天重掃全部歷史資料的路徑。

## Dollar bars 與 FFD

Dollar bar 以 train-only IX0001 lagged market-amount baseline 校準，全樣本 `q* = 0.0289130774`，
參數於 2020-12-31 freeze、2021-01-04 生效；bar 的 threshold/calibration version/availability 都寫入資料表。v5 每個 ETF 的 Dollar bar 數均達至少 588（market_cap 為 3,772）。

FFD 使用 fixed-width causal convolution，對 log NAV 的訓練資料自主搜尋最小通過 ADF 的 `d`；不會使用 validation/test 來選參數。13 個 ETF 都取得 `stationarity_reached`：

| ETF | d* | width | ADF p-value | 與 log NAV correlation |
|---|---:|---:|---:|---:|
| market_cap | 0.32 | 2,084 | 0.02877 | 0.87575 |
| monthly_sales | 0.00 | 0 | 0.04208 | 1.00000 |
| chip | 0.68 | 408 | 0.00010 | 0.42281 |
| roe | 0.54 | 773 | 0.00041 | 0.67990 |
| momentum | 0.56 | 706 | 0.00179 | 0.56994 |
| low_volatility | 0.59 | 616 | 0.00000 | 0.08949 |
| financial | 0.62 | 538 | 0.00000 | 0.20632 |
| shipping | 0.88 | 142 | 0.00000 | 0.26464 |
| volume_ratio | 0.71 | 354 | 0.00000 | 0.47705 |
| traded_amount | 0.32 | 2,084 | 0.03223 | 0.85487 |
| turnover | 0.62 | 538 | 0.00007 | 0.56366 |
| sharpe_60d | 0.59 | 616 | 0.00004 | 0.57240 |
| sortino_60d | 0.60 | 589 | 0.00008 | 0.55636 |

`monthly_sales` 的最小足夠階數為 0；這是 ADF gate 的結果，不是缺失 FFD。所有 p-value 均低於 0.05 且 ADF statistic 低於各自 5% critical value。

## 標籤、PIT 與資料可用時點

- Triple barrier 使用 `pt_mult = sl_mult = 2`、`vertical_bars = 60`、截至 `t0` 的 EWMA bar volatility；daily close 僅可作 first-touch path，沒有假裝使用 intraday path。
- 18,056 events 中 17,016 已 resolved：上界 9,664、下界 7,349、vertical 3；另有 780 筆 `unresolved_tail` 與 260 筆 `insufficient_target_volatility`，均保留為不可訓練資料，沒有縮短 tail 或補造標籤。
- `for_ml` 按 `label_available_at`、split eligibility 與 embargo/uniqueness 規則選取，不允許 label 尚不可得的 row 進入訓練；`for_trading` 只由 `bar_available_at`、`feature_available_at` 與 frozen calibration 產出，沒有 label、`t1` 或 future-touch 欄位。
- SADF/QADF/CADF 與 dispersion 已保留。22,517 structural rows 有值；826 筆是歷史不足，280 筆 CADF dispersion 為零而使 z-score 缺失，均以 `structural_quality_reason` 明示，沒有補值。

## Source capability 與限制

| 項目 | 狀態 | 原因 |
|---|---|---|
| IX0001 | `PARTIAL_COVERAGE` | 5,287 日資料可用，但 publication availability/revision history 尚未驗證。 |
| VPIN | `UNAVAILABLE_SOURCE_GRAIN` | 缺 tick、等量 bucket 與買賣方向。 |
| Kyle lambda | `UNAVAILABLE_SOURCE_GRAIN` | 缺 signed order flow/aggressor side。 |
| ATR / ADX | `UNAVAILABLE_SOURCE_GRAIN` | 缺真實同步 synthetic ETF OHLC； constituent OHLC 不可替代。 |
| Taiwan VIX | `UNAVAILABLE_SOURCE_GRAIN` | 尚無 manifest-declared PIT-safe artifact。 |
| Trading calendar | `PARTIAL` | 實際 session 完整，但 manifest coverage 尚未宣告。 |

因此目前可進行：資料探索、特徵研究、purged/embargo 的後續模型實驗。不得進行：production 交易宣告、將 unavailable 特徵當作零或 proxy、或將 PIT revision 未證實的資料解讀為最終歷史真值。

## Notebook 使用入口

以 repository root 為工作目錄，使用 `.venv` kernel。讀取既有 artifact 後，依目的選擇 `dataset.for_ml(...)` 或 `dataset.for_trading(...)`；前者用於已完成 label 的研究資料，後者用於決策當下可得 snapshot。這兩條路徑不得混用。
