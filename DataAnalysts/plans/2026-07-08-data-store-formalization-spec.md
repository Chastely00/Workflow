# Data Store Formalization Spec

## Goal

將 DataAnalysts 正式化為可攜資料產品，清楚分離「程式與規格根目錄」和「資料產品儲存位置」，並移除目前不清楚的 `runtime/`、`runs/`、`real_all_products/` 語意。

正式化後，使用者執行 full history / backfill / verify / inspect 時，資料只會寫入 `data_store/`，不會污染 DataAnalysts 專案母資料夾。

## Non-Goals

- 不修改 ALF 主流程。
- 不建立 ALF runtime adapter。
- 不保留 `--root` 作為相容 alias。
- 不自動搬移或刪除既有 `runtime/`、`runs/` 舊資料。
- 不新增 smoke-test 專用層級，例如 `real_all_products`。

## Current Problem

目前 `--root .` 同時代表：

- DataAnalysts project root：`configs/`、`contracts/`、`src/`、`tests/` 所在位置。
- output root：`runtime/`、`runs/` 等資料輸出位置。

因此使用者照 README 執行：

```powershell
python -m data_analysts.cli run-full-history --root .
```

會產生：

```text
DataAnalysts/
  runtime/
  runs/
```

這違反正式資料產品語意。`runtime` 對使用者不是 runtime，而是資料；`runs/real_all_products` 是 smoke/test 遺留名稱，不應成為正式資料層級。

## Target Naming

### Project Root

`project_root` 是 DataAnalysts 程式與規格根目錄。

預設：

```text
C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
```

內容：

```text
configs/
contracts/
plans/
src/
tests/
README.md
pyproject.toml
```

### Data Store

`data_store` 是正式資料產品儲存位置。

預設：

```text
<project_root>/data_store
```

可用 CLI 參數改到其他磁碟：

```powershell
python -m data_analysts.cli run-full-history --data-store D:\DataAnalystsStore
```

`data_store` 內不需要再加 `real_all_products`。DataAnalysts 本身就是此資料產品；若未來要支援不同市場、供應商、版本，應用明確語意新增，例如：

```text
data_store/
  canonical/
  manifests/
  diagnostics/
  jobs/
  output/
```

而不是：

```text
data_store/
  real_all_products/
```

## Target Layout

正式資料輸出結構：

```text
data_store/
  canonical/
    raw/
      <family_id>/
    derived/
      events/
      pit/
      security_panel/
      security_panel_history/
      universes/
  manifests/
    <artifact_id>.json
  metadata/
    data_store_manifest.json
    config_snapshot/
      mongodb_sources.json
      source_family_profiles.json
      universe_specs.json
      source_catalog.json
      pit_registry.json
  diagnostics/
    pit_foundation/
    raw_families/
    historical_universe/
  jobs/
    pipeline_result.json
    verification_result.json
    daily_results/
  output/
    universes/
```

禁止正式流程產生：

```text
runtime/
runs/
runs/real_all_products/
```

## CLI Contract

### Removed

完全移除：

```text
--root
```

若使用者傳入 `--root`，CLI 必須 fail closed，並顯示明確錯誤：

```text
--root has been removed. Use --project-root and --data-store.
```

### Added

`--project-root`

- 預設 `.`。
- 指向 DataAnalysts project root。
- 只用來讀 `configs/`、`contracts/`，以及解析相對 `data_store` 預設位置。
- 不作為資料輸出根目錄。

`--data-store`

- 預設 `<project_root>/data_store`。
- 指向正式資料產品儲存位置。
- 所有 canonical parquet、manifests、diagnostics、jobs、output 都只可寫入此目錄。
- 可是絕對路徑或相對於 `project_root` 的路徑。

### Default Commands

正式使用者命令：

```powershell
cd C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
.\.venv\Scripts\Activate.ps1

python -m data_analysts.cli run-full-history
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

等同於：

```powershell
python -m data_analysts.cli run-full-history --project-root . --data-store .\data_store
```

指定外部資料儲存位置：

```powershell
python -m data_analysts.cli run-full-history --project-root . --data-store D:\DataAnalystsStore
python -m data_analysts.cli verify --project-root . --data-store D:\DataAnalystsStore
python -m data_analysts.cli inspect-artifacts --project-root . --data-store D:\DataAnalystsStore
```

## Path Contract

新增或重構 path context：

```text
DataAnalystsContext
  project_root: Path
  data_store: Path
```

責任：

- `config_path(name)` -> `<project_root>/configs/<name>`
- `contract_path(name)` -> `<project_root>/contracts/<name>`
- `store_path(*parts)` -> `<data_store>/<parts>`
- `artifact_path(relative_path)` -> `<data_store>/<relative_path>`

Boundary rules：

- config / contract 讀取不可逃出 `project_root`。
- data output 不可逃出 `data_store`。
- manifest `artifact_paths` 必須是 `data_store` 相對路徑。
- manifest `artifact_paths` 不可包含 forbidden path segment。

Forbidden path segments:

```text
runtime
runs
real_all_products
```

Forbidden rule 必須以 path segment 判斷，不可以 substring 判斷。例如：

```text
canonical/raw/company_runs_metric/year=2025/part.parquet
```

不應因為欄位名稱或 family id 含 `runs` 字串就被誤擋。只有 path segment 正好等於 `runs` 才違規。

## Artifact Path Contract

舊 path：

```text
runtime/data_canonical/raw/daily_price_volume/year=2025/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=2025/part.parquet
runtime/manifests/<artifact_id>.json
runtime/jobs/verification_result.json
```

新 path：

```text
canonical/raw/daily_price_volume/year=2025/part.parquet
canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=2025/part.parquet
manifests/<artifact_id>.json
jobs/verification_result.json
```

實體位置：

```text
<data_store>/canonical/raw/daily_price_volume/year=2025/part.parquet
<data_store>/canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=2025/part.parquet
<data_store>/manifests/<artifact_id>.json
<data_store>/jobs/verification_result.json
```

Manifest 中只記錄 data-store-relative path：

```json
{
  "artifact_paths": [
    "canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"
  ]
}
```

## Diagnostics Contract

Diagnostics 也屬於資料產品 metadata，放在：

```text
<data_store>/diagnostics/
```

不再放在：

```text
runs/real_all_products/diagnostics/
runtime/diagnostics/
```

Verify result：

```text
<data_store>/jobs/verification_result.json
```

Pipeline result：

```text
<data_store>/jobs/pipeline_result.json
```

## Metadata Contract

`data_store` 必須可自描述。即使資料被搬離原本的 `project_root`，接收者也應能判斷這批資料是由哪些 config、source catalog、PIT registry、universe specs 產生。

每次 pipeline publish 必須寫入：

```text
<data_store>/metadata/data_store_manifest.json
<data_store>/metadata/config_snapshot/mongodb_sources.json
<data_store>/metadata/config_snapshot/source_family_profiles.json
<data_store>/metadata/config_snapshot/universe_specs.json
<data_store>/metadata/config_snapshot/source_catalog.json
<data_store>/metadata/config_snapshot/pit_registry.json
```

`data_store_manifest.json` 必須至少包含：

```json
{
  "schema_version": "1.0",
  "created_at": "2026-07-08T00:00:00Z",
  "project_root_at_build_time": "C:/Users/ChastLai/Documents/ALF/量化積木/DataAnalysts",
  "data_store": "C:/Users/ChastLai/Documents/ALF/量化積木/DataAnalysts/data_store",
  "config_hashes": {
    "mongodb_sources.json": "<sha256>",
    "source_family_profiles.json": "<sha256>",
    "universe_specs.json": "<sha256>",
    "source_catalog.json": "<sha256>",
    "pit_registry.json": "<sha256>"
  },
  "source_family_count": 19,
  "universe_spec_count": 8
}
```

Rules:

- Config snapshot 是正式資料產品 metadata，不是 debug artifact。
- Snapshot 必須來自本次執行使用的 `project_root/configs`。
- `config_hashes` 必須可由 snapshot 檔案重新計算並一致。
- Snapshot 不可包含 secret、token、個人帳號或遠端 MongoDB URI；現有 config contract 對 plaintext URI 的限制仍適用。

## Migration Policy

不自動搬移舊資料。

原因：

- 舊 `runtime/` 與 `runs/` 可能包含先前 smoke、debug、部分回補資料。
- 自動搬移容易把不完整 artifact 當成正式資料。
- 正式化後應由 fresh run 產生乾淨 `data_store/`。

README 必須明確寫：

```text
runtime/ and runs/ are legacy development outputs. They are not read by the formalized CLI.
After confirming data_store is ready, they may be deleted manually.
```

Verify / inspect 不讀 legacy layout。

Legacy layout handling:

- Formalized pipeline/CLI 不可新建 `project_root/runtime` 或 `project_root/runs`。
- Verify 不因為既有 legacy `project_root/runtime` 或 `project_root/runs` 存在而 blocked。
- Inspect 可以回報 `legacy_layout_detected = true`，但不可把 legacy layout 併入正式 artifact surface。
- 在乾淨 temp project root 的 CLI/pipeline tests 中，必須量化確認 formalized command 後沒有新建 legacy layout。

## Verification Requirements

Formalized verify 必須 fail closed：

1. `data_store` 不存在或缺 `manifests/`。
2. manifest artifact path 解析後逃出 `data_store`。
3. manifest artifact path 含 forbidden path segment：
   - `runtime`
   - `runs`
   - `real_all_products`
4. manifest artifact path 是 absolute path。
5. `data_store/manifests` 缺 required manifest。
6. `data_store/jobs/verification_result.json` 無法寫入。
7. `data_store/metadata/data_store_manifest.json` 缺失。
8. `data_store/metadata/config_snapshot/` 缺 required config snapshot。
9. metadata config hash 無法由 snapshot 重算一致。

Verify 不應查 MongoDB，不應產生 canonical parquet。

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

其中：

- `absolute_artifact_path_count` 必須為 `0`。
- `artifact_path_escape_count` 必須為 `0`。
- `forbidden_path_segment_count` 必須為 `0`。
- `config_snapshot_file_count` 必須等於 expected required snapshot count。
- `config_snapshot_hash_mismatch_count` 必須為 `0`。
- `legacy_project_runtime_exists` / `legacy_project_runs_exists` 只作為 inspect/diagnostic signal，不可單獨造成 verify blocked。

## Inspect Requirements

Inspect 只讀：

```text
<data_store>/manifests/
<data_store>/diagnostics/
<data_store>/jobs/
```

Inspect summary 必須顯示：

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

## Testing Requirements

### Unit Tests

- `DataAnalystsContext` resolves config from `project_root/configs`.
- `DataAnalystsContext` writes data only under `data_store`.
- `DataAnalystsContext` rejects data paths outside `data_store`.
- `DataAnalystsContext` rejects artifact paths containing legacy segments.
- `DataAnalystsContext` rejects absolute manifest artifact paths.
- Forbidden artifact path validation uses path segments, not substring matching.
- `company_runs_metric` or similar non-segment substrings are not rejected.
- Metadata config snapshot hashes are reproducible from snapshot files.

### CLI Tests

- default command creates `<project_root>/data_store`.
- default command does not create `<project_root>/runtime`.
- default command does not create `<project_root>/runs`.
- `--project-root` and `--data-store` work independently.
- `--root` fails with explicit removed-argument message.
- default command writes `data_store/metadata/data_store_manifest.json`.
- default command writes all required files under `data_store/metadata/config_snapshot/`.
- clean temp project root after command has `runtime_exists == false` and `runs_exists == false`.

### Pipeline Tests

- raw families publish to `canonical/raw/...`.
- derived artifacts publish to `canonical/derived/...`.
- manifests publish to `manifests/...`.
- jobs publish to `jobs/...`.
- diagnostics publish to `diagnostics/...`.
- manifest `artifact_paths` are data-store-relative and contain no legacy segments.
- metadata publishes to `metadata/...`.
- manifest `artifact_paths` metrics satisfy:
  - `absolute_artifact_path_count == 0`
  - `artifact_path_escape_count == 0`
  - `forbidden_path_segment_count == 0`

### Verify Tests

- verify reads only `data_store`.
- verify blocks legacy artifact paths.
- verify blocks artifact paths escaping `data_store`.
- verify blocks absolute artifact paths.
- verify blocks missing metadata manifest.
- verify blocks missing config snapshot file.
- verify blocks config snapshot hash mismatch.
- verify reports legacy project `runtime/` or `runs/` without treating them as formal artifacts.
- verify remains fail-closed on historical universe diagnostics.

### Integration Smoke

Small bounded smoke:

```powershell
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31
python -m data_analysts.cli verify --project-root . --data-store .\data_store
python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store
```

Expected:

- `data_store/canonical/...` exists.
- `data_store/manifests/...` exists.
- `data_store/metadata/data_store_manifest.json` exists.
- `data_store/metadata/config_snapshot/...` exists.
- `data_store/jobs/verification_result.json` has `status = ready`.
- `runtime/` is not newly created.
- `runs/` is not newly created by formalized CLI.
- `artifact_path_escape_count == 0`.
- `absolute_artifact_path_count == 0`.
- `forbidden_path_segment_count == 0`.
- `config_snapshot_hash_mismatch_count == 0`.

## Documentation Updates

Update:

- `README.md`
- `contracts/CLI_CONTRACT.md`
- `contracts/OUTPUT_CONTRACT.md`
- `contracts/VERIFICATION_CONTRACT.md`
- `contracts/CONFIG_CONTRACT.md`

Docs must use only:

```text
project_root
data_store
canonical
manifests
metadata
diagnostics
jobs
output
```

Docs must not instruct users to use:

```text
--root
runtime
runs
real_all_products
```

except in a short legacy warning section.

## Acceptance Criteria

The formalization is complete when all conditions are true:

1. CLI no longer accepts `--root`.
2. Default CLI writes to `<project_root>/data_store`.
3. No formalized command creates `<project_root>/runtime`.
4. No formalized command creates `<project_root>/runs`.
5. Manifest artifact paths are data-store-relative.
6. Manifest artifact paths contain no `runtime`, `runs`, or `real_all_products`.
7. Verify reads only `data_store`.
8. Inspect reads only `data_store`.
9. README commands work from a fresh PowerShell after activating `.venv`.
10. Full tests pass.
11. A bounded real Mongo smoke writes canonical / manifests / diagnostics / jobs under `data_store`.
12. `data_store/metadata/data_store_manifest.json` exists and hash-checks all config snapshots.
13. `absolute_artifact_path_count == 0`.
14. `artifact_path_escape_count == 0`.
15. `forbidden_path_segment_count == 0`.
16. Clean temp project root command creates neither `runtime/` nor `runs/`.

## Rollout Order

1. Update contracts and README wording first.
2. Add `DataAnalystsContext` and tests.
3. Update CLI args and remove `--root`.
4. Update publisher/path code to write `data_store` layout.
5. Add metadata manifest and config snapshot publishing.
6. Update verify / inspect with quantitative metrics.
7. Update all tests.
8. Run bounded real smoke.
9. Only after `data_store` verifies ready, mark legacy `runtime/` and `runs/` as manually removable.

## Open Decisions

Resolved:

- `--root` will be fully removed.
- `runtime/` will not be used in formal output layout.
- `runs/` will not be used in formal output layout.
- `real_all_products/` will not be used in formal output layout.
- `data_store/` is the formal default storage name.

No unresolved naming decision remains for this spec.
