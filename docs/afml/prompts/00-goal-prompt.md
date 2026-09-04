# Goal Prompt — ETF Tricks AFML 分層策略

## 目標

以已 finalized 的 AFML dataset 為 immutable input，完成可重用、PIT 安全、可復現的 AFML 研究與紙上交易流程：Tier 1 方向機會、Tier 2 Meta-Labeling、Tier 3 Equal / Inverse-vol / HRP 資金配置與實際成分股交易帳，最後以 DSR 與 sealed test 做出 `NOT_READY`、`RESEARCH_ONLY` 或 `PAPER_TRADE_ELIGIBLE` 的證據化結論。此 Goal 不授權 live trading。

## 每次恢復時的強制程序

1. 讀取 `AGENTS.md`、本檔、`README.md`，以及當前唯一活動的編號 Prompt；讀取其要求的上游 Prompt 與計畫。權威順序由 `README.md` 定義。
2. 讀取 immutable AFML dataset artifact 的 manifest、schema、coverage、code/config/input hash 與 readiness 報告。現行基線為 `.artifacts/etf_afml/full-history-20050103-20260707-v5`，其狀態必須仍為 `READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS` 且 `ML_ELIGIBLE`。確認資料可用時間、PIT join、未來 append 不改寫既有結果、無存活者/下市/停牌/公司行動污染。除非 artifact 契約失效，不得重建 01/02；資料契約不成立時，只修復或回報該契約，不得訓練、回測或宣稱績效。
3. 維持 `docs/afml/progress/decision-log.jsonl` 這份可提交的 append-only progress/decision record：時間、active stage、輸入版本、假設、設定、驗證、失敗原因、下一個最小行動。每次完成 bounded slice 後更新它與 manifest；不得以聊天摘要取代磁碟證據。
4. 一次只執行一個 stage。前一 stage 的 hand-off artifact、hash 與 gate 尚未完成，不得進入下一 stage，也不得以 tuning 繞過失敗。

## 固定階段與交接

```text
01/02 AFML dataset 已完成（Dollar bar、FFD、PIT 特徵、events、labels）；每次只做 artifact/readiness 驗證，不重建
-> 03/04 Tier 1（目前活動起點：成本感知、only-long {-1,+1}、purged/embargo OOF p1）
-> 05 Tier 2（只過濾 Tier 1 候選的 {0,1}、只用 OOF/walk-forward p1）
-> 06 Tier 3（同一訊號下 Equal、Inverse-vol、HRP 與 constituent paper ledger）
-> 07 registry、PSR/DSR、sealed test 與最終驗收
```

- Tier 1 的 `-1` 只代表不開多單；不建立空方部位、不配置 NTD。
- Tier 2 不可創造 Tier 1 未產生的 side，不可使用樣本內 Tier 1 預測，不可下單。
- Tier 3 不可重新訓練或改寫 Tier 1/2 標籤；三種配置必須使用相同候選、總資金、成本、raw-OPEN 執行規則與限制。研究標籤不是策略 PnL；策略 PnL 只能來自可對帳的 paper ledger。
- 交易執行使用下一個合法交易日之 constituent 原始 OPEN；不得以調整價或 FFD 價格成交。整數股數、手續費、交易稅、最低 1 元手續費、現金、停牌、下市與已驗證公司行動由共用執行引擎處理。

## PIT、驗證與測試順序

- 特徵、scaler、imputer、threshold、calibration、volatility、covariance、模型與標籤一律只可讀決策時已可得資料；跨 ETF 依 availability time 向後 join，禁止以未來 bar id 對齊。
- 模型驗證使用事件 `t0/t1`、purging、embargo、concurrency/uniqueness；禁止 IID random CV。Tier 2 只接收 Tier 1 OOF 或嚴格 walk-forward 預測。
- 2024–2026 只可作手算後的 smoke、schema 與效能檢查，不能作為 60 根 Dollar-bar 標籤、ETF 選擇或策略失敗的證據。Tier 1 的最小正式有界研究／OOF 區間固定為 2020–2026；必須保留每個 ETF 的成熟／未成熟 60-bar event 數。
- 要對某 ETF 或特徵提出「長期未見可重現訊號」的否定結論，另須以 2005–2026 的 expanding、purged、embargoed OOF 長歷史診斷支持。它必須使用時間先後的 train/validation partitions，不能把完整歷史混成 IID CV，也不能把診斷結果拿來反覆調參；最新未碰觸的期間仍保留給 sealed test。資料工程的一般單元／整合／效能測試仍不得直接把 13 ETF 全歷史當捷徑。
- 現行 ETF Trick 沒有可交易、同步的 High/Low/Open，Tier 1 horizontal barrier 只能以每日 NAV 收盤路徑確認；`close_path_*` 欄位不得作為 OHLC、特徵或成交價。故 daily-close path 不存在同日雙觸及；只有未來另有 PIT-safe 逐筆/日內序列且新 trial 已登錄時，才可建立 double-touch 規則。
- 未通過 gate 時，停止該分支的績效結論並明確寫出污染/失敗路徑、已驗證事實、未驗證假設與下一個合理修復方向。模型可自主搜尋合理且預先記錄的設定範圍，但任何看過績效後的候選都必須登錄 trial registry。

## 試驗治理與完成定義

- 在結果影響選擇前，登錄模型、特徵、障礙、threshold、calibration、Tier 2、配置與 HRP 變體；保留失敗與淘汰試驗。以保守的有效獨立試驗數計算 PSR/DSR。
- Sealed test 只允許一條已鎖定 lineage 進入；若 test 改變選擇，test 即不再 sealed，所有受影響候選納入試驗數。
- 僅在完整 PIT/OOF/執行對帳、三種配置比較、成本與容量診斷、sealed test，以及 `DSR >= 0.95` 均有可重現證據時，才可由 07 評為 `PAPER_TRADE_ELIGIBLE`。通過不代表未來獲利，絕不代表可 live。

## 結束與續跑

每個階段結束時交付 hash-linked artifacts、manifest、測試輸出、限制與下一 stage hand-off。最終報告必須同時回答：失敗時是哪一層失敗、可驗證的修復方向；成功時，哪些 regime/cost/capacity/calibration/HRP 或真實執行偏差研究最值得以預先登錄假設繼續測試。
