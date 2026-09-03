# Data Analysts: MongoDB -> PIT Parquet 核心流程

## 目的

Data Analysts 的責任不是找 alpha，也不是用回測結果修策略。它只負責把上游 MongoDB 來源治理成任何下游系統都可以直接使用的 DataAnalysts-owned artifacts：

```text
MongoDB
-> canonical raw parquet
-> PIT semantic event / adjusted price parquet
-> security panel
-> universe membership
```

核心原則：

- MongoDB 只允許在 canonical raw extraction 邊界被讀取。
- canonical raw parquet 和 manifest 才是 DataAnalysts runtime 的資料真相。
- 下游 Feature Analysts、Strategists、Backtesters 不可直接查 MongoDB。
- 缺資料、缺 partition、schema 不支援、PIT 狀態不清楚時 fail closed。
- 每個資料集保留 source family 邏輯邊界，不合併成一張全域寬表。
- 實體讀寫粒度依資料量、更新頻率、下游讀取模式決定；小表不應為了形式上的模組化被切成大量 tiny queries 或 tiny parquet files。
- raw market data 和 generated canonical data 不進 git；交付 manifest、diagnostics、程式與輕量 evidence。

## 硬邊界：獨立 DataAnalysts 套件

本對話所有產物只允許放在：

```text
C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
```

DataAnalysts 的目標不是驅動 ALF 主流程內既有程式，而是把 Data Analysts 的完整資料流程拆出來，製作成一套可攜資料產品。未來把這個資料夾搬到任何系統，只要環境具備 MongoDB 讀取權限與必要 Python 套件，就應能獨立產出：

```text
MongoDB
-> PIT canonical parquet
-> adjusted price parquet
-> event parquet
-> security panel
-> universe membership
```

禁止事項：

- 不寫入 `C:\Users\ChastLai\Documents\ALF` 主流程中的 `alf/`、`config/`、`data_canonical/`、`feature_store/`、`jobs/`、`output/`、`reports/`。
- 不依賴 `alf.canonical.*`、`alf.universe.*`、`alf.features.*` 作為 runtime adapter。
- 不用 ALF CLI 當 DataAnalysts 的入口。
- 不把 DataAnalysts 輸出寫到 DataAnalysts 資料夾外。

允許事項：

- 可以把現有 ALF 文件或程式邏輯當作設計參考。
- 若未來複用任何 ALF 邏輯，必須複製並正規化成 `DataAnalysts/src/data_analysts/` 內的獨立模組。
- 所有輸出路徑都必須是 DataAnalysts root 的相對路徑。

## DataAnalysts 目標資料夾結構

DataAnalysts 最終應自含以下內容：

```text
DataAnalysts/
  Data_Analysts_MongoDB_to_PIT_Parquet.md
  README.md
  pyproject.toml
  configs/
    mongodb_sources.json
    source_family_profiles.json
    universe_specs.json
  contracts/
    data_analysts_pipeline.schema.json
    canonical_manifest.schema.json
    universe_membership.schema.json
  src/
    data_analysts/
      cli.py
      intake.py
      extract.py
      normalize.py
      publish.py
      events.py
      adjusted_prices.py
      security_panel.py
      universe.py
      verify.py
  tests/
  runtime/
    data_canonical/
    manifests/
    diagnostics/
    output/
    jobs/
```

`runtime/` 是 DataAnalysts 的相對輸出根目錄。所有 parquet、manifest、diagnostics、daily result 都寫在這裡。它可被任何下游系統掛載或複製使用。

## Companion Contracts

本文件定義核心流程與責任邊界。可執行產品的使用面與檢查面拆到以下文件：

- `README.md`: 使用手冊、三種回補模式、verify / inspect 入口。
- `contracts/CLI_CONTRACT.md`: CLI 參數、拒絕條件、exit contract。
- `contracts/OUTPUT_CONTRACT.md`: runtime artifact surface、schema、manifest、atomic publish。
- `contracts/CONFIG_CONTRACT.md`: `configs/*.json` 格式與治理規則。
- `contracts/VERIFICATION_CONTRACT.md`: 完整性、PIT、adjusted price、universe、fail-closed 檢查。

## DataAnalysts 完成定義

DataAnalysts 被視為完成時，必須能在本資料夾內用相對路徑產出以下 artifact surface：

```text
runtime/data_canonical/raw/<dataset_id>/...
runtime/data_canonical/derived/events/dividend_events/...
runtime/data_canonical/derived/events/capital_action_events/...
runtime/data_canonical/raw/daily_price_volume/...            # contains raw prices and adj_* prices
runtime/data_canonical/raw/corporate_actions/...
runtime/data_canonical/derived/security_panel/...
runtime/data_canonical/derived/universes/<universe_id>/...
runtime/manifests/*.json
runtime/diagnostics/*.json
runtime/jobs/*.json
runtime/output/universes/...
```

最終資料流：

```text
MongoDB
-> runtime/data_canonical/raw
-> runtime/data_canonical/derived/events
-> adjusted daily_price_volume + corporate_actions
-> runtime/data_canonical/derived/security_panel
-> runtime/data_canonical/derived/universes
```

Universe 產出不得依賴外部 `feature_store`。DataAnalysts 只允許用自己產出的 security panel 欄位與 `configs/universe_specs.json` 產出 universe membership。若某個 universe 需要 alpha feature、IC、feature importance、或 strategy-owned signal，該 universe 不屬於 DataAnalysts 的獨立完成範圍。

所有 CLI 必須預設 `--root .`，並且拒絕把輸出寫到 root 外。絕對路徑只有 MongoDB connection / external read-only inputs 可以出現；輸出永遠是 root-relative。

## Data Analysts 流程工具邊界

這裡的「流程工具」不是指每段未來都要交給一個 subagent。它只是把資料責任切清楚，讓同一個程式、同一個 runner、或同一個人實作時，也知道哪些事情可以合併處理，哪些邊界不能混在一起。

Data Analysts 的流程不應被理解成八段固定線性 pipeline。比較正確的形狀是：

```text
Data Intake Controller
  -> Canonical Raw Publisher
      -> Corporate Event and Adjusted Price Publisher
  -> Security Panel Publisher
  -> Universe Handoff Publisher
```

其中 `Data Intake Controller` 是外層控制器，不是中後段資料轉換步驟。它先決定 as_of policy、source readiness、要跑哪些 family、失敗時 blocked_step 與 next_actions，再呼叫資料發布流程。

### 1. Data Intake Controller

輸入：

```text
config/source_families.json
requested_as_of_date
MongoDB collection availability / source readiness sample
existing canonical manifests
trading_calendar, when available
```

處理：

- 檢查 source family catalog 的 database、collection、source date、availability date、PIT status、canonical status。
- 判斷 requested date 是否可用，解析 effective as_of_date。
- 做 source readiness gate。
- 依 full-history 或 daily mode 決定要跑哪些 family。
- 記錄 blocked_step、message、next_actions。

產出物：

```text
runtime/diagnostics/source_readiness_<as_of_date>.json
runtime/diagnostics/data_intake_<as_of_date>.json
runtime/jobs/daily_results/<as_of_date>.json
runtime/jobs/pipeline_state_<as_of_date>.json
```

可合併實作：

- Source Catalog Auditor、Source Readiness、Daily Orchestrator 可以合併在同一個 controller。
- 不需要為 catalog audit 單獨做一個長期獨立工具，除非未來 source governance 複雜到需要獨立審核。

不可跨越邊界：

- 不寫 canonical parquet。
- 不計算 features。
- 不產出 universe membership。
- 不因策略需求臨時放寬 source readiness。

### 2. Canonical Raw Publisher

輸入：

```text
MongoDB source collections
source profile: small_snapshot / medium_pit_table / large_daily_panel
start_date / end_date or as_of_date
selected family ids
data_cutoff_at
existing same-partition canonical rows for partial refresh
```

處理：

- 從 MongoDB 抽取 bounded rows。
- 正規化 date、ticker、source_date、source_available_date、source_period_date、event_date。
- 在 date normalization 後做 duplicate key detection。
- 依 source profile 決定讀取與寫入粒度。
- 寫入 DataAnalysts-owned canonical raw parquet。
- 寫入 manifest、duplicate diagnostics、omitted row counts。

```text
small_snapshot
  -> 單次 full snapshot 或少量整表讀取
  -> 單檔 parquet 或少量固定檔案
  -> 適用：security_master, trading_calendar

medium_pit_table
  -> full-history 用 source_date / source_available_date partition
  -> daily 用 availability window 或 event affected window
  -> 適用：APIMT1, APISTK1, AINVFINB, APISALE, TEJ event/governance collections

large_daily_panel
  -> year + ticker collection + bounded date window
  -> 必須避免一次載入全市場全歷史
  -> 適用：APIPRCD.<ticker>, APISTKATTR.<ticker>, APISHRACT.<ticker>
```

產出物：

```text
runtime/data_canonical/raw/<dataset_id>/year=YYYY/part.parquet
runtime/data_canonical/raw/<dataset_id>/available_year=YYYY/part.parquet
runtime/data_canonical/raw/<dataset_id>/<dataset_id>.parquet
runtime/manifests/<dataset_id>.json
runtime/diagnostics/<dataset_id>_duplicates_*.json
runtime/jobs/canonical_raw_refresh_result.json
```

主要來源：

```text
APIPRCD.<ticker>                 -> daily_price_volume
APISTKATTR.<ticker>              -> daily_tradability
APISHRACT.<ticker>               -> daily_chip
TEJ.APIMT1                       -> dividend_policy
TEJ.APISTK1                      -> capital_formation
TEJ.AINVFINB                     -> financial_quarterly
TEJ.APISALE                      -> monthly_sales
TEJ.APISTOCK                     -> security_master
TEJ.TRADEDAY_TWSE                -> trading_calendar
TEJ event/governance collections -> candidate event/governance raw families
TEJ.APISHRACTW                   -> shareholding_depository_inventory
Futures_TAIFEX_TX.TX_1           -> taiwan_index_futures_near_month
```

可合併實作：

- Mongo extraction、PIT normalization、canonical parquet publish 可以在同一個 per-family writer 中實作。
- 小表與中型表尤其適合抽取後立刻 normalize 並 publish，不必硬拆成多個 process。
- chunk 是執行單位，不必等同於最終 parquet 檔案數量。

不可跨越邊界：

- 不直接產出 feature values。
- 不做 feature importance、IC/IR、策略訊號。
- 不因下游策略需求改寫 raw source semantics。
- 不把 MongoDB fallback 暴露給下游。
- 不把小表過度拆成高 overhead tiny partitions。

### 3. Corporate Event and Adjusted Price Publisher

輸入：

```text
runtime/data_canonical/raw/dividend_policy
runtime/data_canonical/raw/capital_formation
runtime/data_canonical/raw/daily_price_volume
existing prior daily_price_volume partitions for adjusted-price seed
data_cutoff_at
```

處理：

- 將 `dividend_policy` 正規化成 `dividend_events`。
- 將 `capital_formation` 正規化成 `capital_action_events`。
- 用 semantic events 建立 `daily_price_volume` 的 adjusted prices。
- 用 semantic events 建立 ledger-ready `corporate_actions`。
- partial adjusted-price refresh 需從 prior partition seed `adj_factor`；缺 seed 必須 fail closed。

```text
APIMT1 q*ex_date / q*mt_div -> cash_dividend_per_share
APIMT1 mex_date / mt_mer    -> stock_dividend_ratio = mt_mer / 10
APISTK1 pct_dec1 / ashback  -> capital reduction shares/cash event
APISTK1 slamt / stk_join    -> split shares multiplier
APISTK1 precls / exprice    -> stock_price_adjustment, adjusted-price only
```

產出物：

```text
runtime/data_canonical/derived/events/dividend_events/event_year=YYYY/part.parquet
runtime/data_canonical/derived/events/capital_action_events/event_year=YYYY/part.parquet
runtime/data_canonical/raw/daily_price_volume/year=YYYY/part.parquet
runtime/data_canonical/raw/corporate_actions/year=YYYY/corporate_actions.parquet
runtime/manifests/dividend_events.json
runtime/manifests/capital_action_events.json
runtime/manifests/daily_price_volume.json
runtime/manifests/corporate_actions.json
```

可合併實作：

- dividend event、capital event、corporate_actions 可以共用 event dedupe 與 source provenance helpers。
- adjusted-price calculation 可以和 `daily_price_volume` publish 在同一個 family stage 內完成。

不可跨越邊界：

- 不把 `stock_price_adjustment` 混入 ledger `corporate_actions`。
- 不用 TEJ raw `adjfac` 取代 DataAnalysts-owned event-based adjusted price。
- 不把 adjusted price 當 execution price。
- 不做 ledger simulation、PnL attribution、策略績效歸因。
- 不把 corporate-action rows 當預設 alpha input。

### 4. Security Panel Publisher

輸入：

```text
runtime/data_canonical/raw/daily_price_volume
runtime/data_canonical/raw/daily_tradability
runtime/data_canonical/raw/security_master
runtime/data_canonical/raw/trading_calendar
runtime/jobs/daily_decision_context.json, when available
```

處理：

- 從 DataAnalysts canonical parquet 建立 shared security panel。
- 整合 price、tradability、security master、trading calendar 與必要的 investability 欄位。
- 將 raw source date 轉成 decision-date panel。
- 從 `daily_price_volume` 計算 universe 所需的最小非 alpha 欄位，例如 `adv20`。
- 檢查 leakage 欄位名稱。

產出物：

```text
runtime/data_canonical/derived/security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet
runtime/manifests/security_panel.json
runtime/jobs/security_panel_refresh_result.json
runtime/jobs/daily_results/security_panel/<as_of_date>.json
```

目標 schema：

```text
as_of_date
source_max_date
ticker
stock_name
market
security_type
listed
tradable
close
adj_close
traded_value
market_cap
adv20
data_cutoff_at
```

可合併實作：

- Security Panel Publisher 可以和 Universe Handoff Publisher 由同一個 daily handoff runner 呼叫。
- 但 security panel artifact 本身要保留，因為它是 universe、feature domain membership、diagnostics 的共同輸入面。

不可跨越邊界：

- 不查 MongoDB。
- 不計算 reusable alpha features。
- 不產出策略訊號或持倉權重。
- 不建立 universe membership。
- 不允許 leakage columns，例如 future、forward、next、realized、outcome、label_return。

### 5. Universe Handoff Publisher

輸入：

```text
runtime/data_canonical/derived/security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet
configs/universe_specs.json
runtime/jobs/daily_decision_context.json, when available
```

處理：

- 根據 security panel 與 `configs/universe_specs.json` 產出 canonical universe membership。
- universe runner 只做 selector。
- selector 僅可使用 DataAnalysts 已產出的 security panel 欄位，例如 listed、tradable、market_cap、traded_value、adv20、close、adj_close。
- 記錄 candidate count、included count、excluded count、missing input reasons、source provenance。

產出物：

```text
runtime/data_canonical/derived/universes/<universe_id>/membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/as_of_date=YYYY-MM-DD/diagnostics.parquet
runtime/manifests/universe_<universe_id>.json
runtime/output/universes/universe_construction_result.json
runtime/output/universes/daily_results/<as_of_date>.json
```

membership schema：

```text
as_of_date
universe_id
ticker
rank
```

可合併實作：

- 可與 Security Panel Publisher 在同一個 handoff command 中順序執行。
- 不代表兩個 artifact 可以合併；security panel 是候選狀態面，universe membership 是選後清單。

不可跨越邊界：

- 不查 MongoDB。
- 不計算 alpha features。
- 不依賴外部 feature_store。
- 不讓策略自建股票池。
- universe 只決定 eligible membership；不決定持倉權重、不做策略參數微調。

## 建議程式拆分

DataAnalysts 應在本資料夾內擁有自己的 runtime namespace。這是程式模組邊界，不是 subagent 邊界；同一個 runner 可以同時呼叫多個模組。

```text
src/data_analysts/cli.py
```

DataAnalysts public entrypoint：

- `run-full-history`
- `run-daily`
- `verify`
- `inspect-artifacts`

```text
src/data_analysts/extract.py
src/data_analysts/normalize.py
src/data_analysts/publish.py
```

共同支援 Canonical Raw Publisher：

- collection iteration
- date-window query
- source profile selection: small_snapshot / medium_pit_table / large_daily_panel
- source row normalization before semantic mapping
- explicit source existence checks
- date parsing
- source_available_date rules
- missing value normalization
- canonical row identity
- duplicate key construction
- partition path construction
- staging / atomic publish
- year partition merge
- manifest writing
- duplicate diagnostics

```text
src/data_analysts/events.py
src/data_analysts/adjusted_prices.py
```

共同支援 Corporate Event and Adjusted Price Publisher：

- dividend_events
- capital_action_events
- corporate_actions aggregation
- event duplicate policy
- adjusted price seed state
- price adjustment events loading
- factor_combined / adj_factor / adj_* calculation
- partial refresh fail-closed rules

```text
src/data_analysts/intake.py
```

支援 Data Intake Controller：

- as_of policy
- source readiness gate
- fixed stage order
- aggregate daily result

```text
src/data_analysts/security_panel.py
src/data_analysts/universe.py
src/data_analysts/verify.py
```

支援 Security Panel Publisher 與 Universe Handoff Publisher：

- security panel
- universe membership
- diagnostics

這樣拆分的重點是讓高風險語意獨立可測，不是要求每個步驟分成獨立人力或獨立 process。可以合併執行，但不能混淆責任。

## 產出物總表

| 流程工具 | 主要輸入 | 主要產出物 | 可合併實作 |
|---|---|---|---|
| Data Intake Controller | source catalog, requested date, source readiness sample, existing manifests | source readiness diagnostics, pipeline state, daily result | catalog audit + readiness + daily orchestration |
| Canonical Raw Publisher | MongoDB rows, source profile, date window, existing partitions | canonical raw parquet, manifests, duplicate diagnostics, refresh result | extraction + PIT normalization + publish |
| Corporate Event and Adjusted Price Publisher | dividend_policy, capital_formation, daily_price_volume, prior adj_factor seed | dividend_events, capital_action_events, adjusted daily_price_volume, corporate_actions | event layers + adjusted price publish |
| Security Panel Publisher | canonical price/tradability/master/calendar parquet | security_panel parquet, security_panel manifest, refresh result | can run in same command as universe handoff |
| Universe Handoff Publisher | security_panel, universe specs | universe membership parquet, universe diagnostics, universe manifests | can run after security panel in same daily handoff |

任何合併都不能取消 artifact 邊界。artifact 邊界是下游可驗證與 fail-closed 的依據。

## 可合併與冗餘判斷

可以合併：

- `Mongo extraction + PIT normalization + canonical parquet publish` 可以合併在同一個 per-family writer。這不是邏輯混淆，因為它們共同產出同一組 canonical raw artifacts。
- `Source catalog audit + source readiness + daily orchestration` 可以合併成 Data Intake Controller。這三者都是控制面，不是資料轉換面。
- `Security Panel Publisher + Universe Handoff Publisher` 可以在同一個 daily handoff command 內順序執行，但 artifacts 仍必須分開。
- 小型 reference 表的 extract / normalize / publish 應該合併，不要拆成多個 tiny read/write 步驟。

不該合併：

- Corporate event / adjusted price 不該併回一般 raw publisher。它有經濟語意與報酬計算影響，錯誤會污染 return、PnL、回測與 paper trade。
- Canonical raw publish 不該併入 Feature Analysts。資料層只證明 PIT-safe 與可讀，不證明訊號有效。
- Universe membership 不該併入 Strategists。策略不應各自私建股票池。
- Security panel artifact 不該被 universe membership 取代。前者是候選狀態面，後者是選後清單，兩者診斷用途不同。

冗餘風險：

- 把 source readiness、catalog audit、daily orchestration 寫成三個彼此獨立的長流程會偏冗餘；它們可以是同一個 controller 的三個檢查面。
- 小表若強制 year/ticker/tiny partition，讀取 overhead 可能大於資料本身。
- 若每個工具都各自重讀 manifest 和全量 parquet，會造成重複 I/O；controller 應傳遞必要 context，publisher 應使用 bounded reads。

不可省略：

- Manifest、diagnostics、duplicate counts、omitted counts 不能省。它們是資料層能不能被下游信任的證據。
- PIT date normalization 不能省，即使實作上和 extraction 合併。
- Artifact 邊界不能省，即使 execution command 合併。

## Full-History Flow

用途：

- 初始建置。
- 歷史修復。
- schema migration。
- adjusted-price 或 corporate-action 語意變更後重建。

流程：

```text
1. source catalog audit
2. source readiness / collection availability check
3. metadata small snapshot refresh: trading_calendar, security_master
4. raw event medium PIT refresh: dividend_policy, capital_formation
5. semantic event refresh: dividend_events, capital_action_events
6. market large daily panel refresh: daily_price_volume, daily_tradability, daily_chip
7. slow medium PIT refresh: financial_quarterly, monthly_sales
8. ledger event refresh: corporate_actions
9. event/governance candidate raw refresh if selected, using source profile granularity
10. manifest and duplicate diagnostics audit
11. build security panel
12. build universe membership from security panel and `configs/universe_specs.json`
```

接受條件：

- 每個 selected family 都有 parquet artifact 與 manifest。
- manifest row_count、date_range、availability_date_range 與 partition paths 可追。
- `daily_price_volume` 有 raw price 與 adjusted price lineage。
- `corporate_actions` 覆蓋 refresh range 內事件。
- 下游讀者不需要 MongoDB。
- 小表沒有被切成高 overhead tiny partitions；大型 daily panel 沒有被整表全歷史載入。

## Daily Flow

用途：

- 每日 production refresh。
- universe 或任何下游系統使用前的資料前置流程。

流程：

```text
1. resolve canonical raw as_of policy
2. source readiness gate
3. metadata stage
4. event raw stage
5. event semantic stage
6. daily market stage
7. slow PIT availability stage
8. ledger event stage
9. write aggregate daily result and diagnostics
10. verify canonical raw daily artifacts
11. downstream systems consume DataAnalysts artifacts only
```

接受條件：

- 不掃 full history。
- 只更新 latest source date、availability window、或 corporate-action affected window。
- 同 year partition 中 refresh window 外 rows 被保留。
- duplicate diagnostics 在 date normalization 後執行。
- blocked result 要指出 blocked_step、message、next_actions。
- daily source profile 必須保守：small snapshot 可重寫單檔；medium PIT 只掃 availability / affected window；large daily panel 只掃必要日期與 collection chunk。

## DataAnalysts 與下游系統邊界

### DataAnalysts 交付給下游

DataAnalysts 交付：

```text
canonical raw parquet
semantic event parquet
adjusted price columns
security panel
universe membership
manifest
diagnostics
source readiness / daily result artifacts
```

下游系統可以做：

```text
feature id 定義
feature formula
feature importance
IC/IR
decay
coverage and missingness interpretation
multi-security domain features
strategy signal
portfolio construction
backtest failure test
```

DataAnalysts 不該做 feature ranking、signal interpretation、strategy parameter tuning、portfolio weighting、或 backtest-based optimization。

若下游發現資料錯誤，只能回到 DataAnalysts 修 source contract、PIT policy、event semantics、adjusted price、security panel、或 universe selector。不能因策略績效不好而改資料層。

## Fail-Closed 檢查清單

Data Analysts 每次變更都應檢查：

- MongoDB row 是否有支援的日期欄位。
- canonical key date 是否成功正規化。
- duplicate key 是否在正規化後計算。
- missing required partition 是否 blocked。
- missing source_available_date 是否 blocked 或被 omitted 並記錄。
- financial/monthly sales 是否用 availability date，而不是 period date 當可用日。
- adjusted price partial refresh 是否有 prior seed。
- `stock_price_adjustment` 是否沒有混入 ledger corporate_actions。
- 小表是否避免過度切碎造成讀取 overhead。
- 大表是否避免一次載入全市場全歷史。
- security panel 是否沒有 leakage columns。
- universe membership 是否只含 `as_of_date, universe_id, ticker, rank`。
- 下游系統是否只讀取 DataAnalysts artifacts，不回頭查 MongoDB。

## 建議驗證命令

不觸發 refresh 的低成本檢查：

```powershell
python -m data_analysts.cli verify --root . --as-of-date <YYYY-MM-DD>
```

DataAnalysts daily refresh：

```powershell
python -m data_analysts.cli run-daily --root . --as-of-date <YYYY-MM-DD>
```

Family-scoped refresh：

```powershell
python -m data_analysts.cli run-full-history --root . --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> --families daily_price_volume,daily_tradability
```

Inspect artifacts：

```powershell
python -m data_analysts.cli inspect-artifacts --root . --as-of-date <YYYY-MM-DD>
```

## 最小可行流程工具切法

建議維持五個流程工具邊界：

1. Data Intake Controller
   - catalog、source readiness、as_of policy、daily orchestration。
   - 只產出 readiness / daily diagnostics，不寫 canonical parquet。

2. Canonical Raw Publisher
   - MongoDB bounded extract -> PIT normalization -> canonical raw parquet -> manifest。
   - 不做 feature、universe、strategy。

3. Corporate Event and Adjusted Price Publisher
   - dividend/capital semantic events、adjusted prices、corporate_actions。
   - 不做 ledger simulation 或策略績效歸因。

4. Security Panel Publisher
   - canonical parquet -> shared security panel。
   - 不產生 universe membership、不產生 alpha features。

5. Universe Handoff Publisher
   - security panel + `configs/universe_specs.json` -> universe membership。
   - 不查 MongoDB、不依賴外部 feature_store、不計算 alpha features、不決定策略持倉權重。

五個邊界的好處是：每段都有明確產出物和 fail-closed 檢查點；缺點是 orchestration 和 artifact 管理比單一大 pipeline 多一些成本。這個成本合理，因為資料洩漏、調整價錯誤、universe 污染，比少幾個檔案或少一段 orchestration 更危險。
