# Goal Prompt - GOAL-ETF-AFML-DATASET-001

Status: Approved authority as of 2026-08-27.

你正在持續推進 `GOAL-ETF-AFML-DATASET-001`：在 `C:\Users\ChastLai\Documents\量化交易Workflow`，以下游、獨立且可由 Jupyter Notebook 輕鬆導入的研究層，將已驗證的 13 個 ETF Tricks Daily NAV 與 `etf_amount` 轉換成 point-in-time safe、可重現、可稽核的 AFML 資料集。目標產物包含日資料衍生 Dollar bars、每個 ETF 專屬的 fixed-width fractional differentiation、完整 `d` 搜尋與 ADF 證據、SADF/QADF/CADF 結構轉換序列、預先定義的一級 ML features，以及可配置的 triple-barrier directional labels。這個目標只建立資料、特徵與標籤，不訓練模型、不做績效結論、不送出交易。

開始或恢復工作前，完整閱讀並依序服從：

1. repository `AGENTS.md` 與更高層 runtime instructions；
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`；
3. `docs/etf_tricks/prompts/01-master-prompt.md`；
4. `docs/afml/prompts/02-dataset-master-prompt.md`；
5. `docs/Marcos Lopez de Prado - Advances in Financial Machine Learning-Wiley (2018).pdf` 中第 2、3、5、17、19 章；
6. 經使用者核准後才建立的 implementation plan。

上游 ETF Tricks 契約不可被下游修改。標準資料流固定為：驗證 Daily NAV/amount -> 形成 Dollar bars -> 對 Dollar-bar log NAV 做 FFD -> 產生 PIT-safe stationarity/regime statistics -> 建立 features -> 建立 labels。不得把 daily FFD 聚合成 Dollar bar，也不得用未來資料決定當下門檻、`d*`、縮放或特徵。

選擇 `d*` 的 gate 是一般 ADF：在 governed grid 中找訓練樣本內最小且通過 `p < alpha` 與臨界值條件的 `d`。搜尋從 `[0,1]` 開始；若未通過，Model 不需等待使用者核准，須先排除資料、有效樣本與檢定規格問題，再自動搜尋 `(1,2]`，必要時依 training-only 診斷、正式文獻與明確停止條件建立下一個有限區間。不得為求通過而放寬 alpha、事後挑檢定或直接指定 `d=1`。SADF、QADF、CADF 用於描述爆炸性與結構轉換並保留為 features，不能取代 `d*` gate。

固定 `d`、tolerance 與 width 後，FFD 是只讀取當期及過去值的單邊轉換，可對完整可用時段逐點計算；洩漏禁令針對的是用 validation/test 或完整歷史反向選擇 `d*`、window 或其他 preprocessing parameters。完整歷史選出的 `d*` 只可標示 `DESCRIPTIVE_ONLY`；供 ML 使用時預設採 `train` mode，只在 training-only calibration 期間選擇，並把凍結時間與版本保存在 metadata。固定寬度權重、截斷長度、ADF 設定、樣本數、記憶保留率及所有 autonomous escalation 證據必須完整保存。

日資料只能形成 close-path Dollar bars：每天最多關閉一根，不拆成虛構的多根同價 bar；每根保存日成員、累積 `bar_amount`、門檻、overshoot、持續日數與資料品質。預設門檻為 bar 開始前 60 個交易日 IX0001 `amt` 中位數乘上 training-only 校準、13 ETF 共用的市場比例 `q*`，並在 bar 開始時凍結。禁止以各 ETF 自身均量強迫固定形成頻率；低量 ETF 必須自然需要較長時間累積足夠資訊。`q*` 不得直接套用選股的 0.05%/0.1%/0.2% 門檻，而是在不讀 labels/績效下選擇仍使全部 ETF 滿足下游觀察數的最大共同候選值。

每根 bar 只有在最後一個 constituent day 的 NAV、`etf_amount`、IX0001 與品質資料全部可得後才 final；其 `available_at` 不得早於最晚來源時間，所有 bar-derived features（包含 `bar_amount`）只能供該時間之後的決策使用，最早交易日由 `TRADEDAY_TWSE` 與資料到達 cutoff 決定，禁止假設可在觸發 bar 的同一收盤成交。跨 ETF 或大盤特徵只能 backward as-of 取得最後已完成 bar，並保存 staleness；不得以 bar_id 對齊、forward join 或使用未完成 bar。append future rows 不得改變既有 finalized bars，open bar 只能以 provisional state 延續。

Triple barrier 預設使用截至事件時點的 60-bar EWMA log-return volatility，profit-taking 與 stop-loss 倍數皆為 2，vertical barrier 為 60 根 Dollar bars；所有參數可配置。標準 directional label 為 `{-1,+1}`，若垂直屏障先到則取期末報酬符號，零報酬列明確捨棄；事件起訖、第一個觸碰日期、屏障值與重疊區間必須保留，供後續 purging、embargo 與 uniqueness 使用。

實作第一階段必須先產出 VPIN、Kyle、ATR、ADX、VIX 的 source-capability matrix，驗證來源粒度、schema、PIT availability、coverage、manifest/hash 與缺口。不得把能力不足的估計冒充正式特徵：沒有 aggressor side/等量 volume buckets 時 VPIN 不可用；沒有 signed order flow 時 Kyle lambda 不可用；沒有同步且真實的 ETF OHLC 時 ATR/ADX 不可用。VIX 只有在 manifest-declared PIT artifact 存在時可啟用；proxy 必須另名、分欄、分狀態。

測試必須依序使用：手算 fixture -> 1 至 2 個 ETF 的 2024-01-01 至 2026-07-07 -> 13 ETF 同一短區間；只有受觀察數限制的測試才可延伸到 2020-01-01 至 2026-07-07。所有 bounded gates 通過後，才可用明確 `full_history_acceptance` 執行一次 13 ETF 完整歷史驗收；一般測試不得直接讀取 13 ETF 全歷史。逐日 replay 與 future append 不得改變 finalized bars、thresholds、`train` mode 已選參數、features 或已結束 labels。禁止安裝 `mlfinlab`、`mlfinpy` 或未核准替代品；`fracdiff-modern` 只能作為經 parity test 驗證的計算工具。

只有 fresh artifacts 同時證明 13 個 ETF 都有可對帳 Dollar bars、有效 FFD、可重現 `d*` 搜尋證據、PIT-safe structural features、feature/label schema、完整 lineage/hash、Notebook 快速匯入介面、無前視偏誤測試及明確的 `目前可用`／`目前缺失／限制` 時，才可將本目標標記完成。任何一檔資料不足、stationarity gate 未通過、來源品質不合格或時間對齊不明，都必須 fail closed，保留證據並繼續改善。
