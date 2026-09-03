# DataAnalysts 使用手冊

DataAnalysts 是一個獨立可攜資料產品，責任是把 MongoDB 來源治理成下游系統可直接使用的 PIT-safe parquet artifacts：

```text
MongoDB
-> canonical parquet
-> adjusted price parquet
-> event parquet
-> security panel
-> universe membership
```

它不是 ALF 主流程的 adapter，也不負責 alpha、feature importance、策略參數、回測最佳化或持倉權重。

## 硬邊界

`project_root` 是 DataAnalysts 程式與規格根目錄。預設為：

```text
C:\Users\ChastLai\Documents\量化交易積木\DataAnalysts
```

`data_store` 是正式資料產品儲存位置。預設為：

```text
<project_root>\data_store
```

正式資料產品只允許寫入：

```text
data_store/
  canonical/
  manifests/
  metadata/
  diagnostics/
  jobs/
  output/
```

禁止：

- 以 ALF CLI 作為入口。
- 以 `alf.*` 模組作為 runtime dependency。
- 讓 Feature Analysts、Strategists、Backtesters 直接查 MongoDB。
- 把正式 artifacts 寫到 `project_root` 其他位置。

允許：

- 讀取 MongoDB 作為 canonical raw extraction 的唯一上游。
- 使用 `project_root/configs` 決定 source family、PIT policy、partition、universe selector。
- 將 `project_root` 與 `data_store` 分離部署。

## 安裝與設定

DataAnalysts 使用 workspace root `.venv/`，不使用 `DataAnalysts/.venv`。

先進入 workspace root：

```powershell
cd C:\Users\ChastLai\Documents\量化交易積木
```

### 第一次建置 root 環境

正式 runtime target 是 Python 3.12：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .\DataAnalysts[test]
```

若 `py -3.12` 不可用，停止並回報 Python discovery output；不要自行改用其他 Python 版本。

確認環境已正確載入：

```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
.\.venv\Scripts\python.exe -c "import data_analysts; print(data_analysts.__file__)"
.\.venv\Scripts\python.exe -m data_analysts.cli --help
```

### 每次執行前啟動環境

```powershell
cd C:\Users\ChastLai\Documents\量化交易積木
.\.venv\Scripts\Activate.ps1
cd .\DataAnalysts
```

後面的 `python -m data_analysts.cli ...` 命令都假設已經使用 root `.venv/`。

MongoDB URI 預設使用 local MongoDB：

```powershell
mongodb://localhost:27017/
```

若要連到其他位置，執行前用環境變數覆蓋。不要把帶帳密或遠端 host 的 URI 寫進 config：

```powershell
$env:DATA_ANALYSTS_MONGODB_URI = "mongodb://localhost:27017/"
```

正式 config 位於：

```text
project_root/configs/mongodb_sources.json
project_root/configs/source_family_profiles.json
project_root/configs/universe_specs.json
project_root/configs/source_catalog.json
project_root/configs/pit_registry.json
project_root/configs/artifact_contracts.json
```

## 正式命令

預設正式命令：

```powershell
cd C:\Users\ChastLai\Documents\量化交易積木
.\.venv\Scripts\Activate.ps1
cd .\DataAnalysts

python -m data_analysts.cli run-full-history
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

`run-full-history` 會從來源可得範圍建立完整資料；正式全量建置與修復不要寫死 start/end date。

等價形式：

```powershell
python -m data_analysts.cli run-full-history --project-root . --data-store .\data_store
```

若要把正式資料產品寫到其他磁碟：

```powershell
python -m data_analysts.cli run-full-history --project-root . --data-store D:\DataAnalystsStore
python -m data_analysts.cli verify --project-root . --data-store D:\DataAnalystsStore
python -m data_analysts.cli inspect-artifacts --project-root . --data-store D:\DataAnalystsStore
```

### 補指定範圍

補 Universe 所需歷史：

```powershell
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date YYYY-MM-DD --end-date YYYY-MM-DD
python -m data_analysts.cli verify --project-root . --data-store .\data_store
python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store
```

只補某些資料表歷史：

```powershell
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families financial_statement_raw,monthly_sales,daily_chip --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

只補某段時間：

```powershell
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

### 每日更新

每日更新用於 production refresh。預設不需要手動指定日期；DataAnalysts 會依 artifact registry 讀取 `trading_calendar` ready manifest 列出的 active version（不讀 legacy fixed file），並搭配 `data_store/jobs/daily_state.json` 自動判斷需要補到哪個交易日。

```powershell
cd C:\Users\ChastLai\Documents\量化交易積木
.\.venv\Scripts\Activate.ps1
cd .\DataAnalysts

python -m data_analysts.cli run-daily
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

若電腦當掉幾天，或需要把資料補到指定日期：

```powershell
python -m data_analysts.cli run-daily --to-date YYYY-MM-DD
python -m data_analysts.cli verify --as-of-date YYYY-MM-DD
python -m data_analysts.cli inspect-artifacts --as-of-date YYYY-MM-DD
```

若要人工指定補漏區間：

```powershell
python -m data_analysts.cli run-daily --from-date YYYY-MM-DD --to-date YYYY-MM-DD
python -m data_analysts.cli verify --as-of-date YYYY-MM-DD
python -m data_analysts.cli inspect-artifacts --as-of-date YYYY-MM-DD
```

若要單日精準重跑或 debug：

```powershell
python -m data_analysts.cli run-daily --as-of-date YYYY-MM-DD
```

若正式資料產品放在其他磁碟：

```powershell
python -m data_analysts.cli run-daily --project-root . --data-store D:\DataAnalystsStore --to-date YYYY-MM-DD
python -m data_analysts.cli verify --project-root . --data-store D:\DataAnalystsStore --as-of-date YYYY-MM-DD
python -m data_analysts.cli inspect-artifacts --project-root . --data-store D:\DataAnalystsStore --as-of-date YYYY-MM-DD
```

### Metadata 路徑修復

既有 store 若仍含舊機器 absolute path keys，只執行：

```powershell
python -m data_analysts.cli repair-metadata --project-root . --data-store .\data_store
```

這是 path-only migration。它保留 `created_at`、counts、active snapshot identity、config hashes
與其他 lineage，並先驗證 snapshot 與 live configs 的 config hashes。此命令不執行 pipeline、
不重建 artifact manifests，也不改寫 parquet；already-portable manifest 只驗證、不改寫。

### Manifest 指紋修復

只修復明確指定的 legacy manifest 指紋時，從 workspace root 執行：

```powershell
& .\.venv\Scripts\python.exe -m data_analysts.cli repair-manifest-fingerprints `
  --project-root .\DataAnalysts `
  --data-store .\data_store `
  --artifact-id daily_price_volume `
  --artifact-id daily_chip `
  --artifact-id universe_tw_equity_liquid_top300
```

這個命令從 workspace root 啟動時，`.\data_store` 會相對於 `--project-root .\DataAnalysts` 解析，所以實際 target 是 `<workspace>\DataAnalysts\data_store`。不要傳入 `.\DataAnalysts\data_store`，否則會再接到 project root 下而解析成錯誤的 `<workspace>\DataAnalysts\DataAnalysts\data_store`。

每個 `--artifact-id` 必須唯一，且至少指定一個。此 maintenance command 只讀指定 manifests 與其列出的 Parquet bytes；未指定 manifests 不讀取、不改寫，command 不執行 pipeline 也不改寫 Parquet。完成後可執行 `verify`，其對 schema `1.1` 只檢查 fingerprint structure，不重新雜湊全部 Parquet bytes。

每日更新後至少檢查：

```powershell
Get-Content .\data_store\jobs\daily_state.json
Get-Content .\data_store\jobs\daily_results\YYYY-MM-DD.json
Get-Content .\data_store\jobs\verification_result.json
Get-Content .\data_store\jobs\current_run.json
```

成功交付條件：

- `run-daily` exit code 為 `0`。
- `verify --as-of-date YYYY-MM-DD` exit code 為 `0`。
- job result `status` 為 `ready`。
- `inspect-artifacts` 沒有 blocked reason。

`--as-of-date` 是單日精準模式，不是正式排程主入口。正式排程建議使用 `run-daily`；補漏建議使用 `run-daily --to-date YYYY-MM-DD` 或 `run-daily --from-date YYYY-MM-DD --to-date YYYY-MM-DD`。

## Publication 與 Manifest 規則

所有正式 path、logical key、partition field 與 publication mode 都來自 `configs/artifact_contracts.json`，pipeline 不自行組合 canonical path。

- `partition_upsert` 每次建立 immutable version；bounded/daily 以 copy-on-write 依 logical key 合併受影響 partition，不會用單日 batch 覆蓋整年，最後由 manifest 原子切換 active inventory。
- `snapshot_by_value` 只替換指定日期，ready manifest 仍列出所有有效 snapshots。
- `full_replace` 先建立 versioned staged output，驗證完成後才以 manifest 原子切換 active version；舊 version 可保留以便 rollback。
- Ready manifest 代表完整 active artifact，不是最後一次 batch。下游只能讀 manifest-listed paths。

publication 失敗時，上一份 manifest 及其 parquet 必須保持可讀。Stateful derived data 會從受影響日期向後重算；daily adjusted price 必須承接 prior factor/close，`adv20` 必須包含必要歷史 lookback。

Legacy flat parquet 切換到 versioned inventory 後會以 `superseded_paths` 明確列管，不會自動刪除。確認所有舊 reader 已停止後，才可用 `archive-superseded --contract-key ... --expected-manifest-sha256 ... --confirm-no-legacy-readers` 做 hash-bound archive。

## Audit 與無固定日期修復

`audit-store` 不查 MongoDB，也不修改 canonical/manifest；它從 parquet 重算 coverage、schema、cutoff、partition 與 logical-key 證據：

對 pre-registry legacy store，audit 可明確標示並使用 project registry fallback 來完成盤點，但不會把舊 active config snapshot 誤報為完整，也不會讓 pipeline 使用該 fallback。修復前請使用 audit 的 `backup_evidence` 與 exact orphan paths 做可恢復備份/封存，不要用 recursive glob 猜測目標。

```powershell
python -m data_analysts.cli audit-store
python -m data_analysts.cli audit-store --output jobs/pre_repair_audit.json
```

Bare filename `--output pre_repair_audit.json` 也會寫入相同的 `data_store/jobs/pre_repair_audit.json`；若提供多段相對路徑，必須明確以 `jobs/` 開頭。

完整修復流程不得使用固定日期：

```powershell
python -m data_analysts.cli audit-store --output jobs/pre_repair_audit.json
python -m data_analysts.cli run-full-history
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
python -m data_analysts.cli audit-store --output jobs/post_repair_audit.json
python -m data_analysts.cli run-daily
python -m data_analysts.cli verify
```

只有 post-repair audit、verify 與 inspect 都 ready，且來源診斷支持的最大日期已落地、歷史 minimum/partition/row coverage 沒退化，才可歸檔 pre-repair audit 指出的 superseded legacy files。只移動已解析且確認位於 `data_store/canonical` 下的精確路徑，不使用 broad glob 或 recursive delete。

## 執行中進度

`run-full-history`、`run-backfill`、`run-daily` 會在 console 印出目前階段：

```text
[progress] phase=extract status=running families=0/19
[progress] phase=raw_family status=running family=daily_price_volume families=3/19 rows=56125
[progress] phase=raw_family status=running family=daily_price_volume families=4/19 rows=56125 published=1
[progress] phase=security_panel status=running families=19/19
[progress] phase=universe status=running families=19/19
[progress] phase=metadata status=running families=19/19
[progress] phase=complete status=ready families=19/19 message=pipeline ready
```

同時會覆寫：

```text
data_store/jobs/current_run.json
```

另一個 PowerShell 視窗可以這樣監控：

```powershell
cd C:\Users\ChastLai\Documents\量化交易積木
.\.venv\Scripts\Activate.ps1
cd .\DataAnalysts

Get-Content .\data_store\jobs\current_run.json
Get-ChildItem .\data_store\manifests |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 Name, LastWriteTime
Get-ChildItem .\data_store\diagnostics\raw_families |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 Name, LastWriteTime
```

若流程失敗，`current_run.json` 會保留：

```text
status=blocked
phase=<最後階段>
current_family=<最後處理 family 或 null>
error=<錯誤訊息>
```

## 正式輸出表面

```text
data_store/canonical/raw/<dataset_id>/...
data_store/canonical/derived/events/dividend_events/...
data_store/canonical/derived/events/capital_action_events/...
data_store/canonical/derived/security_panel/...
data_store/canonical/derived/security_panel_history/...
data_store/canonical/derived/universes/<universe_id>/...
data_store/manifests/*.json
data_store/metadata/data_store_manifest.json
data_store/metadata/config_snapshot/*.json
data_store/diagnostics/*.json
data_store/jobs/*.json
data_store/output/universes/...
```

下游系統只應讀取這些 artifacts 與 manifests。若缺資料、schema 不支援、PIT 狀態不明、adjusted-price seed 不足、universe input 不完整，DataAnalysts 必須 fail closed。

## Legacy Warning

`--root` has been removed. Use `--project-root` and `--data-store`.

`runtime/` and `runs/` are legacy development outputs. They are not read by the formalized CLI. After confirming `data_store` is ready, they may be deleted manually.

## 文件地圖

- `AGENTS.md`: DataAnalysts operational contract，給 DataAnalysts subagent 使用。
- `README.md`: 人類 operator 使用手冊。
- `AGENT_DATA_USAGE.md`: 給下游 agent 的 manifest-first、PIT-safe、read-only 使用規則。
- `memory/README.md`: DataAnalysts private memory boundary。
- `contracts/CLI_CONTRACT.md`: CLI 命令、正式參數、拒絕規則。
- `contracts/OUTPUT_CONTRACT.md`: `data_store` artifact surface、manifest、metadata。
- `contracts/CONFIG_CONTRACT.md`: `project_root/configs` 與 `data_store/metadata/config_snapshot` 規則。
- `contracts/VERIFICATION_CONTRACT.md`: verify/inspect 的 fail-closed 檢查與量化 metrics。
