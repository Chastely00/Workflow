# Goal Prompt — ETF Tricks AFML 分層策略

## 目標

以已 finalized 的 AFML dataset 為 immutable input，完成可重用、PIT 安全、可復現的 AFML 研究與紙上交易流程：每個 ETF Trick 各自的 Tier 1 方向機會與預設獨立 Tier 2 Meta-Labeling，然後才以 Tier 3 Equal / Inverse-vol / HRP 跨 ETF 資金配置與實際成分股交易帳，最後以 DSR 與 expanding OOF 做出 `NOT_READY`、`RESEARCH_ONLY` 或 `PAPER_TRADE_ELIGIBLE` 的證據化結論。固定末段 sealed test 只可作可選 paper-monitoring，不是准入條件。此 Goal 不授權 live trading。

## 每次恢復時的強制程序

1. 讀取 `AGENTS.md`、本檔、`README.md`，以及當前唯一活動的編號 Prompt；讀取其要求的上游 Prompt 與計畫。權威順序由 `README.md` 定義。
2. 讀取 immutable AFML dataset artifact 的 manifest、schema、coverage、code/config/input hash 與 readiness 報告。現行基線為 `.artifacts/etf_afml/full-history-20050103-20260707-v5`，其狀態必須仍為 `READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS` 且 `ML_ELIGIBLE`。確認資料可用時間、PIT join、未來 append 不改寫既有結果、無存活者/下市/停牌/公司行動污染。除非 artifact 契約失效，不得重建 01/02；資料契約不成立時，只修復或回報該契約，不得訓練、回測或宣稱績效。
3. 維持 `docs/afml/progress/decision-log.jsonl` 這份可提交的 append-only progress/decision record：時間、active stage、輸入版本、假設、設定、驗證、失敗原因、下一個最小行動。每次完成 bounded slice 後更新它與 manifest；不得以聊天摘要取代磁碟證據。
4. 一次只執行一個 stage。前一 stage 的 hand-off artifact、hash 與 gate 尚未完成，不得進入下一 stage，也不得以 tuning 繞過失敗。

## 固定階段與交接

```text
01/02 AFML dataset 已完成（Dollar bar、FFD、PIT 特徵、events、labels）；每次只做 artifact/readiness 驗證，不重建
-> 03/04 Tier 1（目前活動起點：每 ETF 獨立、成本感知、only-long {-1,+1}、purged/embargo OOF p1）
-> 05 Tier 2（預設每 ETF 獨立，只過濾該 ETF Tier 1 候選的 {0,1}、只用 OOF/walk-forward p1）
-> 06 Tier 3（同一訊號下 Equal、Inverse-vol、HRP 與 constituent paper ledger）
-> 07 registry、PSR/DSR、sealed test 與最終驗收
```

- Tier 1 的 `-1` 只代表不開多單；不建立空方部位、不配置 NTD。
- Tier 1 的唯一研究、訓練、門檻選擇、OOF 診斷與經濟 gate 單位是單一 `etf_id`。每個模型只可讀該 ETF 的事件與特徵；不得以 `etf_id` one-hot 或任一跨 ETF 資料共同 fit 一個 primary model。各 ETF 可共用已鎖定的特徵定義、成本政策與驗證演算法，但不得共用擬合參數、imputer、scaler、calibrator、threshold 或訓練列。
- pooled/panel Tier 1 僅可作為另行登錄的比較研究，不能取代、否決或宣稱等同於任一 ETF 的獨立模型；它的 pooled AUC 也不得當作每 ETF AUC 的平均或單一 ETF 的 gate。
- Tier 2 不可創造 Tier 1 未產生的 side，不可使用樣本內 Tier 1 預測，不可下單。
- Tier 2 預設以相同 `etf_id` 的 Tier 1 OOF/walk-forward candidates 獨立訓練。跨 ETF pooled meta-model 是額外、預先登錄且獨立驗證的候選，不是預設捷徑。
- Tier 3 不可重新訓練或改寫 Tier 1/2 標籤；三種配置必須使用相同候選、總資金、成本、raw-OPEN 執行規則與限制。研究標籤不是策略 PnL；策略 PnL 只能來自可對帳的 paper ledger。
- 每根完成 Dollar bar 的 `p1` 是資訊證據，不是自動 round-trip。Tier 1 策略必須以預登記的一側 CUSUM stateful aggregation 決定 `flat -> long` 與 `long -> flat`：空手只累積正向 `p1-0.5`、持有時只累積負向 `p1-0.5`，反向舊證據歸零，轉換後重設分數；持倉時不得重複開倉，只有真實狀態轉換才計入交易成本與損益。每 ETF 的 OOF `p1` 必須先轉為不可重疊 position ledger，再作策略績效結論。
- Stateful ledger 必須報告 completed round-trips、持倉結尾、成本、daily NAV proxy、turnover 與 drawdown，但它是 Tier 1 的執行一致性／baseline 診斷，不得作為 Tier 2 准入 gate。60-bar target 與高 autocorrelation 的 `p1` 可以合理形成低週轉的 regime signal；不得以任意最低交易筆數把它誤判為模型或經濟失敗，也不得以 proxy Sharpe 作准入。Tier 2 准入只能由 ETF-local、purged OOF 的方向模型 discrimination、calibration、已解析 event-level 經濟結果與資料契約決定。任何 state-policy 的新替代仍須預登錄並計入 DSR，但不可以策略交易筆數阻擋 Meta-Labeling。
- 交易執行使用下一個合法交易日之 constituent 原始 OPEN；不得以調整價或 FFD 價格成交。整數股數、手續費、交易稅、最低 1 元手續費、現金、停牌、下市與已驗證公司行動由共用執行引擎處理。

## PIT、驗證與測試順序

- 特徵、scaler、imputer、threshold、calibration、volatility、covariance、模型與標籤一律只可讀決策時已可得資料；跨 ETF 依 availability time 向後 join，禁止以未來 bar id 對齊。
- 模型驗證使用事件 `t0/t1`、purging、embargo、concurrency/uniqueness；禁止 IID random CV。每 ETF 獨立產生 chronological folds、fold-local preprocessing/calibration/threshold 與 OOF 指標；Tier 2 只接收同 ETF 的 Tier 1 OOF 或嚴格 walk-forward 預測。
- 2024–2026 只可作手算後的 smoke、schema 與效能檢查，不能作為 60 根 Dollar-bar 標籤、ETF 選擇或策略失敗的證據。Tier 1 的最小正式有界研究／OOF 區間固定為 2020–2026；必須保留每個 ETF 的成熟／未成熟 60-bar event 數。
- 要對某 ETF 或特徵提出「長期未見可重現訊號」的否定結論，另須以該 ETF 的 2005–2024 chronological expanding、event-end-purged OOF 長歷史診斷支持。它必須使用時間先後的 train/validation partitions，不能把完整歷史混成 IID CV，也不能把診斷結果拿來反覆調參。2025–2026 不是這個 OOF 的 validation 區間；若完整 target artifact 已可直接讀取該期間的 outcomes，該期間也不得事後稱為 sealed test。只有在研究開始前已有可證明的 outcome-access boundary、且後續新到資料落在 boundary 之後時，才可建立新的 sealed scope。資料工程的一般單元／整合／效能測試仍不得直接把 13 ETF 全歷史當捷徑。
- 現行 ETF Trick 沒有可交易、同步的 High/Low/Open，Tier 1 horizontal barrier 只能以每日 NAV 收盤路徑確認；`close_path_*` 欄位不得作為 OHLC、特徵或成交價。故 daily-close path 不存在同日雙觸及；只有未來另有 PIT-safe 逐筆/日內序列且新 trial 已登錄時，才可建立 double-touch 規則。
- 未通過 gate 時，停止該分支的績效結論並明確寫出污染/失敗路徑、已驗證事實、未驗證假設與下一個合理修復方向。模型可自主搜尋合理且預先記錄的設定範圍，但任何看過績效後的候選都必須登錄 trial registry。
- OOF 前必須預先登記有限的 `IF OOF state -> allowed action`。樣本不足時只允許向前延長既有歷史範圍；仍不足即棄置該設計，且因未產生可比較績效不增加 DSR trial count。AUC 無辨識力、無淨邊際或不穩定時，只可啟用已登記替代模型、特徵、barrier/horizon 或診斷；每個看過 OOF 後觸發的績效替代都增加 effective DSR trial count。禁止等待未來資料、放寬成本／標籤／ETF 定義，或事後新增未登記的搜尋。
- 對所有成熟事件與 Tier 1 candidates 分別保存 barrier diagnostics：first-touch 類型、time-to-touch bars/days、gross/net return、成本、MFE、MAE，及提前停利後至原 60-bar horizon 的延續。預登記診斷停損過多／過近、停利過近、barrier 過寬與 volatility mismatch；診斷本身不計 trial，但任何依結果採用的 barrier 變體必須預登記並計入 DSR。

## 試驗治理與完成定義

- 在結果影響選擇前，登錄 `etf_id`、模型、特徵、障礙、threshold、calibration、Tier 2、配置與 HRP 變體；保留失敗與淘汰試驗。跨 ETF 選擇或比較的 13 個獨立模型，以及任何 pooled benchmark，都必須保守納入有效獨立試驗數。
- Sealed test 的 scope 必須明示 `etf_id` 與 lineage；每個 scope 僅允許一條事前鎖定 lineage 進入。若模型、ETF 選擇或 threshold 因該 test 結果改變，該 scope 不再 sealed，所有受影響候選納入試驗數。不能證明 sealed outcome 未被研究程式或決策讀取時，必須標示 `NOT_SEALED`，不可借用 sealed 名稱。
- 僅在完整 PIT/OOF/執行對帳、三種配置比較、成本與容量診斷，以及 `DSR >= 0.95` 均有可重現證據時，才可由 07 評為 `PAPER_TRADE_ELIGIBLE`。通過不代表未來獲利，絕不代表可 live。

## 結束與續跑

每個階段結束時交付 hash-linked artifacts、manifest、測試輸出、限制與下一 stage hand-off。最終報告必須同時回答：失敗時是哪一層失敗、可驗證的修復方向；成功時，哪些 regime/cost/capacity/calibration/HRP 或真實執行偏差研究最值得以預先登錄假設繼續測試。
