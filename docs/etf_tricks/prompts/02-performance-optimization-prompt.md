# Performance Optimization Prompt — `ETFTrickLab.run_all()`

Goal reference: `GOAL-ETF-TRICKS-001`

## Role and authority

你是 Taiwan Equity ETF Tricks 子系統的效能研究與實作負責人。工作目錄為 `C:\Users\ChastLai\Documents\量化交易Workflow`。

開始前必須完整閱讀：

1. repository `AGENTS.md` 與更高層 runtime instructions；
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`；
3. `docs/etf_tricks/prompts/00-goal-prompt.md`；
4. `docs/etf_tricks/prompts/01-master-prompt.md`；
5. 本 Prompt；
6. 現有 `etf_tricks/` code、`tests/etf_tricks/` 與 readiness evidence。

本 Prompt 只授權做「結果等價」的效能研究與優化，不得覆寫量化、PIT、會計、費用、整股或 schema 契約。若速度與正確性衝突，正確性優先；若無法證明等價，必須保留原實作並回報阻塞。

## Objective

針對公開 API：

```python
lab = ETFTrickLab.from_data_analysts(DATA_ANALYSTS_ROOT)
result = lab.run_all(
    start_date="2005-01-01",
    end_date="2026-07-07",
    initial_capital=INITIAL_CAPITAL,
)
```

以實際 DataAnalysts canonical artifacts 完成 representative-window profiling，找出 `from_data_analysts()` 與 `run_all()` 的耗時、記憶體、I/O、演算法複雜度與重複計算來源，提出並依效益順序實作可驗證的高速解法。日常研究、效能測試與迭代固定使用 `2024-01-01` 至 `2026-07-07`；不得為測試一個局部改動反覆執行完整歷史。目標不是只讓合成 fixture 變快，而是以 representative real-data window 證明解法，最終縮短 13 個 ETF、完整歷史、Daily NAV 與完整 audit ledger 的真實 wall-clock time。

## Non-negotiable semantic invariants

任何優化前後必須維持：

- 完全相同的 13 個 ETF ID、formation/execution calendar、PIT availability cutoff、候選資格、排序 tie-break、月度 targets 與 target weights；
- 完全相同的實際整數股數、逐日交易、現金、commission、tax、backlog、公司行動、stale price、delisting 與 no-empty-holdings 行為；
- 完全相同的 Daily NAV、daily return、ETF amount 定義與前一日經濟權重時間對齊；
- 完全相同的 canonical long-form output schema、keys、排序、diagnostics、metadata 與 fail-closed validation；
- 不增加前視偏誤、存活者偏誤、無聲 fallback、無聲刪列、近似費用、fractional shares 或浮點取代 `Decimal` 的會計偏差；
- 不減少 candidate audit、holdings、trades、targets 或 readiness evidence 來換速度。

允許 feature 中間陣列使用 NumPy/pandas 浮點運算，但必須證明所有有效值、NaN mask、排名、選股與最終 ledger 等價。涉及金額、股數、費用與現金的路徑不得因向量化改變 rounding order 或精度。

## Required investigation sequence

### 1. Reproduce and establish baseline

- 分開計時 `ETFTrickLab.from_data_analysts()` 與 `lab.run_all()`，不得只量 Notebook cell 總時間。
- 記錄 Python、pandas、NumPy、pyarrow 版本、CPU、可用記憶體、artifact manifest identity/hash、日期範圍與 initial capital。
- 開發與 profiling baseline 固定使用 `2024-01-01` 至 `2026-07-07`。先執行一次環境/檔案系統 warm-up；可在合理時間重複的 stage 或 slice 使用至少 3 次並報 median、min、max。
- 在所有 planned optimizations 完成、representative-window 等價性與速度門檻通過前，禁止執行 `2005-01-01` 起的完整歷史。完整歷史只在最終驗收執行一次；若失敗，先回到能重現該失敗的最短窗口修正，不得以 full-history 反覆除錯。
- 同時記錄 peak RSS 或可重現的 peak-memory 指標；不得只以 CPU time 代替 wall time。
- baseline 輸出保存到 ignored `.artifacts/etf_tricks/performance/`，不可提交市場資料或 Notebook outputs。

### 2. Trace the complete call path

逐層建立 stage timing 與 call graph，至少分開：

1. manifest/schema/coverage validation；
2. parquet discovery、read、filter、concat、sort、dtype normalization；
3. trading calendar 與 formation dates；
4. PIT feature preparation；
5. 每個 formation date 的 base rows、liquidity與 13 ETF signals；
6. universe eligibility、ranking、targets 與 candidate audit；
7. 13 個 ETF 的逐日 execution/accounting；
8. result concat/pivot/daily-return construction；
9. readiness validation 與 cross-ledger reconciliation。

每個 stage 回報 wall time、占總時間比例、call count、主要輸入/輸出 rows、columns、bytes，以及是否隨 `dates × tickers × ETFs × formations` 成長。不得只列 cProfile function name；必須解釋其資料流與複雜度。

### 3. Audit I/O and materialization

對每一 canonical artifact 建立 read ledger：實際 path/partition、read 次數、columns、predicate、rows、bytes、後續 consumers。檢查：

- 同一 parquet/manifest 是否被重複開啟、讀取、驗證或排序；
- 是否先讀全欄位再丟棄大部分 columns/rows；
- 13 ETF 是否各自重建相同 base panel、rolling statistics、date/ticker lookup 或 calendar slice；
- pandas object/Decimal/date dtype 是否造成不必要複製；
- `concat`, `merge`, `groupby`, `pivot`, `sort_values`, `reset_index` 是否在內層迴圈重複 materialize；
- result/validation 是否再次建立與 engine 已有內容相同的大型索引或 dict。

所有快取必須有明確 scope 與 key，至少包含 artifact identity、requested date range、feature specification/version；禁止跨 run 使用無版本、不可失效的隱性 global cache。

### 4. Mathematical and algorithmic audit

逐一檢查每個公式與 state transition，對現行複雜度與更快的等價算法提出證明：

- 20/60/252 日 rolling sum、mean、sample variance、downside LPM2、momentum lag 與 volume-ratio non-overlap window；
- `qfii_examt + fund_examt + dlrp_examt` 的 20 日完整窗；
- IX0001 aligned 20 日 denominator 與 liquidity threshold fallback；
- 月營收與 ROE 的 latest-available PIT as-of join；
- 每月 stable top-k、equal weight 與 market-cap weight；
- variable-`N` cumulative share schedule；
- cash-constrained proportional buy、floor、residual allocation、backlog 與 sell-before-buy；
- previous-close weights 下的 ETF amount；
- NAV/holdings/trades/cash reconciliation。

候選方法至少比較：一次性 wide/2D arrays、`groupby().rolling()`、prefix sums/sums-of-squares、`searchsorted`/`merge_asof`、`argpartition` 加 deterministic final sort、factorization/categorical integer codes、預建 `(date,ticker)` dense/sparse lookup，以及 13 ETF 共用 panel。不得為了使用「張量」而張量化；只有當資料形狀、記憶體 locality 與等價性證據支持時才採用。

逐日 execution 是 path-dependent state machine。除非能證明 fees、rounding、cash constraint、backlog、corporate action 與 missing-price semantics 完全等價，不得把時間軸錯誤地平行化。可優先評估「特徵與選股跨 ticker/ETF 向量化、execution 保留時間序列 loop，但將同日 lookup、估值與可批次操作向量化」。

### 5. Classify every optimization candidate

每個候選必須列出：

- bottleneck evidence 與 baseline 占比；
- 現行與新算法的時間/空間複雜度；
- 預估速度與記憶體影響；
- semantic/PIT/accounting risk；
- 需要修改的 exact files/functions；
- 測試方法、equivalence oracle 與 rollback 條件；
- 分類為 `implement now`、`benchmark first`、`reject` 或 `defer`。

優先順序使用「實測可節省 wall time × 信心 ÷ correctness risk」，不是依程式碼改動容易度排序。對占比很低的 micro-optimization 明確拒絕。

## Optimization rules

- 先 profile、再寫 failing performance/equivalence test、最後做單一優化；禁止一次混入多個改法。
- 優先消除重複 I/O、重複 rolling、重複排序/merge/materialization，再考慮 multiprocessing、JIT、GPU 或新增依賴。
- 只讀必要 columns 與 date partitions，predicate pushdown 必須保留 coverage 驗證。
- 同一個 run 內共用 immutable prepared panels、rolling feature matrices、date/ticker integer maps 與 formation snapshots。
- pandas/NumPy 優先；新增 Numba/Polars/Dask/GPU 等依賴前，必須用 isolated benchmark 證明淨效益並取得使用者同意。
- parallelism 不得造成非 deterministic 排序、浮點 reduction 順序差異、過度記憶體複製或同時重讀相同 parquet。
- 不可透過關閉 validation、減少輸出、縮短日期、抽樣股票或只跑部分 ETF 宣稱 full-history 加速。

## Equivalence and performance gates

建立固定 baseline oracle，將所有 canonical tables 依 approved unique keys 排序後比較：

- schema、dtype、row count、key uniqueness、NaN/flag mask、ETF/date coverage 必須相同；
- candidates、targets、ticker、weights、shares、orders、costs、cash、diagnostics 必須逐值相同；
- 會計與交易金額採 exact comparison；float feature 可先以 zero-tolerance 比較，若底層向量算法僅有不可避免的 ULP 差異，必須證明不改變 rank/selection/ledger，明列最大 absolute/relative error，並由既有規格決定是否接受；
- 13 條 Daily NAV 與 amount 曲線必須通過現有 full-history readiness 與 cross-ledger reconciliation；
- focused tests、全部 `tests/etf_tricks/`、environment verification 與 Notebook public-API smoke 必須通過。

每一 optimization slice 以 `2024-01-01` 至 `2026-07-07` 報告 before/after 相同資料、相同 cold/warm 條件下的 wall time、speedup、peak memory 與 profile flame/cumulative evidence。若 improvement 小於量測噪音、增加記憶體不可接受、或無法證明結果等價，rollback。

最终接受條件：

1. 所有局部測試與效能迭代均使用 `2024-01-01` 至 `2026-07-07`，不得反覆消耗完整歷史；
2. 優化完成後，僅執行一次 full-history `2005-01-01` 至 `2026-07-07` 的 `run_all()` 最終驗收並成功；
3. 13 ETF 全部通過 readiness，無結果或時間對齊回歸；
4. 提供可重現 representative-window baseline、final benchmark 與一次性 full-history 結果；
5. 明確列出端到端 speedup、各 stage speedup、peak memory 與剩餘瓶頸；
6. 至少消除所有已證明的高占比重複 I/O/計算；
7. 未達成預期高速時，不以「已向量化」作為完成，必須回報剩餘主因與下一個最高價值切片。

## Required deliverables

先產出不修改 production code 的研究報告，內容必須包含：

1. 完整 call-path 與 stage timing table；
2. function-level profiler top list；
3. artifact I/O/read ledger；
4. 13 ETF 共用與重複計算矩陣；
5. 各公式的現行算法、等價快速算法與風險；
6. `implement now / benchmark first / reject / defer` 決策表；
7. 依 TDD 拆分、可逐一 rollback 的 implementation roadmap；
8. baseline、equivalence oracle、performance budget 與完成門檻；
9. `目前可用`、`目前缺失／限制`、下一個最小高價值切片。

研究報告獲使用者核准後，才可修改 production code。每次只實作一個已量測 bottleneck，保存 before/after evidence；不得把研究期的臨時 profiler、raw artifacts 或市場資料提交到 Git。
