import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from data_analysts.artifacts import ArtifactPublisher
from data_analysts.config import load_runtime_config
from data_analysts.extract import ExtractError, extract_family_rows_from_database
from data_analysts.paths import DataAnalystsContext
import data_analysts.pipeline as pipeline_module
from data_analysts.pipeline import run_pipeline


def _active_path(context: DataAnalystsContext, manifest_name: str, token: str):
    manifest = json.loads(
        context.store_path("manifests", manifest_name).read_text(encoding="utf-8")
    )
    matches = [path for path in manifest["artifact_paths"] if token in path]
    assert len(matches) == 1
    return context.artifact_path(matches[0])


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def find(self, query=None):
        self.queries.append(query or {})
        return list(self.rows)


class FakeDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]

    def list_collection_names(self):
        return list(self.collections)


def test_daily_chip_mongo_extraction_replaces_epoch_cutoff_with_extraction_snapshot():
    family = {
        "family_id": "daily_chip",
        "collection_pattern": "{ticker}",
        "source_profile": "large_daily_panel",
        "primary_key": ["date", "ticker"],
        "date_fields": {"source_date": "mdate"},
        "data_cutoff_policy": "extraction_completed_fallback",
    }
    database = FakeDatabase({
        "2330": FakeCollection([{
            "coid": "2330", "mdate": "2024-01-02",
            "data_cutoff_at": "1970-01-01T00:00:00Z",
        }]),
    })

    rows = extract_family_rows_from_database(
        database,
        family,
        start_date="2024-01-02",
        end_date="2024-01-02",
        run_scope="bounded_backfill",
        extraction_completed_at="2026-09-04T01:23:45Z",
    )

    assert rows[0]["data_cutoff_at"] == "2026-09-04T01:23:45Z"
    assert rows[0]["data_cutoff_origin"] == "extraction_completed_fallback"


def test_extraction_cutoff_recovery_preserves_real_source_datetime():
    family = {
        "family_id": "daily_chip",
        "collection_pattern": "{ticker}",
        "source_profile": "large_daily_panel",
        "primary_key": ["date", "ticker"],
        "date_fields": {"source_date": "mdate"},
        "data_cutoff_policy": "extraction_completed_fallback",
    }
    source_cutoff = datetime(2026, 9, 4, 1, 23, 45, tzinfo=timezone.utc)
    database = FakeDatabase({
        "2330": FakeCollection([{
            "coid": "2330", "mdate": "2024-01-02",
            "data_cutoff_at": source_cutoff,
        }]),
    })

    rows = extract_family_rows_from_database(
        database,
        family,
        start_date="2024-01-02",
        end_date="2024-01-02",
        run_scope="bounded_backfill",
        extraction_completed_at="2026-09-04T02:00:00Z",
    )

    assert rows[0]["data_cutoff_at"] == source_cutoff
    assert "data_cutoff_origin" not in rows[0]


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
        "artifact_contracts.json",
    ]:
        payload = json.loads((source / name).read_text(encoding="utf-8"))
        if name == "source_family_profiles.json":
            payload["families"] = [
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
                        {"zdate": "2025-01-02", "mkt": "TWSE", "date_rmk": "", "source_row_id": "a", "data_cutoff_at": "2025-01-02T10:00:00Z"},
                        {"zdate": "2025-01-03", "mkt": "TWSE", "date_rmk": "休市", "source_row_id": "b", "data_cutoff_at": "2025-01-03T10:00:00Z"},
                    ],
                },
                {
                    "family_id": "financial_statement_raw",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "AINVFINB",
                    "source_profile": "medium_pit_table",
                    "primary_key": [
                        "ticker",
                        "no",
                        "sem",
                        "curr",
                        "merg",
                        "period_end_date",
                        "source_available_date",
                        "revision_date",
                    ],
                    "date_fields": {"source_date": "key3"},
                    "availability": {"type": "source_available_date", "field": "key3"},
                    "partitioning": ["available_year"],
                    "pit_policy": "source_available_date",
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "no": "Q",
                            "sem": "2",
                            "curr": "TWD",
                            "merg": "Y",
                            "endd": "2025-06-30",
                            "key3": "2025-08-14",
                            "mdate": "2025-08-15",
                            "eps": 10,
                            "source_row_id": "a",
                            "data_cutoff_at": "2025-08-14T10:00:00Z",
                        },
                        {
                            "coid": "2330",
                            "no": "Q",
                            "sem": "2",
                            "curr": "TWD",
                            "merg": "Y",
                            "endd": "2025-06-30",
                            "key3": "2025-08-14",
                            "mdate": "2025-08-20",
                            "eps": 11,
                            "source_row_id": "b",
                            "data_cutoff_at": "2025-08-20T10:00:00Z",
                        },
                    ],
                },
            ]
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_pipeline_publishes_raw_family_artifacts_and_diagnostics(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    result = run_pipeline(
        context,
        config,
        families={"trading_calendar", "financial_statement_raw"},
        as_of_date="2025-08-31",
        run_scope="daily",
    )

    assert result["status"] == "verifying"
    calendar_manifest = json.loads(
        (tmp_path / "data_store" / "manifests" / "trading_calendar.json").read_text(
            encoding="utf-8"
        )
    )
    calendar_path = tmp_path / "data_store" / calendar_manifest["artifact_paths"][0]
    assert calendar_path.exists()
    calendar_rows = pq.read_table(calendar_path).to_pylist()
    assert calendar_rows[0]["is_trading_day"] is True

    raw_path = _active_path(
        context, "financial_statement_raw.json", "available_year=2025"
    )
    assert raw_path.exists()
    raw_rows = pq.read_table(raw_path).to_pylist()
    assert len(raw_rows) == 2

    selected_path = _active_path(
        context, "financial_statement_pit_selected.json", "decision_year=2025"
    )
    assert selected_path.exists()
    selected_rows = pq.read_table(selected_path).to_pylist()
    assert selected_rows[0]["eps"] == 11
    assert len(raw_rows) > len(selected_rows)
    selected_manifest = json.loads(
        (tmp_path / "data_store" / "manifests" / "financial_statement_pit_selected.json").read_text(
            encoding="utf-8"
        )
    )
    assert selected_manifest["date_range"] == ["2025-08-31", "2025-08-31"]
    assert selected_manifest["availability_date_range"] == ["2025-08-14", "2025-08-14"]

    diagnostic = json.loads(
        (
            tmp_path
            / "data_store"
            / "diagnostics"
            / "raw_families"
            / "financial_statement_raw.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["source_row_count"] == 2
    assert diagnostic["unresolved_duplicate_count"] == 0


@pytest.mark.parametrize(
    "cutoff",
    [None, "", "1970-01-01T00:00:00Z", "1970-01-01T08:00:00+08:00", "not-a-timestamp"],
)
def test_source_row_without_real_cutoff_fails_with_source_identity(cutoff):
    row = {
        "source_collection": "TRADEDAY_TWSE",
        "source_row_id": "calendar:7",
        "data_cutoff_at": cutoff,
    }

    with pytest.raises(
        ExtractError,
        match="trading_calendar.*TRADEDAY_TWSE.*calendar:7.*data_cutoff_at",
    ):
        pipeline_module._normalize_source_row("trading_calendar", row, 7)


def test_artifact_publisher_normalizes_mixed_integer_scalars_before_parquet(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)

    target = publisher.publish_parquet(
        "canonical/raw/monthly_sales/available_year=2025/part.parquet",
        rows=[
            {"ticker": "2330", "source_available_date": "2025-07-10", "d0005": 1, "raw_note": b"alpha"},
            {
                "ticker": "2317",
                "source_available_date": "2025-07-10",
                "d0005": np.int64(2),
                "raw_note": np.nan,
            },
            {
                "ticker": "2454",
                "source_available_date": "2025-07-10",
                "d0005": pd.Int64Dtype().type(3),
                "raw_note": bytearray(b"beta"),
            },
        ],
        required_columns=["ticker", "source_available_date", "d0005"],
    )

    rows = pq.read_table(target).to_pylist()
    assert [row["d0005"] for row in rows] == [1, 2, 3]
    assert [row["raw_note"] for row in rows] == [b"alpha", None, b"beta"]


def test_financial_statement_range_backfill_publishes_only_end_date_selected_snapshot(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    run_pipeline(
        context,
        config,
        families={"financial_statement_raw"},
        start_date="2025-08-01",
        end_date="2025-08-31",
        run_scope="bounded_backfill",
    )

    raw_path = _active_path(
        context, "financial_statement_raw.json", "available_year=2025"
    )
    raw_rows = pq.read_table(raw_path).to_pylist()
    assert len(raw_rows) == 2
    assert {row["source_row_id"] for row in raw_rows} == {"a", "b"}

    selected_path = _active_path(
        context, "financial_statement_pit_selected.json", "decision_year=2025"
    )
    selected_rows = pq.read_table(selected_path).to_pylist()
    assert len(selected_rows) == 1
    assert selected_rows[0]["decision_date"] == "2025-08-31"
    assert selected_rows[0]["eps"] == 11


def test_full_history_financial_statement_uses_complete_fresh_calendar_domain(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    calendar = next(
        item for item in payload["families"]
        if item["family_id"] == "trading_calendar"
    )
    calendar["fixture_rows"] = [
        {
            "zdate": value,
            "mkt": "TWSE",
            "date_rmk": "",
            "data_cutoff_at": f"{value}T10:00:00Z",
        }
        for value in ("2025-08-14", "2025-08-15", "2025-08-18")
    ]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)

    run_pipeline(
        context,
        load_runtime_config(context),
        families={"financial_statement_raw"},
        run_scope="full_history",
    )

    selected_path = _active_path(
        context, "financial_statement_pit_selected.json", "decision_year=2025"
    )
    selected = pq.read_table(selected_path).to_pylist()
    assert [row["decision_date"] for row in selected] == ["2025-08-14"]
    assert [row["eps"] for row in selected] == [11]


def test_full_history_selected_pit_without_fresh_calendar_evidence_fails_closed(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["families"] = [
        item for item in payload["families"]
        if item["family_id"] != "trading_calendar"
    ]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(
        ExtractError,
        match="financial_statement_pit_selected.*fresh trading calendar",
    ):
        run_pipeline(
            context,
            load_runtime_config(context),
            families={"financial_statement_raw"},
            run_scope="full_history",
        )


@pytest.mark.parametrize("family_id", ["dividend_policy", "capital_formation"])
def test_empty_source_only_event_family_is_not_blocked_by_transitive_outputs(
    tmp_path, family_id
):
    _write_configs(tmp_path)
    config = load_runtime_config(DataAnalystsContext.from_paths(tmp_path))

    pipeline_module._fail_on_illegal_empty_full_history_family(
        config, family_id, [], "full_history"
    )


def test_per_ticker_daily_extraction_reports_source_collection_count(tmp_path):
    _write_configs(tmp_path)
    config_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["families"] = [
        {
            "family_id": "daily_tradability",
            "enabled": True,
            "connection": "apistkattr",
            "collection_pattern": "{ticker}",
            "source_profile": "large_daily_panel",
            "primary_key": ["date", "ticker"],
            "date_fields": {"source_date": "mdate"},
            "availability": {"type": "source_available_date", "field": "mdate"},
            "partitioning": ["year"],
            "pit_policy": "source_available_date",
        }
    ]
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    fake_db = FakeDatabase({
        "2330": FakeCollection([{"coid": "2330", "date": "2025-01-02", "mdate": "2025-01-02", "source_row_id": "a", "data_cutoff_at": "2025-01-02T10:00:00Z"}]),
        "2317": FakeCollection([{"coid": "2317", "date": "2025-01-02", "mdate": "2025-01-02", "source_row_id": "b", "data_cutoff_at": "2025-01-02T10:00:00Z"}]),
    })

    run_pipeline(
        context,
        config,
        families={"daily_tradability"},
        start_date="2025-01-01",
        end_date="2025-01-31",
        mongo_databases={"apistkattr": fake_db},
        run_scope="bounded_backfill",
    )

    diagnostic = json.loads(
        (
            tmp_path
            / "data_store"
            / "diagnostics"
            / "raw_families"
            / "daily_tradability.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["source_collection_count"] == 2
    assert diagnostic["source_collections"] == ["2317", "2330"]
    assert diagnostic["source_collection_sample_truncated"] is False
    assert diagnostic["published_row_count"] == 2


def test_small_snapshot_uses_single_collection_for_trading_calendar(tmp_path):
    _write_configs(tmp_path)
    config_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for family in payload["families"]:
        if family["family_id"] == "trading_calendar":
            family.pop("fixture_rows", None)
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    fake_collection = FakeCollection([{"zdate": "2025-01-02", "mkt": "TWSE", "date_rmk": "", "data_cutoff_at": "2025-01-02T10:00:00Z"}])
    fake_db = FakeDatabase({"TRADEDAY_TWSE": fake_collection})

    run_pipeline(
        context,
        config,
        families={"trading_calendar"},
        mongo_databases={"tej": fake_db},
        run_scope="bounded_backfill",
    )

    diagnostic = json.loads(
        (
            tmp_path
            / "data_store"
            / "diagnostics"
            / "raw_families"
            / "trading_calendar.json"
        ).read_text(encoding="utf-8")
    )
    assert fake_collection.queries == [{}]
    assert diagnostic["source_collection_count"] == 1
    assert diagnostic["source_collections"] == ["TRADEDAY_TWSE"]


def test_pipeline_default_layout_does_not_create_runtime_or_runs(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"trading_calendar", "financial_statement_raw"},
        start_date="2025-08-01",
        end_date="2025-08-31",
        run_scope="bounded_backfill",
    )

    assert result["status"] == "verifying"
    assert (tmp_path / "data_store" / "canonical").exists()
    assert (tmp_path / "data_store" / "manifests").exists()
    assert (tmp_path / "data_store" / "metadata" / "data_store_manifest.json").exists()
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path / "runs").exists()


def test_pipeline_writes_progress_status_and_console_updates(tmp_path, capsys):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    run_pipeline(
        context,
        config,
        families={"trading_calendar", "financial_statement_raw"},
        start_date="2025-08-01",
        end_date="2025-08-31",
        run_scope="bounded_backfill",
    )

    output = capsys.readouterr().out
    assert "[progress] phase=extract status=running" in output
    assert "[progress] phase=raw_family status=running family=financial_statement_raw" in output
    assert "[progress] phase=verify status=verifying" in output

    current_run = json.loads(
        (tmp_path / "data_store" / "jobs" / "current_run.json").read_text(encoding="utf-8")
    )
    assert current_run["status"] == "verifying"
    assert current_run["phase"] == "verify"
    assert current_run["completed_families"] == 2
    assert current_run["total_families"] == 2
    assert current_run["current_family"] is None


def test_pipeline_defers_ready_state_until_fresh_verification(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"trading_calendar", "financial_statement_raw"},
        start_date="2025-08-01",
        end_date="2025-08-31",
        run_scope="bounded_backfill",
        publish_ready_state=False,
    )

    pipeline_result = json.loads(
        (tmp_path / "data_store" / "jobs" / "pipeline_result.json").read_text(
            encoding="utf-8"
        )
    )
    current_run = json.loads(
        (tmp_path / "data_store" / "jobs" / "current_run.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "verifying"
    assert pipeline_result["status"] == "verifying"
    assert current_run["status"] == "verifying"
    assert current_run["phase"] == "verify"


def test_pipeline_writes_blocked_progress_on_failure(tmp_path, monkeypatch):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    def fail_rows(*args, **kwargs):
        raise ValueError("synthetic progress failure")

    monkeypatch.setattr(pipeline_module, "_iter_family_rows", fail_rows)

    try:
        run_pipeline(
            context,
            config,
            families={"trading_calendar"},
            run_scope="bounded_backfill",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("run_pipeline should have failed")

    current_run = json.loads(
        (tmp_path / "data_store" / "jobs" / "current_run.json").read_text(encoding="utf-8")
    )
    assert current_run["status"] == "blocked"
    assert current_run["phase"] == "extract"
    assert current_run["error"] == "synthetic progress failure"


def test_pipeline_releases_non_dependency_raw_family_before_next_extraction(
    tmp_path, monkeypatch
):
    import gc
    import weakref

    class WeakRow(dict):
        pass

    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    non_dependency_family = next(
        family_id
        for family_id in config.family_ids
        if family_id in pipeline_module.RAW_EXPANSION_FAMILIES
        and family_id not in pipeline_module.RAW_DEPENDENCY_FAMILIES
    )
    released = {"value": False}

    def streaming_rows(*args, **kwargs):
        row = WeakRow(data_cutoff_at="2026-07-08T10:00:00Z")
        reference = weakref.ref(row)
        rows = [row]
        yield non_dependency_family, rows
        del row, rows
        gc.collect()
        released["value"] = reference() is None

    monkeypatch.setattr(pipeline_module, "_iter_family_rows", streaming_rows)
    monkeypatch.setattr(
        pipeline_module,
        "normalize_raw_family",
        lambda *args, **kwargs: {
            "raw_rows": args[1],
            "selected_rows": [],
            "diagnostics": {},
        },
    )
    monkeypatch.setattr(
        pipeline_module, "_publish_raw_family_outputs", lambda *args: []
    )

    run_pipeline(
        context,
        config,
        families={non_dependency_family},
        run_scope="bounded_backfill",
    )

    assert released["value"] is True
    current = json.loads(
        context.store_path("jobs", "current_run.json").read_text(encoding="utf-8")
    )
    pipeline_result = json.loads(
        context.store_path("jobs", "pipeline_result.json").read_text(encoding="utf-8")
    )
    assert current["selected_families"] == [non_dependency_family]
    assert pipeline_result["families"] == [non_dependency_family]
    assert current["run_attestation"]["selected_families"] == [non_dependency_family]
