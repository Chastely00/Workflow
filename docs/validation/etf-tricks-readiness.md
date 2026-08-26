# ETF Tricks Full-History Readiness

## 結論

**READY（資料與會計可用）**。

以 manifest-backed public API `ETFTrickLab.run_all()` 對共同資料區間 `2005-01-03` 至 `2026-07-07` 完整執行後，13 個 ETF Tricks 均已產出自各自 inception 起、涵蓋每個 `TRADEDAY_TWSE` 的 Daily NAV 與 Daily ETF amount。強化後的 fail-closed validator 無 hard failure；artifact 經 SHA-256、row count、重新載入及第二次 readiness 驗證。

這個 READY 只代表資料、PIT 時點、帳本與輸出契約可用，不代表策略具有 alpha、經濟獲利或樣本外績效。

## 執行契約

- DataAnalysts root：`C:\Users\ChastLai\Documents\量化交易Workflow\DataAnalysts`
- Start / end：`2005-01-03` / `2026-07-07`
- Validation capital：NT$10,000,000；不是固定本金
- Full compute：2,260.92 秒；含 v1 write 為 2,271.38 秒
- 實測主要 Python process working set：約 7.09 GB
- Registry：13 個 ETF；只有 `market_cap` 使用市值權重，其餘等權
- Execution：原始 close、整股、實際下月交易日 N、Decimal fee/tax、自償式現金帳本
- ETF amount：當日個股成交金額乘以前一收盤實際經濟權重

## Final artifact

目錄：`C:\Users\ChastLai\Documents\量化交易Workflow\.worktrees\etf-tricks-engine\.artifacts\etf_tricks\full-history-20050103-20260707-v3`

| Table | Rows | SHA-256 |
|---|---:|---|
| `daily_etf` | 65,053 | `a0925b064af61b63e4a691e6b78d86b759cc952fced92f350ba0ecb37026dee0` |
| `daily_holdings` | 880,761 | `b0a2d943f3944a5eb45a3b006d51c34cee9ab9a0cade133016e37a5e3a546212` |
| `trades` | 883,593 | `e9939e4eccc9e89348bd0fa267a40f1973fb3d5859f818794ef18f1ac5e7b54e` |
| `monthly_targets` | 30,988 | `c7e5b6fd1e0257265c719e5f4d7c01c1b8f66159cbb6cebc905b343e9a25b528` |
| `candidate_audit` | 6,192,420 | `65e691bf2cfefa7d86b2624548974237ef4aa0036285ffc8b09592ba8daa0ee3` |
| `diagnostics` | 216 | `da0304afba68cd1dc03ab27d798d7fda7b3b03b3b0d8482679fdd0e01c4333ac` |

`monthly_targets` 有 50 欄，包含 source period / availability、觀察數、流動性、signal 與 tie-break 稽核欄位。`result_manifest.json` 同時記錄 run config、spec hash 與六個 DataAnalysts manifest hashes。

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

- NT$10,000,000：10 檔整股配置、estimated cost NT$14,230、residual cash NT$132、7 月 schedule 23 個 `TRADEDAY_TWSE`。
- NT$25,000,000：產生不同整股數、estimated cost NT$35,573、residual cash NT$1,454、同為 23 日 schedule。

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
- Full-history 執行約 37.7 分鐘、working set 約 7.09 GB；數值可重現，但互動式效能仍需 formation-level vectorization 或安全 cache 改善。
- Dollar bars、FFD、ADF/`d*`、VPIN、Kyle's lambda、ATR/ADX、ML 訓練與績效判斷全部不在本次範圍。未來預測研究不得用完整歷史選 `d*` 後再於同一歷史評估，必須使用 training-only 或 expanding-window 邊界。

## Evidence commands

```powershell
& '.venv\Scripts\python.exe' -m pip check
& '.venv\Scripts\python.exe' -m pytest -q -W error
& '.venv\Scripts\python.exe' -m compileall -q etf_tricks
```

Full-history 與 artifact round-trip 均經 `ETFTrickLab.from_data_analysts(...)`、`run_all(...)`、`lab.validate(...)`、`ETFTrickResult.write(...)`、`ETFTrickResult.read(...)` 執行；最終驗證命令的 fresh 結果須以交付時輸出為準。
