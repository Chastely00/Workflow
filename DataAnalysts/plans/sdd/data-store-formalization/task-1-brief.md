# Task 1 Brief: Contract and Documentation Formalization

## Context

DataAnalysts is being formalized as a portable data product. Project code/configs must live under `project_root`; all data products must live under `data_store`.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Do not introduce an ALF runtime adapter.
- Remove `--root`; do not keep it as a compatibility alias.
- Formal output layout must not contain `runtime/`, `runs/`, or `real_all_products/`.
- `data_store` is the default formal storage directory.
- Artifact path validation must use path segments, not substring matching.
- Verify and inspect must not read legacy `runtime/` or `runs/` as formal artifacts.
- Legacy `runtime/` and `runs/` must not be auto-migrated or auto-deleted.

## Files

- Modify: `README.md`
- Modify: `contracts/CLI_CONTRACT.md`
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`
- Modify: `contracts/CONFIG_CONTRACT.md`
- Reference: `plans/2026-07-08-data-store-formalization-spec.md`

## Requirements

1. Update README formal command examples to use:

```powershell
cd C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts
.\.venv\Scripts\Activate.ps1

python -m data_analysts.cli run-full-history
python -m data_analysts.cli verify
python -m data_analysts.cli inspect-artifacts
```

2. Add explicit equivalent form:

```powershell
python -m data_analysts.cli run-full-history --project-root . --data-store .\data_store
```

3. Move every `--root` example to a short legacy warning section:

```text
--root has been removed. Use --project-root and --data-store.
```

4. Update `contracts/CLI_CONTRACT.md`:

- remove `--root` as formal parameter
- add `--project-root`
- add `--data-store`
- add removed-argument rejection rule

5. Update `contracts/OUTPUT_CONTRACT.md`:

- replace formal `runtime/data_canonical/...` paths with `canonical/...`
- add `metadata/`
- include `data_store/metadata/data_store_manifest.json`
- include `data_store/metadata/config_snapshot/*.json`

6. Update `contracts/VERIFICATION_CONTRACT.md`:

- add quantitative metrics:
  - `artifact_path_count`
  - `absolute_artifact_path_count`
  - `artifact_path_escape_count`
  - `forbidden_path_segment_count`
  - `manifest_count`
  - `required_manifest_missing_count`
  - `config_snapshot_file_count`
  - `config_snapshot_hash_mismatch_count`
  - `legacy_project_runtime_exists`
  - `legacy_project_runs_exists`
- clarify `legacy_project_runtime_exists` and `legacy_project_runs_exists` are diagnostics only, not blockers by themselves.

7. Update `contracts/CONFIG_CONTRACT.md`:

- configs are loaded from `project_root/configs`
- config snapshots are stored under `data_store/metadata/config_snapshot`
- snapshots must not contain secrets or remote MongoDB URI.

## Verification

Run:

```powershell
rg -n "--root|runtime/data_canonical|runs/real_all_products|runtime/manifests|runtime/jobs|runtime/diagnostics" README.md contracts
```

Expected:

- Matches may exist only in current-problem, old-path, migration, removed-argument, or legacy warning sections.
- No formal command example may use `--root`.

Also run:

```powershell
rg -n "project_root|data_store|metadata|config_snapshot" README.md contracts
```

Expected:

- Relevant contracts mention the new formal terms.

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-1-report.md
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
