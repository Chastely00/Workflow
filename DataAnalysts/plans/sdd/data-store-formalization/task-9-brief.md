# Task 9 Brief: Bounded Real Mongo Smoke and Final Evidence

## Context

After formalization tasks are implemented and reviewed, run a bounded real Mongo smoke to prove the formal CLI can produce `data_store` artifacts without creating formal `runtime/`, `runs/`, or `real_all_products/` output. This task records final evidence and prepares the branch for whole-change review.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Do not introduce an ALF runtime adapter.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Artifact path validation must use path segments, not substring matching.
- Verify and inspect must not read legacy `runtime/` or `runs/` as formal artifacts.
- Legacy `runtime/` and `runs/` must not be auto-migrated or auto-deleted.

## Files

- Modify: `plans/sdd/data-store-formalization/progress.md`
- Runtime output may be written under: `data_store/`
- Write report: `plans/sdd/data-store-formalization/task-9-report.md`

## Required Commands

Run from:

```powershell
cd C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
```

Use the local environment already documented in `README.md`.

## Work

1. Run bounded smoke:

```powershell
$env:DATA_ANALYSTS_MONGODB_URI = "mongodb://localhost:27017/"
python -m data_analysts.cli run-backfill --project-root . --data-store .\data_store --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31
```

Expected:

```text
ready
```

2. Run verify:

```powershell
python -m data_analysts.cli verify --project-root . --data-store .\data_store
```

Expected:

```text
ready
```

3. Run inspect:

```powershell
python -m data_analysts.cli inspect-artifacts --project-root . --data-store .\data_store
```

Expected summary fields:

```text
status == ready
legacy_layout_detected may be true if old runtime/runs already exist
forbidden_path_segment_count == 0
absolute_artifact_path_count == 0
artifact_path_escape_count == 0
config_snapshot_hash_mismatch_count == 0
```

4. Run filesystem guard:

```powershell
@'
from pathlib import Path
project = Path(".")
store = Path("data_store")
print({
    "canonical_exists": (store / "canonical").exists(),
    "manifests_exists": (store / "manifests").exists(),
    "metadata_exists": (store / "metadata" / "data_store_manifest.json").exists(),
    "jobs_exists": (store / "jobs").exists(),
    "formal_runtime_under_store": (store / "runtime").exists(),
    "formal_runs_under_store": (store / "runs").exists(),
})
'@ | python -
```

Expected:

```text
canonical_exists == True
manifests_exists == True
metadata_exists == True
jobs_exists == True
formal_runtime_under_store == False
formal_runs_under_store == False
```

## Final Review Scope

After smoke evidence is recorded, the controller will dispatch a read-only final reviewer for the whole formalization. Do not self-approve the whole change from this task alone.

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-9-report.md
```

Report format:

```text
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED

Files changed:
- ...

Commands run:
- ...

Results:
- ...

Concerns:
- ...
```
