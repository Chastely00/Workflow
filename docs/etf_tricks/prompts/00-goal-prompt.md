# Goal Prompt — GOAL-ETF-TRICKS-001

你正在持續推進 `GOAL-ETF-TRICKS-001`：在 `C:\Users\ChastLai\Documents\量化交易Workflow` 建立一套可由 Jupyter Notebook 輕鬆導入、manifest-first、PIT-safe、可重現且 fail-closed 的台股 ETF Trick 系統，使用同一個共用引擎產出市值、月營收、籌碼、ROE、動能、低波、金融、航運、量能、金額、週轉率、60 日 Sharpe、60 日 Sortino 共 13 個 ETF 的完整 Daily NAV 與 Daily ETF 成交金額曲線，並能把任意分配資金與現有持倉拆解成股票、整數股數、成本、剩餘現金及逐日執行排程。NT$10,000,000 只可作為預設驗證案例，不得寫死。

開始或恢復工作前，完整閱讀並視為權威資料：

1. `AGENTS.md`
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`
3. `docs/etf_tricks/prompts/01-master-prompt.md`
4. 當前 ETF 對應的 `docs/etf_tricks/prompts/etfs/*.md`
5. `docs/superpowers/plans/2026-08-26-etf-tricks-implementation.md`

若內容衝突，依上述順序處理並明確回報，不得自行折衷或靜默 fallback。沿用既有實際 code path、artifact、測試與 readiness report；不得每次重建另一套 runner。先檢查目前狀態與未完成證據，選擇最小且能獨立驗證的阻塞切片，依 TDD 完成、執行 fresh tests、核對 PIT／現金／股數／NAV／輸出 schema，再更新 `目前可用`、`目前缺失／限制` 與下一個阻塞點。失敗時先定位根因，不以放寬驗證、刪列、補 0、改路徑或只留下 warning 取得曲線。

除非權威規格正式修改，禁止把 Dollar bar、FFD、ADF/CADF/SADF、ML 特徵、訓練或券商送單納入本目標；`for_ffd()` 只輸出乾淨的 `date, etf_id, nav, daily_return, etf_amount`。不得因部分 ETF 成功、單元測試通過或程式已存在而宣稱完成。

只有在 fresh full-history 驗證同時證明以下事項時，才可將目標標記完成：13 個唯一 ETF 全部成立；各自成立日起涵蓋每個 `TRADEDAY_TWSE` 日期且 NAV 有限；Daily amount 可用；持股、現金、交易、成本、目標與 NAV 完整對帳；PIT 測試無洩漏；Notebook 可快速取得全部研究資料；任意資金的 `allocate()` 與既有持倉的 `rebalance()` 可輸出可稽核整股計畫；readiness headline 為 `READY`，且所有限制已如實揭露。未達成時保持目標進行中，保留證據並在後續工作中繼續改善，直到上述條件全部成立。
