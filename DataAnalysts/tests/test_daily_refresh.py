import json
from types import SimpleNamespace

import pytest

import data_analysts.cli as cli_module
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.cli import main
from data_analysts.daily import (
    DailyRefreshError,
    plan_daily_refresh_dates,
    write_daily_refresh_success,
    write_daily_refresh_blocked,
)
from data_analysts.dataset_publication import publish_dataset
from data_analysts.paths import DataAnalystsContext
from data_analysts.pipeline import run_pipeline


def _calendar_contract() -> ArtifactContract:
    return ArtifactContract(
        contract_key="trading_calendar",
        artifact_id="trading_calendar",
        variant="default",
        layer="raw",
        base_path="canonical/raw/trading_calendar",
        file_name="trading_calendar.parquet",
        required_columns=(
            "date",
            "market",
            "is_trading_day",
            "source_available_date",
            "data_cutoff_at",
        ),
        logical_key=("date", "market"),
        publication_mode="full_replace",
        partition_name=None,
        partition_field=None,
        date_field="date",
        availability_field="source_available_date",
        pit_policy="source_available_date",
        source_families=("trading_calendar",),
    )


def _publish_trading_calendar(context, rows):
    normalized = [
        {
            "date": row["date"],
            "market": "TWSE",
            "is_trading_day": row["is_trading_day"],
            "source_available_date": row["date"],
            "data_cutoff_at": f"{row['date']}T12:00:00Z",
        }
        for row in rows
    ]
    publish_dataset(context, _calendar_contract(), normalized, "full_history")


def test_no_arg_planner_reads_only_manifest_listed_versioned_calendar(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_trading_calendar(
        context,
        [
            {"date": "2026-07-06", "is_trading_day": True},
            {"date": "2026-07-07", "is_trading_day": True},
        ],
    )
    legacy = context.store_path(
        "canonical", "raw", "trading_calendar", "trading_calendar.parquet"
    )
    legacy.parent.mkdir(parents=True, exist_ok=True)
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(
        pa.Table.from_pylist(
            [{"date": "2026-07-08", "market": "TWSE", "is_trading_day": True}]
        ),
        legacy,
    )

    dates = plan_daily_refresh_dates(
        context,
        _calendar_contract(),
        today="2026-07-08",
    )

    assert dates == ["2026-07-07"]


def test_blocked_state_preserves_last_ready_anchor_for_next_catch_up(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_trading_calendar(
        context,
        [
            {"date": "2026-07-06", "is_trading_day": True},
            {"date": "2026-07-07", "is_trading_day": True},
            {"date": "2026-07-08", "is_trading_day": True},
        ],
    )
    write_daily_refresh_success(
        context.data_store,
        as_of_date="2026-07-06",
        result={"status": "ready"},
    )
    write_daily_refresh_blocked(
        context.data_store,
        as_of_date="2026-07-07",
        message="synthetic failure",
    )

    dates = plan_daily_refresh_dates(
        context,
        _calendar_contract(),
        to_date="2026-07-08",
        today="2026-07-08",
    )

    assert dates == ["2026-07-07", "2026-07-08"]


@pytest.mark.parametrize(
    ("argv", "calendar_end"),
    [([], "2026-07-07"), (["--to-date", "2026-07-08"], "2026-07-08")],
)
def test_cli_records_actual_first_failed_daily_attempt_and_keeps_catch_up_anchor(
    tmp_path, monkeypatch, argv, calendar_end
):
    context = DataAnalystsContext.from_paths(tmp_path)
    calendar_rows = [
        {"date": "2026-07-06", "is_trading_day": True},
        {"date": "2026-07-07", "is_trading_day": True},
    ]
    if calendar_end == "2026-07-08":
        calendar_rows.append({"date": "2026-07-08", "is_trading_day": True})
    _publish_trading_calendar(context, calendar_rows)
    write_daily_refresh_success(
        context.data_store,
        as_of_date="2026-07-06",
        result={"status": "ready"},
    )
    config = SimpleNamespace(
        family_ids=set(),
        artifact_contracts={"trading_calendar": _calendar_contract()},
    )
    monkeypatch.setattr(cli_module, "load_runtime_config", lambda loaded: config)
    monkeypatch.setattr(
        cli_module,
        "audit_store",
        lambda loaded, contracts: {"status": "ready", "artifacts": {}},
    )
    monkeypatch.setattr(
        cli_module,
        "run_pipeline",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("synthetic daily failure")),
    )

    result = main(["run-daily", "--project-root", str(tmp_path), *argv])

    assert result == 1
    state = json.loads(
        context.store_path("jobs", "daily_state.json").read_text(encoding="utf-8")
    )
    blocked = json.loads(
        context.store_path("jobs", "daily_results", "2026-07-07.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["last_ready_as_of_date"] == "2026-07-06"
    assert state["last_attempted_as_of_date"] == "2026-07-07"
    assert blocked["status"] == "blocked"
    assert blocked["as_of_date"] == "2026-07-07"
    assert not context.store_path(
        "jobs", "daily_results", "2026-07-08.json"
    ).exists()
    assert plan_daily_refresh_dates(
        context,
        _calendar_contract(),
        to_date=calendar_end,
        today="2026-07-20",
    ) == (
        ["2026-07-07"]
        if calendar_end == "2026-07-07"
        else ["2026-07-07", "2026-07-08"]
    )


@pytest.mark.parametrize(
    "boundary",
    [
        {"start_date": "2026-07-01"},
        {"end_date": "2026-07-08"},
        {"as_of_date": "2026-07-08"},
    ],
)
def test_full_history_runtime_rejects_all_date_boundaries(tmp_path, boundary):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ValueError, match="full_history.*date boundaries"):
        run_pipeline(context, object(), run_scope="full_history", **boundary)


def test_plan_daily_refresh_without_arguments_uses_latest_trading_day(tmp_path):
    context_root = tmp_path / "project"
    data_store = context_root / "data_store"
    _write_trading_calendar(
        data_store,
        [
            {"date": "2026-07-03", "is_trading_day": True},
            {"date": "2026-07-04", "is_trading_day": False},
            {"date": "2026-07-06", "is_trading_day": True},
        ],
    )

    dates = plan_daily_refresh_dates(
        DataAnalystsContext.from_paths(context_root),
        _calendar_contract(),
        today="2026-07-08",
    )

    assert dates == ["2026-07-06"]


def test_plan_daily_refresh_to_date_catches_up_from_last_ready_trading_day(tmp_path):
    context_root = tmp_path / "project"
    data_store = context_root / "data_store"
    _write_trading_calendar(
        data_store,
        [
            {"date": "2026-07-06", "is_trading_day": True},
            {"date": "2026-07-07", "is_trading_day": True},
            {"date": "2026-07-08", "is_trading_day": True},
        ],
    )
    _write_daily_state(data_store, last_ready_as_of_date="2026-07-06")

    dates = plan_daily_refresh_dates(
        DataAnalystsContext.from_paths(context_root),
        _calendar_contract(),
        to_date="2026-07-08",
        today="2026-07-08",
    )

    assert dates == ["2026-07-07", "2026-07-08"]


def test_plan_daily_refresh_from_to_date_skips_non_trading_days(tmp_path):
    data_store = tmp_path / "data_store"
    _write_trading_calendar(
        data_store,
        [
            {"date": "2026-07-03", "is_trading_day": True},
            {"date": "2026-07-04", "is_trading_day": False},
            {"date": "2026-07-06", "is_trading_day": True},
        ],
    )

    dates = plan_daily_refresh_dates(
        DataAnalystsContext.from_paths(tmp_path, data_store),
        _calendar_contract(),
        from_date="2026-07-03",
        to_date="2026-07-06",
        today="2026-07-08",
    )

    assert dates == ["2026-07-03", "2026-07-06"]


def test_plan_daily_refresh_as_of_date_is_single_day_even_without_calendar(tmp_path):
    dates = plan_daily_refresh_dates(
        DataAnalystsContext.from_paths(tmp_path),
        _calendar_contract(),
        as_of_date="2026-07-08",
        today="2026-07-08",
    )

    assert dates == ["2026-07-08"]


def test_plan_daily_refresh_blocks_future_target_date(tmp_path):
    data_store = tmp_path / "data_store"
    _write_trading_calendar(data_store, [{"date": "2026-07-08", "is_trading_day": True}])

    with pytest.raises(DailyRefreshError, match="future"):
        plan_daily_refresh_dates(
            DataAnalystsContext.from_paths(tmp_path, data_store),
            _calendar_contract(),
            to_date="2026-07-09",
            today="2026-07-08",
        )


def test_write_daily_refresh_success_records_daily_result_and_state(tmp_path):
    data_store = tmp_path / "data_store"

    write_daily_refresh_success(
        data_store,
        as_of_date="2026-07-08",
        result={"status": "ready", "families": ["trading_calendar"]},
    )

    daily_result = json.loads(
        (data_store / "jobs" / "daily_results" / "2026-07-08.json").read_text(encoding="utf-8")
    )
    state = json.loads((data_store / "jobs" / "daily_state.json").read_text(encoding="utf-8"))
    assert daily_result["status"] == "ready"
    assert daily_result["as_of_date"] == "2026-07-08"
    assert state["last_ready_as_of_date"] == "2026-07-08"
    assert state["status"] == "ready"


@pytest.mark.parametrize(
    ("command", "expected_scope"),
    [("run-full-history", "full_history"), ("run-backfill", "bounded_backfill")],
)
def test_cli_maps_pipeline_run_scope_exactly(
    tmp_path, monkeypatch, command, expected_scope
):
    calls = []
    monkeypatch.setattr(
        cli_module,
        "load_runtime_config",
        lambda context: type(
            "Config", (), {"family_ids": set(), "artifact_contracts": {}}
        )(),
    )
    monkeypatch.setattr(
        cli_module,
        "run_pipeline",
        lambda context, config, **kwargs: calls.append(kwargs) or {"status": "ready"},
    )
    monkeypatch.setattr(
        cli_module,
        "verify_runtime",
        lambda context, as_of_date=None, **kwargs: {"status": "ready"},
    )

    assert main([command, "--project-root", str(tmp_path)]) == 0
    assert calls[0]["run_scope"] == expected_scope
    assert "allow_full_history" not in calls[0]


def _write_trading_calendar(data_store, rows):
    context = DataAnalystsContext.from_paths(data_store.parent, data_store)
    _publish_trading_calendar(context, rows)


def _write_daily_state(data_store, *, last_ready_as_of_date):
    target = data_store / "jobs" / "daily_state.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "last_ready_as_of_date": last_ready_as_of_date,
                "last_attempted_as_of_date": last_ready_as_of_date,
                "status": "ready",
            }
        ),
        encoding="utf-8",
    )
