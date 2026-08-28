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
- Requested-session coverage 會逐 ETF 與 IX0001 對 TWSE calendar fail closed；membership 保存 observation/availability/ingestion/revision/manifest lineage，且實際有 3 根 bar 被正確標記為跨 split boundary。
- Notebook 位於 repository root，使用 repository `.venv`，所有 code cells 已用 synthetic artifact 逐格執行且沒有提交 outputs。

## Bounded 13 ETF 驗證證據

Artifact：`.artifacts/etf_tricks/afml/optimized-final-20240101-20260707`（worktree runtime，Git ignored）

Schema：`etf-afml-dataset-v2`

Readiness：`READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS`  
總受量測 stage time：26.299 秒。RSS 是各 stage 邊界的 process RSS 觀測值，不冒充持續取樣的真正峰值。

| Stage | 秒 | stage 邊界最高 RSS | rows |
|---|---:|---:|---:|
| source_adapter | 0.134 | 1,196,224,512 | 7,878 |
| dollar_bar_calibration | 7.390 | 1,196,224,512 | 0 |
| dollar_bars | 0.486 | 800,538,624 | 5,172 |
| ffd | 0.115 | 801,792,000 | 5,418 |
| structural | 3.840 | 801,792,000 | 11,556 |
| features | 3.689 | 810,127,360 | 5,172 |
| labels | 10.644 | 819,269,632 | 5,172 |

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

驗證包含 5,172 根 `bar_amount == sum(member etf_amount)` 對帳、所有 canonical key 唯一、13 個 ETF IDs 完整、逐 requested session coverage、membership replay lineage、3 根 split-crossing bar、artifact finalized round-trip、source capability evidence 與 trading-label schema 隔離。

## Canonical artifact identities

| Table | rows | SHA-256 |
|---|---:|---|
| source_capabilities | 6 | `8f5824dc13fa43885cc27656273ca3e5452e708aa1c8532608457e8a95557672` |
| dollar_bars | 5,172 | `056284b2916ec4155994cfc1f672afd2e6e3d09f9a0360e87bf7b56897776cf3` |
| open_bar_checkpoints | 3 | `56326beed589c2ff07808484e4935292cbbebf6d60e1e2d5139fb262f00aa3bd` |
| bar_daily_membership | 7,608 | `8021af3117f579b1fadeb261e72a8ca0dc1896a51d085e5ad8a6ad4f93c574d9` |
| ffd_weights | 803 | `119a460cb0d54dd93954a094399b0869d57a058240a343d5c8a38421ccfcb60e` |
| ffd_search | 233 | `e2f3240cdc4a01196dcf9614757558954cf8ae597722945aa43c2b5c8d44bd3a` |
| ffd_series | 4,382 | `5ef70d9b21f0a92d102ef46edebd1d3429008d0847429b579251436667e86bf8` |
| structural_features | 5,778 | `4b446c674b5d19add3a31a27611afda0c53a7c0154777aa02dff75170ca9cdf4` |
| features | 5,172 | `f2cfec964b2924fafa7b96be7e4be84cd31eed3db4047404c740caf0dd165537` |
| events | 5,172 | `5ea4a9d92e6170097ba2711b97d618f9fadc79d557a632b8750bde9fc61b24a9` |
| labels | 5,172 | `9d825cbe978a4ae31b99612b6f462f616c34ae1e510941e4a67f640769952dc3` |
| diagnostics | 20 | `b590b586984e581616db31a540c5bc690a993e9551ea161571ac713074e8bf50` |

Metadata SHA-256：`7128243d7b458768e073b3a22be144e4b840c5e49561e8a5031595624c24633d`

Readiness SHA-256：`d5080d9536520ac8f7fcccb7a96ccac67781e278c7b9538f8ec6d4801d28cb6f`

## 目前缺失／限制

1. `PIT_REVISION_UNVERIFIED`：IX0001 與上游來源可做保守 after-close 研究，但 manifest 尚未證明 historical publication vintage/revision safety，因此不能稱 production PIT verified。
2. `TRADING_CALENDAR_MANIFEST_COVERAGE_UNDECLARED`：真實 `trading_calendar` manifest 的 date ranges 為 null；目前以明確受限的完整 calendar scan、bounded 裁切及逐 session 對帳補足 correctness evidence，但仍應在 DataAnalysts 補回正式 coverage metadata。
3. `UNAVAILABLE_SOURCE_GRAIN`：VPIN、Kyle lambda、ATR、ADX、VIX 沒有合格來源。程式不建立同名 proxy 欄位。
4. Triple-barrier 是 daily-close first touch，不是 intraday high/low first touch。
5. `etf_amount` 是依前一期持倉權重加權的合成成交金額 proxy，不是真實上市 ETF 成交額或可執行容量。
6. Full-history acceptance 已按 gate **只執行一次**，並在 source adapter fail closed：2005–2026 上游結果有 432 個 ETF-day 的 `missing_traded_value_count > 0`／quality flag，範圍為 2005-12-20 至 2018-06-13，因此沒有發布 full-history AFML artifact。

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
