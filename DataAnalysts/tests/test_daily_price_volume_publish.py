from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import data_analysts.filesystem as filesystem_module
import data_analysts.pipeline as pipeline_module
import data_analysts.partition_transactions as transactions_module

from data_analysts.adjusted_ohlc import (
    ADJUSTMENT_POLICY_ID,
    REQUIRED_ADJUSTED_OHLC_COLUMNS,
    AdjustmentSeed,
)
from data_analysts.adjusted_prices import AdjustmentError
from data_analysts.adjusted_ohlc_evidence import (
    audit_adjusted_ohlc as _audit_adjusted_ohlc_impl,
)
from data_analysts.artifacts import ArtifactPublisher
from data_analysts.config import load_runtime_config
from data_analysts.metadata import publish_data_store_metadata
from data_analysts.paths import DataAnalystsContext
from data_analysts.partition_transactions import PartitionSpec, PublishTransactionError
from data_analysts.pipeline import (
    _load_adjustment_seeds as _load_adjustment_seeds_impl,
    _prove_new_series_tickers as _prove_new_series_tickers_impl,
    _publish_daily_price_volume as _publish_daily_price_volume_impl,
    run_pipeline,
)


_TEST_DAILY_PRICE_SPEC = PartitionSpec(
    base_path="canonical/raw/daily_price_volume",
    partition_field="date",
    partition_name="year",
    key_fields=("date", "ticker"),
    required_columns=pipeline_module._DAILY_PRICE_REQUIRED_COLUMNS,
)
_TEST_EVENT_SPECS = {
    artifact_id: PartitionSpec(
        base_path=f"canonical/derived/events/{artifact_id}",
        partition_field="event_date",
        partition_name="event_year",
        key_fields=("event_date", "ticker", "source_dataset_id", "source_row_id"),
        required_columns=pipeline_module._EVENT_REQUIRED_COLUMNS[artifact_id],
        column_types=pipeline_module._EVENT_COLUMN_TYPES[artifact_id],
    )
    for artifact_id in pipeline_module._OFFICIAL_EVENT_IDS
}


def audit_adjusted_ohlc(context, manifest, **kwargs):
    kwargs.setdefault(
        "contracts",
        pipeline_module._contracts_for_explicit_specs(
            _TEST_DAILY_PRICE_SPEC, _TEST_EVENT_SPECS
        ),
    )
    return _audit_adjusted_ohlc_impl(context, manifest, **kwargs)


def _prove_new_series_tickers(*args, **kwargs):
    kwargs.setdefault(
        "contracts",
        pipeline_module._contracts_for_explicit_specs(
            _TEST_DAILY_PRICE_SPEC, _TEST_EVENT_SPECS
        ),
    )
    return _prove_new_series_tickers_impl(*args, **kwargs)


def _load_adjustment_seeds(*args, **kwargs):
    kwargs.setdefault(
        "price_contract",
        pipeline_module._contracts_for_explicit_specs(
            _TEST_DAILY_PRICE_SPEC, _TEST_EVENT_SPECS
        )["daily_price_volume"],
    )
    return _load_adjustment_seeds_impl(*args, **kwargs)


def _publish_daily_price_volume(*args, **kwargs):
    kwargs.setdefault("price_spec", _TEST_DAILY_PRICE_SPEC)
    kwargs.setdefault("event_specs", _TEST_EVENT_SPECS)
    return _publish_daily_price_volume_impl(*args, **kwargs)


def _raw_row(
    row_date: str,
    *,
    ticker: str = "2330",
    close: float = 10.0,
) -> dict[str, object]:
    return {
        "date": row_date,
        "ticker": ticker,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100.0,
        "traded_value": close * 100.0,
        "data_cutoff_at": f"{row_date}T10:00:00Z",
        "source_collection": "fixture.daily_price_volume",
    }


def _manifest(context: DataAnalystsContext) -> dict[str, object]:
    return json.loads(
        context.store_path("manifests", "daily_price_volume.json").read_text(
            encoding="utf-8"
        )
    )


def _formal_snapshot(context: DataAnalystsContext) -> dict[str, bytes]:
    paths = [
        context.store_path("manifests", "daily_price_volume.json"),
        context.store_path("diagnostics", "adjusted_ohlc_verification.json"),
        *sorted(
            context.store_path("canonical", "raw", "daily_price_volume").glob(
                "year=*/part.parquet"
            )
        ),
        *sorted(context.store_path("manifests").glob("*events.json")),
        *sorted(
            context.store_path("canonical", "derived", "events").glob(
                "*/event_year=*/part.parquet"
            )
        ),
    ]
    return {
        path.relative_to(context.data_store).as_posix(): path.read_bytes()
        for path in paths
        if path.exists()
    }


def _publish_full(
    context: DataAnalystsContext, rows: list[dict[str, object]]
):
    return _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        rows,
        [],
        [],
        full_rebuild=True,
    )


def _write_runtime_configs(project_root: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = project_root / "configs"
    target.mkdir(parents=True)
    for name in (
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
        "artifact_contracts.json",
    ):
        (target / name).write_bytes((source / name).read_bytes())


def _dividend_row(event_date: str, *, ticker: str = "2330") -> dict[str, object]:
    return {
        "event_date": event_date,
        "ex_date": event_date,
        "ticker": ticker,
        "cash_dividend_per_share": 1.0,
        "stock_dividend_ratio": 0.0,
        "source_dataset_id": "fixture.dividend",
        "source_row_id": f"{ticker}-{event_date}",
        "data_cutoff_at": f"{event_date}T09:00:00Z",
    }


def _capital_action_row(
    event_date: str, *, ticker: str = "2330"
) -> dict[str, object]:
    return {
        "event_date": event_date,
        "ex_date": event_date,
        "ticker": ticker,
        "action_type": "capital_reduction",
        "share_multiplier": 0.9,
        "cash_return_per_share": 0.0,
        "price_adjustment_reference": None,
        "source_dataset_id": "fixture.capital",
        "source_row_id": f"{ticker}-{event_date}",
        "data_cutoff_at": f"{event_date}T09:00:00Z",
    }


def _publish_event_fixture(
    context: DataAnalystsContext,
    artifact_id: str,
    rows: list[dict[str, object]],
) -> None:
    publisher = ArtifactPublisher(context)
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["event_date"])[:4], []).append(row)
    artifact_paths = []
    for year, partition_rows in grouped.items():
        path = (
            f"canonical/derived/events/{artifact_id}/"
            f"event_year={year}/part.parquet"
        )
        publisher.publish_parquet(
            path,
            rows=partition_rows,
            required_columns=list(
                _TEST_EVENT_SPECS[artifact_id].required_columns
            ),
        )
        artifact_paths.append(path)
    event_dates = [str(row["event_date"]) for row in rows]
    publisher.publish_manifest(
        artifact_id=artifact_id,
        layer="derived",
        source_families=[artifact_id],
        source_collections=[],
        columns=list(rows[0]),
        artifact_paths=artifact_paths,
        row_count=len(rows),
        date_range=[min(event_dates), max(event_dates)],
        availability_date_range=[min(event_dates), max(event_dates)],
        partitioning=["event_year"],
        pit_policy="event_date",
        data_cutoff_at=max(str(row["data_cutoff_at"]) for row in rows),
        duplicate_count=0,
        omitted_row_count=0,
        status="ready",
    )


def test_partial_publish_preserves_rows_and_builds_complete_manifest(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2025-12-31", close=8.0),
            _raw_row("2026-01-02", close=10.0),
            _raw_row("2026-01-03", close=11.0),
        ],
    )

    result = _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-03", close=12.0)],
        [],
        [],
        full_rebuild=False,
    )

    path = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    rows = pq.ParquetFile(path).read().to_pylist()
    assert [(row["date"], row["close"]) for row in rows] == [
        ("2026-01-02", 10.0),
        ("2026-01-03", 12.0),
    ]
    assert set(REQUIRED_ADJUSTED_OHLC_COLUMNS).issubset(rows[0])
    manifest = _manifest(context)
    assert manifest["artifact_paths"] == [
        "canonical/raw/daily_price_volume/year=2025/part.parquet",
        "canonical/raw/daily_price_volume/year=2026/part.parquet",
    ]
    assert manifest["row_count"] == 3
    assert manifest["date_range"] == ["2025-12-31", "2026-01-03"]
    assert manifest["adjustment_policy_id"] == ADJUSTMENT_POLICY_ID
    assert result.changed_paths == (
        "canonical/raw/daily_price_volume/year=2026/part.parquet",
    )
    assert result.evidence_payload["status"] == "ready"


def test_full_history_empty_allowed_event_replaces_old_ready_manifest(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-01"), _raw_row("2026-01-02")],
        [_dividend_row("2026-01-02")],
        [],
        full_rebuild=True,
    )
    before = json.loads(
        context.store_path("manifests", "dividend_events.json").read_text(
            encoding="utf-8"
        )
    )

    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-01"), _raw_row("2026-01-02")],
        [],
        [],
        replace_event_ids={"dividend_events"},
        full_rebuild=True,
    )

    after = json.loads(
        context.store_path("manifests", "dividend_events.json").read_text(
            encoding="utf-8"
        )
    )
    assert before["row_count"] == 1
    assert after["row_count"] == 0
    assert after["artifact_paths"] == []


def test_daily_price_manifest_fingerprints_use_staged_partition_bytes(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02", close=10.0)])
    formal_path = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    old_sha256 = hashlib.sha256(formal_path.read_bytes()).hexdigest()
    captured = {}

    def capture_transaction(
        transaction_context,
        staged_partitions,
        metadata_payloads,
        *,
        source_preconditions,
    ):
        captured["context"] = transaction_context
        captured["partitions"] = list(staged_partitions)
        captured["manifest"] = metadata_payloads[
            pipeline_module._DAILY_PRICE_MANIFEST_PATH
        ]
        captured["source_preconditions"] = source_preconditions

    monkeypatch.setattr(
        pipeline_module, "commit_publish_transaction", capture_transaction
    )

    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-02", close=20.0)],
        [],
        [],
        full_rebuild=False,
    )

    manifest = captured["manifest"]
    daily_partition = next(
        partition
        for partition in captured["partitions"]
        if partition.artifact_path
        == "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    assert captured["context"] == context
    assert captured["source_preconditions"]
    assert manifest["schema_version"] == "1.1"
    assert manifest["artifact_fingerprints"] == [
        {
            "artifact_path": daily_partition.artifact_path,
            "sha256": daily_partition.content_sha256,
        }
    ]
    assert daily_partition.content_sha256 != old_sha256
    assert hashlib.sha256(formal_path.read_bytes()).hexdigest() == old_sha256


def test_data_publish_integrates_with_feature_fingerprint_capture_and_validation(
    tmp_path,
):
    from feature_analysts.data_access import (
        capture_input_partition_fingerprints,
        load_manifest,
    )
    from feature_analysts.errors import FailClosedError

    _write_runtime_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    publish_data_store_metadata(context, load_runtime_config(context))
    _publish_full(
        context,
        [
            _raw_row("2025-12-31", close=8.0),
            _raw_row("2026-01-02", close=10.0),
        ],
    )
    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-02", close=12.0)],
        [],
        [],
        full_rebuild=False,
    )

    manifest = load_manifest(context.data_store, "daily_price_volume")
    selected_paths = tuple(
        context.artifact_path(path) for path in manifest.artifact_paths
    )
    captured = capture_input_partition_fingerprints(
        context.data_store,
        {"daily_price_volume": manifest},
        {"daily_price_volume": selected_paths},
    )

    assert manifest.schema_version == "1.1"
    assert [(item.artifact_path, item.sha256) for item in captured] == [
        (item.artifact_path, item.sha256) for item in manifest.artifact_fingerprints
    ]

    selected_paths[0].write_bytes(selected_paths[0].read_bytes() + b"tampered")
    with pytest.raises(FailClosedError, match="artifact_fingerprint_mismatch"):
        capture_input_partition_fingerprints(
            context.data_store,
            {"daily_price_volume": manifest},
            {"daily_price_volume": selected_paths},
        )


def test_partial_seed_is_loaded_from_verified_previous_formal_row(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02", close=10.0)])
    manifest = _manifest(context)

    seeds = _load_adjustment_seeds(
        context,
        manifest,
        tickers={"2330"},
        before_date="2026-01-03",
    )

    assert seeds == {"2330": AdjustmentSeed(adj_factor=1.0, previous_close=10.0)}


def test_seed_read_pushes_ticker_and_strict_before_date_filters(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [_raw_row("2026-01-02"), _raw_row("2026-12-31", close=99.0)],
    )
    manifest = _manifest(context)
    real_read_table = pq.read_table
    observed = []

    def require_pit_filters(path, *args, **kwargs):
        filters = kwargs.get("filters")
        observed.append(filters)
        assert ("ticker", "in", ["2330"]) in filters
        assert ("date", "<", "2026-06-01") in filters
        return real_read_table(path, *args, **kwargs)

    monkeypatch.setattr(pipeline_module.pq, "read_table", require_pit_filters)

    seeds = _load_adjustment_seeds(
        context, manifest, tickers={"2330"}, before_date="2026-06-01"
    )

    assert observed
    assert seeds["2330"].previous_close == 10.0


def test_new_series_proof_checks_price_and_official_event_manifests(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])

    assert _prove_new_series_tickers(
        context, tickers={"9999"}, before_date="2026-01-03"
    ) == {"9999"}

    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    target = context.artifact_path(event_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    event_rows = [
        {
            "event_date": "2026-01-02",
            "ticker": "9999",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }
    ]
    pq.write_table(pa.Table.from_pylist(event_rows), target)
    event_manifest = {
        "artifact_id": "dividend_events",
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": [event_path],
        "row_count": 1,
        "date_range": ["2026-01-02", "2026-01-02"],
        "columns": list(event_rows[0]),
    }
    manifest_path = context.store_path("manifests", "dividend_events.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")

    assert _prove_new_series_tickers(
        context, tickers={"9999"}, before_date="2026-01-03"
    ) == set()


def test_new_series_proof_does_not_read_future_event_partitions(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    future_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2027/part.parquet"
    )
    target = context.artifact_path(future_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"future partition must not be opened")
    event_manifest = {
        "artifact_id": "dividend_events",
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": [future_path],
        "row_count": 1,
        "date_range": ["2027-01-02", "2027-01-02"],
        "columns": [
            "event_date",
            "ticker",
            "cash_dividend_per_share",
            "stock_dividend_ratio",
        ],
    }
    manifest_path = context.store_path("manifests", "dividend_events.json")
    manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")

    assert _prove_new_series_tickers(
        context, tickers={"9999"}, before_date="2026-01-03"
    ) == {"9999"}


def test_new_series_reads_push_strict_before_date_filter_with_same_year_future_rows(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    target = context.artifact_path(event_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [_dividend_row("2026-12-31", ticker="9999")]
    pq.write_table(pa.Table.from_pylist(rows), target)
    manifest_path = context.store_path("manifests", "dividend_events.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "artifact_id": "dividend_events",
                "schema_version": "1.0",
                "status": "ready",
                "artifact_paths": [event_path],
                "row_count": 1,
                "date_range": ["2026-12-31", "2026-12-31"],
                "columns": list(rows[0]),
            }
        ),
        encoding="utf-8",
    )
    real_read_table = pq.read_table

    def require_pit_filters(path, *args, **kwargs):
        filters = kwargs.get("filters")
        assert ("ticker", "in", ["9999"]) in filters
        date_column = kwargs["columns"][0]
        assert (date_column, "<", "2026-06-01") in filters
        return real_read_table(path, *args, **kwargs)

    monkeypatch.setattr(pipeline_module.pq, "read_table", require_pit_filters)

    assert _prove_new_series_tickers(
        context, tickers={"9999"}, before_date="2026-06-01"
    ) == {"9999"}


def test_event_and_price_publish_commit_atomically_without_event_self_block(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02", close=10.0)])

    result = _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-03", close=9.0)],
        [_dividend_row("2026-01-03")],
        [],
        full_rebuild=False,
    )

    event_manifest = json.loads(
        context.store_path("manifests", "dividend_events.json").read_text(
            encoding="utf-8"
        )
    )
    assert event_manifest["status"] == "ready"
    assert event_manifest["row_count"] == 1
    assert result.evidence_payload["status"] == "ready"
    dependency = result.evidence_payload["event_dependencies"]["dividend_events"]
    assert dependency["manifest_fingerprint"] is not None


@pytest.mark.parametrize(
    ("artifact_id", "row_factory"),
    [
        ("dividend_events", _dividend_row),
        ("capital_action_events", _capital_action_row),
    ],
)
def test_transactional_event_manifest_preserves_generic_complete_surface_semantics(
    tmp_path, artifact_id, row_factory
):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)
    prior_row = row_factory("2025-12-31")
    prior_row["data_cutoff_at"] = "2026-07-01T00:00:00Z"
    _publish_event_fixture(context, artifact_id, [prior_row])
    current_manifest = json.loads(
        context.store_path("manifests", f"{artifact_id}.json").read_text(
            encoding="utf-8"
        )
    )
    incoming = row_factory("2025-01-01")
    staged = pipeline_module.stage_partition_rows(
        context,
        [incoming],
        _TEST_EVENT_SPECS[artifact_id],
        mode="upsert",
    )

    prospective = pipeline_module._build_event_manifest(
        context,
        artifact_id,
        pipeline_module._contracts_for_explicit_specs(
            _TEST_DAILY_PRICE_SPEC, _TEST_EVENT_SPECS
        )[artifact_id],
        current_manifest,
        staged,
        [incoming],
        full_rebuild=False,
    )

    assert prospective["artifact_paths"] == current_manifest["artifact_paths"]
    assert prospective["row_count"] == 2
    assert prospective["date_range"] == ["2025-01-01", "2025-12-31"]
    assert prospective["availability_date_range"] == prospective["date_range"]
    assert prospective["data_cutoff_at"] == "2026-07-01T00:00:00Z"


def test_changed_event_recalculates_existing_price_suffix_and_recertifies(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [_raw_row("2025-01-02", close=10.0), _raw_row("2026-01-02", close=11.0)],
    )
    before = _formal_snapshot(context)

    result = _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-03", close=12.0)],
        [_dividend_row("2025-06-01")],
        [],
        full_rebuild=False,
    )

    assert result.evidence_payload["status"] == "ready"
    assert set(result.changed_paths) == {
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    }
    assert _formal_snapshot(context) != before
    immediate_full = audit_adjusted_ohlc(context, _manifest(context), mode="full")
    assert immediate_full["status"] == "ready", immediate_full


def test_out_of_band_older_event_drift_blocks_later_transaction_and_preserves_formal_bytes(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2025-01-02", close=10.0),
            _raw_row("2025-12-30", close=10.0),
        ],
    )
    publisher = ArtifactPublisher(context)
    _publish_event_fixture(context, "dividend_events", [_dividend_row("2025-06-01")])
    immediate_full = audit_adjusted_ohlc(context, _manifest(context), mode="full")
    assert immediate_full["status"] == "blocked"
    before = _formal_snapshot(context)

    with pytest.raises(AdjustmentError, match="core adjusted OHLC violations"):
        _publish_daily_price_volume(
            context,
            publisher,
            [_raw_row("2026-01-02", close=9.0)],
            [_dividend_row("2026-01-02")],
            [],
            full_rebuild=False,
        )

    assert _formal_snapshot(context) == before


def test_out_of_band_event_drift_revalidates_unchanged_price_suffix_without_rewrite(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2025-12-30", close=10.0),
            _raw_row("2026-01-02", close=10.0),
        ],
    )
    publisher = ArtifactPublisher(context)
    old_event = _dividend_row("2025-12-31")
    _publish_event_fixture(context, "dividend_events", [old_event])
    unchanged_2025 = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2025/part.parquet"
    )
    unchanged_bytes = unchanged_2025.read_bytes()
    replaced_price_paths: list[str] = []
    real_replace = filesystem_module.os.replace

    def record_price_replacements(source, destination):
        destination_path = Path(destination)
        if (
            destination_path.suffix == ".parquet"
            and "daily_price_volume" in destination_path.as_posix()
        ):
            replaced_price_paths.append(destination_path.parent.name)
        return real_replace(source, destination)

    monkeypatch.setattr(filesystem_module.os, "replace", record_price_replacements)

    corrected = _raw_row("2026-01-02", close=9.0)
    corrected.update(
        {
            "adj_factor": 10.0 / 9.0,
            "cash_dividend": 1.0,
            "stock_event_factor": None,
            "cash_factor": 10.0 / 9.0,
            "factor_combined": 10.0 / 9.0,
            "price_adjustment_status": "adjusted_close_ready",
            "adj_open": 10.0,
            "adj_high": 10.0,
            "adj_low": 10.0,
            "adj_close": 10.0,
        }
    )
    metadata_snapshot = pipeline_module._formal_metadata_snapshot(context)
    current_manifest = metadata_snapshot[
        pipeline_module._DAILY_PRICE_MANIFEST_PATH
    ].payload
    previous_evidence = metadata_snapshot[
        pipeline_module._ADJUSTED_OHLC_EVIDENCE_PATH
    ].payload
    captured_event_manifests = {
        artifact_id: metadata_snapshot[f"manifests/{artifact_id}.json"].payload
        for artifact_id in pipeline_module._OFFICIAL_EVENT_IDS
    }
    staged = pipeline_module.stage_partition_rows(
        context,
        [corrected],
        _TEST_DAILY_PRICE_SPEC,
        mode="upsert",
    )
    changed_paths = {partition.artifact_path for partition in staged}
    manifest_payload = pipeline_module._build_daily_price_manifest(
        context,
        pipeline_module._contracts_for_explicit_specs(
            _TEST_DAILY_PRICE_SPEC, _TEST_EVENT_SPECS
        )["daily_price_volume"],
        current_manifest,
        previous_evidence,
        staged,
        [corrected],
        full_rebuild=False,
    )
    evidence_payload = audit_adjusted_ohlc(
        context,
        manifest_payload,
        mode="incremental",
        changed_paths=changed_paths,
        previous_evidence=previous_evidence,
        path_overrides={
            partition.artifact_path: partition.staged_path for partition in staged
        },
        manifest_overrides=captured_event_manifests,
        formal_event_manifest_overrides=captured_event_manifests,
        changed_event_paths=set(),
    )
    assert evidence_payload["status"] == "ready", evidence_payload
    source_preconditions = pipeline_module._daily_price_source_preconditions(
        context,
        evidence_payload,
        staged,
        changed_paths=changed_paths,
        changed_event_paths=set(),
        metadata_snapshot=metadata_snapshot,
    )
    pipeline_module.commit_publish_transaction(
        context,
        staged,
        {
            pipeline_module._DAILY_PRICE_MANIFEST_PATH: manifest_payload,
            pipeline_module._ADJUSTED_OHLC_EVIDENCE_PATH: evidence_payload,
        },
        source_preconditions=source_preconditions,
    )

    assert changed_paths == {
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    }
    assert unchanged_2025.read_bytes() == unchanged_bytes
    assert replaced_price_paths == ["year=2026"]
    immediate_full = audit_adjusted_ohlc(context, _manifest(context), mode="full")
    assert immediate_full["status"] == "ready", immediate_full


def test_entry_metadata_snapshot_rejects_concurrent_update(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    real_audit = pipeline_module.audit_adjusted_ohlc
    manifest_path = context.store_path("manifests", "daily_price_volume.json")

    def mutate_after_audit(*args, **kwargs):
        result = real_audit(*args, **kwargs)
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(pipeline_module, "audit_adjusted_ohlc", mutate_after_audit)

    with pytest.raises(PublishTransactionError, match="source precondition hash mismatch"):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03")],
            [],
            [],
            full_rebuild=False,
        )


def test_retained_partition_drift_after_audit_aborts_incremental_publish_atomically(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2025-12-31", close=8.0),
            _raw_row("2026-01-02", close=10.0),
        ],
    )
    retained_path = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2025/part.parquet"
    )
    rewritten_path = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    manifest_path = context.store_path("manifests", "daily_price_volume.json")
    evidence_path = context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    )
    rewritten_before = rewritten_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    evidence_before = evidence_path.read_bytes()
    real_audit = pipeline_module.audit_adjusted_ohlc
    retained_after_drift = None

    def drift_retained_partition_after_audit(*args, **kwargs):
        nonlocal retained_after_drift
        result = real_audit(*args, **kwargs)
        rows = pq.ParquetFile(retained_path).read().to_pylist()
        rows[0]["volume"] = 101.0
        pq.write_table(pa.Table.from_pylist(rows), retained_path)
        retained_after_drift = retained_path.read_bytes()
        return result

    monkeypatch.setattr(
        pipeline_module,
        "audit_adjusted_ohlc",
        drift_retained_partition_after_audit,
    )

    with pytest.raises(PublishTransactionError, match="source precondition hash mismatch"):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03", close=11.0)],
            [],
            [],
            full_rebuild=False,
        )

    assert retained_after_drift is not None
    assert retained_path.read_bytes() == retained_after_drift
    assert rewritten_path.read_bytes() == rewritten_before
    assert manifest_path.read_bytes() == manifest_before
    assert evidence_path.read_bytes() == evidence_before


def test_formal_metadata_is_opened_once_and_payload_matches_captured_bytes(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    formal_paths = {
        context.store_path("manifests", "daily_price_volume.json"),
        context.store_path("diagnostics", "adjusted_ohlc_verification.json"),
        context.store_path("manifests", "dividend_events.json"),
        context.store_path("manifests", "capital_action_events.json"),
    }
    expected_bytes = {
        path: path.read_bytes() if path.is_file() else None for path in formal_paths
    }
    real_open = Path.open
    opens = {path: 0 for path in formal_paths}
    captured = {}
    real_snapshot = pipeline_module._formal_metadata_snapshot

    def counted_open(path, *args, **kwargs):
        resolved = Path(path)
        if resolved in opens:
            opens[resolved] += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    def capture_snapshot(*args, **kwargs):
        captured.update(real_snapshot(*args, **kwargs))
        return captured

    monkeypatch.setattr(pipeline_module, "_formal_metadata_snapshot", capture_snapshot)
    monkeypatch.setattr(pipeline_module, "commit_publish_transaction", lambda *a, **k: None)

    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-03")],
        [],
        [],
        full_rebuild=False,
    )

    assert opens == {
        path: (1 if path.is_file() else 0) for path in formal_paths
    }
    for artifact_path, snapshot in captured.items():
        target = context.artifact_path(artifact_path)
        content = expected_bytes[target]
        if content is None:
            assert snapshot.sha256 is None
            assert snapshot.payload is None
        else:
            assert snapshot.sha256 == hashlib.sha256(content).hexdigest()
            assert snapshot.payload == json.loads(content)


def test_entry_snapshot_cannot_resurrect_transient_manifest_after_rollback(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-01")])
    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-02")],
        [_dividend_row("2026-01-02")],
        [],
        full_rebuild=False,
    )
    manifest_path = context.store_path("manifests", "dividend_events.json")
    stable_bytes = manifest_path.read_bytes()
    transient = json.loads(stable_bytes)
    transient["source_collections"] = ["transient.concurrent.publish"]
    transient_bytes = json.dumps(transient, sort_keys=True).encode("utf-8")
    real_snapshot = pipeline_module._formal_metadata_snapshot

    def publish_transient_after_snapshot(*args, **kwargs):
        snapshot = real_snapshot(*args, **kwargs)
        manifest_path.write_bytes(transient_bytes)
        return snapshot

    real_build_manifest = pipeline_module._build_event_manifest

    def rollback_before_commit(*args, **kwargs):
        result = real_build_manifest(*args, **kwargs)
        manifest_path.write_bytes(stable_bytes)
        return result

    monkeypatch.setattr(
        pipeline_module, "_formal_metadata_snapshot", publish_transient_after_snapshot
    )
    monkeypatch.setattr(
        pipeline_module, "_build_event_manifest", rollback_before_commit
    )

    try:
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03", close=8.0)],
            [_dividend_row("2026-01-03")],
            [],
            full_rebuild=False,
        )
    except PublishTransactionError:
        pass

    final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "transient.concurrent.publish" not in final_manifest["source_collections"]


def test_new_series_proof_rejects_stale_formal_price_partition(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    path = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    rows = pq.ParquetFile(path).read().to_pylist()
    rows[0]["close"] = 11.0
    pq.write_table(pa.Table.from_pylist(rows), path)

    assert _prove_new_series_tickers(
        context, tickers={"9999"}, before_date="2026-01-03"
    ) == set()


def test_partial_publish_does_not_treat_unverified_missing_seed_as_new(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    context.store_path("diagnostics", "adjusted_ohlc_verification.json").unlink()

    with pytest.raises(AdjustmentError, match="cannot prove new adjustment series"):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03", ticker="9999")],
            [],
            [],
            full_rebuild=False,
        )


def test_cross_year_backfill_upserts_only_touched_years(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2024-12-31", close=7.0),
            _raw_row("2025-12-31", close=8.0),
            _raw_row("2026-01-02", close=9.0),
        ],
    )
    untouched = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2024/part.parquet"
    )
    untouched_hash = hashlib.sha256(untouched.read_bytes()).hexdigest()

    result = _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [
            _raw_row("2025-12-31", close=18.0),
            _raw_row("2026-01-02", close=19.0),
        ],
        [],
        [],
        full_rebuild=False,
    )

    assert hashlib.sha256(untouched.read_bytes()).hexdigest() == untouched_hash
    assert set(result.changed_paths) == {
        "canonical/raw/daily_price_volume/year=2025/part.parquet",
        "canonical/raw/daily_price_volume/year=2026/part.parquet",
    }
    assert _manifest(context)["row_count"] == 3


def test_full_rebuild_replaces_rows_within_touched_partition(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2026-01-02", close=10.0),
            _raw_row("2026-01-03", close=11.0),
        ],
    )

    _publish_full(context, [_raw_row("2026-01-03", close=20.0)])

    path = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    rows = pq.ParquetFile(path).read().to_pylist()
    assert [(row["date"], row["close"]) for row in rows] == [
        ("2026-01-03", 20.0)
    ]
    assert _manifest(context)["row_count"] == 1


def test_staged_validation_failure_leaves_formal_bytes_unchanged(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    before = _formal_snapshot(context)
    invalid = _raw_row("2026-01-03")
    invalid["high"] = 9.0

    with pytest.raises(AdjustmentError, match="staged adjusted OHLC validation"):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [invalid],
            [],
            [],
            full_rebuild=False,
        )

    assert _formal_snapshot(context) == before


@pytest.mark.parametrize(
    "failure_target", ["year=2026", "adjusted_ohlc_verification.json"]
)
def test_commit_failure_rolls_back_partitions_manifest_and_evidence(
    tmp_path, monkeypatch, failure_target
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2025-01-02", close=8.0),
            _raw_row("2026-01-02", close=9.0),
        ],
    )
    before = _formal_snapshot(context)
    real_replace = __import__("os").replace
    injected = False

    def fail_once(source, target):
        nonlocal injected
        if not injected and failure_target in str(target):
            injected = True
            raise OSError(f"injected {failure_target} failure")
        return real_replace(source, target)

    monkeypatch.setattr("data_analysts.filesystem.os.replace", fail_once)

    with pytest.raises(PublishTransactionError):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [
                _raw_row("2025-01-03", close=18.0),
                _raw_row("2026-01-03", close=19.0),
            ],
            [],
            [],
            full_rebuild=False,
        )

    assert injected is True
    assert _formal_snapshot(context) == before


@pytest.mark.parametrize(
    "failure_target",
    [
        "dividend_events\\event_year=2026",
        "daily_price_volume\\year=2026",
        "dividend_events.json",
        "daily_price_volume.json",
        "adjusted_ohlc_verification.json",
    ],
)
def test_event_price_transaction_failure_restores_every_formal_surface(
    tmp_path, monkeypatch, failure_target
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02", close=10.0)])
    before = _formal_snapshot(context)
    real_replace = __import__("os").replace
    injected = False

    def fail_once(source, target):
        nonlocal injected
        if not injected and failure_target in str(target):
            injected = True
            raise OSError(f"injected {failure_target} failure")
        return real_replace(source, target)

    monkeypatch.setattr("data_analysts.filesystem.os.replace", fail_once)

    with pytest.raises(PublishTransactionError):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03", close=9.0)],
            [_dividend_row("2026-01-03")],
            [],
            full_rebuild=False,
        )

    assert injected is True
    assert _formal_snapshot(context) == before


@pytest.mark.parametrize("inject_failure", [False, True])
def test_run_pipeline_event_day_advances_or_rolls_back_all_formal_surfaces(
    tmp_path, monkeypatch, inject_failure
):
    _write_runtime_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    _publish_full(context, [_raw_row("2026-01-02", close=10.0)])
    before = _formal_snapshot(context)
    family_rows = {
        "daily_price_volume": [_raw_row("2026-01-03", close=9.0)],
        "dividend_policy": [
            {
                "ticker": "2330",
                "ex_date": "2026-01-03",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
                "source_row_id": "dividend-2026-01-03",
                "data_cutoff_at": "2026-01-03T09:00:00Z",
            }
        ],
        "capital_formation": [
            {
                "ticker": "2330",
                "ex_date": "2026-01-03",
                "action_type": "capital_reduction",
                "share_multiplier": 0.9,
                "cash_return_per_share": 0.0,
                "price_adjustment_reference": 1.0,
                "source_row_id": "capital-2026-01-03",
                "data_cutoff_at": "2026-01-03T09:30:00Z",
            }
        ],
    }

    def fixed_rows(*args, **kwargs):
        requested = args[1]
        yield from (
            (family_id, rows)
            for family_id, rows in family_rows.items()
            if not requested or family_id in requested
        )

    monkeypatch.setattr(pipeline_module, "_iter_family_rows", fixed_rows)
    if inject_failure:
        real_replace = __import__("os").replace
        injected = False

        def fail_price_replace_once(source, target):
            nonlocal injected
            if (
                not injected
                and "daily_price_volume" in str(target)
                and "year=2026" in str(target)
            ):
                injected = True
                raise OSError("injected run_pipeline price publish failure")
            return real_replace(source, target)

        monkeypatch.setattr(
            "data_analysts.filesystem.os.replace",
            fail_price_replace_once,
        )
        with pytest.raises(PublishTransactionError):
            run_pipeline(
                context,
                config,
                families=set(family_rows),
                start_date="2026-01-03",
                end_date="2026-01-03",
            )
        assert injected is True
        assert _formal_snapshot(context) == before
        return

    result = run_pipeline(
        context,
        config,
        families=set(family_rows),
        start_date="2026-01-03",
        end_date="2026-01-03",
    )

    assert result["status"] == "verifying"
    assert result["daily_price_volume"] == {
        "changed_partition_count": 1,
        "verification_status": "ready",
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
    }
    assert "rows_for_downstream" not in result
    after = _formal_snapshot(context)
    for path in (
        "manifests/dividend_events.json",
        "manifests/capital_action_events.json",
        "manifests/daily_price_volume.json",
        "diagnostics/adjusted_ohlc_verification.json",
    ):
        assert after[path] != before.get(path)
    for artifact_id in ("dividend_events", "capital_action_events"):
        manifest = json.loads(after[f"manifests/{artifact_id}.json"])
        assert manifest["row_count"] == 1
        assert manifest["availability_date_range"] == ["2026-01-03", "2026-01-03"]


@pytest.mark.parametrize("failure_surface", ["evidence", "metadata"])
def test_precommit_failure_leaves_all_formal_bytes_unchanged(
    tmp_path, monkeypatch, failure_surface
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    before = _formal_snapshot(context)

    if failure_surface == "evidence":
        monkeypatch.setattr(
            "data_analysts.pipeline.audit_adjusted_ohlc",
            lambda *args, **kwargs: {"status": "blocked", "blocked_reasons": ["injected"]},
        )
    else:
        monkeypatch.setattr(
            "data_analysts.pipeline.build_manifest_payload",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected metadata")),
        )

    with pytest.raises((AdjustmentError, RuntimeError)):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03")],
            [],
            [],
            full_rebuild=False,
        )

    assert _formal_snapshot(context) == before


def test_consecutive_event_only_refreshes_merge_both_event_families_and_recertify_prices(
    tmp_path, monkeypatch
):
    _write_runtime_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    _publish_full(
        context,
        [
            _raw_row("2026-01-01", close=10.0),
            _raw_row("2026-01-02", close=9.0),
            _raw_row("2026-01-03", close=8.0),
        ],
    )
    batches = [
        {
            "dividend_policy": [
                {
                    "ticker": "2330",
                    "ex_date": "2026-01-02",
                    "cash_dividend_per_share": 1.0,
                    "stock_dividend_ratio": 0.0,
                    "source_row_id": "dividend-first",
                    "data_cutoff_at": "2026-01-02T09:00:00Z",
                }
            ]
        },
        {
            "dividend_policy": [
                {
                    "ticker": "2330",
                    "ex_date": "2026-01-03",
                    "cash_dividend_per_share": 0.5,
                    "stock_dividend_ratio": 0.0,
                    "source_row_id": "dividend-second",
                    "data_cutoff_at": "2026-01-03T09:00:00Z",
                }
            ],
            "capital_formation": [
                {
                    "ticker": "2330",
                    "ex_date": "2026-01-03",
                    "precls": 10.0,
                    "exprice": 5.0,
                    "source_row_id": "capital-second",
                    "data_cutoff_at": "2026-01-03T09:30:00Z",
                }
            ],
        },
    ]

    def event_only_rows(*args, **kwargs):
        yield from batches.pop(0).items()

    monkeypatch.setattr(pipeline_module, "_iter_family_rows", event_only_rows)
    first = run_pipeline(
        context,
        config,
        families={"dividend_policy"},
        start_date="2026-01-02",
        end_date="2026-01-02",
    )
    first_evidence = context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    ).read_bytes()
    second = run_pipeline(
        context,
        config,
        families={"dividend_policy", "capital_formation"},
        start_date="2026-01-03",
        end_date="2026-01-03",
    )

    assert first["daily_price_volume"]["verification_status"] == "ready"
    assert second["daily_price_volume"]["verification_status"] == "ready"
    assert first_evidence != context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    ).read_bytes()
    dividend_manifest = json.loads(
        context.store_path("manifests", "dividend_events.json").read_text(
            encoding="utf-8"
        )
    )
    capital_manifest = json.loads(
        context.store_path("manifests", "capital_action_events.json").read_text(
            encoding="utf-8"
        )
    )
    assert dividend_manifest["row_count"] == 2
    assert capital_manifest["row_count"] == 1
    immediate_full = audit_adjusted_ohlc(context, _manifest(context), mode="full")
    assert immediate_full["status"] == "ready", immediate_full


@pytest.mark.parametrize(
    "failure_target",
    [
        "dividend_events\\event_year=2026",
        "daily_price_volume\\year=2026",
        "dividend_events.json",
        "daily_price_volume.json",
        "adjusted_ohlc_verification.json",
    ],
)
def test_event_only_failure_rolls_back_event_price_manifest_and_evidence(
    tmp_path, monkeypatch, failure_target
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2026-01-01", close=10.0),
            _raw_row("2026-01-02", close=9.0),
        ],
    )
    before = _formal_snapshot(context)
    real_replace = filesystem_module.os.replace
    injected = False

    def fail_once(source, target):
        nonlocal injected
        if not injected and failure_target in str(target):
            injected = True
            raise OSError(f"injected event-only failure: {failure_target}")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", fail_once)

    with pytest.raises(PublishTransactionError):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [],
            [_dividend_row("2026-01-02")],
            [],
            full_rebuild=False,
        )

    assert injected is True
    assert _formal_snapshot(context) == before


def test_touched_price_partition_changed_after_entry_snapshot_is_not_legalized(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-02")])
    metadata_before = {
        path: content
        for path, content in _formal_snapshot(context).items()
        if path.endswith(".json")
    }
    target = context.artifact_path(
        "canonical/raw/daily_price_volume/year=2026/part.parquet"
    )
    real_stage = pipeline_module.stage_partition_rows
    mutated = False

    def mutate_before_stage(*args, **kwargs):
        nonlocal mutated
        spec = args[2]
        if not mutated and spec is _TEST_DAILY_PRICE_SPEC:
            rows = pq.ParquetFile(target).read().to_pylist()
            rows[0]["volume"] = 101.0
            pq.write_table(pa.Table.from_pylist(rows), target)
            mutated = True
        return real_stage(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "stage_partition_rows", mutate_before_stage)

    with pytest.raises(PublishTransactionError, match="source.*changed|precondition"):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03")],
            [],
            [],
            full_rebuild=False,
        )

    assert mutated is True
    assert {
        path: content
        for path, content in _formal_snapshot(context).items()
        if path.endswith(".json")
    } == metadata_before


def test_event_dependency_changed_after_entry_snapshot_is_not_legalized(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(context, [_raw_row("2026-01-01", close=10.0)])
    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-02", close=9.0)],
        [_dividend_row("2026-01-02")],
        [],
        full_rebuild=False,
    )
    metadata_before = {
        path: content
        for path, content in _formal_snapshot(context).items()
        if path.endswith(".json")
    }
    target = context.artifact_path(
        "canonical/derived/events/dividend_events/event_year=2026/part.parquet"
    )
    real_audit = pipeline_module.audit_adjusted_ohlc
    mutated = False

    def mutate_before_audit(*args, **kwargs):
        nonlocal mutated
        if not mutated:
            rows = pq.ParquetFile(target).read().to_pylist()
            rows[0]["data_cutoff_at"] = "2026-01-02T09:01:00Z"
            pq.write_table(pa.Table.from_pylist(rows), target)
            mutated = True
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "audit_adjusted_ohlc", mutate_before_audit)

    with pytest.raises(PublishTransactionError, match="source precondition hash mismatch"):
        _publish_daily_price_volume(
            context,
            ArtifactPublisher(context),
            [_raw_row("2026-01-03", close=8.0)],
            [],
            [],
            full_rebuild=False,
        )

    assert mutated is True
    assert {
        path: content
        for path, content in _formal_snapshot(context).items()
        if path.endswith(".json")
    } == metadata_before


def test_mixed_ticker_incremental_uses_each_tickers_own_horizon_and_matches_full(
    tmp_path,
):
    baseline = [
        _raw_row("2025-12-31", ticker="A", close=10.0),
        _raw_row("2026-02-01", ticker="B", close=10.0),
    ]
    incoming_a = _raw_row("2026-01-02", ticker="A", close=11.0)
    incoming_b = _raw_row("2026-06-01", ticker="B", close=9.0)
    event_b = _capital_action_row("2026-03-01", ticker="B")
    event_b.update(
        {
            "action_type": "stock_price_adjustment",
            "share_multiplier": 1.0,
            "price_adjustment_reference": 2.0,
        }
    )

    mixed_context = DataAnalystsContext.from_paths(tmp_path / "mixed")
    _publish_full(mixed_context, baseline)
    mixed = _publish_daily_price_volume(
        mixed_context,
        ArtifactPublisher(mixed_context),
        [incoming_a, incoming_b],
        [],
        [event_b],
        full_rebuild=False,
    )

    b_only_context = DataAnalystsContext.from_paths(tmp_path / "b-only")
    _publish_full(b_only_context, baseline)
    b_only = _publish_daily_price_volume(
        b_only_context,
        ArtifactPublisher(b_only_context),
        [incoming_b],
        [],
        [event_b],
        full_rebuild=False,
    )

    full_context = DataAnalystsContext.from_paths(tmp_path / "full")
    full = _publish_daily_price_volume(
        full_context,
        ArtifactPublisher(full_context),
        [*baseline, incoming_a, incoming_b],
        [],
        [event_b],
        full_rebuild=True,
    )

    def b_row(result):
        return next(
            row
            for row in result.rows_for_downstream
            if row["ticker"] == "B" and row["date"] == "2026-06-01"
        )

    assert b_row(mixed)["adj_factor"] == pytest.approx(2.0)
    assert b_row(mixed)["adj_factor"] == b_row(b_only)["adj_factor"]
    assert b_row(mixed)["adj_factor"] == b_row(full)["adj_factor"]
    immediate_full = audit_adjusted_ohlc(
        mixed_context, _manifest(mixed_context), mode="full"
    )
    assert immediate_full["status"] == "ready", immediate_full


def test_raw_only_incremental_consumes_formal_event_after_tickers_trusted_boundary(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row("2025-12-31", ticker="A", close=10.0),
            _raw_row("2026-02-01", ticker="B", close=10.0),
        ],
    )
    event_b = _capital_action_row("2026-03-01", ticker="B")
    event_b.update(
        {
            "action_type": "stock_price_adjustment",
            "share_multiplier": 1.0,
            "price_adjustment_reference": 2.0,
        }
    )
    _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [],
        [],
        [event_b],
        full_rebuild=False,
    )

    result = _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [
            _raw_row("2026-01-02", ticker="A", close=11.0),
            _raw_row("2026-06-01", ticker="B", close=9.0),
        ],
        [],
        [],
        full_rebuild=False,
    )

    b_row = next(row for row in result.rows_for_downstream if row["ticker"] == "B")
    assert b_row["adj_factor"] == pytest.approx(2.0)
    immediate_full = audit_adjusted_ohlc(context, _manifest(context), mode="full")
    assert immediate_full["status"] == "ready", immediate_full


def test_event_only_pipeline_accepts_nullable_capital_reduction_reference(tmp_path, monkeypatch):
    _write_runtime_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    _publish_full(
        context,
        [
            _raw_row("2026-01-01", close=10.0),
            _raw_row("2026-01-02", close=9.0),
        ],
    )

    monkeypatch.setattr(
        pipeline_module,
        "_iter_family_rows",
        lambda *args, **kwargs: iter(
            [("capital_formation", [
                {
                    "ticker": "2330",
                    "ex_date": "2026-01-02",
                    "action_type": "capital_reduction",
                    "share_multiplier": 0.9,
                    "cash_return_per_share": 0.0,
                    "price_adjustment_reference": None,
                    "source_row_id": "capital-reduction-null-reference",
                    "data_cutoff_at": "2026-01-02T09:30:00Z",
                }
            ])]
        ),
    )

    result = run_pipeline(
        context,
        config,
        families={"capital_formation"},
        start_date="2026-01-02",
        end_date="2026-01-02",
    )

    event_manifest = json.loads(
        context.store_path("manifests", "capital_action_events.json").read_text(
            encoding="utf-8"
        )
    )
    event_path = context.artifact_path(event_manifest["artifact_paths"][0])
    event_schema = pq.ParquetFile(event_path).schema_arrow
    evidence = json.loads(
        context.store_path(
            "diagnostics", "adjusted_ohlc_verification.json"
        ).read_text(encoding="utf-8")
    )

    assert result["status"] == "verifying"
    assert result["daily_price_volume"]["verification_status"] == "ready"
    assert event_manifest["row_count"] == 1
    assert pa.types.is_floating(event_schema.field("price_adjustment_reference").type)
    assert evidence["status"] == "ready"
    assert evidence["event_dependencies"]["capital_action_events"]["partitions"][0][
        "artifact_path"
    ] == event_manifest["artifact_paths"][0]
    assert audit_adjusted_ohlc(context, _manifest(context), mode="full")["status"] == "ready"


def test_recent_year_incremental_binds_all_partitions_but_reads_exact_closure(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_full(
        context,
        [
            _raw_row(f"{year}-01-02", close=float(year - 2000))
            for year in range(2020, 2027)
        ],
    )
    expected_read_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    expected_hashed_paths = {
        f"canonical/raw/daily_price_volume/year={year}/part.parquet"
        for year in range(2020, 2027)
    }
    hashed_source_paths: set[str] = set()
    read_source_paths: set[str] = set()
    real_content_sha256 = transactions_module._content_sha256
    real_read_parquet_rows = transactions_module._read_parquet_rows
    real_read_table = pipeline_module.pq.read_table

    def formal_path(path):
        try:
            relative = Path(path).resolve().relative_to(context.data_store.resolve())
        except ValueError:
            return None
        artifact_path = relative.as_posix()
        return artifact_path if artifact_path.startswith("canonical/") else None

    def counted_content_sha256(path):
        artifact_path = formal_path(path)
        if artifact_path is not None:
            hashed_source_paths.add(artifact_path)
        return real_content_sha256(path)

    def counted_read_parquet_rows(path):
        artifact_path = formal_path(path)
        if artifact_path is not None:
            read_source_paths.add(artifact_path)
        return real_read_parquet_rows(path)

    def counted_read_table(source, *args, **kwargs):
        artifact_path = formal_path(source)
        if artifact_path is not None:
            read_source_paths.add(artifact_path)
        return real_read_table(source, *args, **kwargs)

    monkeypatch.setattr(transactions_module, "_content_sha256", counted_content_sha256)
    monkeypatch.setattr(transactions_module, "_read_parquet_rows", counted_read_parquet_rows)
    monkeypatch.setattr(pipeline_module.pq, "read_table", counted_read_table)

    result = _publish_daily_price_volume(
        context,
        ArtifactPublisher(context),
        [_raw_row("2026-01-03", close=27.0)],
        [],
        [],
        full_rebuild=False,
    )

    assert result.evidence_payload["status"] == "ready"
    assert hashed_source_paths == expected_hashed_paths
    assert read_source_paths == {expected_read_path}
