# Output Contract

DataAnalysts 的正式輸出是下游唯一可依賴的資料產品表面。所有正式 artifacts 都必須位於 `data_store/` 之下。

## Formal Layout

```text
data_store/
  canonical/
    raw/
      <dataset_id>/
    derived/
      events/
        dividend_events/
        capital_action_events/
      security_panel/
      security_panel_history/
      universes/
        <universe_id>/
  manifests/
  metadata/
    data_store_manifest.json
    config_snapshot/
      mongodb_sources.json
      source_family_profiles.json
      universe_specs.json
      source_catalog.json
      pit_registry.json
      artifact_contracts.json
  diagnostics/
  jobs/
  output/
    universes/
```

## Legacy Layout Warning

正式流程不得把下列路徑當成 formal layout：

```text
runtime/
runs/
runs/real_all_products/
```

## Canonical Raw Artifacts

路徑：

```text
canonical/raw/<dataset_id>/...
```

Manifest 中記錄的 `artifact_paths` 必須是 data-store-relative path，而不是絕對路徑。

### `daily_price_volume` Adjusted OHLC

Adjusted OHLC policy ID 是 `event_based_adjusted_ohlc_v1`；正式 ready row 的既有 `price_adjustment_status` literal 是 `adjusted_close_ready`。

`open`、`high`、`low`、`close`、`adj_open`、`adj_high`、`adj_low`、`adj_close`、`adj_factor`、`price_adjustment_status` 是不可拆分的十欄 atomic schema。每個 raw 欄位都必須符合 `adj_<field> = <field> * adj_factor`，比較容差為 `rel_tol=1e-10`、`abs_tol=1e-8`。

`daily_price_volume` manifest 必須新增 `adjustment_policy_id: "event_based_adjusted_ohlc_v1"`，且 `columns` 必須完整包含上述十欄；缺少任一欄即不是 adjusted OHLC ready artifact。

Adjusted OHLC 是 PIT-safe 分析價格，不是 execution price；下游不得直接用作成交或執行價格。

## Event Artifacts

Dividend events：

```text
canonical/derived/events/dividend_events/event_year=YYYY/part.parquet
```

Capital action events：

```text
canonical/derived/events/capital_action_events/event_year=YYYY/part.parquet
```

## Security Panel

路徑：

```text
canonical/derived/security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet
canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet
```

## Universe Membership

路徑：

```text
canonical/derived/universes/<universe_id>/membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet
canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet
canonical/derived/universes/<universe_id>/diagnostics/as_of_date=YYYY-MM-DD/diagnostics.parquet
canonical/derived/universes/<universe_id>/diagnostics/diagnostics.parquet
output/universes/universe_construction_result.json
```

`membership_by_date/...` 是 latest convenience output only。historical canonical surface 以 `membership_by_year/as_of_year=YYYY/part.parquet` 為準。

## Manifest Schema

每個 publishable artifact contract 必須有 manifest：

```text
manifests/<artifact_id>.json
```

具有多個 variant 的 universe 使用互不衝突的 identity：

```text
manifests/universe_<universe_id>.historical.json
manifests/universe_<universe_id>.exact_date.json
```

Ready manifest 表示該 contract variant 的「完整 active artifact」，不是本次 batch。`artifact_paths` 必須包含所有仍有效的年度 partition 或 snapshot；下游不得把未列入 manifest 的 parquet 當成正式資料。

## Publication Modes And Atomic Visibility

- `partition_upsert`：每次 publish 都建立 immutable `versions/<run_id>/<partition>=.../` inventory。bounded/daily 以 logical key copy-on-write 合併受影響 partition；`full_history` 則只從本次完整 source-domain extract 建立 active inventory，來源已刪除的 row/partition 必須消失。parquet 全部驗證完成後，才以 manifest 原子切換 active version；舊 version 可保留作 rollback evidence。
- `snapshot_by_value`：只替換指定 partition value，manifest 累積全部有效 snapshot。
- `full_replace`：輸出到 `versions/<run_id>/`；只有 manifest 指向的 version 是 active，舊 version 可保留作 rollback evidence，不算 orphan。

Manifest 是 visibility switch。資料檔、schema、partition membership、logical-key uniqueness 與 coverage 驗證未完成前，不得發布新 manifest。失敗時上一份 manifest 與其列出的資料必須保持可讀。

Legacy flat migration 不得在 manifest switch 後自動刪檔。新 manifest 以 `superseded_paths` 列出 original/immutable-retained path、SHA-256、size 與 `state: retained`；兩份 evidence 都一致時 audit 才豁免 original。清理只能經 `archive-superseded`，並同時提供 expected manifest SHA-256 與 `--confirm-no-legacy-readers`。archive 成功後 original 移入 hash-bound receipt，manifest 移除 `superseded_paths`，audit 不再把 original 視為 superseded。

空資料不是隱含例外。只有 contract 明示 `allow_empty: true` 且 scope 是 `full_history` 時，才可發布 `row_count: 0`、`artifact_paths: []` 的 ready manifest；這代表正式移除舊 active inventory。其他 empty publication 必須 fail closed，且不得切換既有 manifest。

最低欄位：

```text
artifact_id
schema_version
layer
source_families
source_collections
row_count
date_range
availability_date_range
columns
partitioning
artifact_paths
artifact_fingerprints
pit_policy
data_cutoff_at
duplicate_count
omitted_row_count
status
created_at
```

`artifact_paths` 規則：

- 必須是 `data_store` 相對路徑。
- 解析後必須仍位於 `data_store` 內。
- 必須以 path segment 驗證 forbidden names，不可用 substring 判斷。
- 不可包含 path segment `runtime`、`runs`、`real_all_products`。

schema `1.1` manifest 範例：

```json
{
  "artifact_id": "daily_price_volume",
  "schema_version": "1.1",
  "layer": "raw",
  "source_families": [
    "daily_price_volume"
  ],
  "source_collections": [
    "2330"
  ],
  "row_count": 2,
  "date_range": [
    "2025-12-31",
    "2026-01-02"
  ],
  "availability_date_range": [
    "2025-12-31",
    "2026-01-02"
  ],
  "columns": [
    "date",
    "ticker",
    "close",
    "data_cutoff_at"
  ],
  "partitioning": [
    "year"
  ],
  "artifact_paths": [
    "canonical/raw/daily_price_volume/year=2025/part.parquet",
    "canonical/raw/daily_price_volume/year=2026/part.parquet"
  ],
  "artifact_fingerprints": [
    {
      "artifact_path": "canonical/raw/daily_price_volume/year=2025/part.parquet",
      "sha256": "b8e85cad9680dcb1a238f852adf1eb0bd4037c4cc5e1e4675b8b871b7746430d"
    },
    {
      "artifact_path": "canonical/raw/daily_price_volume/year=2026/part.parquet",
      "sha256": "98eb1dc4b2e792a7b9e79951b7821db69fe667af90a3d3922c053197ed9dbf13"
    }
  ],
  "pit_policy": "source_date_lagged_to_decision_date",
  "data_cutoff_at": "2026-01-02T09:00:00+00:00",
  "duplicate_count": 0,
  "omitted_row_count": 0,
  "status": "ready",
  "created_at": "2026-01-02T09:05:00+00:00"
}
```

`artifact_fingerprints` 必須與 `artifact_paths` 維持相同順序的一對一關係。每個 `artifact_path` 必須完全等於對應的 data-store-relative `artifact_paths` 值；`sha256` 必須是 final Parquet bytes 的 lowercase SHA-256。duplicate path、missing entry、extra entry、非相對路徑或 malformed hash 必須 fail-closed。

publisher 必須先以 atomic replace 發布 final Parquet，再計算 final bytes 的 SHA-256；所有 partitions 的 hash 完成後，最後才以 atomic text replace 發布 schema `1.1` manifest。legacy schema `1.0` 只允許尚未被選取的過渡資料；被下游選取的 manifest 必須是 schema `1.1` 並有正確 fingerprints。

## Metadata Contract

每次正式 publish 必須寫入：

```text
data_store/metadata/data_store_manifest.json
data_store/metadata/config_snapshot/mongodb_sources.json
data_store/metadata/config_snapshot/source_family_profiles.json
data_store/metadata/config_snapshot/universe_specs.json
data_store/metadata/config_snapshot/source_catalog.json
data_store/metadata/config_snapshot/pit_registry.json
data_store/metadata/config_snapshot/artifact_contracts.json
```

`data_store_manifest.json` 至少必須能描述：

- `schema_version`
- `created_at`
- `path_reference: DataAnalysts project root`
- `project_root: .`
- `data_store_root`
- `path_mode: project_relative | external_unrecorded`
- `config_snapshot_path`
- `config_hashes`
- `source_family_count`
- `universe_spec_count`

Publisher path mode 只有以下兩種：

- `project_relative`：data store 位於 DataAnalysts project root 內；`data_store_root`
  必須記錄從 project root 出發的非空相對路徑。
- `external_unrecorded`：CLI 可用 `--data-store <absolute-path>` 指向 project root
  外的 store；global metadata 必須寫 `data_store_root: null`，不得記錄該機器的
  absolute external store path。

不得把 external store 偽裝成 project-relative path。`external_unrecorded` 表示 store
位置由本次 CLI invocation 提供，而不是把不可攜路徑寫入 global metadata。兩種
publisher mode 的 `artifact_paths` 都必須維持 data-store-relative，且解析後仍位於
該次明確指定的 data store 內。

`repair-metadata` 是既有 global metadata manifest 的 path-only migration。它必須保留
`created_at`、counts、active snapshot identity、config hashes 與其他 lineage 欄位；只移除
legacy absolute path keys 並加入 portable path fields。遷移前必須驗證 snapshot 與目前 live
configs 都仍符合既有 config hashes。它不得重建或改寫 artifact manifests、parquet 或任何
canonical artifact；already-portable manifest 驗證通過時不得改寫 lineage。

## Diagnostics and Jobs

正式 diagnostics 與 job results 必須位於：

```text
diagnostics/
jobs/
```

例如：

```text
jobs/pipeline_result.json
jobs/verification_result.json
jobs/daily_state.json
jobs/daily_results/<as_of_date>.json
```

`daily_state.json` 至少必須包含：

```text
last_ready_as_of_date
last_attempted_as_of_date
status
updated_at
```

Blocked attempt 必須保留先前的 `last_ready_as_of_date`，只更新 `last_attempted_as_of_date` 與 blocked 狀態；下一次 catch-up 仍從 last-ready anchor 後的第一個交易日開始，不得跳過失敗日。

執行階段失敗時，`last_attempted_as_of_date` 與 `daily_results/<date>.json` 必須使用實際正在執行的日期；multi-day catch-up 第一日失敗不得誤記為最終 `to_date`。只有 planner 在尚未產生任何 attempt date 前失敗時，attempt identity 才可為 `null`，且不得建立日期型 daily result。

`daily_results/<as_of_date>.json` 必須記錄單一交易日 refresh 的結果。成功時 `status` 必須為 `ready`；阻擋時 `status` 必須為 `blocked` 並包含可讀錯誤訊息。

## Inspect Summary Surface

`inspect-artifacts` 必須只總結正式 surface，且 historical universe summary 至少包含：

```text
historical_universe_file_count
historical_universe_count
historical_universe_date_min
historical_universe_date_max
small_file_daily_partition_count
```
