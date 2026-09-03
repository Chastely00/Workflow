# Verification Contract

Verify 的目的不是重跑資料流程，而是判斷既有正式資料產品是否可以交付給下游。它只讀取 `data_store` 內的正式 artifact surface，以及 `project_root/configs`、`project_root/contracts` 的必要規格；不查 MongoDB，不寫 canonical parquet。

## Verify Command

```powershell
python -m data_analysts.cli verify
python -m data_analysts.cli verify --project-root . --data-store .\data_store --as-of-date 2026-07-03
```

產出：

```text
data_store/jobs/verification_result.json
data_store/diagnostics/verification_<scope>.json
```

## Formal Read Boundary

Verify / inspect 只可把下列位置視為正式 artifact surface：

```text
data_store/manifests/
data_store/metadata/
data_store/diagnostics/
data_store/jobs/
```

`project_root/runtime` 與 `project_root/runs` 若存在，只能作為 legacy diagnostics signal，不可當成正式 artifacts 讀入或合併。

## Verification Requirements

Formalized verify 必須 fail closed：

1. `data_store` 不存在或缺 `manifests/`。
2. manifest artifact path 解析後逃出 `data_store`。
3. manifest artifact path 含 forbidden path segment `runtime`、`runs`、`real_all_products`。
4. manifest artifact path 是 absolute path。
5. `data_store/manifests` 缺 required manifest。
6. `data_store/jobs/verification_result.json` 無法寫入。
7. `data_store/metadata/data_store_manifest.json` 缺失。
8. `data_store/metadata/config_snapshot/` 缺 required config snapshot。
9. metadata config hash 無法由 snapshot 重算一致。

Verify 不應查 MongoDB，不應產生 canonical parquet。

## Adjusted OHLC Evidence

Candidate 與 formal evidence 的 exact data-store-relative paths 是：

```text
jobs/adjusted_ohlc_audit_candidate.json
diagnostics/adjusted_ohlc_verification.json
```

Full audit 掃描 manifest 的全部 `daily_price_volume` partitions。Incremental audit 只有在現行 manifest 已帶 `adjustment_policy_id = event_based_adjusted_ohlc_v1`、明示 `changed_paths` 且前一版 evidence 可驗證時，才可重用未變 partition；event dependency 漂移必須把受影響 suffix 納入重掃，否則 blocked 並要求 full audit。

Candidate 與 formal evidence 的 `violation_totals`、每個 partition 的 `violation_counts` 都必須完整包含十個 counters，ready 時全部為零：

```text
missing_required_column_count
invalid_adj_factor_count
null_mismatch_count
adjusted_value_mismatch_count
raw_ohlc_order_violation_count
adjusted_ohlc_order_violation_count
duplicate_key_count
unapproved_adjustment_status_count
factor_transition_violation_count
row_order_violation_count
```

一般 `verify` 是 evidence-only：只從同一次 formal evidence bytes 計算 SHA-256 並 parse JSON，不掃 parquet，也不重跑公式 validator。Adjusted OHLC check 必須記錄 `formal_evidence_pointer = "diagnostics/adjusted_ohlc_verification.json"` 與該次 bytes 的 `formal_evidence_sha256`。缺少或無效的 manifest/evidence、fingerprint 不一致、partition paths/records 不一致、非零 violations、stale evidence 或不一致的 event dependency evidence 都是 hard blockers，必須以 `blocked_step = "adjusted_ohlc"` 停止。

Promotion 在 lock 外重算 row-derived summaries 與 boundaries，但不重跑完整公式 validator；取得 publish lock 後，必須在 lock 內重新核對 candidate、manifest、price partitions 與 event dependencies 的 SHA-256 preconditions，並以同一 transaction 原子寫入 manifest policy 與 `diagnostics/adjusted_ohlc_verification.json`。任一 SHA 或 transaction 失敗都不得部分發布。

## Audit And Coverage Gate

`audit-store` 是 read-only repair evidence surface：

- 若 legacy active config snapshot 尚未含 `artifact_contracts.json`，只有 audit 可明確 fallback 到目前 project registry；輸出必須標示 `project_registry_fallback`、缺檔與 `active_snapshot_complete=false`。Pipeline 仍嚴格要求完整 active snapshot。
- `backup_evidence` 列出 manifests、metadata 與 job-state 的 absolute/relative target path、存在狀態、size 與 SHA-256（存在時）；不得包含 parquet row 或連線 secret。
- full-replace 的合法 inactive `versions/<run_id>` 可保留；owned directory 內舊 flat/unlisted parquet 仍是 exact orphan，必須列出供後續精確 archive。

```powershell
python -m data_analysts.cli audit-store
python -m data_analysts.cli audit-store --output jobs/pre_repair_audit.json
```

它必須由 registry 限定 inventory，並逐 artifact/variant 重算 row count、date/availability range、schema fingerprint、partition values 與 maximum cutoff。以下任一項非零都必須 blocked：

- `orphan_partition_count`
- `missing_partition_count`
- `manifest_mismatch_count`
- `wrong_partition_count`
- `duplicate_logical_key_count`
- `malformed_cutoff_count`
- `unavailable_cutoff_count`
- `parquet_read_error_count`
- `configured_field_error_count`

Verify 必須把 audit evidence 納入 checks，且只在 config/metadata/path/audit/coverage 全部通過後回報 ready。pre-publication coverage 比對必須收到 explicit `run_scope`：bounded/daily 不得遺失 partition、縮短 date range 或減少 row coverage；`full_history` 可合法反映來源刪除而縮小，但仍須通過 schema、PIT、logical-key、manifest 與 inventory audit。

Caller 的 `run_scope` 只是 expected value，不是 authority。transaction publication 必須在 `pipeline_result.json` 與 `current_run.json` 持久化同一 run attestation（run_id、scope、selected/enabled families、run-intent config hashes、manifest identities/hashes、`status: verifying`）。Verify 必須重算並交叉核對；缺失、stale run_id、scope/family/config/manifest mismatch 一律在 `run_attestation` gate fail closed。Project config 必須在 run intent 先 hash-freeze，尾端 snapshot 不一致不得進 verification。

`full_replace` 與 `partition_upsert` 的 inactive immutable version 是 rollback evidence，不是 active orphan；active version 內未被 manifest 列出的 parquet 則是 orphan。universe `historical` / `exact_date` 必須依 contract key 與獨立 manifest 驗證，不得用 shared legacy manifest 混淆。

## Quantitative Metrics

Verify 必須產出或回報下列量化 metrics：

```json
{
  "artifact_path_count": 0,
  "absolute_artifact_path_count": 0,
  "artifact_path_escape_count": 0,
  "forbidden_path_segment_count": 0,
  "manifest_count": 0,
  "required_manifest_missing_count": 0,
  "config_snapshot_file_count": 0,
  "config_snapshot_hash_mismatch_count": 0,
  "legacy_project_runtime_exists": false,
  "legacy_project_runs_exists": false
}
```

規則：

- `absolute_artifact_path_count` 必須為 `0`。
- `artifact_path_escape_count` 必須為 `0`。
- `forbidden_path_segment_count` 必須為 `0`。
- `config_snapshot_file_count` 必須等於 required snapshot count。
- `config_snapshot_hash_mismatch_count` 必須為 `0`。
- `legacy_project_runtime_exists` 與 `legacy_project_runs_exists` 只作為 diagnostics signal，不可單獨造成 blocked。

## Result Contract

Ready result：

```json
{
  "status": "ready",
  "checked_at": "2026-07-03T00:00:00Z",
  "scope": "2026-07-03",
  "metrics": {
    "artifact_path_count": 0,
    "absolute_artifact_path_count": 0,
    "artifact_path_escape_count": 0,
    "forbidden_path_segment_count": 0,
    "manifest_count": 0,
    "required_manifest_missing_count": 0,
    "config_snapshot_file_count": 5,
    "config_snapshot_hash_mismatch_count": 0,
    "legacy_project_runtime_exists": false,
    "legacy_project_runs_exists": false
  },
  "checks": []
}
```

Blocked result：

```json
{
  "status": "blocked",
  "checked_at": "2026-07-03T00:00:00Z",
  "scope": "2026-07-03",
  "blocked_step": "metadata",
  "message": "missing required config snapshot file",
  "next_actions": [
    "run the formalized pipeline to republish metadata",
    "inspect data_store/metadata/config_snapshot"
  ],
  "metrics": {
    "artifact_path_count": 0,
    "absolute_artifact_path_count": 0,
    "artifact_path_escape_count": 0,
    "forbidden_path_segment_count": 0,
    "manifest_count": 0,
    "required_manifest_missing_count": 0,
    "config_snapshot_file_count": 4,
    "config_snapshot_hash_mismatch_count": 0,
    "legacy_project_runtime_exists": true,
    "legacy_project_runs_exists": true
  },
  "checks": []
}
```

## Inspect Summary

Inspect summary 至少必須顯示：

```json
{
  "project_root": "...",
  "data_store": "...",
  "status": "ready",
  "legacy_layout_detected": false,
  "legacy_project_runtime_exists": false,
  "legacy_project_runs_exists": false,
  "manifest_count": 0,
  "artifact_path_count": 0,
  "forbidden_path_segment_count": 0,
  "config_snapshot_file_count": 0,
  "config_snapshot_hash_mismatch_count": 0
}
```

若偵測到 `project_root/runtime` 或 `project_root/runs`，inspect 可提示 legacy layout 存在，但不可把它當成正式 artifact surface。
