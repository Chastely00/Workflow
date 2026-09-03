# CLI Contract

DataAnalysts CLI 是可攜資料產品的唯一 public entrypoint。它不呼叫 ALF CLI，不要求下游知道 MongoDB schema，且正式資料只可寫入 `data_store`。

## Entry Points

### `run-full-history`

目的：

- 初始建置完整正式資料產品。
- 歷史修復。
- schema migration。
- adjusted price 或 event semantics 改版後重建。

形式：

```powershell
python -m data_analysts.cli run-full-history
python -m data_analysts.cli run-full-history --project-root . --data-store .\data_store
python -m data_analysts.cli run-full-history --project-root . --data-store D:\DataAnalystsStore --start-date 2010-01-01 --end-date 2026-07-03
python -m data_analysts.cli run-full-history --project-root . --data-store D:\DataAnalystsStore --families daily_price_volume,daily_tradability
```

### `run-backfill`

目的：

- 選定部分資料回補。
- 選定部分時間回補。
- 同時指定 family 與時間時，代表指定資料在指定時間內回補。

形式：

```powershell
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families daily_price_volume,daily_tradability
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --start-date 2024-01-01 --end-date 2024-12-31
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families daily_price_volume --start-date 2024-01-01 --end-date 2024-12-31
```

### `run-daily`

目的：

- 每日 production refresh。
- 不傳日期時，根據交易日曆與 `daily_state.json` 自動更新到最近可更新交易日。
- 電腦停機或排程漏跑後，可補到指定交易日。
- `--as-of-date` 僅作為單日精準重跑或 debug 模式。

形式：

```powershell
python -m data_analysts.cli run-daily
python -m data_analysts.cli run-daily --project-root . --data-store .\data_store
python -m data_analysts.cli run-daily --project-root . --data-store .\data_store --to-date 2026-07-03
python -m data_analysts.cli run-daily --project-root . --data-store .\data_store --from-date 2026-07-01 --to-date 2026-07-03
python -m data_analysts.cli run-daily --project-root . --data-store .\data_store --as-of-date 2026-07-03
```

### `verify`

目的：

- 檢查既有正式 artifacts 是否可交付給下游。
- 不查 MongoDB。
- 不產生 canonical data。
- 對 schema `1.1` manifest 只驗證 fingerprint structure；不得重新雜湊 Parquet bytes。
- 未被本次工作選取的 legacy schema `1.0` manifest 可暫時存在；不得只因其仍是 `1.0` 而阻擋 routine `verify`。

形式：

```powershell
python -m data_analysts.cli verify
python -m data_analysts.cli verify --project-root . --data-store .\data_store --as-of-date 2026-07-03
```

### `certify-adjusted-ohlc`

形式：

```powershell
python -m data_analysts.cli certify-adjusted-ohlc --project-root . --data-store .\data_store --mode full
python -m data_analysts.cli certify-adjusted-ohlc --project-root . --data-store .\data_store --publish-candidate
```

`--mode full` 只建立 `jobs/adjusted_ohlc_audit_candidate.json`，不得修改 manifest 或 formal evidence；candidate ready 時 exit `0`，blocked 或執行失敗時 exit `1`。`--publish-candidate` 只晉升既有 ready、未 stale candidate，成功時以同一 transaction 發布 manifest policy 與 `diagnostics/adjusted_ohlc_verification.json` 並 exit `0`；blocked、stale、SHA precondition 或 transaction 失敗時 exit `1`，不得部分發布。

### `inspect-artifacts`

目的：

- 只讀取正式 artifact surface。
- 回報 row count、date range、availability range、artifact paths、blocked reasons。

形式：

```powershell
python -m data_analysts.cli inspect-artifacts
python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store --as-of-date 2026-07-03
```

### `repair-metadata`

目的：

- 將既有 `data_store_manifest.json` 做 path-only migration。
- 保留 `created_at`、counts、active snapshot identity、config hashes 與其他 lineage。
- 在寫入前驗證 snapshot 與 live configs 仍符合既有 config hashes。
- 不執行 pipeline，不重建 artifact manifests，不改寫 parquet。

形式：

```powershell
python -m data_analysts.cli repair-metadata --project-root . --data-store .\data_store
```

### `repair-manifest-fingerprints`

目的：

- 只為明確指定的 artifact manifests 補齊 schema `1.1` 的 partition fingerprints。
- 不查 MongoDB、不執行 pipeline、不改寫 Parquet。
- 寫入前必須先完成所有指定 manifest 與其 artifact paths 的 preflight；任一項失敗時不得改寫任何 manifest。

形式：

```powershell
& .\.venv\Scripts\python.exe -m data_analysts.cli repair-manifest-fingerprints `
  --project-root .\DataAnalysts `
  --data-store .\data_store `
  --artifact-id daily_price_volume `
  --artifact-id daily_chip `
  --artifact-id universe_tw_equity_liquid_top300
```

此範例從 workspace root 執行。相對 `--data-store .\data_store` 是相對於 `--project-root .\DataAnalysts`，解析後為 `<workspace>\DataAnalysts\data_store`；不得寫成 `.\DataAnalysts\data_store`，否則會解析到錯誤的 `<workspace>\DataAnalysts\DataAnalysts\data_store`。

`--artifact-id` 至少必須出現一次，可多次出現，但每個 artifact ID 值必須唯一；不得預設處理全部 manifests。未指定的 manifests 不讀 Parquet bytes、不改寫。已是 schema `1.1` 且 fingerprints 正確的 manifest 必須 byte-stable；已存在 fingerprint 但與實際 bytes 不一致時必須 fail-closed，不得覆寫。

### `audit-store`

目的：

- 以 `artifact_contracts.json` 限定的 inventory 重算 parquet 證據。
- 找出 missing/orphan partition、manifest/evidence mismatch、錯誤 partition、跨檔 logical-key 重複與 malformed cutoff。
- 不查 MongoDB、不修改 canonical parquet 或 manifest。

形式：

```powershell
python -m data_analysts.cli audit-store
python -m data_analysts.cli audit-store --output jobs/pre_repair_audit.json
```

`--output pre_repair_audit.json` 與 `--output jobs/pre_repair_audit.json` 都解析到 `data_store/jobs/pre_repair_audit.json`。其他多段 path 必須以 `jobs/` 開頭；absolute、path escape、`jobs/jobs` 與非 jobs subtree 必須拒絕。Audit blocked 時仍可留下修復前證據。

## Run Scope And Publication Semantics

- `run-full-history` 使用 `full_history`：完整來源抽取；`full_replace` artifact 先寫 versioned staging，驗證後以 manifest 原子切換可見版本。
- `run-backfill` 使用 `bounded_backfill`：只抽指定範圍，但 `partition_upsert` 依 logical key 合併既有 partition，再由完整 inventory 重建 manifest。
- `run-daily` 使用 `daily`：無參數為排程入口；`--to-date` / `--from-date` 用於 catch-up，`--as-of-date` 只用於精準重跑。
- `snapshot_by_value` 只替換指定 snapshot；其他日期仍列在完整 ready manifest。
- 任何 publication/verification 失敗都不得使 partial batch 成為正式可見 artifact；上一份 manifest 保持可讀。
- `archive-superseded` 只處理一個 exact contract，必須提供 `--expected-manifest-sha256` 與 `--confirm-no-legacy-readers`；hash/path drift 或 partial move 必須保留原 manifest 與所有 legacy bytes。

## Common Parameters

`--project-root`

- 預設 `.`。
- 指向 DataAnalysts 程式與規格根目錄。
- 只用來讀 `configs/`、`contracts/`，以及解析預設 `data_store` 位置。

`--data-store`

- 預設 `<project_root>/data_store`。
- 指向正式資料產品儲存位置。
- 所有 canonical parquet、manifests、metadata、diagnostics、jobs、output 都只可寫入此目錄。
- 可是絕對路徑或相對於 `project_root` 的路徑。

`--families`

- comma-separated source family ids。
- 必須存在於 `project_root/configs/source_family_profiles.json`。
- 未知 family 必須 fail closed。

`--start-date`, `--end-date`

- ISO date，含頭含尾。
- `--end-date` 不可早於 `--start-date`。
- 只適用於 `run-backfill`；`run-full-history` parser 與 runtime 都必須拒絕任何 date boundary。

`--as-of-date`

- ISO date。
- 用於 `run-daily` 單日精準重跑，以及 `verify`、`inspect-artifacts` 的檢查 scope。
- 不可用 future date 迫使 MongoDB 查詢尚不可得資料。

`--from-date`, `--to-date`

- ISO date，含頭含尾。
- 用於 `run-daily` catch-up。
- `--to-date` 不可早於 `--from-date`。
- 不可與 `--as-of-date` 同時使用。
- `run-daily` 只處理交易日；非交易日應跳過或 no-op，不得產生假的 as-of artifact。

## Removed Argument

`--root` 已從正式 CLI contract 移除。

若使用者傳入 `--root`，CLI 必須 fail closed，並顯示：

```text
--root has been removed. Use --project-root and --data-store.
```

## Rejection Rules

CLI 必須拒絕：

- `--root`。
- unknown family。
- unsupported source profile。
- `end_date < start_date`。
- `run-full-history` 的 `--start-date` / `--end-date`，以及 runtime full-history date boundaries。
- `to_date < from_date`。
- `--as-of-date` 與 `--from-date` / `--to-date` 同時使用。
- required config 不存在。
- `project_root` config / contract 讀取路徑逃出 `project_root`。
- `data_store` artifact / output path 逃出 `data_store`。
- artifact path 含 forbidden path segment `runtime`、`runs`、`real_all_products`。
- manifest artifact path 是 absolute path。
- required manifest 缺漏但 downstream stage 被要求執行。
- adjusted-price partial refresh 缺 prior `adj_factor` seed。
- universe selector 使用 security panel 以外欄位。
- 任何 ALF CLI 或 `alf.*` runtime adapter。
- `repair-metadata` 發現 config snapshot 或 live config 與 manifest config hashes 不一致。
- `repair-manifest-fingerprints` 缺少 `--artifact-id`、artifact ID 重複、manifest 或 artifact path 缺失，或既有 fingerprint 與實際 bytes 不一致。

## Canonical CLI Exit Matrix

本表是 CLI exit code、job-result commitment、artifact commitment 與 output stream 的唯一 normative source。新增 surface 應新增 row；沿用既有 stage 與 outcome 時，不需修改既有 row 或 parser。

| surface_id | scope | stage | outcome | exit_code | job_result | artifact_commitment | stream | commands | command_exit_overrides | command_stream_overrides | command_artifact_overrides |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| common.legacy_root_rejection | all | pre_parser | rejection | 1 | none | none | stderr | all | - | - | - |
| pipeline.parser_rejection | pipeline | parser | rejection | 2 | none | none | stderr | run-full-history<br>run-backfill<br>run-daily | - | - | - |
| pipeline.preflight_failure | pipeline | preflight | failure | 1 | none | none | stderr | run-full-history<br>run-backfill<br>run-daily | - | - | - |
| pipeline.execution_blocked | pipeline | execution | blocked | 1 | blocked | blocked_result | stderr | run-full-history<br>run-backfill<br>run-daily | - | - | - |
| pipeline.planning_no_op | pipeline | planning | no_op | 0 | none | none | stdout | run-daily | - | - | - |
| pipeline.executed_success | pipeline | execution | success | 0 | ready | command_defined | stdout | run-full-history<br>run-backfill<br>run-daily | - | - | - |
| maintenance.parser_rejection | maintenance | parser | rejection | 2 | none | none | stderr | verify<br>repair-metadata<br>inspect-artifacts<br>repair-manifest-fingerprints<br>certify-adjusted-ohlc<br>run-feature-gate<br>archive-evidence<br>verify-evidence<br>run-verification-replay<br>compare-replay | - | - | - |
| maintenance.handler_failure | maintenance | handler | failure | 1 | none | none | stderr | verify<br>repair-metadata<br>inspect-artifacts<br>repair-manifest-fingerprints<br>certify-adjusted-ohlc<br>run-feature-gate<br>archive-evidence<br>verify-evidence<br>run-verification-replay<br>compare-replay | compare-replay:mismatch=3 | inspect-artifacts=stdout<br>certify-adjusted-ohlc=stdout | - |
| maintenance.success | maintenance | handler | success | 0 | none | selected_manifests | stdout | verify<br>repair-metadata<br>inspect-artifacts<br>repair-manifest-fingerprints<br>certify-adjusted-ohlc<br>run-feature-gate<br>archive-evidence<br>verify-evidence<br>run-verification-replay<br>compare-replay | - | - | verify=none<br>repair-metadata=data_store_metadata<br>inspect-artifacts=none<br>repair-manifest-fingerprints=selected_manifests<br>certify-adjusted-ohlc=command_defined<br>run-feature-gate=feature_gate_output<br>archive-evidence=evidence_archive<br>verify-evidence=none<br>run-verification-replay=replay_output<br>compare-replay=none |

## Exit Surface Notes

- `common.legacy_root_rejection`: legacy `--root` 在 `build_parser()` 與 `parse_args()` 之前被拒絕，且不進入 command handler。
- `pipeline.parser_rejection`: pipeline command 的無效或缺漏 CLI arguments 由 argparse 處理。
- `pipeline.preflight_failure`: `load_runtime_config()`、date-range validation 或 family validation 在 pipeline execution 接管前失敗。
- `pipeline.execution_blocked`: execution stage 的 blocked envelope 包含 `status`、`blocked_step`、`message`、`next_actions`。
- `pipeline.planning_no_op`: 僅適用 `run-daily` empty plan，且不呼叫 `run_pipeline()`。
- `pipeline.executed_success`: 僅適用實際呼叫 `run_pipeline()` 並得到 ready outcome 的 pipeline command。
- `maintenance.parser_rejection`: `repair-manifest-fingerprints` 缺少 required `--artifact-id`。
- `maintenance.handler_failure`: parser 通過後發生 invalid targets、manifest error 或 fingerprint error。
- `maintenance.success`: `repair-manifest-fingerprints` 完成明確由 `--artifact-id` 選取的 target。
- `commands` 只列 stable runtime command IDs；feature IDs、universe、日期與其他 request scope 由各 command payload 決定，不在 matrix 寫死。
- `command_exit_overrides`、`command_stream_overrides` 與 `command_artifact_overrides` 是 generic surface 的 command-specific normative overrides；`compare-replay` deterministic evidence mismatch 固定 exit `3`。
