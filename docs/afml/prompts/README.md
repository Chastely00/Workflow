# AFML Authoritative Prompt Set

ETF Tricks 是上游資料與可交易組合建構子系統；AFML 是消費其 immutable Daily artifact 的下游研究系統。兩者不可互相改寫對方的計算、manifest 或會計規則。

## Authority order

1. Repository `AGENTS.md` 與更高優先級 runtime 指令。
2. ETF Tricks 核准設計：`docs/superpowers/specs/2026-08-26-etf-tricks-design.md`。
3. 上游 ETF Prompt：`docs/etf_tricks/prompts/00-goal-prompt.md`、`01-master-prompt.md`、`02-performance-optimization-prompt.md` 與個別 `etfs/` 契約。
4. `00-goal-prompt.md` — 本 AFML 進度、PIT 與驗收治理。
5. `01-dataset-goal-prompt.md` 與 `02-dataset-master-prompt.md` — Dollar bar、FFD、features、labels 與 AFML dataset 契約。
6. `03-tiered-ml-strategy-master-prompt.md` — Tier 1/2/3 共用交接與執行契約；Tier 1 與預設 Tier 2 以 `etf_id` 獨立建模，Tier 3 才跨 ETF 配置。
7. 當前唯一活動 child：`04-tier1-directional-label-and-model-prompt.md`、`05-tier2-meta-labeling-prompt.md`、`06-tier3-allocation-and-paper-execution-prompt.md` 或 `07-strategy-governance-dsr-acceptance-prompt.md`。
8. 只要不與以上衝突的後續 implementation notes。

## Usage

- 從 `00-goal-prompt.md` 恢復 AFML 工作；每次僅讀取並執行一份活動 child Prompt。
- `01`、`02` 是所有後續層的 immutable input contract；未通過其 gate 不得開啟模型或績效結論。
- `03` 只協調跨層資料邊界；`04`、`05`、`06`、`07` 各自只做一個可獨立驗收的責任。
- 所有編號均只在此 AFML namespace 中有效。ETF Tricks upstream Prompt 永遠使用自己的 `00`–`02` 編號。
- 任何 child prompt 都不授權另建平行 backtester、改寫上游 artifact，或進行 live orders。
