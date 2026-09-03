# Task 6 Review Package


## FILE: plans\sdd\historical-universe\task-6-brief.md
```
# Task 6 Brief

### Task 6: Inspect, Diagnostics, and Documentation

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\inspect.py`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\README.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\OUTPUT_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\VERIFICATION_CONTRACT.md`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_pipeline.py`

**Interfaces:**
- Consumes: `runtime/manifests/*`, diagnostics under `runs/real_all_products/diagnostics/historical_universe`.
- Produces: inspect summary fields:
  - `historical_universe_file_count`
  - `historical_universe_count`
  - `historical_universe_date_min`
  - `historical_universe_date_max`
  - `small_file_daily_partition_count`

- [ ] **Step 1: Add inspect assertions**

Extend `tests/test_historical_universe_pipeline.py`:

```python
from data_analysts.inspect import inspect_artifacts


def test_inspect_reports_historical_universe_summary(tmp_path):
    # Reuse the pipeline fixture from the publish test.
    result = inspect_artifacts(DataAnalystsRoot.from_path(tmp_path))
    assert result["historical_universe"]["status"] == "ready"
    assert result["historical_universe"]["small_file_daily_partition_count"] == 0
    assert result["historical_universe"]["historical_universe_count"] >= 1
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q
```

Expected: FAIL because inspect does not summarize historical universes.

- [ ] **Step 3: Implement inspect summary**

Modify `src/data_analysts/inspect.py` to scan manifests whose `artifact_id` starts with `universe_` and whose `partitioning == ["as_of_year"]`. Count artifact paths, date ranges, and any path containing `membership_by_date/as_of_date=`.

- [ ] **Step 4: Update README**

Add a concise section:

```text
Historical Universe:
- `as_of_date`: observation date after close.
- `effective_date`: next trading day from `trading_calendar`; downstream systems may trade membership no earlier than this date.
- Canonical membership is year-partitioned under `membership_by_year/as_of_year=YYYY/part.parquet`.
- Latest `membership_by_date` outputs are convenience artifacts only.
```

- [ ] **Step 5: Run docs/inspect tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src\data_analysts\inspect.py README.md contracts\OUTPUT_CONTRACT.md contracts\VERIFICATION_CONTRACT.md tests\test_historical_universe_pipeline.py
git commit -m "docs: surface historical universe diagnostics"
```


```


## FILE: plans\sdd\historical-universe\task-6-report.md
```
# Task 6 Report

## STATUS

GREEN

## Changed Files

- `src/data_analysts/inspect.py`
- `tests/test_historical_universe_pipeline.py`
- `README.md`
- `contracts/OUTPUT_CONTRACT.md`
- `contracts/VERIFICATION_CONTRACT.md`

## RED Test

- Command: `$env:PYTHONPATH='src'; python -m pytest tests/test_historical_universe_pipeline.py -q`
- Result: FAIL
- Failure:
  - `test_inspect_reports_historical_universe_summary`
  - `KeyError: 'historical_universe'`

## GREEN Test

- Command: `$env:PYTHONPATH='src'; python -m pytest tests/test_historical_universe_pipeline.py -q`
- Result: PASS
- Output:
  - `2 passed in 0.74s`

## Self-Review

- 只新增 inspect summary surface，沒有改 pipeline publishing semantics。
- 沒有改 verify gate 行為；只在文件補上 inspect 與 verify 的責任邊界。
- historical universe summary 只統計 `artifact_id` 以 `universe_` 開頭且 `partitioning == ["as_of_year"]` 的 manifest，符合 brief。
- `small_file_daily_partition_count` 只數 `membership_by_date/as_of_date=` convenience path，避免把 year-partition canonical path 算進去。
- 測試先紅後綠，失敗原因與目標行為一致。

## Concerns

- `historical_universe.status` 目前是 inspect 層自己的摘要狀態，規則是「有 historical manifest 且 `small_file_daily_partition_count == 0` 則 ready」；它不是 verify result 的替代品。
- 只跑了 brief 指定的 `tests/test_historical_universe_pipeline.py`，沒有重跑整包測試。

```


## FILE: src\data_analysts\inspect.py
```
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_analysts.paths import DataAnalystsRoot


def inspect_artifacts(root: DataAnalystsRoot, as_of_date: str | None = None) -> dict[str, Any]:
    manifests_dir = root.runtime_path("manifests")
    artifacts: list[dict[str, Any]] = []
    if manifests_dir.exists():
        for manifest_path in sorted(manifests_dir.glob("*.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifacts.append(
                {
                    "artifact_id": manifest.get("artifact_id"),
                    "status": manifest.get("status"),
                    "row_count": manifest.get("row_count"),
                    "date_range": manifest.get("date_range"),
                    "availability_date_range": manifest.get("availability_date_range"),
                    "partitioning": manifest.get("partitioning"),
                    "pit_policy": manifest.get("pit_policy"),
                    "artifact_paths": manifest.get("artifact_paths", []),
                }
            )
    raw_error, raw_metrics = check_raw_family_diagnostics(root)
    historical_universe = summarize_historical_universe(artifacts, root)
    return {
        "status": "ready" if artifacts else "blocked",
        "scope": as_of_date or "all",
        "artifacts": artifacts,
        "historical_universe": historical_universe,
        "raw_family_diagnostics": {
            "status": "ready" if raw_error is None else "blocked",
            **raw_metrics,
        },
    }


def check_raw_family_diagnostics(root: DataAnalystsRoot) -> tuple[str | None, dict[str, Any]]:
    diagnostics_dir = root.diagnostics_path("raw_families")
    if not diagnostics_dir.exists():
        return None, {
            "family_count": 0,
            "raw_family_diagnostic_count": 0,
            "pit_parse_failure_count_total": 0,
            "unresolved_duplicate_count_total": 0,
            "forbidden_source_usage_count_total": 0,
        }
    totals = {
        "family_count": 0,
        "raw_family_diagnostic_count": 0,
        "pit_parse_failure_count_total": 0,
        "unresolved_duplicate_count_total": 0,
        "forbidden_source_usage_count_total": 0,
    }
    for path in sorted(diagnostics_dir.glob("*.json")):
        payload = _load_json_object(path)
        totals["family_count"] += 1
        totals["raw_family_diagnostic_count"] += 1
        totals["pit_parse_failure_count_total"] += int(
            payload.get("pit_parse_failure_count") or 0
        )
        totals["unresolved_duplicate_count_total"] += int(
            payload.get("unresolved_duplicate_count") or 0
        )
        totals["forbidden_source_usage_count_total"] += int(
            payload.get("forbidden_source_usage_count") or 0
        )
    if totals["pit_parse_failure_count_total"] != 0:
        return "raw family PIT parse failures are nonzero", totals
    if totals["unresolved_duplicate_count_total"] != 0:
        return "raw family unresolved duplicate count is nonzero", totals
    if totals["forbidden_source_usage_count_total"] != 0:
        return "raw family forbidden source usage is nonzero", totals
    return None, totals


def _load_json_object(path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"JSON file is missing: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def summarize_historical_universe(
    artifacts: list[dict[str, Any]], root: DataAnalystsRoot
) -> dict[str, Any]:
    historical_manifests = [
        artifact
        for artifact in artifacts
        if _is_historical_universe_manifest(artifact)
    ]
    file_count = 0
    universe_ids: set[str] = set()
    date_values: list[str] = []
    small_file_daily_partition_count = 0

    for artifact in historical_manifests:
        artifact_id = artifact.get("artifact_id")
        if isinstance(artifact_id, str):
            universe_ids.add(artifact_id.removeprefix("universe_"))
        artifact_paths = artifact.get("artifact_paths")
        if isinstance(artifact_paths, list):
            file_count += len(artifact_paths)
            for artifact_path in artifact_paths:
                if not isinstance(artifact_path, str):
                    continue
                normalized = artifact_path.replace("\\", "/")
                if "membership_by_date/as_of_date=" in normalized:
                    small_file_daily_partition_count += 1
        date_range = artifact.get("date_range")
        if (
            isinstance(date_range, list)
            and len(date_range) == 2
            and all(isinstance(value, str) and value for value in date_range)
        ):
            date_values.extend(date_range)

    diagnostics_dir = root.diagnostics_path("historical_universe")
    diagnostic_file_count = _diagnostic_file_count(diagnostics_dir)
    status = "blocked"
    if historical_manifests and small_file_daily_partition_count == 0:
        status = "ready"

    return {
        "status": status,
        "historical_universe_file_count": file_count,
        "historical_universe_count": len(universe_ids),
        "historical_universe_date_min": min(date_values) if date_values else None,
        "historical_universe_date_max": max(date_values) if date_values else None,
        "small_file_daily_partition_count": small_file_daily_partition_count,
        "diagnostic_file_count": diagnostic_file_count,
    }


def _is_historical_universe_manifest(artifact: dict[str, Any]) -> bool:
    artifact_id = artifact.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("universe_"):
        return False
    return artifact.get("partitioning") == ["as_of_year"]


def _diagnostic_file_count(diagnostics_dir: Path) -> int:
    if not diagnostics_dir.exists():
        return 0
    return sum(1 for path in diagnostics_dir.rglob("*.json") if path.is_file())

```


## FILE: README.md
```
# DataAnalysts 使用手冊

DataAnalysts 是一個獨立可攜資料產品，責任是把 MongoDB 來源治理成下游系統可直接使用的 PIT-safe parquet artifacts：

```text
MongoDB
-> PIT canonical parquet
-> adjusted price parquet
-> event parquet
-> security panel
-> universe membership
```

它不是 ALF 主流程的 adapter，也不負責 alpha、feature importance、策略參數、回測最佳化或持倉權重。

## 硬邊界

所有 DataAnalysts 產物都必須位於 DataAnalysts root 之下。預設 root 是本資料夾：

```text
C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
```

輸出根目錄固定為 root-relative：

```text
runtime/
```

禁止：

- 寫入 DataAnalysts root 以外的路徑。
- 以 ALF CLI 作為入口。
- 以 `alf.*` 模組作為 runtime dependency。
- 讓 Feature Analysts、Strategists、Backtesters 直接查 MongoDB。

允許：

- 讀取 MongoDB 作為 canonical raw extraction 的唯一上游。
- 使用 DataAnalysts 內的 config 決定 source family、PIT policy、partition、universe selector。
- 將 DataAnalysts root 搬到其他系統後，以相同相對目錄產出 runtime artifacts。

## 安裝與設定

在 DataAnalysts root 內安裝本套件：

```powershell
python -m pip install -e .[test]
```

MongoDB URI 預設使用 local MongoDB：

```powershell
mongodb://localhost:27017/
```

若要連到其他位置，執行前用環境變數覆蓋。不要把帶帳密或遠端 host 的 URI 寫進 config：

```powershell
$env:DATA_ANALYSTS_MONGODB_URI = "mongodb://localhost:27017/"
```

預設 config 位於：

```text
configs/mongodb_sources.json
configs/source_family_profiles.json
configs/universe_specs.json
```

其中 `source_family_profiles.json` 可用 `collection` 或 `collection_pattern` 指定 Mongo collection，並用 `field_map` 把來源欄位轉成 DataAnalysts canonical 欄位。大型 daily panel 必須提供日期 window；`run-daily --as-of-date` 會自動使用該日作為 bounded window。

## 使用者介面

DataAnalysts 只暴露三種生產模式，加上 verify 與 inspect。其他細節由 config 管理，不應變成日常操作參數。

### 1. 全歷史回補

用於初始建置、歷史修復、schema migration、adjusted-price 或 corporate-action 語意改版後重建。

```powershell
python -m data_analysts.cli run-full-history --root .
```

可選擇限制日期或 family，但這仍然屬於全歷史 runner 的範圍控制，不是新模式：

```powershell
python -m data_analysts.cli run-full-history --root . --start-date 2010-01-01 --end-date 2026-07-03
python -m data_analysts.cli run-full-history --root . --families daily_price_volume,daily_tradability
```

### 2. 選定部分資料回補

用於某些 source family 發生修正，但不想重建所有資料。

```powershell
python -m data_analysts.cli run-backfill --root . --families daily_price_volume,daily_tradability
```

### 3. 選定部分時間回補

用於某段時間資料缺漏、來源修正、或 corporate action 影響 window 需要重算。

```powershell
python -m data_analysts.cli run-backfill --root . --start-date 2024-01-01 --end-date 2024-12-31
```

若同時指定 family 與日期，意義是「指定資料在指定時間內回補」，不是第四種模式：

```powershell
python -m data_analysts.cli run-backfill --root . --families daily_price_volume --start-date 2024-01-01 --end-date 2024-12-31
```

### Verify

Verify 只檢查既有 runtime artifacts，不應查 MongoDB，不應產生 canonical data。

```powershell
python -m data_analysts.cli verify --root .
python -m data_analysts.cli verify --root . --as-of-date 2026-07-03
```

### Inspect

Inspect 用於列出目前 artifact surface、manifest、row count、date range 與 blocked reason。

```powershell
python -m data_analysts.cli inspect-artifacts --root .
python -m data_analysts.cli inspect-artifacts --root . --as-of-date 2026-07-03
```

Historical Universe:

- `as_of_date`: 收盤後觀測日。
- `effective_date`: 由 `trading_calendar` 推出的下一個交易日；下游不得早於此日交易 membership。
- canonical membership 固定放在 `membership_by_year/as_of_year=YYYY/part.parquet`。
- `membership_by_date` 只是一個 latest convenience output，不是 historical canonical surface。
- inspect summary 會回報 `historical_universe_file_count`、`historical_universe_count`、`historical_universe_date_min`、`historical_universe_date_max`、`small_file_daily_partition_count`。

## 主要輸出

```text
runtime/data_canonical/raw/<dataset_id>/...
runtime/data_canonical/derived/events/dividend_events/...
runtime/data_canonical/derived/events/capital_action_events/...
runtime/data_canonical/derived/security_panel/...
runtime/data_canonical/derived/universes/<universe_id>/...
runtime/manifests/*.json
runtime/diagnostics/*.json
runtime/jobs/*.json
runtime/output/universes/...
```

下游系統只應讀取這些 artifacts 與 manifests。若缺資料、schema 不支援、PIT 狀態不明、adjusted-price seed 不足、universe input 不完整，DataAnalysts 必須 fail closed。

## Raw Family Coverage

Raw Family Expansion publishes trading calendar, daily tradability, daily chip, monthly sales, financial statements from `TEJ.AINVFINB`, self-reported numbers from `TEJ.AFESTM1`, governance/event tables, and TX near-month futures. `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden and fail verification.

## 文件地圖

- `Data_Analysts_MongoDB_to_PIT_Parquet.md`: 核心流程、工具邊界、可合併與不可跨越邊界。
- `contracts/CLI_CONTRACT.md`: 三種回補模式、verify、inspect、參數與拒絕條件。
- `contracts/OUTPUT_CONTRACT.md`: runtime artifact surface、schema、manifest、atomic publish。
- `contracts/CONFIG_CONTRACT.md`: `configs/*.json` 的格式與治理規則。
- `contracts/VERIFICATION_CONTRACT.md`: 完整性、PIT、adjusted price、universe、fail-closed 檢查。

```


## FILE: contracts\OUTPUT_CONTRACT.md
```
# Output Contract

DataAnalysts 的輸出是下游唯一可依賴的資料產品表面。所有 runtime 產物必須是 DataAnalysts root 的相對路徑，且位於 `runtime/` 之下。

## Runtime Layout

```text
runtime/
  data_canonical/
    raw/
      <dataset_id>/
    derived/
      events/
        dividend_events/
        capital_action_events/
      security_panel/
      universes/
        <universe_id>/
  manifests/
  diagnostics/
  jobs/
  output/
    universes/
```

## Canonical Raw Artifacts

路徑：

```text
runtime/data_canonical/raw/<dataset_id>/...
```

可接受 partition 形式：

```text
year=YYYY/part.parquet
available_year=YYYY/part.parquet
<dataset_id>.parquet
```

選擇規則：

- `small_snapshot`: 單檔或少量固定檔案。
- `medium_pit_table`: 依 source date 或 availability date partition。
- `large_daily_panel`: 依 year、ticker chunk、bounded date window 控制 I/O。

每列至少要保留：

```text
source_dataset_id
source_collection
source_row_id
data_cutoff_at
```

若 source family 有 PIT 意義，還必須有至少一個可審核日期欄位：

```text
source_date
source_available_date
source_period_date
event_date
```

## Raw Family Expansion Outputs

Raw family artifacts are registry-driven canonical parquet surfaces. They are not feature tables and they are not strategy inputs until Feature Analysts consume them.

Required raw outputs:

| artifact_id | layer | partitioning | PIT field |
|---|---|---|---|
| trading_calendar | raw | single_file | zdate |
| daily_tradability | raw | year | mdate |
| daily_chip | raw | year | mdate |
| monthly_sales | raw | available_year | annd_s |
| financial_statement_raw | raw | available_year | key3 |
| financial_statement_pit_selected | derived | decision_year | source_available_date |
| self_reported_numbers_raw | raw | available_year | annd |
| self_reported_numbers_pit_selected | derived | decision_year | source_available_date |
| taiwan_index_futures_near_month | raw | year | 日期 |

Governance and event raw families use `mdate` as `source_available_date` and publish by `available_year`.

## Adjusted Price Artifact

`daily_price_volume` 同時承載 raw price 與 DataAnalysts-owned adjusted price。

最低欄位：

```text
date
ticker
open
high
low
close
volume
traded_value
adj_factor
adj_open
adj_high
adj_low
adj_close
data_cutoff_at
```

規則：

- raw price 不得覆寫。
- adjusted price 必須可追溯到 semantic events。
- partial refresh 若缺 prior `adj_factor` seed 必須 blocked。
- 不得用 TEJ raw `adjfac` 取代 DataAnalysts-owned event-based adjusted price。

## Event Artifacts

Dividend events：

```text
runtime/data_canonical/derived/events/dividend_events/event_year=YYYY/part.parquet
```

最低欄位：

```text
event_date
ex_date
ticker
cash_dividend_per_share
stock_dividend_ratio
source_dataset_id
source_row_id
data_cutoff_at
```

Capital action events：

```text
runtime/data_canonical/derived/events/capital_action_events/event_year=YYYY/part.parquet
```

最低欄位：

```text
event_date
ex_date
ticker
action_type
share_multiplier
cash_return_per_share
price_adjustment_reference
source_dataset_id
source_row_id
data_cutoff_at
```

## Corporate Actions

路徑：

```text
runtime/data_canonical/raw/corporate_actions/year=YYYY/corporate_actions.parquet
```

規則：

- 只包含 ledger-relevant events。
- `stock_price_adjustment` 只供 adjusted price 使用，不得混入 ledger corporate_actions。
- 每列必須能回追 source event。

## Security Panel

路徑：

```text
runtime/data_canonical/derived/security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet
runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet
```

最低欄位：

```text
as_of_date
effective_date
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

禁止 leakage 欄位名稱：

```text
future
forward
next
realized
outcome
label_return
```

historical canonical `security_panel_history/as_of_year=YYYY/part.parquet` 必須至少包含上述 required columns，且不得省略 `effective_date`。

## Universe Membership

路徑：

```text
runtime/data_canonical/derived/universes/<universe_id>/membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet
runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/as_of_date=YYYY-MM-DD/diagnostics.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/diagnostics.parquet
runtime/output/universes/universe_construction_result.json
```

`membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` 是 latest-date convenience output only。

latest convenience schema 固定為：

```text
as_of_date
universe_id
ticker
rank
```

historical canonical `membership_by_year/as_of_year=YYYY/part.parquet` schema 至少必須包含：

```text
as_of_date
effective_date
universe_id
ticker
rank
included
reason
market
security_type
listed
tradable
close
adj_close
market_cap
adv20
data_cutoff_at
```

規則：

- 每個 `(as_of_date, universe_id, ticker)` 必須唯一。
- `rank` 必須在同一個 `(as_of_date, universe_id)` 內可排序且不重複。
- selector 只可使用 security panel 欄位。
- 不得依賴外部 feature store。
- `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` 不得與 historical canonical schema 混淆。
- historical canonical surface 必須使用 `membership_by_year/as_of_year=YYYY/part.parquet`，且 required columns 以 historical canonical schema 為準。

## Manifest Schema

每個 publishable artifact 必須有 manifest：

```text
runtime/manifests/<artifact_id>.json
```

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
pit_policy
data_cutoff_at
duplicate_count
omitted_row_count
status
created_at
```

`artifact_paths` 必須都是 root-relative path，且解析後位於 DataAnalysts root 之內。

## Atomic Publish

任何 parquet 或 manifest publish 必須遵守：

- 先寫 staging。
- 驗證 row count、schema、partition、manifest path。
- 成功後 atomic rename 或替換。
- 失敗時不得留下 partial final artifact。
- job result 必須寫入 blocked reason。

## PIT Foundation Diagnostics

PIT Foundation writes diagnostics to:

```text
runs/real_all_products/diagnostics/pit_foundation/source_catalog.json
```

The diagnostic must include:

```text
forbidden_source_count
approved_source_count
pit_registry_family_count
forbidden_source_usage_count
missing_pit_field_count
missing_logical_key_count
```

Metric meanings:

- `forbidden_source_count`: count of forbidden source definitions found in the configured catalog surface.
- `approved_source_count`: count of catalog sources approved for PIT Foundation use.
- `pit_registry_family_count`: count of source families with PIT registry entries.
- `forbidden_source_usage_count`: count of forbidden source references found in configs, catalogs, manifests, diagnostics, or runtime output metadata.
- `missing_pit_field_count`: count of PIT source families without a declared availability/PIT field.
- `missing_logical_key_count`: count of PIT source families without a declared logical key.

The diagnostic is part of the publishable contract surface. Missing metrics or non-integer metric values make the diagnostic invalid.

## Inspect Summary Surface

`inspect-artifacts` must summarize historical universe manifests whose `artifact_id` starts with `universe_` and whose `partitioning == ["as_of_year"]`.

Required historical universe inspect fields:

```text
historical_universe_file_count
historical_universe_count
historical_universe_date_min
historical_universe_date_max
small_file_daily_partition_count
```

`small_file_daily_partition_count` counts artifact paths that match daily convenience outputs under `membership_by_date/as_of_date=...`. Historical canonical runs are expected to keep this at zero.

```


## FILE: contracts\VERIFICATION_CONTRACT.md
```
# Verification Contract

Verify 的目的不是重跑資料流程，而是判斷既有 DataAnalysts runtime 是否可以交付給下游。它應只讀取 `runtime/`、`configs/` 與 contracts，不查 MongoDB，不寫 canonical parquet。

## Verify Command

```powershell
python -m data_analysts.cli verify --root .
python -m data_analysts.cli verify --root . --as-of-date 2026-07-03
```

產出：

```text
runtime/diagnostics/verification_<scope>.json
runtime/jobs/verification_result.json
```

## Scope Checks

必檢：

- DataAnalysts root 可解析。
- 所有 manifest `artifact_paths` 解析後都位於 root 內。
- `runtime/` 外沒有 DataAnalysts output。
- config 不含 plaintext credential。
- CLI contract 中被禁止的 ALF adapter 不存在於 DataAnalysts runtime path。

## Config Checks

必檢：

- `configs/mongodb_sources.json` exists。
- `configs/source_family_profiles.json` exists。
- `configs/universe_specs.json` exists。
- schema version supported。
- enabled family ids unique。
- all enabled families have supported source profile。
- source profile 為 `small_snapshot`、`medium_pit_table` 或 `large_daily_panel`。
- universe selector 欄位都屬於 security panel schema。

## Manifest Checks

每個 manifest 必檢：

- `artifact_id` exists。
- `schema_version` supported。
- `row_count` exists and non-negative。
- `columns` exists and non-empty。
- `artifact_paths` non-empty。
- `date_range` exists when artifact has date semantics。
- `availability_date_range` exists when artifact has PIT semantics。
- `duplicate_count` exists。
- `omitted_row_count` exists。
- `status` is `ready` or `blocked`。

若 `status = "blocked"`，必須包含：

```text
blocked_step
message
next_actions
```

## PIT Checks

必檢：

- date columns parse as ISO or supported source date format。
- duplicate key 在 date normalization 後計算。
- PIT family 不可只用 period date 當 availability date。
- financial/monthly sales 必須使用 availability date 或明確 cutoff policy。
- missing required source_available_date 必須 blocked 或 omitted with diagnostics。

## Raw Family Thresholds

Verification blocks unless:

- `pit_parse_failure_count_total == 0`
- `unresolved_duplicate_count_total == 0`
- `forbidden_source_usage_count_total == 0`
- every manifest artifact path stays under DataAnalysts root
- every selected PIT view has `source_available_date <= decision_date`

Every raw family diagnostic must report:

- `source_row_count`
- `published_row_count`
- `omitted_row_count`
- `pit_null_count`
- `pit_parse_failure_count`
- `duplicate_logical_key_count`
- `resolved_duplicate_count`
- `unresolved_duplicate_count`
- `date_min`
- `date_max`
- `artifact_file_count`

## Adjusted Price Checks

必檢：

- `daily_price_volume` 有 raw price 欄位。
- `daily_price_volume` 有 `adj_factor` 與 adjusted OHLC。
- partial refresh manifest 記錄 prior seed。
- 缺 prior seed 時不能 ready。
- adjusted price event sources 可追溯到 dividend/capital semantic events。

## Corporate Action Checks

必檢：

- `dividend_events` manifest exists when dividend source selected。
- `capital_action_events` manifest exists when capital source selected。
- `corporate_actions` rows can trace source event。
- `stock_price_adjustment` 不得出現在 ledger corporate_actions。

## Security Panel Checks

必檢：

- security panel artifact exists for requested as_of_date。
- required columns all exist。
- `(as_of_date, ticker)` unique。
- `source_max_date <= as_of_date` under configured decision policy。
- leakage columns 不存在。

禁止欄位名稱包含：

```text
future
forward
next
realized
outcome
label_return
```

## Universe Checks

必檢：

- universe membership exists for requested as_of_date。
- membership schema exactly includes required fields or required fields plus explicitly allowed diagnostics fields。
- `(as_of_date, universe_id, ticker)` unique。
- `rank` non-null。
- rank does not duplicate within `(as_of_date, universe_id)`。
- universe diagnostics records candidate count, included count, excluded count。
- selector uses only security panel fields。
- no dependency on external feature store。
- `effective_date > as_of_date` for historical universe membership rows。
- duplicate `(effective_date, universe_id, ticker)` count == 0。
- duplicate `(effective_date, universe_id, rank)` count == 0。
- `small_file_daily_partition_count == 0` on the historical canonical surface。
- top-N universe `row_count <= N` for every `effective_date`。
- top-N universe `row_count == N` when `eligible_count >= N`。

## Result Contract

Ready result：

```json
{
  "status": "ready",
  "checked_at": "2026-07-03T00:00:00Z",
  "scope": "2026-07-03",
  "checks": []
}
```

Blocked result：

```json
{
  "status": "blocked",
  "checked_at": "2026-07-03T00:00:00Z",
  "scope": "2026-07-03",
  "blocked_step": "security_panel",
  "message": "missing required security_panel artifact",
  "next_actions": [
    "run DataAnalysts daily refresh for the requested as_of_date",
    "inspect runtime/manifests/security_panel.json"
  ],
  "checks": []
}
```

Verify 不得把 warning 當 ready。若 downstream 需要的 artifact 不完整，結果必須 blocked。

## Inspect Consistency

Inspect is diagnostic-only and must not relax verify gates. Historical universe inspect summary should mirror the canonical surface:

- summarize only manifests with `artifact_id` prefixed by `universe_` and `partitioning == ["as_of_year"]`
- report `historical_universe_file_count`
- report `historical_universe_count`
- report `historical_universe_date_min`
- report `historical_universe_date_max`
- report `small_file_daily_partition_count`

If `small_file_daily_partition_count > 0`, inspect should surface it as a diagnostics signal; verify still remains the hard fail-closed gate.

## PIT Foundation Thresholds

Verification is blocked unless:

- `forbidden_source_usage_count == 0`
- `missing_pit_field_count == 0`
- `missing_logical_key_count == 0`
- `TEJ.AINVFQ1` references are absent
- `TEJ.APISHRACTW` references are absent

The PIT Foundation diagnostic at `runs/real_all_products/diagnostics/pit_foundation/source_catalog.json` must be readable and must contain all required metrics from `OUTPUT_CONTRACT.md`.

Verification must treat missing PIT Foundation diagnostics as blocked whenever PIT Foundation artifacts or configs are in scope. Threshold breaches are hard failures, not warnings.

```


## FILE: tests\test_historical_universe_pipeline.py
```
import json
from pathlib import Path

import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.inspect import inspect_artifacts
from data_analysts.paths import DataAnalystsRoot
from data_analysts.pipeline import run_pipeline


def _write_configs(root: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = root / "configs"
    target.mkdir(parents=True)
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        payload = json.loads((source / name).read_text(encoding="utf-8"))
        if name == "source_family_profiles.json":
            payload["families"] = [
                {
                    "family_id": "daily_price_volume",
                    "enabled": True,
                    "connection": "apiprcd",
                    "collection_pattern": "{ticker}",
                    "source_profile": "large_daily_panel",
                    "primary_key": ["date", "ticker"],
                    "date_fields": {"source_date": "mdate"},
                    "availability": {"type": "same_day_after_close", "field": "mdate"},
                    "partitioning": ["year"],
                    "pit_policy": "source_date_lagged_to_decision_date",
                    "field_map": {
                        "date": "mdate",
                        "ticker": "coid",
                        "open": "open_d",
                        "high": "high_d",
                        "low": "low_d",
                        "close": "close_d",
                        "volume": "vol",
                        "traded_value": "amt",
                        "market_cap": "mktcap",
                        "data_cutoff_at": "data_cutoff_at",
                    },
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "mdate": "2025-01-02",
                            "open_d": 100,
                            "high_d": 101,
                            "low_d": 99,
                            "close_d": 100,
                            "vol": 10,
                            "amt": 20000000,
                            "mktcap": 500000000,
                            "data_cutoff_at": "2025-01-02T00:00:00Z",
                        },
                        {
                            "coid": "2330",
                            "mdate": "2025-01-03",
                            "open_d": 101,
                            "high_d": 102,
                            "low_d": 100,
                            "close_d": 101,
                            "vol": 11,
                            "amt": 22000000,
                            "mktcap": 510000000,
                            "data_cutoff_at": "2025-01-03T00:00:00Z",
                        },
                    ],
                },
                {
                    "family_id": "security_master",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "APISTOCK",
                    "source_profile": "small_snapshot",
                    "primary_key": ["ticker"],
                    "date_fields": {},
                    "availability": {"type": "snapshot_as_of_cutoff"},
                    "partitioning": ["single_file"],
                    "pit_policy": "snapshot_cutoff",
                    "field_map": {
                        "ticker": "coid",
                        "stock_name": "stk_name",
                        "market": "mkt",
                        "security_type": "stktp_e",
                        "data_cutoff_at": "data_cutoff_at",
                    },
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "stk_name": "TSMC",
                            "mkt": "TWSE",
                            "stktp_e": "common_stock",
                            "data_cutoff_at": "2025-01-01T00:00:00Z",
                        }
                    ],
                },
                {
                    "family_id": "trading_calendar",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "TRADEDAY_TWSE",
                    "source_profile": "small_snapshot",
                    "primary_key": ["date", "market"],
                    "date_fields": {"source_date": "zdate"},
                    "availability": {"type": "source_available_date", "field": "zdate"},
                    "partitioning": ["single_file"],
                    "pit_policy": "source_available_date",
                    "fixture_rows": [
                        {
                            "zdate": "2025-01-02",
                            "mkt": "TWSE",
                            "date_rmk": "",
                            "date": "2025-01-02",
                            "market": "TWSE",
                            "is_trading_day": True,
                        },
                        {
                            "zdate": "2025-01-03",
                            "mkt": "TWSE",
                            "date_rmk": "",
                            "date": "2025-01-03",
                            "market": "TWSE",
                            "is_trading_day": True,
                        },
                        {
                            "zdate": "2025-01-06",
                            "mkt": "TWSE",
                            "date_rmk": "",
                            "date": "2025-01-06",
                            "market": "TWSE",
                            "is_trading_day": True,
                        },
                    ],
                },
            ]
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_pipeline_publishes_historical_universe_memberships_by_year(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)

    result = run_pipeline(
        root,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    assert result["status"] == "ready"

    security_panel_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "security_panel_history"
        / "as_of_year=2025"
        / "part.parquet"
    )
    membership_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "membership_by_year"
        / "as_of_year=2025"
        / "part.parquet"
    )
    assert security_panel_path.exists()
    assert membership_path.exists()
    assert not list(
        (
            tmp_path
            / "runtime"
            / "data_canonical"
            / "derived"
            / "universes"
            / "tw_equity_liquid_top500"
        ).glob("membership_by_date/as_of_date=*/membership.parquet")
    )

    membership_rows = pq.read_table(membership_path).to_pylist()
    assert {(row["as_of_date"], row["effective_date"], row["ticker"]) for row in membership_rows} == {
        ("2025-01-02", "2025-01-03", "2330"),
        ("2025-01-03", "2025-01-06", "2330"),
    }

    diagnostics_path = (
        tmp_path
        / "runtime"
        / "data_canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "diagnostics"
        / "diagnostics.parquet"
    )
    assert diagnostics_path.exists()

    manifest = json.loads(
        (
            tmp_path
            / "runtime"
            / "manifests"
            / "universe_tw_equity_liquid_top500.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["partitioning"] == ["as_of_year"]
    assert manifest["pit_policy"] == "effective_next_trading_day_membership"
    assert manifest["date_range"] == ["2025-01-02", "2025-01-03"]
    assert manifest["source_families"] == ["security_panel_history"]

    security_panel_manifest = json.loads(
        (
            tmp_path
            / "runtime"
            / "manifests"
            / "security_panel_history.json"
        ).read_text(encoding="utf-8")
    )
    assert security_panel_manifest["source_families"] == [
        "daily_price_volume",
        "security_master",
        "trading_calendar",
        "daily_tradability",
    ]


def test_inspect_reports_historical_universe_summary(tmp_path):
    _write_configs(tmp_path)
    root = DataAnalystsRoot.from_path(tmp_path)
    config = load_runtime_config(root)

    result = run_pipeline(
        root,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    assert result["status"] == "ready"

    inspect_result = inspect_artifacts(root)
    assert inspect_result["historical_universe"]["status"] == "ready"
    assert inspect_result["historical_universe"]["small_file_daily_partition_count"] == 0
    assert inspect_result["historical_universe"]["historical_universe_count"] >= 1

```

