import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pymongo
import pytest

import data_analysts.cli as cli_module
import data_analysts.dataset_publication as publication_module
from data_analysts.adjusted_ohlc import ADJUSTMENT_POLICY_ID
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.cli import build_parser, main
from data_analysts.dataset_publication import publish_dataset
from data_analysts.paths import DataAnalystsContext


CLI_CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "CLI_CONTRACT.md"
EXIT_MATRIX_HEADING = "## Canonical CLI Exit Matrix"


def _exit_matrix_rows() -> list[dict[str, str]]:
    text = CLI_CONTRACT.read_text(encoding="utf-8")
    section = re.search(
        rf"^{re.escape(EXIT_MATRIX_HEADING)}\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert section is not None
    lines = [
        line.strip()
        for line in section.group("body").splitlines()
        if line.strip().startswith("|")
    ]
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    return [
        dict(
            zip(
                headers,
                [cell.strip() for cell in line.strip("|").split("|")],
                strict=True,
            )
        )
        for line in lines[2:]
    ]


def _matrix_commands(row: dict[str, str]) -> set[str]:
    return {command.strip() for command in row["commands"].split("<br>")}


def test_main_rejects_removed_root_argument(capsys):
    result = main(["verify", "--root", "."])

    captured = capsys.readouterr()
    assert result == 1
    assert "--root has been removed. Use --project-root and --data-store." in captured.err


def test_main_rejects_removed_root_equals_argument(capsys):
    result = main(["verify", "--root=.", "--project-root", "."])

    captured = capsys.readouterr()
    assert result == 1
    assert "--root has been removed. Use --project-root and --data-store." in captured.err


def test_parser_accepts_project_root_and_data_store(tmp_path):
    parser = build_parser()

    args = parser.parse_args(
        [
            "verify",
            "--project-root",
            str(tmp_path),
            "--data-store",
            str(tmp_path / "store"),
        ]
    )

    assert args.project_root == str(tmp_path)
    assert args.data_store == str(tmp_path / "store")


def test_default_project_root_and_data_store_arguments():
    parser = build_parser()

    args = parser.parse_args(["inspect-artifacts"])

    assert args.project_root == "."
    assert args.data_store is None


def test_cli_exit_matrix_structurally_covers_data_runtime_commands():
    rows = _exit_matrix_rows()
    required = {"verify", "repair-metadata", "inspect-artifacts"}

    assert rows
    assert all("commands" in row for row in rows)
    for command in required:
        command_rows = [row for row in rows if command in _matrix_commands(row)]
        assert {row["stage"] for row in command_rows} >= {"parser", "handler"}
        assert {row["exit_code"] for row in command_rows} >= {"0", "1", "2"}


def test_main_inspect_returns_blocked_audit_config_issue_when_config_is_unavailable(
    tmp_path, capsys
):
    result = main(["inspect-artifacts", "--project-root", str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 1
    assert payload["status"] == "blocked"
    assert payload["artifact_audit"]["issues"][0]["check"] == "audit_config"


def test_parser_accepts_run_daily_without_as_of_date():
    parser = build_parser()

    args = parser.parse_args(["run-daily"])

    assert args.command == "run-daily"
    assert args.as_of_date is None


def test_parser_accepts_audit_store_job_output():
    args = build_parser().parse_args(
        ["audit-store", "--output", "jobs/pre_repair_audit.json"]
    )

    assert args.command == "audit-store"
    assert args.output == "jobs/pre_repair_audit.json"


@pytest.mark.parametrize(
    "output",
    ["pre_repair_audit.json", "jobs/pre_repair_audit.json"],
)
def test_main_audit_store_normalizes_supported_job_output_forms(
    tmp_path, monkeypatch, output
):
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(family_ids=set(), artifact_contracts={}),
    )

    result = main(
        [
            "audit-store",
            "--project-root",
            str(tmp_path),
            "--output",
            output,
        ]
    )

    assert result == 0
    assert (
        tmp_path / "data_store" / "jobs" / "pre_repair_audit.json"
    ).exists()
    assert not (tmp_path / "data_store" / "jobs" / "jobs").exists()
    assert not (tmp_path / "data_store" / "canonical").exists()


@pytest.mark.parametrize(
    "output",
    [
        "../outside.json",
        "jobs/../outside.json",
        "/outside.json",
        "C:outside.json",
        r"C:\outside.json",
        r"\\server\share\outside.json",
        "jobs/C:outside.json",
    ],
)
def test_main_audit_store_rejects_output_escape(
    tmp_path, monkeypatch, capsys, output
):
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(family_ids=set(), artifact_contracts={}),
    )

    result = main(
        ["audit-store", "--project-root", str(tmp_path), "--output", output]
    )

    assert result == 1
    assert "safe path relative" in capsys.readouterr().err
    assert not (tmp_path / "outside.json").exists()


@pytest.mark.parametrize(
    "output",
    ["audits/store.json", "canonical/store.json", "jobs/jobs/store.json"],
)
def test_main_audit_store_rejects_non_jobs_subtree(
    tmp_path, monkeypatch, capsys, output
):
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(family_ids=set(), artifact_contracts={}),
    )

    result = main(
        ["audit-store", "--project-root", str(tmp_path), "--output", output]
    )

    assert result == 1
    assert "data_store/jobs" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--start-date", "--end-date"])
def test_run_full_history_parser_rejects_date_boundaries(flag):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run-full-history", flag, "2026-07-08"])


def test_main_run_daily_processes_planned_dates(tmp_path, monkeypatch, capsys):
    planned_dates = ["2026-07-07", "2026-07-08"]
    pipeline_calls = []
    success_calls = []

    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(
            family_ids=set(), artifact_contracts={"trading_calendar": object()}
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "plan_daily_refresh_dates",
        lambda context, contract, **kwargs: planned_dates,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "audit_store",
        lambda context, contracts: {"status": "ready", "artifacts": {}},
    )

    def fake_run_pipeline(context, config, **kwargs):
        pipeline_calls.append(kwargs)
        return {"status": "ready", "as_of_date": kwargs["as_of_date"], "families": []}

    def fake_write_success(data_store, *, as_of_date, result):
        success_calls.append((str(data_store), as_of_date, result["status"]))

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        cli_module,
        "verify_runtime",
        lambda context, as_of_date=None, **kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(cli_module, "write_daily_refresh_success", fake_write_success, raising=False)

    result = main(["run-daily", "--project-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert "ready" in captured.out
    assert [call["as_of_date"] for call in pipeline_calls] == planned_dates
    assert all(call["publish_ready_state"] is False for call in pipeline_calls)
    assert [call[1] for call in success_calls] == planned_dates


def test_main_run_daily_blocks_ready_state_when_fresh_verify_fails(tmp_path, monkeypatch, capsys):
    success_calls = []
    blocked_calls = []
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(
            family_ids=set(), artifact_contracts={"trading_calendar": object()}
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "plan_daily_refresh_dates",
        lambda context, contract, **kwargs: ["2026-07-08"],
    )
    monkeypatch.setattr(
        cli_module,
        "audit_store",
        lambda context, contracts: {"status": "ready", "artifacts": {}},
    )
    monkeypatch.setattr(
        cli_module,
        "run_pipeline",
        lambda *args, **kwargs: {"status": "ready", "as_of_date": "2026-07-08"},
    )
    monkeypatch.setattr(
        cli_module,
        "verify_runtime",
        lambda context, as_of_date=None, **kwargs: {
            "status": "blocked",
            "message": "daily_price_volume row_count mismatch",
        },
    )
    monkeypatch.setattr(
        cli_module,
        "write_daily_refresh_success",
        lambda *args, **kwargs: success_calls.append(kwargs),
    )
    monkeypatch.setattr(
        cli_module,
        "write_daily_refresh_blocked",
        lambda *args, **kwargs: blocked_calls.append(kwargs),
    )

    result = main(["run-daily", "--project-root", str(tmp_path), "--as-of-date", "2026-07-08"])

    assert result == 1
    assert success_calls == []
    assert blocked_calls[0]["as_of_date"] == "2026-07-08"
    assert "daily_price_volume row_count mismatch" in capsys.readouterr().err


def test_cli_verification_failure_rolls_back_complete_formal_store_and_blocks_current_run(
    tmp_path, monkeypatch
):
    context_root = tmp_path
    store = context_root / "data_store"
    baseline_files = {
        "canonical/raw/sample/year=2025/part.parquet": b"old parquet bytes",
        "manifests/sample.json": b'{"artifact_id":"sample","status":"ready"}',
        "metadata/data_store_manifest.json": b'{"version":"old"}',
        "diagnostics/sample.json": b'{"status":"old"}',
    }
    for relative, payload in baseline_files.items():
        path = store / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    baseline_tree = _formal_tree(store)
    baseline_directories = _formal_directories(store)

    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(family_ids=set(), artifact_contracts={}),
    )

    def fake_pipeline(context, config, **kwargs):
        assert kwargs["publish_ready_state"] is False
        old_path = context.store_path("canonical", "raw", "sample", "year=2025", "part.parquet")
        old_staging = old_path.with_name(".part.parquet.tmp")
        old_staging.write_bytes(b"changed")
        os.replace(old_staging, old_path)
        new_path = context.store_path("canonical", "raw", "sample", "year=2026", "part.parquet")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(b"new partition")
        cli_module.atomic_write_text(
            context.store_path("manifests", "sample.json"), '{"status":"changed"}'
        )
        cli_module.atomic_write_text(
            context.store_path("metadata", "data_store_manifest.json"), '{"version":"new"}'
        )
        cli_module.atomic_write_text(
            context.store_path("diagnostics", "sample.json"), '{"status":"new"}'
        )
        jobs = context.store_path("jobs")
        jobs.mkdir(parents=True, exist_ok=True)
        context.store_path("jobs", "pipeline_result.json").write_text('{"status":"verifying"}')
        context.store_path("jobs", "current_run.json").write_text('{"status":"running"}')
        return {"status": "ready", "families": []}

    def blocked_verify(context, *args, **kwargs):
        assert json.loads(context.store_path("jobs", "pipeline_result.json").read_text())["status"] != "ready"
        assert json.loads(context.store_path("jobs", "current_run.json").read_text())["status"] != "ready"
        return {"status": "blocked", "message": "sample row_count mismatch"}

    monkeypatch.setattr(cli_module, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(cli_module, "verify_runtime", blocked_verify)

    result = main(["run-full-history", "--project-root", str(context_root)])

    assert result == 1
    assert _formal_tree(store) == baseline_tree
    assert _formal_directories(store) == baseline_directories
    current_run = json.loads((store / "jobs" / "current_run.json").read_text())
    assert current_run["status"] == "blocked"
    assert "sample row_count mismatch" in current_run["message"]


def test_cli_pipeline_failure_rolls_back_legacy_manifest_migration(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = ArtifactContract(
        contract_key="universe_u:exact_date", artifact_id="universe_u",
        variant="exact_date", layer="derived",
        base_path="canonical/derived/universes/u/membership_by_date",
        file_name="membership.parquet",
        required_columns=(
            "as_of_date", "universe_id", "ticker", "rank", "data_cutoff_at",
        ),
        logical_key=("as_of_date", "universe_id", "ticker"),
        publication_mode="snapshot_by_value", partition_name="as_of_date",
        partition_field="as_of_date", date_field="as_of_date",
        availability_field="as_of_date", pit_policy="decision_date_membership",
        source_families=("security_panel",),
    )
    result = publish_dataset(context, contract, [{
        "as_of_date": "2026-07-08", "universe_id": "u", "ticker": "2330",
        "rank": 1, "data_cutoff_at": "2026-07-08T12:00:00Z",
    }], "daily")
    legacy_payload = dict(result.manifest)
    legacy_payload.pop("contract_key")
    legacy_payload.pop("variant")
    legacy = context.store_path("manifests", "universe_u.json")
    legacy_bytes = json.dumps(legacy_payload, sort_keys=True).encode()
    legacy.write_bytes(legacy_bytes)
    result.manifest_path.unlink()
    config = SimpleNamespace(
        family_ids=set(), artifact_contracts={contract.contract_key: contract}
    )
    monkeypatch.setattr(cli_module, "load_runtime_config", lambda context: config)

    def migrate_then_fail(context, config, **kwargs):
        publication_module.migrate_legacy_variant_manifests(
            context, config.artifact_contracts.values()
        )
        assert result.manifest_path.exists()
        assert not legacy.exists()
        raise ValueError("synthetic post-migration pipeline failure")

    monkeypatch.setattr(cli_module, "run_pipeline", migrate_then_fail)

    exit_code = main(["run-full-history", "--project-root", str(tmp_path)])

    assert exit_code == 1
    assert legacy.read_bytes() == legacy_bytes
    assert not result.manifest_path.exists()


def _formal_tree(store: Path) -> dict[str, bytes]:
    if not store.exists():
        return {}
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in sorted(store.rglob("*"))
        if path.is_file() and "jobs" not in path.relative_to(store).parts
    }


def _formal_directories(store: Path) -> set[str]:
    if not store.exists():
        return set()
    return {
        path.relative_to(store).as_posix()
        for path in sorted(store.rglob("*"))
        if path.is_dir() and "jobs" not in path.relative_to(store).parts
    }


def test_main_run_daily_noops_when_planner_returns_no_dates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(
            family_ids=set(), artifact_contracts={"trading_calendar": object()}
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "plan_daily_refresh_dates",
        lambda context, contract, **kwargs: [],
        raising=False,
    )

    result = main(["run-daily", "--project-root", str(tmp_path), "--to-date", "2026-07-08"])

    captured = capsys.readouterr()
    assert result == 0
    assert "no-op" in captured.out


def test_parser_accepts_repair_metadata_command():
    args = build_parser().parse_args(["repair-metadata", "--project-root", "DataAnalysts"])

    assert args.command == "repair-metadata"
    assert args.project_root == "DataAnalysts"
    assert args.data_store is None


def test_repair_metadata_command_runs_path_only_migration(monkeypatch, capsys):
    calls = []

    def fake_repair(context):
        calls.append((context.project_root.name, context.data_store.name))
        return {"status": "ready"}

    def unexpected_call(*args, **kwargs):
        raise AssertionError("repair-metadata must not load config, publish, or run the pipeline")

    monkeypatch.setattr("data_analysts.cli.repair_data_store_metadata_paths", fake_repair)
    monkeypatch.setattr("data_analysts.cli.load_runtime_config", unexpected_call)
    monkeypatch.setattr("data_analysts.cli.publish_data_store_metadata", unexpected_call)
    monkeypatch.setattr("data_analysts.cli.run_pipeline", unexpected_call)

    exit_code = main(["repair-metadata", "--project-root", "."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "ready"
    assert calls


def test_repair_manifest_fingerprints_requires_at_least_one_artifact_id():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["repair-manifest-fingerprints"])


def test_repair_manifest_fingerprints_accepts_repeatable_artifact_ids():
    args = build_parser().parse_args(
        [
            "repair-manifest-fingerprints",
            "--artifact-id",
            "daily_price_volume",
            "--artifact-id",
            "daily_chip",
        ]
    )

    assert args.artifact_ids == ["daily_price_volume", "daily_chip"]


def test_repair_manifest_fingerprints_command_does_not_load_config_or_pipeline(
    monkeypatch,
    capsys,
):
    calls = []
    monkeypatch.setattr(
        cli_module,
        "repair_manifest_fingerprints",
        lambda context, artifact_ids: calls.append(tuple(artifact_ids)),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("config loaded")),
    )
    monkeypatch.setattr(
        cli_module,
        "run_pipeline",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline run")),
    )

    exit_code = main(
        ["repair-manifest-fingerprints", "--artifact-id", "daily_price_volume"]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "ready"
    assert calls == [("daily_price_volume",)]


def _certification_fixture(tmp_path, *, adj_close=10.0):
    context = DataAnalystsContext.from_paths(tmp_path)
    artifact_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    row = {
        "date": "2026-01-02",
        "ticker": "2330",
        "open": 10.0,
        "high": 10.0,
        "low": 10.0,
        "close": 10.0,
        "adj_factor": 1.0,
        "adj_open": 10.0,
        "adj_high": 10.0,
        "adj_low": 10.0,
        "adj_close": adj_close,
        "price_adjustment_status": "adjusted_close_ready",
    }
    target = context.artifact_path(artifact_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([row]), target)
    manifest = {
        "artifact_id": "daily_price_volume",
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": [artifact_path],
        "columns": list(row),
        "row_count": 1,
        "date_range": ["2026-01-02", "2026-01-02"],
        "created_at": "2026-07-16T00:00:00Z",
    }
    manifest_path = context.store_path("manifests", "daily_price_volume.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return context, target, manifest_path


def _reject_pipeline_paths(monkeypatch):
    monkeypatch.setattr(
        pymongo,
        "MongoClient",
        lambda *args, **kwargs: pytest.fail(
            "certification must not instantiate a MongoDB client"
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "run_pipeline",
        lambda *args, **kwargs: pytest.fail("certification must not run the pipeline"),
    )
    def contract(artifact_id, base_path, partition_field):
        return ArtifactContract(
            contract_key=artifact_id,
            artifact_id=artifact_id,
            variant="test",
            layer="raw" if artifact_id == "daily_price_volume" else "derived",
            base_path=base_path,
            file_name="part.parquet",
            required_columns=(partition_field, "ticker"),
            logical_key=(partition_field, "ticker"),
            publication_mode="partition_upsert",
            partition_name="year" if artifact_id == "daily_price_volume" else "event_year",
            partition_field=partition_field,
            date_field=partition_field,
            availability_field=partition_field,
            pit_policy="test",
            source_families=(artifact_id,),
            allow_empty=artifact_id != "daily_price_volume",
        )
    contracts = {
        "daily_price_volume": contract(
            "daily_price_volume", "canonical/raw/daily_price_volume", "date"
        ),
        "dividend_events": contract(
            "dividend_events", "canonical/derived/events/dividend_events", "event_date"
        ),
        "capital_action_events": contract(
            "capital_action_events",
            "canonical/derived/events/capital_action_events",
            "event_date",
        ),
    }
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda *args, **kwargs: SimpleNamespace(artifact_contracts=contracts),
    )


def _canonical_tree_snapshot(context):
    canonical_root = context.store_path("canonical")
    if not canonical_root.exists():
        return {}
    return {
        path.relative_to(canonical_root).as_posix(): path.read_bytes()
        for path in sorted(canonical_root.rglob("*"))
        if path.is_file()
    }


def test_parser_accepts_full_adjusted_ohlc_audit():
    args = build_parser().parse_args(["certify-adjusted-ohlc", "--mode", "full"])

    assert args.command == "certify-adjusted-ohlc"
    assert args.mode == "full"
    assert args.publish_candidate is False


def test_parser_accepts_adjusted_ohlc_candidate_promotion():
    args = build_parser().parse_args(
        ["certify-adjusted-ohlc", "--publish-candidate"]
    )

    assert args.mode is None
    assert args.publish_candidate is True


@pytest.mark.parametrize(
    "argv",
    [
        ["certify-adjusted-ohlc"],
        ["certify-adjusted-ohlc", "--mode", "full", "--publish-candidate"],
    ],
)
def test_certification_parser_requires_exactly_one_mode(argv):
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_full_certification_writes_only_ready_candidate(
    tmp_path, monkeypatch, capsys
):
    context, partition_path, manifest_path = _certification_fixture(tmp_path)
    canonical_before = _canonical_tree_snapshot(context)
    manifest_before = manifest_path.read_bytes()
    _reject_pipeline_paths(monkeypatch)

    result = main(
        [
            "certify-adjusted-ohlc",
            "--project-root",
            str(tmp_path),
            "--mode",
            "full",
        ]
    )

    candidate = json.loads(
        context.store_path("jobs", "adjusted_ohlc_audit_candidate.json").read_text(
            encoding="utf-8"
        )
    )
    assert result == 0
    assert "ready" in capsys.readouterr().out
    assert candidate["status"] == "ready"
    assert _canonical_tree_snapshot(context) == canonical_before
    assert manifest_path.read_bytes() == manifest_before
    assert not context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    ).exists()


def test_main_run_daily_planning_failure_has_no_attempt_identity(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: SimpleNamespace(
            family_ids=set(), artifact_contracts={"trading_calendar": object()}
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "plan_daily_refresh_dates",
        lambda context, contract, **kwargs: (_ for _ in ()).throw(
            ValueError("synthetic planning failure")
        ),
    )

    result = main(["run-daily", "--project-root", str(tmp_path)])

    assert result == 1
    state = json.loads(
        (tmp_path / "data_store" / "jobs" / "daily_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["last_attempted_as_of_date"] is None
    assert state["last_ready_as_of_date"] is None
    assert not (tmp_path / "data_store" / "jobs" / "daily_results").exists()
    assert "synthetic planning failure" in capsys.readouterr().err


def test_blocked_full_certification_returns_one_and_writes_only_candidate(
    tmp_path, monkeypatch, capsys
):
    context, partition_path, manifest_path = _certification_fixture(
        tmp_path, adj_close=11.0
    )
    canonical_before = _canonical_tree_snapshot(context)
    manifest_before = manifest_path.read_bytes()
    _reject_pipeline_paths(monkeypatch)

    result = main(
        [
            "certify-adjusted-ohlc",
            "--project-root",
            str(tmp_path),
            "--mode",
            "full",
        ]
    )

    candidate = json.loads(
        context.store_path("jobs", "adjusted_ohlc_audit_candidate.json").read_text(
            encoding="utf-8"
        )
    )
    assert result == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "blocked"
    assert captured.err == ""
    assert candidate["status"] == "blocked"
    publish_result = main(
        [
            "certify-adjusted-ohlc",
            "--project-root",
            str(tmp_path),
            "--publish-candidate",
        ]
    )
    assert publish_result == 1
    publish_output = capsys.readouterr()
    assert publish_output.out == ""
    assert "candidate evidence is not ready" in publish_output.err
    assert _canonical_tree_snapshot(context) == canonical_before
    assert manifest_path.read_bytes() == manifest_before
    assert not context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    ).exists()


def test_candidate_promotion_updates_only_manifest_and_formal_evidence(
    tmp_path, monkeypatch
):
    context, partition_path, manifest_path = _certification_fixture(tmp_path)
    canonical_before = _canonical_tree_snapshot(context)
    _reject_pipeline_paths(monkeypatch)
    full_args = [
        "certify-adjusted-ohlc",
        "--project-root",
        str(tmp_path),
        "--mode",
        "full",
    ]
    assert main(full_args) == 0

    result = main(
        [
            "certify-adjusted-ohlc",
            "--project-root",
            str(tmp_path),
            "--publish-candidate",
        ]
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    formal = json.loads(
        context.store_path(
            "diagnostics", "adjusted_ohlc_verification.json"
        ).read_text(encoding="utf-8")
    )
    assert result == 0
    assert manifest["adjustment_policy_id"] == ADJUSTMENT_POLICY_ID
    assert formal["status"] == "ready"
    assert _canonical_tree_snapshot(context) == canonical_before


def test_candidate_promotion_rejects_stale_manifest_without_formal_or_canonical_writes(
    tmp_path, monkeypatch, capsys
):
    context, partition_path, manifest_path = _certification_fixture(tmp_path)
    _reject_pipeline_paths(monkeypatch)
    full_args = [
        "certify-adjusted-ohlc",
        "--project-root",
        str(tmp_path),
        "--mode",
        "full",
    ]
    assert main(full_args) == 0
    capsys.readouterr()
    canonical_before = _canonical_tree_snapshot(context)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = "2026-07-16T00:00:01Z"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    stale_manifest = manifest_path.read_bytes()

    result = main(
        [
            "certify-adjusted-ohlc",
            "--project-root",
            str(tmp_path),
            "--publish-candidate",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stale candidate manifest fingerprint" in captured.err
    assert _canonical_tree_snapshot(context) == canonical_before
    assert manifest_path.read_bytes() == stale_manifest
    assert not context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    ).exists()
