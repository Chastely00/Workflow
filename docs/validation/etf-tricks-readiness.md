# ETF Tricks Full-History Readiness

## 結論

**READY（資料與會計可用）**。

以 manifest-backed public API `ETFTrickLab.run_all()` 對共同資料區間 `2005-01-03` 至 `2026-07-07` 完整執行後，13 個 ETF Tricks 均已產出自各自 inception 起、涵蓋每個 `TRADEDAY_TWSE` 的 Daily NAV 與 Daily ETF amount。強化後的 fail-closed validator 無 hard failure；artifact 經 SHA-256、row count、重新載入及第二次 readiness 驗證。全歷史每日最低持股數為 1，沒有完全無持倉日期；每日 shares 與 cash 均已由前日狀態、精確公司行動 delta、交易、費用與稅逐日重建對帳。

這個 READY 只代表資料、PIT 時點、帳本與輸出契約可用，不代表策略具有 alpha、經濟獲利或樣本外績效。

## 執行契約

- DataAnalysts root：`C:\Users\ChastLai\Documents\量化交易Workflow\DataAnalysts`
- Start / end：`2005-01-03` / `2026-07-07`
- Validation capital：NT$10,000,000；不是固定本金
- v5 full compute：1,943.35 秒；優化後的完整 artifact round-trip validator：109.32 秒
- Registry：13 個 ETF；只有 `market_cap` 使用市值權重，其餘等權
- Execution：原始 close、整股、實際下月交易日 N、Decimal fee/tax、自償式現金帳本
- ETF amount：當日個股成交金額乘以前一收盤實際經濟權重

## Final artifact

目錄：`C:\Users\ChastLai\Documents\量化交易Workflow\.artifacts\etf_tricks\full-history-20050103-20260707-v5`

| Table | Rows | SHA-256 |
|---|---:|---|
| `daily_etf` | 65,053 | `a0925b064af61b63e4a691e6b78d86b759cc952fced92f350ba0ecb37026dee0` |
| `daily_holdings` | 880,761 | `b81c51759ec682ca824ace4ece4891cf4eab5f523edaf55aec0b134994455c75` |
| `trades` | 883,593 | `dcc98cf2567a293273e150c99c3d7b939db0bfb01184dd324954cfab2ddf5e13` |
| `monthly_targets` | 30,988 | `d7ca175aa446e2774cc0691b3fd04db5372beb28808dd86925c01e0111f00265` |
| `candidate_audit` | 6,192,420 | `1dc73c3aac6e8901168d7dc2422e55f57a3cadce21712a381c8abb980fb6b3e5` |
| `diagnostics` | 216 | `da0304afba68cd1dc03ab27d798d7fda7b3b03b3b0d8482679fdd0e01c4333ac` |

`monthly_targets` 有 51 欄，包含 source period / availability、ROE revision date、觀察數、流動性、signal 與 tie-break 稽核欄位。`result_manifest.json` 同時記錄 run config、spec hash 與六個 DataAnalysts manifest hashes。

## Per-ETF evidence

| ETF | Inception | Daily rows | Final NAV | Max stale days | Shortage months | Zero-candidate carry | Incomplete transitions | Forced delist | Total cost (NT$) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| market_cap | 2005-02-01 | 5,266 | 4,231.9579 | 0 | 0 | 0 | 105 | 0 | 2,985,002 |
| monthly_sales | 2005-03-01 | 5,253 | 688.7419 | 43 | 0 | 11 | 144 | 1 | 9,045,533 |
| chip | 2015-01-05 | 2,803 | 772.3111 | 8 | 0 | 119 | 69 | 3 | 11,385,691 |
| roe | 2007-02-01 | 4,770 | 1,242.5334 | 0 | 3 | 43 | 117 | 0 | 6,990,257 |
| momentum | 2006-02-03 | 5,022 | 400.9313 | 43 | 0 | 12 | 127 | 1 | 8,832,941 |
| low_volatility | 2005-02-01 | 5,266 | 1,940.5953 | 8 | 0 | 0 | 154 | 3 | 28,930,087 |
| financial | 2005-02-01 | 5,266 | 1,495.6309 | 0 | 0 | 0 | 165 | 0 | 6,604,259 |
| shipping | 2005-02-01 | 5,266 | 367.5283 | 0 | 18 | 0 | 140 | 0 | 4,959,847 |
| volume_ratio | 2005-06-01 | 5,189 | 380.7265 | 8 | 0 | 4 | 150 | 2 | 17,281,353 |
| traded_amount | 2005-02-01 | 5,266 | 2,622.3612 | 8 | 0 | 0 | 139 | 1 | 15,664,322 |
| turnover | 2005-02-01 | 5,266 | 65.0255 | 226 | 0 | 0 | 141 | 1 | 3,994,262 |
| sharpe_60d | 2005-05-03 | 5,210 | 761.6150 | 8 | 0 | 3 | 154 | 3 | 20,493,279 |
| sortino_60d | 2005-05-03 | 5,210 | 701.3413 | 8 | 0 | 3 | 153 | 3 | 18,310,832 |

## Allocation evidence

對 full artifact 的 `momentum`、formation `2026-06-30` 實測：

- NT$10,000,000：10 檔整股配置、按每日實際拆單計算的 cost NT$14,204、residual cash NT$158、7 月 schedule 23 個 `TRADEDAY_TWSE`，每日現金均不為負。
- NT$25,000,000：產生不同整股數；證明 NT$10,000,000 沒有 privileged code path。
- 以非目標舊部位 `1101` 做 `rebalance()`，能正確取得 formation-date 原始收盤價、先賣後買並輸出 23 日自償式 schedule。

這證明 NT$10,000,000 沒有 privileged code path，輸入資金可轉成「股票、權重、raw close、實際股數、費用、剩餘現金、逐日執行量」。

## 目前可用

- 13 個 ETF 的 Daily NAV、Daily return 與 Daily ETF amount wide views。
- `daily_holdings`、`trades`、`monthly_targets`、`candidate_audit`、`diagnostics` 六張 canonical long-form audit tables。
- `result.for_ffd(etf_id)` 的固定欄位輸出；此方法不執行 FFD。
- `ETFTrickResult.read(path)` 可用 SHA-256/row-count fail-closed 載入完整 artifact。
- `lab.allocate(...)` 與 `lab.rebalance(...)` 可接受任意資金與既有部位，輸出整股與可變 N schedule。
- Notebook 入口：`scripts/etf_tricks_quickstart.ipynb`，無 cell output，核心公式不在 Notebook。

## 目前缺失／限制

- `security_master.main_industry` 是 snapshot，不是完整 PIT 產業歷史。
- 公司行動採核准的 synthetic total-return conversion，不是 broker-exact corporate-action reconstruction。
- `daily_tradability` 保留但未使用；停牌期間依 last valid raw close 估值並持久化 stale flag。最長 staleness 為 turnover ETF 的 226 日，使用者應在後續容量/可交易性研究中特別處理。
- 216 筆 diagnostics 包含 candidate shortage、zero-candidate carry；逐月 gradual execution 也產生 backlog / incomplete transition warnings。曲線會延續，但不應解讀成每月都完整到達理論 target。
- 部分日期缺個股 traded value 時，ETF amount 以零貢獻並升起品質旗標；此限制已持久化。
- Full-history 核心計算約 32.4 分鐘，完整 validator 約 1.8 分鐘；數值可重現，但互動式效能仍需 formation-level vectorization 或安全 cache 改善。
- Dollar bars、FFD、ADF/`d*`、VPIN、Kyle's lambda、ATR/ADX、ML 訓練與績效判斷全部不在本次範圍。未來預測研究不得用完整歷史選 `d*` 後再於同一歷史評估，必須使用 training-only 或 expanding-window 邊界。

## Evidence commands

```powershell
& '.venv\Scripts\python.exe' -m pip check
& '.venv\Scripts\python.exe' -m pytest -q -W error
& '.venv\Scripts\python.exe' -m compileall -q etf_tricks
```

Full-history 與 artifact round-trip 均經 `ETFTrickLab.from_data_analysts(...)`、`run_all(...)`、`lab.validate(...)`、`ETFTrickResult.write(...)`、`ETFTrickResult.read(...)` 執行；最終驗證命令的 fresh 結果須以交付時輸出為準。
