# Task 6 Brief: CLI Migration

## Context

Formal CLI must remove `--root` and use `--project-root` plus `--data-store`. Default execution from DataAnalysts project root writes to `./data_store`.

Global constraints:

- Work only under `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Do not modify ALF main-flow modules.
- Remove `--root`; do not keep it as a compatibility alias.
- `data_store` is the default formal storage directory.
- Use TDD: add failing tests first, then implementation.

## Files

- Modify: `src/data_analysts/cli.py`
- Create: `tests/test_data_store_cli.py`
- Write report: `plans/sdd/data-store-formalization/task-6-report.md`

## Required Behavior

Every subcommand must support:

```text
--project-root
--data-store
```

Defaults:

```text
--project-root .
--data-store None
```

`DataAnalystsContext.from_paths(args.project_root, args.data_store)` resolves default data store to `<project_root>/data_store`.

`--root` must fail closed with exact message:

```text
--root has been removed. Use --project-root and --data-store.
```

## Tests To Add First

Create `tests/test_data_store_cli.py`:

```python
from data_analysts.cli import build_parser, main


def test_main_rejects_removed_root_argument(capsys):
    result = main(["verify", "--root", "."])

    captured = capsys.readouterr()
    assert result == 1
    assert "--root has been removed. Use --project-root and --data-store." in captured.err


def test_parser_accepts_project_root_and_data_store(tmp_path):
    parser = build_parser()

    args = parser.parse_args([
        "verify",
        "--project-root",
        str(tmp_path),
        "--data-store",
        str(tmp_path / "store"),
    ])

    assert args.project_root == str(tmp_path)
    assert args.data_store == str(tmp_path / "store")


def test_default_project_root_and_data_store_arguments():
    parser = build_parser()

    args = parser.parse_args(["inspect-artifacts"])

    assert args.project_root == "."
    assert args.data_store is None
```

## Implementation Requirements

- Remove `_add_root`.
- Add `_add_project_and_store(parser)`.
- Every subcommand calls `_add_project_and_store`.
- Before parsing, reject `--root` from explicit `argv` or `sys.argv[1:]`.
- Construct:

```python
context = DataAnalystsContext.from_paths(args.project_root, args.data_store)
```

- Pass `context` to `load_runtime_config`, `run_pipeline`, `verify_runtime`, `inspect_artifacts`, and `_write_blocked_pipeline_result`.

## Verification

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_data_store_cli.py -q
```

Expected:

```text
3 passed
```

Do not run full pipeline in this task.

## Report

Write report to:

```text
plans/sdd/data-store-formalization/task-6-report.md
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
