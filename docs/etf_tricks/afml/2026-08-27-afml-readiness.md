# ETF Tricks AFML Dataset Readiness

驗證日期：2026-08-28  
權威目標：`GOAL-ETF-AFML-DATASET-001`  
結論：**13 ETF 的 2024–2026 bounded research dataset 已可用；2005–2026 full-history 尚未通過，因此整體 Goal 不得宣稱 complete 或 production-ready。**

## 目前可用

- 公開入口：`ETFAFMLLab.from_data_analysts(...).build_all(...)`、`AFMLDataset.read/write`、`dataset.for_ml(...)`、`dataset.for_trading(...)`。
- 12 張 canonical tables 均可寫入 Parquet、原子發布、記錄 schema/key/dtype/row count/SHA-256；`etf-afml-dataset-v2` 只有在 write 後 read-back 的 hash/schema/key/dtype 與 PIT cross-table clocks 全部通過才取得 finalized READY。
- 13 個 ETF 在 2024-01-01 至 2026-07-07 的 bounded run 全部有 Dollar bars、FFD selection、features 及已完成 labels。
- `bar_amount` 是未縮放正式 feature；`amount_ratio_20` 與 amount EWMA z-score 只使用前期 finalized bars。
- Dollar bar threshold 使用 training-only IX0001 lagged market-amount baseline；threshold/version 在 bar 開始後凍結，walk-forward 不重切已開 bar。
- FFD 使用 fixed-width causal convolution；`d*` 在 training-only 搜尋，必要時可自主延伸至 `d>1`，達 governed hard limit 才回報 `stationarity_not_reached`。
- ETF raw Dollar-bar log NAV 與 IX0001 daily log close 均保存 rolling SADF、QADF、QADF dispersion、Conditional ADF、conditional dispersion 與 `SADF_CADF_z`；IX0001 只做 backward as-of join。
- Triple-barrier 使用截至 `t0` 的 EWMA bar volatility、每日收盤 first-touch path 與恰好 60 根完成 bars 的 vertical horizon；terminal tail 不縮短。
- `for_trading()` 明確同時套用 `bar_available_at`、`feature_available_at`、calibration 與 live gates；13 檔 bounded snapshot 全部為 `AVAILABLE`，保存 `decision_cutoff/source_bar_id/snapshot_status`，且 schema 不含 `label`、`t1`、`label_available_at` 或 future touch path。
- 在 calibration effective 日 2025-07-01 的 production-schema regression 中，只有 5 檔已有完成的 live bar 而回傳 `AVAILABLE`；其餘 8 檔明確 unavailable，沒有任何 `CALIBRATION_HISTORY` row 被選入 snapshot，且輸出不含 `_x/_y` merge suffix。
- Requested-session coverage 會逐 ETF 與 IX0001 對 TWSE calendar fail closed；membership 保存 observation/availability/ingestion/revision/manifest lineage，且實際有 3 根 bar 被正確標記為跨 split boundary。
- Notebook 位於 repository root，使用 repository `.venv`，所有 code cells 已用 synthetic artifact 逐格執行且沒有提交 outputs。

## Bounded 13 ETF 驗證證據

Artifact：`.artifacts/etf_tricks/afml/optimized-final-20240101-20260707`（worktree runtime，Git ignored）

Schema：`etf-afml-dataset-v2`

Readiness：`READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS`  
總受量測 stage time：25.617 秒。RSS 是各 stage 邊界的 process RSS 觀測值，不冒充持續取樣的真正峰值。

| Stage | 秒 | stage 邊界最高 RSS | rows |
|---|---:|---:|---:|
| source_adapter | 0.139 | 1,212,108,800 | 7,878 |
| dollar_bar_calibration | 7.654 | 1,212,108,800 | 0 |
| dollar_bars | 0.602 | 790,253,568 | 5,172 |
| ffd | 0.109 | 791,896,064 | 5,418 |
| structural | 3.991 | 791,896,064 | 11,556 |
| features | 3.488 | 800,722,944 | 5,172 |
| labels | 9.633 | 806,952,960 | 5,172 |

| ETF | bars | FFD | feature rows | resolved labels |
|---|---:|---|---:|---:|
| market_cap | 586 | reached | 586 | 506 |
| monthly_sales | 415 | reached | 415 | 335 |
| chip | 547 | reached | 547 | 467 |
| roe | 401 | reached | 401 | 321 |
| momentum | 396 | reached | 396 | 316 |
| low_volatility | 386 | reached | 386 | 306 |
| financial | 202 | reached | 202 | 122 |
| shipping | 227 | reached | 227 | 147 |
| volume_ratio | 281 | reached | 281 | 201 |
| traded_amount | 586 | reached | 586 | 506 |
| turnover | 302 | reached | 302 | 222 |
| sharpe_60d | 418 | reached | 418 | 338 |
| sortino_60d | 425 | reached | 425 | 345 |

驗證包含 5,172 根 `bar_amount == sum(member etf_amount)` 對帳、所有 canonical key 唯一、13 個 ETF IDs 完整、逐 requested session coverage、membership replay lineage、3 根 split-crossing bar 且其 ML eligibility 全為 false、artifact finalized round-trip、source capability evidence、trading-label schema 隔離，以及「revision 已驗證但 calendar manifest coverage 未宣告時仍只能是 bounded readiness」的直接回歸測試。

## Canonical artifact identities

| Table | rows | SHA-256 |
|---|---:|---|
| source_capabilities | 6 | `9d7948d5569ae8e4e5b274698b349991da7f32bea2e6fed6b8b2d0e0ddf9562d` |
| dollar_bars | 5,172 | `056284b2916ec4155994cfc1f672afd2e6e3d09f9a0360e87bf7b56897776cf3` |
| open_bar_checkpoints | 3 | `56326beed589c2ff07808484e4935292cbbebf6d60e1e2d5139fb262f00aa3bd` |
| bar_daily_membership | 7,608 | `8021af3117f579b1fadeb261e72a8ca0dc1896a51d085e5ad8a6ad4f93c574d9` |
| ffd_weights | 803 | `119a460cb0d54dd93954a094399b0869d57a058240a343d5c8a38421ccfcb60e` |
| ffd_search | 233 | `e2f3240cdc4a01196dcf9614757558954cf8ae597722945aa43c2b5c8d44bd3a` |
| ffd_series | 4,382 | `5ef70d9b21f0a92d102ef46edebd1d3429008d0847429b579251436667e86bf8` |
| structural_features | 5,778 | `4b446c674b5d19add3a31a27611afda0c53a7c0154777aa02dff75170ca9cdf4` |
| features | 5,172 | `f2cfec964b2924fafa7b96be7e4be84cd31eed3db4047404c740caf0dd165537` |
| events | 5,172 | `ebc96a811266425e07f6c8a5723baa6ac9b7baf706698500e1c68c338b3b4306` |
| labels | 5,172 | `511be99f2e6a2aa4bc062441ecdb562e243d462e27cff4e291c9aca38536379e` |
| diagnostics | 20 | `91f743c2546e97d0c10e11dd1a4d38da9af4ebb70a3592af4398f1bcaaff741f` |

Metadata SHA-256：`7128243d7b458768e073b3a22be144e4b840c5e49561e8a5031595624c24633d`

Readiness SHA-256：`d19d7053313fd62edd61dc7bbde4e765223114ac604a029abc0c38e9a6a3cb2e`

## 目前缺失／限制

1. `PIT_REVISION_UNVERIFIED`：IX0001 與上游來源可做保守 after-close 研究，但 manifest 尚未證明 historical publication vintage/revision safety，因此不能稱 production PIT verified。
2. `TRADING_CALENDAR_MANIFEST_COVERAGE_UNDECLARED`：真實 `trading_calendar` manifest 的 date ranges 為 null；目前以明確受限的完整 calendar scan、bounded 裁切及逐 session 對帳補足 correctness evidence，但仍應在 DataAnalysts 補回正式 coverage metadata。
3. `UNAVAILABLE_SOURCE_GRAIN`：VPIN、Kyle lambda、ATR、ADX、VIX 沒有合格來源。程式不建立同名 proxy 欄位。
4. Triple-barrier 是 daily-close first touch，不是 intraday high/low first touch。
5. `etf_amount` 是依前一期持倉權重加權的合成成交金額 proxy，不是真實上市 ETF 成交額或可執行容量。
6. Full-history acceptance 已按 gate **只執行一次**，並在 source adapter fail closed：2005–2026 上游結果有 432 個 ETF-day 的 `missing_traded_value_count > 0`／quality flag，範圍為 2005-12-20 至 2018-06-13，因此沒有發布 full-history AFML artifact。

Full-history 缺口經唯讀重播前一期持倉對齊後，確認為 441 個 constituent-day 缺失、343 個唯一 `date × ticker` 缺口，集中於 11 檔股票。441 筆全部是 canonical `daily_price_volume` 整列不存在，不是既有列的 `traded_value` 為 null 或負數。

| ticker | constituent-day | 唯一缺失 session | 缺失區間 | 事後可觀察證據 |
|---|---:|---:|---|---|
| `3662` | 227 | 227 | 2016-11-17–2017-10-19 | 最後 DPV 為 2016-11-16；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `5505` | 88 | 44 | 2015-05-14–2015-07-16 | 最後 DPV 為 2015-05-13；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `2325` | 36 | 9 | 2018-04-18–2018-04-30 | 最後 DPV 為 2018-04-17；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `3009` | 27 | 9 | 2010-03-08–2010-03-18 | 最後 DPV 為 2010-03-05；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `2422` | 27 | 9 | 2005-12-20–2005-12-30 | 最後 DPV 為 2005-12-19；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `2311` | 9 | 9 | 2018-04-18–2018-04-30 | 最後 DPV 為 2018-04-17；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `5854` | 9 | 9 | 2011-11-21–2011-12-01 | 最後 DPV 為 2011-11-18；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `3068` | 5 | 5 | 2018-06-07–2018-06-13 | 最後 DPV 為 2018-06-06；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `3474` | 5 | 5 | 2016-11-30–2016-12-06 | 最後 DPV 為 2016-11-29；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `3658` | 5 | 5 | 2016-11-16–2016-11-22 | 最後 DPV 為 2016-11-15；缺口終點等於 snapshot `delist_date`，之後無 DPV |
| `9157` | 3 | 3 | 2010-10-01–2010-10-05 | 2010-09-30 後缺 3 個 session，2010-10-06 恢復 DPV；snapshot `delist_date` 為 2019-11-12 |

前 10 檔共 438 個 constituent-day，在事後資料看起來是終止上市前的連續無成交區間；`9157` 的 3 列在事後資料看起來是暫時性缺口。這些只能作為根因線索，不能作為歷史當下可得的交易狀態：目前 `security_master.delist_date` 沒有相應的 historical availability/revision lineage，而利用「之後是否恢復成交」分類更直接使用了未來資料。因此在沒有 PIT-safe tradability/suspension 證據前，不能自行把缺列補成成交金額 0，也不能用 `allow_flagged` 冒充完整驗收通過。

## Gate 結論

| Gate | 結果 |
|---|---|
| 手算、數學 parity、prefix invariance | PASS |
| 2 ETF 2024–2026 | PASS with documented source limitations |
| 13 ETF 2024–2026 | PASS with documented source limitations |
| Canonical schema/key/hash round-trip | PASS |
| Notebook public API / output-free | PASS |
| 13 ETF full history | **FAIL — upstream traded-value quality, 432 ETF-days** |
| Goal complete / production ready | **NO** |

下一個正確動作不是放寬 alpha、FFD `d_max` 或 amount quality gate；應先在 DataAnalysts／上游 ETF result 層，對上述 11 檔股票的缺列建立逐 `date × ticker`、PIT-safe 的 `TRADING | HALTED | DELISTED | MISSING` 分類，至少保存 observation date、source available-at、revision/source identity 與分類依據。只有官方且當時已可得的 `HALTED/DELISTED` 才能把成交金額定義為 0；`MISSING` 必須繼續 fail closed。完成 classification、source lineage 與前綴不變性驗證後，才可重建 full-history ETF result 並授權下一次 full-history acceptance run。
