import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import data_analysts.artifacts as artifacts_module
from data_analysts.config import load_runtime_config
from data_analysts.cli import main
from data_analysts.dataset_publication import publish_dataset
from data_analysts.inspect import inspect_artifacts
from data_analysts.metadata import publish_data_store_metadata
from data_analysts.paths import DataAnalystsContext
from data_analysts.verify import verify_runtime


def test_verify_blocks_malformed_cutoff_in_current_raw_manifest(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"daily_price_volume"})
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    _publish_metadata(context)
    row = {
        "date": "2026-07-08",
        "ticker": "2330",
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 1000,
        "traded_value": 100000,
        "adj_factor": 1.0,
        "adj_close": 100.0,
        "data_cutoff_at": "2026-07-08T12:00:00Z",
    }
    result = publish_dataset(
        context,
        config.artifact_contracts["daily_price_volume"],
        [row],
        "full_history",
    )
    parquet_path = context.artifact_path(result.manifest["artifact_paths"][0])
    row["data_cutoff_at"] = "malformed"
    pq.write_table(pa.Table.from_pylist([row]), parquet_path)

    verification = verify_runtime(context)

    assert verification["status"] == "blocked"
    assert verification["blocked_step"] == "artifact_inventory"
    assert "malformed data_cutoff_at" in verification["message"]


def _copy_configs(src_root: Path, dst_root: Path) -> None:
    (dst_root / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
        "artifact_contracts.json",
    ]:
        (dst_root / "configs" / name).write_text(
            (src_root / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _publish_metadata(context: DataAnalystsContext) -> None:
    config = load_runtime_config(context)
    publish_data_store_metadata(context, config)


def _set_enabled_families(tmp_path: Path, enabled_family_ids: set[str]) -> None:
    config_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for family in payload.get("families", []):
        if isinstance(family, dict):
            family_id = family.get("family_id")
            family["enabled"] = family_id in enabled_family_ids
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_raw_family_fixture(
    tmp_path: Path,
    *,
    pit_parse_failure_count: int = 0,
    unresolved_duplicate_count: int = 0,
) -> None:
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)
    artifact = (
        tmp_path
        / "data_store"
        / "canonical"
        / "raw"
        / "trading_calendar"
        / "trading_calendar.parquet"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"not-read-by-this-check")
    (manifests / "trading_calendar.json").write_text(
        json.dumps(
            {
                "artifact_id": "trading_calendar",
                "schema_version": "1.0",
                "artifact_paths": [
                    "canonical/raw/trading_calendar/trading_calendar.parquet"
                ],
                "columns": ["date"],
                "source_collections": ["TEJ.TRADEDAY_TWSE"],
            }
        ),
        encoding="utf-8",
    )
    diagnostic_dir = (
        tmp_path / "data_store" / "diagnostics" / "raw_families"
    )
    diagnostic_dir.mkdir(parents=True)
    (diagnostic_dir / "trading_calendar.json").write_text(
        json.dumps(
            {
                "source_row_count": 1,
                "published_row_count": 1,
                "pit_parse_failure_count": pit_parse_failure_count,
                "unresolved_duplicate_count": unresolved_duplicate_count,
            }
        ),
        encoding="utf-8",
    )


def _write_manifest_fixture(
    tmp_path: Path,
    *,
    artifact_id: str,
    artifact_path: str,
    columns: list[str] | None = None,
) -> None:
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    artifact = tmp_path / "data_store" / Path(*artifact_path.split("/"))
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"not-read-by-this-check")
    (manifests / f"{artifact_id}.json").write_text(
        json.dumps(
            {
                "artifact_id": artifact_id,
                "schema_version": "1.0",
                "artifact_paths": [artifact_path],
                "columns": columns or ["date"],
                "source_collections": ["fixture"],
            }
        ),
        encoding="utf-8",
    )


def _write_raw_family_diagnostic(
    tmp_path: Path,
    *,
    family_id: str,
    source_row_count: object,
    published_row_count: object = 0,
    pit_parse_failure_count: object = 0,
    unresolved_duplicate_count: object = 0,
) -> None:
    diagnostic_dir = tmp_path / "data_store" / "diagnostics" / "raw_families"
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    (diagnostic_dir / f"{family_id}.json").write_text(
        json.dumps(
            {
                "source_row_count": source_row_count,
                "published_row_count": published_row_count,
                "pit_parse_failure_count": pit_parse_failure_count,
                "unresolved_duplicate_count": unresolved_duplicate_count,
            }
        ),
        encoding="utf-8",
    )


def _disable_family_in_live_config(tmp_path: Path, family_id: str) -> None:
    config_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for family in payload.get("families", []):
        if isinstance(family, dict) and family.get("family_id") == family_id:
            family["enabled"] = False
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_selected_pit_fixture(
    tmp_path: Path,
    *,
    artifact_id: str = "financial_statement_pit_selected",
    rows: list[dict[str, object]],
) -> None:
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    artifact = (
        tmp_path
        / "data_store"
        / "canonical"
        / "derived"
        / "pit"
        / artifact_id
        / "decision_year=2025"
        / "part.parquet"
    )
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact)
    (manifests / f"{artifact_id}.json").write_text(
        json.dumps(
            {
                "artifact_id": artifact_id,
                "schema_version": "1.0",
                "artifact_paths": [
                    f"canonical/derived/pit/{artifact_id}/decision_year=2025/part.parquet"
                ],
                "columns": list(rows[0].keys()) if rows else [],
                "source_collections": ["TEJ.AINVFINB"],
            }
        ),
        encoding="utf-8",
    )


def test_verify_blocks_on_raw_family_pit_parse_failure(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path, pit_parse_failure_count=1)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "raw_family_diagnostics"


def test_verify_blocks_on_raw_family_unresolved_duplicate(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path, unresolved_duplicate_count=1)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "raw_family_diagnostics"

def test_verify_blocks_selected_pit_when_source_available_after_decision_date(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, set())
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_selected_pit_fixture(
        tmp_path,
        rows=[
            {
                "ticker": "2330",
                "decision_date": "2025-08-31",
                "source_available_date": "2025-09-01",
                "revision_date": "2025-09-02",
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "selected_pit_artifacts"
    assert "source_available_date > decision_date" in result["message"]


def test_verify_blocks_selected_pit_when_required_columns_are_missing(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, set())
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_selected_pit_fixture(
        tmp_path,
        artifact_id="self_reported_numbers_pit_selected",
        rows=[
            {
                "ticker": "2330",
                "source_available_date": "2025-07-20",
                "revision_date": "2025-07-21",
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "selected_pit_artifacts"
    assert "missing required columns" in result["message"]


def test_inspect_reports_raw_family_diagnostics_without_reading_parquet(tmp_path):
    _write_raw_family_fixture(tmp_path, pit_parse_failure_count=2)

    result = inspect_artifacts(DataAnalystsContext.from_paths(tmp_path))

    assert result["raw_family_diagnostics"] == {
        "status": "blocked",
        "family_count": 1,
        "raw_family_diagnostic_count": 1,
        "pit_parse_failure_count_total": 2,
        "unresolved_duplicate_count_total": 0,
    }
    assert result["status"] == "blocked"
    assert result["artifact_audit"]["issues"][0]["check"] == "audit_config"


def test_inspect_cli_blocks_raw_diagnostic_failure_with_valid_artifact(tmp_path, capsys):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"daily_price_volume"})
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    publish_dataset(
        context,
        config.artifact_contracts["daily_price_volume"],
        [{
            "date": "2026-07-08", "ticker": "2330", "open": 100,
            "high": 101, "low": 99, "close": 100, "volume": 1000,
            "traded_value": 100000, "adj_factor": 1.0, "adj_close": 100.0,
            "data_cutoff_at": "2026-07-08T12:00:00Z",
        }],
        "full_history",
    )
    publish_data_store_metadata(context, config)
    _write_raw_family_diagnostic(
        tmp_path,
        family_id="daily_price_volume",
        source_row_count=1,
        published_row_count=1,
        pit_parse_failure_count=1,
    )

    exit_code = main(["inspect-artifacts", "--project-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["artifact_audit"]["status"] == "ready"
    assert payload["raw_family_diagnostics"]["status"] == "blocked"
    assert payload["status"] == "blocked"
    assert exit_code == 1

    diagnostic_path = context.store_path(
        "diagnostics", "raw_families", "daily_price_volume.json"
    )
    diagnostic_path.write_text("{", encoding="utf-8")
    exit_code = main(["inspect-artifacts", "--project-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["raw_family_diagnostics"]["status"] == "blocked"
    assert payload["raw_family_diagnostics"]["diagnostic_error_count"] == 1
    assert payload["status"] == "blocked"
    assert exit_code == 1


def test_inspect_reports_legacy_layout_without_using_it(tmp_path):
    (tmp_path / "runtime").mkdir()

    result = inspect_artifacts(DataAnalystsContext.from_paths(tmp_path))

    assert result["legacy_layout_detected"] is True
    assert result["legacy_project_runtime_exists"] is True


def test_verify_blocks_absolute_artifact_path(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)
    manifests = tmp_path / "data_store" / "manifests"
    (manifests / "trading_calendar.json").write_text(
        json.dumps(
            {
                "artifact_id": "trading_calendar",
                "artifact_paths": [
                    (
                        tmp_path
                        / "data_store"
                        / "canonical"
                        / "raw"
                        / "trading_calendar"
                        / "trading_calendar.parquet"
                    ).as_posix()
                ],
                "columns": ["date"],
                "source_collections": ["TEJ.TRADEDAY_TWSE"],
            }
        ),
        encoding="utf-8",
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["path_metrics"]["absolute_artifact_path_count"] == 1


def test_verify_blocks_missing_required_enabled_raw_family_manifest(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_paths"
    assert result["path_metrics"]["required_manifest_missing_count"] > 0
    assert "missing required manifest" in result["message"]


def test_verify_accepts_event_manifests_as_formal_replacements(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"dividend_policy", "capital_formation"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    config = load_runtime_config(context)
    publish_dataset(
        context,
        config.artifact_contracts["dividend_events"],
        [{
            "event_date": "2026-07-08", "ex_date": "2026-07-08", "ticker": "2330",
            "cash_dividend_per_share": 1.0, "stock_dividend_ratio": 0.0,
            "source_dataset_id": "fixture", "source_row_id": "dividend:1",
            "data_cutoff_at": "2026-07-08T12:00:00Z",
        }],
        "full_history",
    )
    publish_dataset(
        context,
        config.artifact_contracts["capital_action_events"],
        [{
            "event_date": "2026-07-08", "ex_date": "2026-07-08", "ticker": "2330",
            "action_type": "capital_reduction", "share_multiplier": 0.9,
            "cash_return_per_share": 0.0, "source_dataset_id": "fixture",
            "source_row_id": "capital:1", "data_cutoff_at": "2026-07-08T12:00:00Z",
        }],
        "full_history",
    )

    result = verify_runtime(context)
    inspect_result = inspect_artifacts(context)

    assert result["status"] == "ready"
    assert result["path_metrics"]["required_manifest_missing_count"] == 0
    assert inspect_result["required_manifest_missing_count"] == 0


def test_verify_blocks_zero_row_raw_family_without_explicit_empty_manifest(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar", "director_supervisor_holdings"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    config = load_runtime_config(context)
    publish_dataset(
        context,
        config.artifact_contracts["trading_calendar"],
        [{
            "date": "2026-07-08", "market": "TWSE", "is_trading_day": True,
            "source_available_date": "2026-07-08",
            "data_cutoff_at": "2026-07-08T12:00:00Z",
        }],
        "full_history",
    )
    _write_raw_family_diagnostic(
        tmp_path,
        family_id="trading_calendar",
        source_row_count=1,
        published_row_count=1,
    )
    _write_raw_family_diagnostic(
        tmp_path,
        family_id="director_supervisor_holdings",
        source_row_count=0,
        published_row_count=0,
    )

    result = verify_runtime(context)
    inspect_result = inspect_artifacts(context)

    assert result["status"] == "blocked"
    assert result["path_metrics"]["required_manifest_missing_count"] == 1
    assert inspect_result["required_manifest_missing_count"] == 1


def test_verify_blocks_missing_raw_manifest_without_zero_row_diagnostic(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar", "director_supervisor_holdings"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)
    _write_raw_family_diagnostic(
        tmp_path,
        family_id="director_supervisor_holdings",
        source_row_count=2,
        published_row_count=0,
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_paths"
    assert result["path_metrics"]["required_manifest_missing_count"] == 1
    assert "missing required manifest" in result["message"]


def test_verify_blocks_missing_raw_manifest_when_diagnostic_is_missing(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar", "director_supervisor_holdings"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_paths"
    assert result["path_metrics"]["required_manifest_missing_count"] == 1
    assert "missing required manifest" in result["message"]


def test_verify_blocks_missing_raw_manifest_when_zero_row_diagnostic_counters_are_not_explicit_zero_ints(
    tmp_path,
):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar", "director_supervisor_holdings"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)

    invalid_cases = [
        {
            "source_row_count": 0,
            "pit_parse_failure_count": None,
            "unresolved_duplicate_count": 0,
        },
        {
            "source_row_count": None,
            "pit_parse_failure_count": 0,
            "unresolved_duplicate_count": 0,
        },
        {
            "source_row_count": 0,
            "pit_parse_failure_count": 0,
            "unresolved_duplicate_count": "",
        },
        {
            "source_row_count": False,
            "pit_parse_failure_count": 0,
            "unresolved_duplicate_count": 0,
        },
        {
            "source_row_count": 0.0,
            "pit_parse_failure_count": 0,
            "unresolved_duplicate_count": 0,
        },
        {
            "source_row_count": "0",
            "pit_parse_failure_count": 0,
            "unresolved_duplicate_count": 0,
        },
    ]

    for payload in invalid_cases:
        _write_raw_family_diagnostic(
            tmp_path,
            family_id="director_supervisor_holdings",
            published_row_count=0,
            **payload,
        )

        result = verify_runtime(context)
        inspect_result = inspect_artifacts(context)

        assert result["status"] == "blocked"
        assert result["blocked_step"] == "manifest_paths"
        assert result["path_metrics"]["required_manifest_missing_count"] == 1
        assert result["path_metrics"]["zero_row_required_family_count"] == 0
        assert inspect_result["required_manifest_missing_count"] == 1
        assert inspect_result["zero_row_required_family_count"] == 0
        assert "missing required manifest" in result["message"]


def test_verify_uses_snapshot_required_family_ids_after_metadata_publish_despite_live_config_drift(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar", "director_supervisor_holdings"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)
    _write_raw_family_diagnostic(
        tmp_path,
        family_id="director_supervisor_holdings",
        source_row_count=2,
        published_row_count=0,
    )
    _disable_family_in_live_config(tmp_path, "director_supervisor_holdings")

    result = verify_runtime(context)
    inspect_result = inspect_artifacts(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_paths"
    assert result["path_metrics"]["required_manifest_missing_count"] == 1
    assert inspect_result["required_manifest_missing_count"] == 1
    assert result["path_metrics"]["zero_row_required_family_count"] == 0
    assert inspect_result["zero_row_required_family_count"] == 0
    assert "missing required manifest" in result["message"]


@pytest.mark.parametrize("schema_version", [[], {}])
def test_verify_blocks_malformed_legacy_schema_version(tmp_path, schema_version):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)
    manifest_path = tmp_path / "data_store" / "manifests" / "trading_calendar.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = schema_version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_fingerprints"


def test_verify_blocks_explicit_null_schema_version(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)
    manifest_path = tmp_path / "data_store" / "manifests" / "trading_calendar.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_fingerprints"


def test_verify_blocks_malformed_schema_1_1_fingerprints(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_raw_family_fixture(tmp_path)
    manifest_path = tmp_path / "data_store" / "manifests" / "trading_calendar.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"schema_version": "1.1", "artifact_fingerprints": []})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_fingerprints"


def test_verify_accepts_valid_schema_1_1_manifest_without_hashing(tmp_path, monkeypatch):
    src_root = Path(__file__).resolve().parents[1]
    _copy_configs(src_root, tmp_path)
    _set_enabled_families(tmp_path, {"trading_calendar"})
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    config = load_runtime_config(context)
    publish_dataset(
        context,
        config.artifact_contracts["trading_calendar"],
        [
            {
                "date": "2026-07-08",
                "market": "TWSE",
                "is_trading_day": True,
                "source_available_date": "2026-07-08",
                "data_cutoff_at": "2026-07-08T12:00:00Z",
            }
        ],
        "full_history",
    )
    _write_raw_family_diagnostic(
        tmp_path,
        family_id="trading_calendar",
        source_row_count=1,
        published_row_count=1,
    )
    manifest_path = tmp_path / "data_store" / "manifests" / "trading_calendar.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = manifest["artifact_paths"][0]
    manifest.update(
        {
            "schema_version": "1.1",
            "artifact_fingerprints": [
                {"artifact_path": artifact_path, "sha256": "a" * 64}
            ],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        artifacts_module,
        "build_artifact_fingerprints",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hashed")),
    )
    monkeypatch.setattr(
        artifacts_module,
        "sha256_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hashed")),
    )

    result = verify_runtime(context)

    assert result["status"] == "ready"


def test_inspect_counts_posix_rooted_absolute_artifact_path(tmp_path):
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "sample.json").write_text(
        json.dumps(
            {
                "artifact_id": "sample",
                "artifact_paths": ["/outside/x"],
                "columns": ["date"],
                "source_collections": ["TEJ.TRADEDAY_TWSE"],
            }
        ),
        encoding="utf-8",
    )

    result = inspect_artifacts(DataAnalystsContext.from_paths(tmp_path))

    assert result["absolute_artifact_path_count"] == 1


def test_inspect_handles_invalid_metadata_config_snapshot_path_without_escaping_boundary(tmp_path):
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "sample.json").write_text(
        json.dumps(
            {
                "artifact_id": "sample",
                "artifact_paths": ["canonical/raw/sample/part.parquet"],
                "columns": ["date"],
                "source_collections": ["TEJ.TRADEDAY_TWSE"],
            }
        ),
        encoding="utf-8",
    )
    metadata_dir = tmp_path / "data_store" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_store_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "config_snapshot_path": "/outside/x",
                "config_hashes": {},
            }
        ),
        encoding="utf-8",
    )

    result = inspect_artifacts(DataAnalystsContext.from_paths(tmp_path))

    assert result["status"] == "blocked"
    assert result["artifact_audit"]["issues"][0]["check"] == "audit_config"
    assert result["manifest_count"] == 1
    assert result["required_manifest_missing_count"] == 0
    assert result["config_snapshot_file_count"] == 0
    assert result["config_snapshot_hash_mismatch_count"] == 0
