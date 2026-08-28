# ETF Tricks AFML Dataset Readiness

驗證日期：2026-08-28  
權威目標：`GOAL-ETF-AFML-DATASET-001`  
結論：**13 ETF 的 2024–2026 bounded research dataset 已可用；2005–2026 full-history 尚未通過，因此整體 Goal 不得宣稱 complete 或 production-ready。**

## 目前可用

- 公開入口：`ETFAFMLLab.from_data_analysts(...).build_all(...)`、`AFMLDataset.read/write`、`dataset.for_ml(...)`、`dataset.for_trading(...)`。
- 12 張 canonical tables 均可寫入 Parquet、原子發布、記錄 schema/key/row count/SHA-256，讀回時 fail closed 驗 hash。
- 13 個 ETF 在 2024-01-01 至 2026-07-07 的 bounded run 全部有 Dollar bars、FFD selection、features 及已完成 labels。
- `bar_amount` 是未縮放正式 feature；`amount_ratio_20` 與 amount EWMA z-score 只使用前期 finalized bars。
- Dollar bar threshold 使用 training-only IX0001 lagged market-amount baseline；threshold/version 在 bar 開始後凍結，walk-forward 不重切已開 bar。
- FFD 使用 fixed-width causal convolution；`d*` 在 training-only 搜尋，必要時可自主延伸至 `d>1`，達 governed hard limit 才回報 `stationarity_not_reached`。
- ETF raw Dollar-bar log NAV 與 IX0001 daily log close 均保存 rolling SADF、QADF、QADF dispersion、Conditional ADF、conditional dispersion 與 `SADF_CADF_z`；IX0001 只做 backward as-of join。
- Triple-barrier 使用截至 `t0` 的 EWMA bar volatility、每日收盤 first-touch path 與恰好 60 根完成 bars 的 vertical horizon；terminal tail 不縮短。
- `for_trading()` 的 13 檔 bounded snapshot 全部為 `AVAILABLE`，且 schema 不含 `label`、`t1`、`label_available_at` 或 future touch path。
- Notebook 位於 repository root，使用 repository `.venv`，所有 code cells 已用 synthetic artifact 逐格執行且沒有提交 outputs。

## Bounded 13 ETF 驗證證據

Artifact：`.artifacts/etf_tricks/afml/bounded-13etf-20240101-20260707`（Git ignored）  
Readiness：`READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS`  
總受量測 stage time：22.294 秒。RSS 是各 stage 邊界的 process RSS 觀測值，不冒充持續取樣的真正峰值。

| Stage | 秒 | stage 邊界最高 RSS | rows |
|---|---:|---:|---:|
| source_adapter | 0.323 | 1,255,264,256 | 7,878 |
| dollar_bar_calibration | 6.580 | 1,255,264,256 | 0 |
| dollar_bars | 0.427 | 972,406,784 | 5,172 |
| ffd | 0.106 | 973,971,456 | 5,418 |
| structural | 3.420 | 973,971,456 | 11,556 |
| features | 3.186 | 985,100,288 | 5,172 |
| labels | 8.252 | 993,595,392 | 5,172 |

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

驗證包含 5,172 根 `bar_amount == sum(member etf_amount)` 對帳、所有 canonical key 唯一、13 個 ETF IDs 完整、artifact round-trip、source capability evidence 與 trading-label schema 隔離。

## Canonical artifact identities

| Table | rows | SHA-256 |
|---|---:|---|
| source_capabilities | 6 | `eadc9334ea303ddcc03313174baf1cde6b301e4dac70616ed436461d8497476d` |
| dollar_bars | 5,172 | `35c0c99da1790d2565387ed6e41d0e00183a21fba4b395b9acd23938db9e518f` |
| open_bar_checkpoints | 3 | `56326beed589c2ff07808484e4935292cbbebf6d60e1e2d5139fb262f00aa3bd` |
| bar_daily_membership | 7,608 | `011182ebffe065be3f8a6806b7c8d6d212a078376eefa5653283066f5701fff1` |
| ffd_weights | 803 | `6ae4e797e150e1b27f73732f299a7377b2d3d0584bcdcccd3da8586fb9ad31e6` |
| ffd_search | 233 | `d7b58008e0da5f3c908ca7252d44144907b39002847838fa6e61ae1564ba2b85` |
| ffd_series | 4,382 | `0db3dc6d7ad397d9b575676e3dd2243b8bcfa5076a4dbd47b852f4d34181d88b` |
| structural_features | 5,778 | `4b446c674b5d19add3a31a27611afda0c53a7c0154777aa02dff75170ca9cdf4` |
| features | 5,172 | `ec3225362f3f9ce2cf1ef44afab9222d560df75f9c6276848e8cd7f23f415a3a` |
| events | 5,172 | `5ea4a9d92e6170097ba2711b97d618f9fadc79d557a632b8750bde9fc61b24a9` |
| labels | 5,172 | `9d825cbe978a4ae31b99612b6f462f616c34ae1e510941e4a67f640769952dc3` |
| diagnostics | 20 | `d6e4ec30b31b50263151ce992972f4ca72d9308a4cfd5b9474d04d6258c9d3cc` |

Metadata SHA-256：`05a8a3b53fd0aaeec06c78f91e625f89c72a5ac2b9b44fec7863ca4722f96e3b`  
Readiness SHA-256：`feb6c4b34ed4a3d4169b0283de099eb7d106a1071bcf2e03dc997a3833d81e27`

## 目前缺失／限制

1. `PIT_REVISION_UNVERIFIED`：IX0001 與上游來源可做保守 after-close 研究，但 manifest 尚未證明 historical publication vintage/revision safety，因此不能稱 production PIT verified。
2. `UNAVAILABLE_SOURCE_GRAIN`：VPIN、Kyle lambda、ATR、ADX、VIX 沒有合格來源。程式不建立同名 proxy 欄位。
3. Triple-barrier 是 daily-close first touch，不是 intraday high/low first touch。
4. `etf_amount` 是依前一期持倉權重加權的合成成交金額 proxy，不是真實上市 ETF 成交額或可執行容量。
5. Full-history acceptance 已按 gate **只執行一次**，並在 source adapter fail closed：2005–2026 上游結果有 432 個 ETF-day 的 `missing_traded_value_count > 0`／quality flag，範圍為 2005-12-20 至 2018-06-13，因此沒有發布 full-history AFML artifact。

Full-history 缺口來自 441 個 constituent-day 缺失，集中於 11 檔股票；主要為 `3662` 227 列、`5505` 88 列、`2325` 36 列、`3009` 27 列、`2422` 27 列，其餘為 `2311,5854,3658,3474,3068,9157`。在沒有 PIT-safe tradability/suspension 證據前，不能自行把缺列補成成交金額 0，也不能用 `allow_flagged` 冒充完整驗收通過。

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

下一個正確動作不是放寬 alpha、FFD `d_max` 或 amount quality gate；應先在 DataAnalysts／上游 ETF result 層，對上述 11 檔股票的缺列建立可驗證的「停牌且成交金額為 0」或「資料缺失」分類，再重建 full-history ETF result。只有 classification 與 source lineage 通過後，才能授權新的 full-history acceptance run。
