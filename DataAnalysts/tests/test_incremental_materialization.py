import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import data_analysts.materialization as materialization_module
from data_analysts.adjusted_prices import AdjustmentError, build_adjusted_daily_prices
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.config import load_runtime_config
from data_analysts.extract import ExtractError
from data_analysts.materialization import (
    changed_tickers,
    load_canonical_rows,
    rematerialization_start,
    with_membership_exclusions,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.pipeline import run_pipeline
from data_analysts.security_panel import build_historical_security_panel
from test_historical_universe_pipeline import _write_configs


def _daily_price_contract() -> ArtifactContract:
    return ArtifactContract(
        contract_key="daily_price_volume",
        artifact_id="daily_price_volume",
        variant="default",
        layer="raw",
        base_path="canonical/raw/daily_price_volume",
        file_name="part.parquet",
        required_columns=("date", "ticker", "adj_factor", "adj_close", "data_cutoff_at"),
        logical_key=("date", "ticker"),
        publication_mode="partition_upsert",
        partition_name="year",
        partition_field="date",
        date_field="date",
        availability_field="date",
        pit_policy="source_date_lagged_to_decision_date",
        source_families=("daily_price_volume",),
    )


def _stock_dividend_profile(*, event_date: str, cutoff: str) -> dict:
    return {
        "family_id": "dividend_policy",
        "enabled": True,
        "connection": "tej",
        "collection": "APIMT1",
        "source_profile": "medium_pit_table",
        "primary_key": ["ticker", "source_date"],
        "date_fields": {"source_date": "mdate"},
        "event_date_fields": ["q1ex_date", "mex_date"],
        "availability": {"type": "source_available_date", "field": "mdate"},
        "partitioning": ["available_year"],
        "pit_policy": "source_available_date",
        "field_map": {
            "ticker": "coid",
            "source_date": "mdate",
            "source_available_date": "mdate",
            "data_cutoff_at": "data_cutoff_at",
            "q1ex_date": "q1ex_date",
            "q1mt_div": "q1mt_div",
            "mex_date": "mex_date",
            "mt_mer": "mt_mer",
        },
        "fixture_rows": [
            {
                "coid": "2330",
                "mdate": event_date,
                "q1ex_date": None,
                "q1mt_div": None,
                "mex_date": event_date,
                "mt_mer": 2.5,
                "data_cutoff_at": cutoff,
            }
        ],
    }


def test_load_canonical_rows_reads_only_manifest_listed_paths(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _daily_price_contract()
    listed = context.artifact_path(contract.path_for_partition("2026"))
    listed.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(
            [{"date": "2026-07-08", "ticker": "2330", "adj_factor": 1.0, "adj_close": 100.0, "data_cutoff_at": "2026-07-08T10:00:00Z"}]
        ),
        listed,
    )
    rogue = listed.parent / "rogue.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{"date": "2026-07-09", "ticker": "2317", "adj_factor": 9.0, "adj_close": 9.0, "data_cutoff_at": "2026-07-09T10:00:00Z"}]
        ),
        rogue,
    )
    manifest = context.store_path("manifests", "daily_price_volume.json")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"artifact_id": "daily_price_volume", "status": "ready", "artifact_paths": [contract.path_for_partition("2026")]}),
        encoding="utf-8",
    )

    rows = load_canonical_rows(context, contract, tickers={"2330", "2317"})

    assert [(row["date"], row["ticker"]) for row in rows] == [("2026-07-08", "2330")]


@pytest.mark.parametrize(
    ("missing_field", "message"),
    [("contract_key", "missing contract_key"), ("variant", "missing variant")],
)
def test_variant_materialization_requires_explicit_manifest_identity(
    tmp_path, missing_field, message
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = ArtifactContract(
        contract_key="universe_u:exact_date", artifact_id="universe_u",
        variant="exact_date", layer="derived",
        base_path="canonical/derived/universes/u/membership_by_date",
        file_name="membership.parquet",
        required_columns=("as_of_date", "ticker", "data_cutoff_at"),
        logical_key=("as_of_date", "ticker"),
        publication_mode="snapshot_by_value", partition_name="as_of_date",
        partition_field="as_of_date", date_field="as_of_date",
        availability_field="as_of_date", pit_policy="decision_date_membership",
        source_families=("security_panel",),
    )
    relative = contract.path_for_partition("2026-07-08")
    artifact = context.artifact_path(relative)
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{
        "as_of_date": "2026-07-08", "ticker": "2330",
        "data_cutoff_at": "2026-07-08T12:00:00Z",
    }]), artifact)
    manifest = context.store_path("manifests", contract.manifest_file_name)
    manifest.parent.mkdir(parents=True)
    payload = {
        "artifact_id": contract.artifact_id,
        "contract_key": contract.contract_key,
        "variant": contract.variant,
        "status": "ready", "artifact_paths": [relative],
    }
    payload.pop(missing_field)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactError, match=message):
        load_canonical_rows(context, contract)


def test_default_materialization_accepts_legacy_manifest_without_identity(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = ArtifactContract(
        contract_key="sample", artifact_id="sample", variant="default", layer="raw",
        base_path="canonical/raw/sample", file_name="part.parquet",
        required_columns=("date", "ticker", "data_cutoff_at"),
        logical_key=("date", "ticker"), publication_mode="partition_upsert",
        partition_name="year", partition_field="date", date_field="date",
        availability_field="date", pit_policy="source_date",
        source_families=("sample",),
    )
    relative = contract.path_for_partition("2026")
    artifact = context.artifact_path(relative)
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{
        "date": "2026-07-08", "ticker": "2330",
        "data_cutoff_at": "2026-07-08T12:00:00Z",
    }]), artifact)
    manifest = context.store_path("manifests", contract.manifest_file_name)
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "artifact_id": "sample", "status": "ready", "artifact_paths": [relative],
    }), encoding="utf-8")

    assert load_canonical_rows(context, contract)[0]["ticker"] == "2330"


def test_variant_materialization_blocks_when_only_legacy_filename_exists(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = ArtifactContract(
        contract_key="universe_u:historical", artifact_id="universe_u",
        variant="historical", layer="derived",
        base_path="canonical/derived/universes/u/membership_by_year",
        file_name="part.parquet",
        required_columns=("as_of_date", "ticker", "data_cutoff_at"),
        logical_key=("as_of_date", "ticker"),
        publication_mode="partition_upsert", partition_name="as_of_year",
        partition_field="as_of_date", date_field="as_of_date",
        availability_field="as_of_date", pit_policy="history",
        source_families=("security_panel_history",),
    )
    legacy = context.store_path("manifests", "universe_u.json")
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "artifact_id": "universe_u", "artifact_paths": [
            contract.path_for_partition("2026")
        ],
    }), encoding="utf-8")

    with pytest.raises(ArtifactError, match="legacy variant manifest requires migration"):
        load_canonical_rows(context, contract)


def test_rematerialization_start_uses_earliest_changed_price_or_action():
    assert rematerialization_start(
        [{"date": "2026-07-08", "ticker": "2330"}],
        [{"event_date": "2026-06-30", "ticker": "2330"}],
    ) == "2026-06-30"


def test_daily_adjustment_uses_prior_cumulative_factor_and_previous_close():
    rows = build_adjusted_daily_prices(
        [{"date": "2026-07-08", "ticker": "2330", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1}],
        dividend_events=[{"event_date": "2026-07-08", "ticker": "2330", "cash_dividend_per_share": 20}],
        initial_state_by_ticker={"2330": {"adj_factor": 1.25, "prev_close": 120.0}},
    )

    assert rows[0]["adj_factor"] == 1.5
    assert rows[0]["adj_close"] == 150.0


def test_daily_adv20_uses_prior_nineteen_observations():
    price_rows = [
        {
            "date": f"2026-07-{day:02d}",
            "ticker": "2330",
            "close": 100.0,
            "adj_close": 100.0,
            "volume": 1,
            "traded_value": float(day),
            "data_cutoff_at": f"2026-07-{day:02d}T10:00:00Z",
        }
        for day in range(1, 21)
    ]
    calendar = [
        {"date": f"2026-07-{day:02d}", "market": "TWSE", "is_trading_day": True}
        for day in range(1, 22)
    ]

    panel, _ = build_historical_security_panel(
        price_rows,
        [{"ticker": "2330", "market": "TWSE", "listed": True}],
        calendar,
        start_date="2026-07-20",
        end_date="2026-07-20",
    )

    assert panel[0]["adv20"] == 10.5


def test_full_history_then_daily_preserves_history_factor_and_adv20(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {item["family_id"]: item for item in payload["families"]}
    dates = [f"2026-07-{day:02d}" for day in range(1, 22)]
    profiles["daily_price_volume"]["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": value,
            "open_d": 100,
            "high_d": 100,
            "low_d": 100,
            "close_d": 100,
            "vol": 1,
            "amt": day,
            "mktcap": 500000000,
            "data_cutoff_at": f"{value}T10:00:00Z",
        }
        for day, value in enumerate(dates[:19], start=1)
    ]
    payload["families"].append(
        _stock_dividend_profile(
            event_date=dates[1], cutoff=f"{dates[1]}T10:00:00Z"
        )
    )
    profiles["trading_calendar"]["fixture_rows"] = [
        {
            "zdate": value,
            "mkt": "TWSE",
            "date_rmk": "",
            "date": value,
            "market": "TWSE",
            "is_trading_day": True,
            "data_cutoff_at": f"{value}T09:00:00Z",
        }
        for value in dates
    ]
    payload["families"].append(
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
            "fixture_rows": [
                {
                    "coid": "2330",
                    "mdate": value,
                    "tradable": True,
                    "data_cutoff_at": f"{value}T10:00:00Z",
                }
                for value in dates[:19]
            ],
        }
    )
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    run_pipeline(context, load_runtime_config(context), run_scope="full_history")

    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {item["family_id"]: item for item in payload["families"]}
    profiles["daily_price_volume"]["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": dates[19],
            "open_d": 100,
            "high_d": 100,
            "low_d": 100,
            "close_d": 100,
            "vol": 1,
            "amt": 20,
            "mktcap": 500000000,
            "data_cutoff_at": f"{dates[19]}T10:00:00Z",
        }
    ]
    profiles["daily_tradability"]["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": dates[19],
            "tradable": True,
            "data_cutoff_at": f"{dates[19]}T10:00:00Z",
        }
    ]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    run_pipeline(
        context,
        load_runtime_config(context),
        as_of_date=dates[19],
        run_scope="daily",
    )

    price_rows = load_canonical_rows(
        context, load_runtime_config(context).artifact_contracts["daily_price_volume"]
    )
    latest = next(row for row in price_rows if row["date"] == dates[19])
    assert len(price_rows) == 20
    assert latest["adj_factor"] == 1.25
    assert latest["adj_close"] == 125.0
    tradability_manifest = json.loads(
        context.store_path("manifests", "daily_tradability.json").read_text(encoding="utf-8")
    )
    assert tradability_manifest["date_range"] == [dates[0], dates[19]]
    panel_rows = load_canonical_rows(
        context,
        load_runtime_config(context).artifact_contracts["security_panel_history"],
        start_date=dates[19],
        end_date=dates[19],
    )
    assert panel_rows[0]["adv20"] == 10.5


def test_full_history_rebuild_ignores_source_adjustment_factor_and_previous_store(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    price_profile = next(
        item for item in payload["families"]
        if item["family_id"] == "daily_price_volume"
    )
    price_profile["field_map"]["adj_factor"] = "adj_factor"
    price_profile["fixture_rows"] = [{
        "coid": "2330", "mdate": "2025-01-02", "open_d": 100,
        "high_d": 100, "low_d": 100, "close_d": 100, "vol": 1,
        "amt": 10, "mktcap": 500000000, "adj_factor": 1.25,
        "data_cutoff_at": "2025-01-02T10:00:00Z",
    }]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    run_pipeline(
        context, load_runtime_config(context),
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )
    assert load_canonical_rows(
        context, load_runtime_config(context).artifact_contracts["daily_price_volume"]
    )[0]["adj_factor"] == 1.0

    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    price_profile = next(
        item for item in payload["families"]
        if item["family_id"] == "daily_price_volume"
    )
    price_profile["fixture_rows"] = [{
        "coid": "2330", "mdate": "2026-07-08", "open_d": 100,
        "high_d": 100, "low_d": 100, "close_d": 100, "vol": 1,
        "amt": 10, "mktcap": 500000000,
        "data_cutoff_at": "2026-07-08T10:00:00Z",
    }]
    calendar_profile = next(
        item for item in payload["families"]
        if item["family_id"] == "trading_calendar"
    )
    calendar_profile["fixture_rows"] = [
        {
            "zdate": value, "mkt": "TWSE", "date_rmk": "",
            "date": value, "market": "TWSE", "is_trading_day": True,
            "data_cutoff_at": f"{value}T09:00:00Z",
        }
        for value in ("2026-07-08", "2026-07-09")
    ]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    run_pipeline(
        context, load_runtime_config(context),
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    rebuilt = load_canonical_rows(
        context, load_runtime_config(context).artifact_contracts["daily_price_volume"]
    )
    assert [(row["date"], row["adj_factor"]) for row in rebuilt] == [
        ("2026-07-08", 1.0)
    ]


def test_historical_action_correction_propagates_forward_to_all_derived_outputs(tmp_path):
    test_full_history_then_daily_preserves_history_factor_and_adv20(tmp_path)
    universe_path = tmp_path / "configs" / "universe_specs.json"
    universe_payload = json.loads(universe_path.read_text(encoding="utf-8"))
    common_stock = next(
        item
        for item in universe_payload["universes"]
        if item["universe_id"] == "tw_common_stock_all"
    )
    common_stock["filters"].append(
        {"field": "adj_close", "op": "gte", "value": 100.0}
    )
    universe_path.write_text(json.dumps(universe_payload), encoding="utf-8")
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["families"].append(
        {
            "family_id": "capital_formation",
            "enabled": True,
            "connection": "tej",
            "collection": "APISTK1",
            "source_profile": "medium_pit_table",
            "primary_key": ["ticker", "ex_date"],
            "date_fields": {"source_date": "ex_date"},
            "availability": {"type": "source_available_date", "field": "ex_date"},
            "partitioning": ["available_year"],
            "pit_policy": "source_available_date",
            "fixture_rows": [
                {
                    "ticker": "2330",
                    "ex_date": "2026-07-10",
                    "precls": 50,
                    "exprice": 100,
                    "data_cutoff_at": "2026-07-20T12:00:00Z",
                }
            ],
        }
    )
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    run_pipeline(
        context,
        config,
        families={"capital_formation"},
        start_date="2026-07-10",
        end_date="2026-07-10",
        run_scope="bounded_backfill",
    )

    prices = load_canonical_rows(
        context,
        config.artifact_contracts["daily_price_volume"],
        start_date="2026-07-20",
        end_date="2026-07-20",
    )
    assert prices[0]["adj_factor"] == 0.625
    panel = load_canonical_rows(
        context,
        config.artifact_contracts["security_panel_history"],
        start_date="2026-07-20",
        end_date="2026-07-20",
    )
    assert panel[0]["adj_close"] == 62.5
    assert panel[0]["data_cutoff_at"] == "2026-07-20T12:00:00Z"
    memberships = load_canonical_rows(
        context,
        config.artifact_contracts["universe_tw_common_stock_all:historical"],
        start_date="2026-07-20",
        end_date="2026-07-20",
    )
    assert memberships[0]["as_of_date"] == "2026-07-20"
    assert memberships[0]["included"] is False
    assert memberships[0]["reason"] == "excluded_after_rematerialization"
    assert memberships[0]["data_cutoff_at"] == "2026-07-20T12:00:00Z"


@pytest.mark.parametrize("first_price_date", ["2026-07-13", "2026-07-16"])
def test_non_price_day_event_applies_to_next_traded_price(first_price_date):
    rows = build_adjusted_daily_prices(
        [
            {
                "date": first_price_date,
                "ticker": "2330",
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1,
                "data_cutoff_at": f"{first_price_date}T10:00:00Z",
            }
        ],
        capital_action_events=[
            {
                "event_date": "2026-07-11",
                "ticker": "2330",
                "action_type": "stock_price_adjustment",
                "price_adjustment_reference": 2.0,
                "data_cutoff_at": "2026-07-12T12:00:00Z",
            }
        ],
        initial_state_by_ticker={
            "2330": {
                "adj_factor": 1.25,
                "prev_close": 100.0,
                "last_materialized_date": "2026-07-10",
                "data_cutoff_at": "2026-07-10T10:00:00Z",
            }
        },
    )

    assert rows[0]["adj_factor"] == 2.5
    assert rows[0]["adj_close"] == 250.0


def test_adjusted_cutoff_accumulates_late_event_lineage_forward():
    rows = build_adjusted_daily_prices(
        [
            {
                "date": value,
                "ticker": "2330",
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1,
                "data_cutoff_at": f"{value}T10:00:00Z",
            }
            for value in ("2026-07-13", "2026-07-14")
        ],
        capital_action_events=[
            {
                "event_date": "2026-07-11",
                "ticker": "2330",
                "action_type": "stock_price_adjustment",
                "price_adjustment_reference": 2.0,
                "data_cutoff_at": "2026-07-20T12:00:00Z",
            }
        ],
        initial_state_by_ticker={
            "2330": {
                "adj_factor": 1.25,
                "prev_close": 100.0,
                "last_materialized_date": "2026-07-10",
                "data_cutoff_at": "2026-07-10T10:00:00Z",
            }
        },
    )

    assert [row["data_cutoff_at"] for row in rows] == [
        "2026-07-20T12:00:00Z",
        "2026-07-20T12:00:00Z",
    ]


def test_daily_applies_canonical_weekend_event_after_prior_materialized_price(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {item["family_id"]: item for item in payload["families"]}
    profiles["daily_price_volume"]["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": "2026-07-09",
            "open_d": 100,
            "high_d": 100,
            "low_d": 100,
            "close_d": 100,
            "vol": 1,
            "amt": 9,
            "mktcap": 500000000,
            "data_cutoff_at": "2026-07-09T10:00:00Z",
        },
        {
            "coid": "2330",
            "mdate": "2026-07-10",
            "open_d": 100,
            "high_d": 100,
            "low_d": 100,
            "close_d": 100,
            "vol": 1,
            "amt": 10,
            "mktcap": 500000000,
            "data_cutoff_at": "2026-07-10T10:00:00Z",
        }
    ]
    payload["families"].append(
        _stock_dividend_profile(
            event_date="2026-07-10", cutoff="2026-07-10T10:00:00Z"
        )
    )
    profiles["trading_calendar"]["fixture_rows"] = [
        {
            "zdate": value,
            "mkt": "TWSE",
            "date_rmk": "",
            "date": value,
            "market": "TWSE",
            "is_trading_day": True,
            "data_cutoff_at": f"{value}T09:00:00Z",
        }
        for value in ("2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14")
    ]
    payload["families"].append(
        {
            "family_id": "capital_formation",
            "enabled": True,
            "connection": "tej",
            "collection": "APISTK1",
            "source_profile": "medium_pit_table",
            "primary_key": ["ticker", "ex_date"],
            "date_fields": {"source_date": "ex_date"},
            "availability": {"type": "source_available_date", "field": "ex_date"},
            "partitioning": ["available_year"],
            "pit_policy": "source_available_date",
            "fixture_rows": [
                {
                    "ticker": "2330",
                    "ex_date": "2026-07-11",
                    "precls": 100,
                    "exprice": 50,
                    "data_cutoff_at": "2026-07-12T12:00:00Z",
                }
            ],
        }
    )
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    run_pipeline(context, load_runtime_config(context), run_scope="full_history")

    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {item["family_id"]: item for item in payload["families"]}
    profiles["daily_price_volume"]["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": "2026-07-13",
            "open_d": 100,
            "high_d": 100,
            "low_d": 100,
            "close_d": 100,
            "vol": 1,
            "amt": 13,
            "mktcap": 500000000,
            "data_cutoff_at": "2026-07-13T10:00:00Z",
        }
    ]
    profiles["capital_formation"]["fixture_rows"] = []
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    config = load_runtime_config(context)
    run_pipeline(
        context,
        config,
        families={"daily_price_volume"},
        as_of_date="2026-07-13",
        run_scope="daily",
    )

    rows = load_canonical_rows(
        context,
        config.artifact_contracts["daily_price_volume"],
        start_date="2026-07-13",
        end_date="2026-07-13",
    )
    assert rows[0]["adj_factor"] == 2.5
    assert rows[0]["data_cutoff_at"] == "2026-07-13T10:00:00Z"


def test_panel_affected_tickers_union_price_action_and_tradability_changes():
    assert changed_tickers(
        [{"ticker": "A"}],
        [{"ticker": "A"}],
        [{"ticker": "B"}],
    ) == {"A", "B"}


@pytest.mark.parametrize("family_id", ["security_master", "trading_calendar"])
def test_empty_full_replace_small_snapshot_fails_closed(tmp_path, family_id):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    for profile in payload["families"]:
        if profile["family_id"] == family_id:
            profile["fixture_rows"] = []
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ExtractError, match=f"{family_id}.*empty.*full-replace"):
        run_pipeline(
            context,
            load_runtime_config(context),
            families={family_id},
            run_scope="full_history",
        )


def test_existing_exclusion_tombstone_replaced_with_later_causal_cutoff():
    old = {
        "as_of_date": "2026-07-20",
        "effective_date": "2026-07-21",
        "universe_id": "tw_common_stock_all",
        "ticker": "2330",
        "rank": 1,
        "included": False,
        "reason": "excluded_after_rematerialization",
        "data_cutoff_at": "2026-07-20T12:00:00Z",
    }

    rows = with_membership_exclusions(
        [old],
        [],
        {"2026-07-20": "2026-07-25T12:00:00Z"},
    )

    assert len(rows) == 1
    assert rows[0]["ticker"] == "2330"
    assert rows[0]["included"] is False
    assert rows[0]["data_cutoff_at"] == "2026-07-25T12:00:00Z"


def test_bounded_price_without_canonical_seed_or_listing_evidence_fails_closed(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    with pytest.raises(ValueError, match="2330.*boundary=2025-01-02.*seed"):
        run_pipeline(
            context,
            config,
            families={"daily_price_volume"},
            start_date="2025-01-02",
            end_date="2025-01-02",
            run_scope="bounded_backfill",
        )


def test_bounded_price_with_corrupt_prior_factor_fails_closed(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    run_pipeline(context, config, run_scope="full_history")
    price_manifest = json.loads(
        context.store_path("manifests", "daily_price_volume.json").read_text(
            encoding="utf-8"
        )
    )
    price_path = context.artifact_path(price_manifest["artifact_paths"][0])
    rows = pq.read_table(price_path).to_pylist()
    rows[-1]["adj_factor"] = None
    pq.write_table(pa.Table.from_pylist(rows), price_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    price_profile = next(
        item for item in payload["families"] if item["family_id"] == "daily_price_volume"
    )
    price_profile["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": "2025-01-06",
            "open_d": 102,
            "high_d": 103,
            "low_d": 101,
            "close_d": 102,
            "vol": 12,
            "amt": 24000000,
            "mktcap": 520000000,
            "data_cutoff_at": "2025-01-06T10:00:00Z",
        }
    ]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="2330.*boundary=2025-01-06.*adj_factor"):
        run_pipeline(
            context,
            load_runtime_config(context),
            families={"daily_price_volume"},
            as_of_date="2025-01-06",
            run_scope="daily",
        )


def test_full_history_legitimate_new_listing_can_start_without_prior_seed(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {item["family_id"]: item for item in payload["families"]}
    profiles["daily_price_volume"]["fixture_rows"] = [
        profiles["daily_price_volume"]["fixture_rows"][0]
    ]
    profiles["security_master"]["fixture_rows"][0]["list_date"] = "2025-01-02"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)

    result = run_pipeline(
        context,
        load_runtime_config(context),
        run_scope="full_history",
    )

    assert result["status"] == "verifying"
    prices = load_canonical_rows(
        context,
        load_runtime_config(context).artifact_contracts["daily_price_volume"],
    )
    assert prices[0]["adj_factor"] == 1.0


def test_current_snapshot_listing_date_cannot_seed_bounded_history(tmp_path):
    _write_configs(tmp_path)
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {item["family_id"]: item for item in payload["families"]}
    profiles["daily_price_volume"]["fixture_rows"] = [
        {
            "coid": "2330",
            "mdate": "2020-01-02",
            "open_d": 100,
            "high_d": 100,
            "low_d": 100,
            "close_d": 100,
            "vol": 1,
            "amt": 10,
            "mktcap": 500000000,
            "data_cutoff_at": "2020-01-02T10:00:00Z",
        }
    ]
    profiles["security_master"]["fixture_rows"][0].update(
        {
            "list_date": "2020-01-02",
            "data_cutoff_at": "2026-07-20T12:00:00Z",
        }
    )
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ValueError, match="2330.*boundary=2020-01-02.*seed"):
        run_pipeline(
            context,
            load_runtime_config(context),
            families={"daily_price_volume", "security_master"},
            start_date="2020-01-02",
            end_date="2020-01-02",
            run_scope="bounded_backfill",
        )


def test_per_ticker_causal_boundaries_keep_later_ticker_prior_seed():
    existing = [
        {
            "ticker": "A",
            "date": "2019-12-31",
            "adj_factor": 1.1,
            "close": 90.0,
            "data_cutoff_at": "2019-12-31T10:00:00Z",
        },
        {
            "ticker": "A",
            "date": "2020-01-02",
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1,
            "adj_factor": 1.1,
            "data_cutoff_at": "2020-01-02T10:00:00Z",
        },
        {
            "ticker": "B",
            "date": "2023-12-29",
            "adj_factor": 1.5,
            "close": 120.0,
            "data_cutoff_at": "2023-12-29T10:00:00Z",
        },
    ]
    changed = [
        {
            "ticker": "B",
            "date": "2024-01-02",
            "open": 125.0,
            "high": 125.0,
            "low": 125.0,
            "close": 125.0,
            "volume": 1,
            "data_cutoff_at": "2024-01-02T10:00:00Z",
        }
    ]
    actions = [
        {
            "ticker": "A",
            "event_date": "2020-01-02",
            "action_type": "stock_price_adjustment",
            "price_adjustment_reference": 2.0,
            "data_cutoff_at": "2020-01-02T12:00:00Z",
        }
    ]
    starts = materialization_module.rematerialization_starts(
        changed,
        actions,
    )
    materialization_module.validate_adjustment_seeds(
        run_scope="daily",
        boundaries=starts,
        existing_prices=existing,
    )
    state = materialization_module.initial_adjustment_state_by_ticker_boundaries(
        existing,
        starts,
    )
    prices = materialization_module.rows_at_or_after_boundaries(
        [*existing, *changed], starts
    )
    applicable_actions = materialization_module.events_after_adjustment_boundary(
        actions,
        starts,
        state,
        starts,
    )
    rebuilt = build_adjusted_daily_prices(
        prices,
        capital_action_events=applicable_actions,
        initial_state_by_ticker=state,
    )

    assert starts == {"A": "2020-01-02", "B": "2024-01-02"}
    assert state["B"]["adj_factor"] == 1.5
    assert state["B"]["last_materialized_date"] == "2023-12-29"
    assert [(row["ticker"], row["date"]) for row in rebuilt] == [
        ("A", "2020-01-02"),
        ("B", "2024-01-02"),
    ]
    assert rebuilt[0]["adj_factor"] == 2.2
    assert rebuilt[1]["adj_factor"] == 1.5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("adj_factor", float("inf")),
        ("adj_factor", float("nan")),
        ("adj_factor", 0.0),
        ("prev_close", float("inf")),
        ("prev_close", float("nan")),
        ("prev_close", 0.0),
    ],
)
def test_bounded_seed_requires_finite_positive_factor_and_close(field, value):
    prior = {
        "ticker": "2330",
        "date": "2026-07-10",
        "adj_factor": 1.25,
        "close": 100.0,
    }
    prior["adj_factor" if field == "adj_factor" else "close"] = value

    with pytest.raises(ValueError, match=f"2330.*boundary=2026-07-13.*{field}"):
        materialization_module.validate_adjustment_seeds(
            run_scope="daily",
            boundaries={"2330": "2026-07-13"},
            existing_prices=[prior],
        )


@pytest.mark.parametrize(
    "state",
    [
        {"adj_factor": 1.25},
        {"adj_factor": 1.25, "prev_close": float("inf")},
        {"adj_factor": 1.25, "prev_close": float("nan")},
        {"adj_factor": 1.25, "prev_close": 0.0},
    ],
)
def test_cash_dividend_without_valid_previous_close_fails_closed(state):
    with pytest.raises(AdjustmentError, match="2330.*2026-07-13.*prev_close"):
        build_adjusted_daily_prices(
            [
                {
                    "date": "2026-07-13",
                    "ticker": "2330",
                    "open": 100,
                    "high": 100,
                    "low": 100,
                    "close": 100,
                    "volume": 1,
                    "data_cutoff_at": "2026-07-13T10:00:00Z",
                }
            ],
            dividend_events=[
                {
                    "event_date": "2026-07-13",
                    "ticker": "2330",
                    "cash_dividend_per_share": 5.0,
                    "data_cutoff_at": "2026-07-13T12:00:00Z",
                }
            ],
            initial_state_by_ticker={"2330": state},
        )


def test_cash_dividend_exceeding_previous_close_fails_closed():
    with pytest.raises(AdjustmentError, match="2330.*2026-07-13.*prev_close.*dividend"):
        build_adjusted_daily_prices(
            [
                {
                    "date": "2026-07-13",
                    "ticker": "2330",
                    "open": 100,
                    "high": 100,
                    "low": 100,
                    "close": 100,
                    "volume": 1,
                    "data_cutoff_at": "2026-07-13T10:00:00Z",
                }
            ],
            dividend_events=[
                {
                    "event_date": "2026-07-13",
                    "ticker": "2330",
                    "cash_dividend_per_share": 120.0,
                    "data_cutoff_at": "2026-07-13T12:00:00Z",
                }
            ],
            initial_state_by_ticker={
                "2330": {"adj_factor": 1.25, "prev_close": 100.0}
            },
        )
