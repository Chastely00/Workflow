# Task 8 Brief: Full Test Migration and Legacy Layout Guard

## Context

Previous tasks migrate the formal DataAnalysts runtime surface from legacy `runtime/`, `runs/`, and `real_all_products/` vocabulary to `data_store`. This task is the cleanup gate: source and tests should no longer depend on the legacy layout except in explicit legacy-warning or rejection tests.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Remove `--root`; do not keep it as a compatibility alias.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Artifact path validation must use path segments, not substring matching.
- Verify and inspect must not read legacy `runtime/` or `runs/` as formal artifacts.
- Legacy `runtime/` and `runs/` must not be auto-migrated or auto-deleted.
- Use TDD for behavior changes.

## Files

- Modify all tests still importing `DataAnalystsRoot`.
- Modify all source/tests still expecting `runtime/`, `runs/`, or `runs/real_all_products`.
- Write report: `plans/sdd/data-store-formalization/task-8-report.md`

## Required Interfaces

Use the formal interfaces from prior tasks:

- `DataAnalystsContext.from_paths(project_root=".", data_store=None)`
- `context.store_path(...)`
- `context.artifact_path(...)`
- CLI without `--root`

## Work

1. Run a legacy-reference scan:

```powershell
rg -n "DataAnalystsRoot|runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests
```

2. Update remaining source/tests:

- `DataAnalystsRoot.from_path(tmp_path)` -> `DataAnalystsContext.from_paths(tmp_path)`.
- `runtime/data_canonical` -> `data_store/canonical`.
- `runtime/manifests` -> `data_store/manifests`.
- `runtime/jobs` -> `data_store/jobs`.
- `runs/real_all_products/diagnostics` -> `data_store/diagnostics`.

3. Keep legacy strings only where they are testing or documenting explicit rejection/warning behavior.

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest -q
```

Then run:

```powershell
rg -n "runtime/data_canonical|runtime/manifests|runtime/jobs|runs/real_all_products|--root" src tests README.md contracts
```

Expected:

- Full tests pass.
- No source matches for formal legacy paths.
- Test/doc matches only in legacy warning or explicit rejection tests.

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-8-report.md
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
