STATUS: DONE

Files changed:
- README.md
- contracts/CLI_CONTRACT.md
- contracts/OUTPUT_CONTRACT.md
- contracts/VERIFICATION_CONTRACT.md
- contracts/CONFIG_CONTRACT.md

Commands run:
- apply_patch on README.md
- apply_patch on contracts/CLI_CONTRACT.md
- apply_patch on contracts/OUTPUT_CONTRACT.md
- apply_patch on contracts/VERIFICATION_CONTRACT.md
- apply_patch on contracts/CONFIG_CONTRACT.md
- `rg -n -- "--root|runtime/data_canonical|runs/real_all_products|runtime/manifests|runtime/jobs|runtime/diagnostics" README.md contracts`
- `rg -n "project_root|data_store|metadata|config_snapshot" README.md contracts`

Results:
- Replaced formal README command examples with `--project-root` / `--data-store` language and default commands without `--root`.
- Moved `--root` guidance into a short legacy warning section.
- Updated CLI contract to remove `--root` as a formal parameter, add `--project-root` and `--data-store`, and specify removed-argument rejection.
- Updated output contract to formalize `data_store/canonical`, `manifests`, `metadata`, `diagnostics`, `jobs`, and `output` paths.
- Added metadata manifest and config snapshot paths to the output contract.
- Updated verification contract to formalize data-store-only reads and the required quantitative metrics.
- Updated config contract to load configs from `project_root/configs` and store config snapshots under `data_store/metadata/config_snapshot`.
- Verification grep found `--root` only in legacy or removed-argument sections, and found `runs/real_all_products/` only in the legacy layout warning section.
- Verification grep confirmed `project_root`, `data_store`, `metadata`, and `config_snapshot` are present across the updated formal contracts.

Concerns:
- Documentation still references command names and file names that source code may not yet implement under the formalized contract; this task did not modify code or tests.
