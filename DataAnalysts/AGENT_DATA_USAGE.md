# Agent Data Usage Guide

這份文件給 Feature Analysts、Strategists、Backtesters 與其他外部 Agent 使用。目標是讓 Agent 直接使用 DataAnalysts 已治理好的 parquet artifacts，避免重查 MongoDB、重做 PIT、或掃描不必要資料。

## Core Rule

CIO handoff 必須提供 workspace root 與 data store root：

```text
workspace_root: C:\Users\ChastLai\Documents\量化交易積木
data_store_root: DataAnalysts/data_store
read_mode: read_only
```

`data_store_root` 是相對於 CIO workspace root 的相對路徑，不是相對於任一子 agent current working directory。

Agent 只能讀取 `data_store_root` 下的正式資料產品：

```text
canonical/
manifests/
metadata/
diagnostics/
output/
```

禁止：

- 直接查 MongoDB。
- 讀取 `runtime/`、`runs/`、`runs/real_all_products/`。
- 自己重建 adjusted price、event date、universe membership。
- 假設自己的 cwd 是 `DataAnalysts/`。
- 使用未經 CIO handoff 解析的裸 `data_store` 相對路徑，或假設 cwd 可代表跨 agent data store root。
- 在不知道 partition 與 date range 時遞迴掃整個 `data_store_root`。
- 使用缺 manifest、`status != ready`、或 verify blocked 的 artifact。

## Read Order

每個 Agent 讀資料前應依序做：

1. 從 CIO handoff 取得 `workspace_root` 與 `data_store_root`。
2. 解析 `data_store = workspace_root / data_store_root`。
3. 讀 `metadata/data_store_manifest.json`，確認這是正式 data store。
4. 讀需要的 `manifests/<artifact_id>.json`。
5. 檢查 manifest：
   - `status == "ready"`
   - `artifact_paths`
   - `date_range`
   - `availability_date_range`
   - `partitioning`
   - `pit_policy`
   - `row_count`
6. 只讀 manifest 中列出的 parquet paths。
7. 依日期與 universe 先裁切，再進行 feature / strategy / backtest 計算。

## Adjusted OHLC Handoff

CIO 在 Task 8 必須提供以下九欄 metadata；欄名與固定 literal 不得改名：

```text
artifact_id=daily_price_volume
manifest_pointer=manifests/daily_price_volume.json
adjustment_policy_id=event_based_adjusted_ohlc_v1
verification_evidence_pointer=diagnostics/adjusted_ohlc_verification.json
verification_mode=full
verified_partition_count=<candidate.ready_partition_count>
violation_totals=<candidate.violation_totals>
known_limitations=<none 或具體限制清單>
decision=adjusted_ohlc_ready|blocked
```

`decision=blocked` 時不得讀取 adjusted OHLC。`decision=adjusted_ohlc_ready` 時，在執行下方既有 read example 前必須：

1. 讀 exact `manifest_pointer`，確認 `status == "ready"`、policy 等於 metadata，且 `columns` 完整包含 raw OHLC、adjusted OHLC、`adj_factor`、`price_adjustment_status`。
2. 讀 exact formal diagnostics evidence，確認 `status == "ready"`、policy 與 metadata 相同，且 `manifest_fingerprint` 等於該 manifest 的 fingerprint。
3. 確認 evidence `partitions` records 全為 ready、policy 一致，record paths 與 manifest `artifact_paths` 完全對應，且 ready count 與 violation totals 等於 CIO metadata；任一不一致都 fail closed。

Ready manifest 一律代表該 artifact contract/variant 的完整 active surface，不是最近一次 refresh batch。即使磁碟上存在其他 parquet、舊 full-replace version 或 legacy file，只要未列入該 manifest，就不得讀取。

## Common Artifacts

價格與成交：

```text
manifests/daily_price_volume.json
canonical/raw/daily_price_volume/year=YYYY/part.parquet
```

事件：

```text
manifests/dividend_events.json
canonical/derived/events/dividend_events/event_year=YYYY/part.parquet

manifests/capital_action_events.json
canonical/derived/events/capital_action_events/event_year=YYYY/part.parquet
```

歷史可投資股票面板：

```text
manifests/security_panel_history.json
canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet
```

最新 convenience security panel：

```text
manifests/security_panel.json
canonical/derived/security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet
```

Universe：

```text
manifests/universe_<universe_id>.json
canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet
```

最新 convenience universe：

```text
canonical/derived/universes/<universe_id>/membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet
```

## Fast Patterns

### Historical research window

快：

1. 從 manifest 找 `membership_by_year/as_of_year=YYYY/part.parquet`。
2. 只讀研究區間涵蓋的年份。
3. 先用 universe membership 限縮 ticker。
4. 再讀相同年份的 `daily_price_volume`、`daily_chip`、`monthly_sales` 等資料。

慢：

- 先讀所有 price parquet，再自己過濾 universe。
- 掃 `membership_by_date/as_of_date=*` 小檔。
- 不看 manifest，直接遞迴掃 `canonical/`。

### Latest daily workflow

快：

1. 先跑過 `verify --as-of-date YYYY-MM-DD`。
2. 讀 latest convenience universe：
   `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet`
3. 讀 latest security panel：
   `security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet`
4. 只讀需要的年度 parquet。

慢：

- 每日工作讀全歷史 universe。
- 從 raw security master / daily tradability 自己重建 panel。

### Event-aware price workflow

快：

1. 使用 `daily_price_volume` 內已產出的 `adj_factor` 與 `adj_close`。
2. 需要解釋調整來源時，再讀 `dividend_events` 與 `capital_action_events`。

慢：

- 從原始 cash dividend / capital action 自己重算 adjusted price。
- 使用未經 DataAnalysts 發布的 Mongo 欄位。

## PIT and Leakage Rules

- `date` 是市場或資料本身的日期，不必然等於可用日期。
- `source_available_date` 是 Agent 能安全使用該 row 的 PIT 日期。
- `decision_date` 是 selected PIT artifacts 對應的決策日。
- 不得在 `decision_date` 之前使用 `source_available_date` 晚於該日的資料。
- Universe、security panel、adjusted price、events 已由 DataAnalysts 負責 PIT-safe 建構；下游不要回頭用 raw Mongo 覆蓋。

## Universe Usage

正式 historical universe surface 是：

```text
membership_by_year/as_of_year=YYYY/part.parquet
```

`membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` 是 latest convenience output only。它適合每日最新狀態，不適合大量歷史回測掃描。

推薦使用順序：

1. 研究或回測：讀 `membership_by_year`。
2. 當日 production / inspect：讀 `membership_by_date`。
3. 需要所有可交易狀態：搭配 `security_panel_history`。

## Fail-Closed Behavior for Agents

Agent 遇到以下情況必須停止，不得猜測補值：

- 找不到 manifest。
- manifest `status` 不是 `ready`。
- `artifact_paths` 指向不存在的 parquet。
- 查詢日期超出 `date_range` 或 `availability_date_range`。
- 需要 PIT-safe 資料但 artifact 沒有 `pit_policy`。
- `verify` 結果為 blocked。
- Universe 或 security panel 缺指定日期。

## Minimal Python Read Pattern

```python
import json
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds

def read_daily_price_volume(
    workspace_root: Path,
    data_store_root: str,
    start_date: str,
    end_date: str,
):
    data_store = workspace_root / data_store_root
    manifest_path = data_store / "manifests" / "daily_price_volume.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest["status"] != "ready":
        raise RuntimeError("daily_price_volume is not ready")

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be <= end_date")

    years = {f"year={year}" for year in range(start.year, end.year + 1)}
    paths = [
        data_store / path
        for path in manifest["artifact_paths"]
        if any(year in path for year in years)
    ]

    return ds.dataset(paths, format="parquet").to_table(
        columns=[
            "date", "ticker", "open", "high", "low", "close",
            "adj_open", "adj_high", "adj_low", "adj_close",
            "adj_factor", "price_adjustment_status", "volume",
        ],
        filter=(ds.field("date") >= start_date) & (ds.field("date") <= end_date),
    )

workspace_root = Path(r"C:\Users\ChastLai\Documents\量化交易積木")
table = read_daily_price_volume(
    workspace_root=workspace_root,
    data_store_root="DataAnalysts/data_store",
    start_date="2025-01-01",
    end_date="2026-07-02",
)
```

原則是由 CIO handoff 提供 `data_store_root`，先用 manifest 縮小 paths，再用 parquet filter / columns 做第二層裁切。
