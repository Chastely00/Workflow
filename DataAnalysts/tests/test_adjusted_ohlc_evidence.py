import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import data_analysts.adjusted_ohlc_evidence as evidence_module
from data_analysts.adjusted_ohlc import ADJUSTMENT_POLICY_ID, empty_violation_counts
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.adjusted_ohlc_evidence import (
    audit_adjusted_ohlc as _audit_adjusted_ohlc_impl,
    manifest_fingerprint,
    promote_audit_candidate as _promote_audit_candidate_impl,
    write_candidate_audit,
)
from data_analysts.paths import DataAnalystsContext


EVIDENCE_FIELDS = {
    "schema_version",
    "artifact_id",
    "adjustment_policy_id",
    "verification_mode",
    "manifest_fingerprint",
    "verified_at",
    "status",
    "partition_count",
    "ready_partition_count",
    "blocked_partition_count",
    "stale_evidence_count",
    "stale_artifact_paths",
    "violation_totals",
    "partitions",
    "blocked_reasons",
    "event_dependencies",
    "ending_state_by_ticker",
    "ending_date_by_ticker",
}
PARTITION_FIELDS = {
    "artifact_path",
    "content_sha256",
    "row_count",
    "date_range",
    "schema_version",
    "adjustment_policy_id",
    "verified_at",
    "status",
    "violation_counts",
    "initial_state_fingerprint",
    "ending_state_by_ticker",
    "ending_date_by_ticker",
}
_PARTITION_METADATA = {}


def _formal_test_contracts() -> dict[str, ArtifactContract]:
    def contract(
        artifact_id: str,
        base_path: str,
        partition_name: str,
        partition_field: str,
        required_columns: tuple[str, ...],
    ) -> ArtifactContract:
        return ArtifactContract(
            contract_key=artifact_id,
            artifact_id=artifact_id,
            variant="test",
            layer="raw" if artifact_id == "daily_price_volume" else "derived",
            base_path=base_path,
            file_name="part.parquet",
            required_columns=required_columns,
            logical_key=(partition_field, "ticker"),
            publication_mode="partition_upsert",
            partition_name=partition_name,
            partition_field=partition_field,
            date_field=partition_field,
            availability_field=partition_field,
            pit_policy="test",
            source_families=(artifact_id,),
            allow_empty=artifact_id != "daily_price_volume",
        )
    return {
        "daily_price_volume": contract(
            "daily_price_volume",
            "canonical/raw/daily_price_volume",
            "year",
            "date",
            tuple(_ready_row("2025-01-02")),
        ),
        "dividend_events": contract(
            "dividend_events",
            "canonical/derived/events/dividend_events",
            "event_year",
            "event_date",
            ("event_date", "ticker", "cash_dividend_per_share", "stock_dividend_ratio"),
        ),
        "capital_action_events": contract(
            "capital_action_events",
            "canonical/derived/events/capital_action_events",
            "event_year",
            "event_date",
            (
                "event_date", "ticker", "action_type", "share_multiplier",
                "cash_return_per_share", "price_adjustment_reference",
            ),
        ),
    }


def audit_adjusted_ohlc(context, manifest, **kwargs):
    kwargs.setdefault("contracts", _formal_test_contracts())
    return _audit_adjusted_ohlc_impl(context, manifest, **kwargs)


def promote_audit_candidate(context, contracts=None):
    return _promote_audit_candidate_impl(
        context, contracts or _formal_test_contracts()
    )


def _event_contract() -> ArtifactContract:
    return ArtifactContract(
        contract_key="dividend_events",
        artifact_id="dividend_events",
        variant="default",
        layer="derived",
        base_path="custom/events/dividend",
        file_name="events.parquet",
        required_columns=(
            "event_date", "ticker", "cash_dividend_per_share",
            "stock_dividend_ratio",
        ),
        logical_key=("event_date", "ticker"),
        publication_mode="partition_upsert",
        partition_name="event_year",
        partition_field="event_date",
        date_field="event_date",
        availability_field="event_date",
        pit_policy="source_available_date",
        source_families=("dividend_policy",),
        allow_empty=True,
    )


@pytest.mark.parametrize(
    "bad_path",
    [
        "wrong/events/dividend/versions/v1/event_year=2025/events.parquet",
        "custom/events/dividend/versions/v1/event_year=2025/wrong.parquet",
        "custom/events/dividend/versions/v1/year=2025/events.parquet",
        "custom/events/dividend/versions/bad!version/event_year=2025/events.parquet",
        "../custom/events/dividend/versions/v1/event_year=2025/events.parquet",
    ],
)
def test_event_manifest_paths_are_exactly_bound_to_registry_contract(tmp_path, bad_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    manifest = {
        "artifact_id": "dividend_events",
        "schema_version": "1.0",
        "status": "ready",
        "row_count": 1,
        "date_range": ["2025-01-02", "2025-01-02"],
        "columns": list(_event_contract().required_columns),
        "artifact_paths": [bad_path],
        "active_version": "v1",
    }

    with pytest.raises(
        evidence_module.AdjustedOhlcEvidenceError,
        match="official event manifest path",
    ):
        evidence_module._validate_official_event_manifest(
            context, manifest, _event_contract()
        )


def _custom_formal_contracts() -> dict[str, ArtifactContract]:
    defaults = _formal_test_contracts()
    return {
        "daily_price_volume": replace(
            defaults["daily_price_volume"],
            base_path="custom/registry/prices",
            file_name="bars.snappy.parquet",
            partition_name="trade_year",
        ),
        "dividend_events": replace(
            defaults["dividend_events"],
            base_path="custom/registry/dividends",
            file_name="dividends.snappy.parquet",
            partition_name="effective_year",
        ),
        "capital_action_events": replace(
            defaults["capital_action_events"],
            base_path="custom/registry/capital",
            file_name="capital.snappy.parquet",
            partition_name="effective_year",
        ),
    }


@pytest.mark.parametrize("artifact_id", ["daily_price_volume", "dividend_events"])
@pytest.mark.parametrize(
    ("path_state", "active_version", "accepted"),
    [
        ("legacy", None, True),
        ("versioned", "runtime-v1", True),
        ("versioned", None, False),
        ("versioned", "runtime-v2", False),
    ],
)
def test_custom_manifest_path_requires_explicit_matching_version_state(
    tmp_path, artifact_id, path_state, active_version, accepted
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _custom_formal_contracts()[artifact_id]
    path = contract.path_for_partition(
        "2025", version="runtime-v1" if path_state == "versioned" else None
    )
    if artifact_id == "daily_price_volume":
        manifest = {
            "artifact_id": artifact_id,
            "schema_version": "1.0",
            "status": "ready",
            "artifact_paths": [path],
            "columns": list(_ready_row("2025-01-02")),
            "row_count": 1,
            "date_range": ["2025-01-02", "2025-01-02"],
        }
        validate = lambda: evidence_module._validate_price_manifest(
            context, manifest, "full", contract
        )
    else:
        manifest = {
            "artifact_id": artifact_id,
            "schema_version": "1.0",
            "status": "ready",
            "artifact_paths": [path],
            "columns": list(contract.required_columns),
            "row_count": 1,
            "date_range": ["2025-01-02", "2025-01-02"],
        }
        validate = lambda: evidence_module._validate_official_event_manifest(
            context, manifest, contract
        )
    if active_version is not None:
        manifest["active_version"] = active_version

    if accepted:
        assert validate() == [path]
    else:
        with pytest.raises(evidence_module.AdjustedOhlcEvidenceError):
            validate()


def test_custom_runtime_registry_paths_pass_audit_and_promotion_full_flow(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contracts = _custom_formal_contracts()
    price_contract = contracts["daily_price_volume"]
    price_path = price_contract.path_for_partition("2025", version="runtime-v1")
    _write_parquet(context, price_path, [_ready_row("2025-01-02")])
    _PARTITION_METADATA[price_path] = {
        "row_count": 1,
        "date_range": ["2025-01-02", "2025-01-02"],
    }
    manifest = _manifest([price_path])
    manifest["active_version"] = "runtime-v1"
    manifest_path = context.store_path("manifests", "daily_price_volume.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    candidate = _audit_adjusted_ohlc_impl(
        context, manifest, contracts=contracts, mode="full"
    )
    assert candidate["status"] == "ready", candidate["blocked_reasons"]
    write_candidate_audit(context, candidate)
    formal = _promote_audit_candidate_impl(context, contracts)
    assert formal["status"] == "ready"


@pytest.mark.parametrize(
    "alien_path",
    [
        "alien/registry/prices/versions/runtime-v1/trade_year=2025/bars.snappy.parquet",
        "custom/registry/prices/versions/runtime-v1/trade_year=2025/wrong.parquet",
        "custom/registry/prices/versions/runtime-v1/year=2025/bars.snappy.parquet",
        "custom/registry/prices/versions/other/trade_year=2025/bars.snappy.parquet",
        "../custom/registry/prices/versions/runtime-v1/trade_year=2025/bars.snappy.parquet",
    ],
)
def test_regex_shaped_alien_price_path_is_rejected_by_runtime_contract(
    tmp_path, alien_path
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contracts = _custom_formal_contracts()
    valid_path = contracts["daily_price_volume"].path_for_partition(
        "2025", version="runtime-v1"
    )
    _write_parquet(context, valid_path, [_ready_row("2025-01-02")])
    _PARTITION_METADATA[valid_path] = {
        "row_count": 1,
        "date_range": ["2025-01-02", "2025-01-02"],
    }
    manifest = _manifest([valid_path])
    manifest["active_version"] = "runtime-v1"
    manifest["artifact_paths"] = [alien_path]

    result = _audit_adjusted_ohlc_impl(
        context, manifest, contracts=contracts, mode="full"
    )
    assert result["status"] == "blocked"
    assert any(
        "partition year" in reason or "manifest path" in reason
        for reason in result["blocked_reasons"]
    )


def _ready_row(date, *, ticker="2330", close=10.0, factor=1.0):
    adjusted = close * factor
    return {
        "date": date,
        "ticker": ticker,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_factor": factor,
        "adj_open": adjusted,
        "adj_high": adjusted,
        "adj_low": adjusted,
        "adj_close": adjusted,
        "price_adjustment_status": "adjusted_close_ready",
    }


def _write_parquet(context, artifact_path, rows):
    rows = list(rows)
    target = context.artifact_path(artifact_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), target)
    if "/daily_price_volume/" in f"/{artifact_path}":
        dates = sorted(str(row["date"]) for row in rows if row.get("date") is not None)
        _PARTITION_METADATA[artifact_path] = {
            "row_count": len(rows),
            "date_range": None if not dates else [dates[0], dates[-1]],
        }
    return target


def _write_capital_action_parquet(context, artifact_path, rows):
    rows = list(rows)
    target = context.artifact_path(artifact_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema(
        [
            pa.field("event_date", pa.string()),
            pa.field("ticker", pa.string()),
            pa.field("action_type", pa.string()),
            pa.field("share_multiplier", pa.float64()),
            pa.field("cash_return_per_share", pa.float64()),
            pa.field("price_adjustment_reference", pa.float64()),
        ]
    )
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), target)
    return target


def _manifest(paths, *, policy=ADJUSTMENT_POLICY_ID):
    partition_metadata = [_PARTITION_METADATA[path] for path in paths]
    date_ranges = [item["date_range"] for item in partition_metadata if item["date_range"]]
    payload = {
        "artifact_id": "daily_price_volume",
        "schema_version": "1.0",
        "artifact_paths": list(paths),
        "columns": list(_ready_row("2025-01-02")),
        "status": "ready",
        "created_at": "2026-07-16T00:00:00Z",
        "row_count": sum(item["row_count"] for item in partition_metadata),
        "date_range": (
            None
            if not date_ranges
            else [min(item[0] for item in date_ranges), max(item[1] for item in date_ranges)]
        ),
    }
    if policy is not None:
        payload["adjustment_policy_id"] = policy
    return payload


def _prepared_store(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    paths = [
        "canonical/raw/daily_price_volume/year=2026/part.parquet",
        "canonical/raw/daily_price_volume/year=2025/part.parquet",
    ]
    _write_parquet(context, paths[0], [_ready_row("2026-01-02")])
    _write_parquet(context, paths[1], [_ready_row("2025-01-02")])
    return context, _manifest(paths), paths


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_current_manifest(context, manifest):
    target = context.store_path("manifests", "daily_price_volume.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return target


def _official_event_manifest(context, artifact_id, paths):
    rows = []
    columns = []
    for artifact_path in paths:
        table = pq.read_table(context.artifact_path(artifact_path))
        rows.extend(table.to_pylist())
        for column in table.column_names:
            if column not in columns:
                columns.append(column)
    dates = sorted(str(row["event_date"]) for row in rows)
    return {
        "artifact_id": artifact_id,
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": list(paths),
        "row_count": len(rows),
        "date_range": None if not dates else [dates[0], dates[-1]],
        "columns": columns,
    }


def _promotion_tamper_fixture(tmp_path):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    candidate_path = write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')
    return context, manifest_path, candidate_path, formal_path


def _rewrite_candidate(candidate_path, mutate):
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    mutate(candidate)
    candidate_path.write_text(json.dumps(candidate, sort_keys=True), encoding="utf-8")


def test_audit_reads_prospective_price_event_paths_and_manifests_without_writes(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    formal_price_bytes = context.artifact_path(price_path).read_bytes()
    staged_price = tmp_path / "staged-price.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                _ready_row("2026-01-02"),
                _ready_row("2026-01-03", close=9.0, factor=10.0 / 9.0),
            ]
        ),
        staged_price,
    )
    staged_event = tmp_path / "staged-event.parquet"
    event_rows = [
        {
            "event_date": "2026-01-03",
            "ticker": "2330",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }
    ]
    pq.write_table(pa.Table.from_pylist(event_rows), staged_event)
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    manifest = _manifest([price_path])
    manifest["row_count"] = 2
    manifest["date_range"] = ["2026-01-02", "2026-01-03"]
    event_manifest = {
        "artifact_id": "dividend_events",
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": [event_path],
        "row_count": 1,
        "date_range": ["2026-01-03", "2026-01-03"],
        "columns": list(event_rows[0]),
    }

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="full",
        path_overrides={price_path: staged_price, event_path: staged_event},
        manifest_overrides={"dividend_events": event_manifest},
    )

    assert result["violation_totals"] == empty_violation_counts()
    assert result["status"] == "ready"
    assert result["event_dependencies"]["dividend_events"][
        "manifest_fingerprint"
    ] == manifest_fingerprint(event_manifest)
    assert context.artifact_path(price_path).read_bytes() == formal_price_bytes
    assert not context.store_path("manifests", "dividend_events.json").exists()


def test_full_audit_has_exact_schema_and_scans_parquet_in_year_order(tmp_path, monkeypatch):
    context, manifest, paths = _prepared_store(tmp_path)
    original = pq.ParquetFile
    opened = []
    batch_sizes = []

    class TrackingParquetFile:
        def __init__(self, path):
            opened.append(Path(path))
            self._delegate = original(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, *, batch_size):
            batch_sizes.append(batch_size)
            yield from self._delegate.iter_batches(batch_size=batch_size)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile", TrackingParquetFile
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert set(result) == EVIDENCE_FIELDS
    assert result["status"] == "ready"
    assert result["partition_count"] == 2
    assert result["ready_partition_count"] == 2
    assert result["blocked_partition_count"] == 0
    assert result["violation_totals"] == empty_violation_counts()
    assert [record["artifact_path"] for record in result["partitions"]] == [
        paths[1],
        paths[0],
    ]
    assert all(set(record) == PARTITION_FIELDS for record in result["partitions"])
    assert [path.name for path in opened] == ["part.parquet", "part.parquet"]
    assert [path.parent.name for path in opened] == ["year=2025", "year=2026"]
    assert batch_sizes == [65_536, 65_536]
    for record in result["partitions"]:
        assert record["content_sha256"] == _sha256(context.artifact_path(record["artifact_path"]))


@pytest.mark.parametrize("bad_type", [pa.string(), pa.bool_()])
def test_full_audit_blocks_non_numeric_price_arrow_schema_before_row_validation(
    tmp_path, monkeypatch, bad_type
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    row = _ready_row("2026-01-02")
    numeric_columns = {
        "open", "high", "low", "close", "adj_factor",
        "adj_open", "adj_high", "adj_low", "adj_close",
    }
    schema = pa.schema([
        pa.field(name, bad_type if name in numeric_columns else pa.string())
        for name in row
    ])
    values = {
        name: (
            [str(value)]
            if name in numeric_columns and pa.types.is_string(bad_type)
            else [bool(value)]
            if name in numeric_columns
            else [value]
        )
        for name, value in row.items()
    }
    target = context.artifact_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(values, schema=schema), target)
    _PARTITION_METADATA[path] = {
        "row_count": 1,
        "date_range": ["2026-01-02", "2026-01-02"],
    }
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.validate_adjusted_ohlc_rows",
        lambda *args, **kwargs: pytest.fail("row validation must not run"),
    )

    result = audit_adjusted_ohlc(context, _manifest([path]), mode="full")

    assert result["status"] == "blocked"
    assert result["partitions"][0]["status"] == "blocked"
    assert "Arrow schema" in " ".join(result["blocked_reasons"])


@pytest.mark.parametrize(
    "columns",
    [
        "ticker,date",
        ["ticker", "date", ""],
        ["ticker", "date", " "],
        ["ticker", "date", "ticker"],
        ["ticker", "date", 1],
        ["ticker", "date"],
    ],
)
@pytest.mark.parametrize("mode", ["full", "incremental"])
def test_price_manifest_columns_are_unique_nonempty_strings_with_required_columns(
    tmp_path, columns, mode
):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest["columns"] = columns

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode=mode,
        changed_paths=set() if mode == "incremental" else None,
        previous_evidence={} if mode == "incremental" else None,
    )

    assert result["status"] == "blocked"
    assert "manifest columns" in " ".join(result["blocked_reasons"])


def test_non_numeric_price_arrow_schema_cannot_be_promoted(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    row = _ready_row("2026-01-02")
    numeric_columns = {
        "open", "high", "low", "close", "adj_factor",
        "adj_open", "adj_high", "adj_low", "adj_close",
    }
    string_row = {
        key: str(value) if key in numeric_columns else value
        for key, value in row.items()
    }
    _write_parquet(context, path, [string_row])
    manifest = _manifest([path], policy=None)
    _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    write_candidate_audit(context, evidence)

    with pytest.raises(ValueError, match="not ready"):
        promote_audit_candidate(context)

    assert not context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    ).exists()


@pytest.mark.parametrize(
    ("field", "bad_type", "bad_value"),
    [
        ("ticker", pa.bool_(), True),
        ("date", pa.timestamp("ns"), 0),
        ("price_adjustment_status", pa.bool_(), True),
    ],
)
def test_full_audit_blocks_noncanonical_identity_and_status_arrow_types(
    tmp_path, monkeypatch, field, bad_type, bad_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    row = _ready_row("2026-01-02")
    fields = []
    values = {}
    for name, value in row.items():
        if name == field:
            fields.append(pa.field(name, bad_type))
            values[name] = [bad_value]
        elif name in {"ticker", "date", "price_adjustment_status"}:
            fields.append(pa.field(name, pa.string()))
            values[name] = [value]
        else:
            fields.append(pa.field(name, pa.float64()))
            values[name] = [value]
    target = context.artifact_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(values, schema=pa.schema(fields)), target)
    _PARTITION_METADATA[path] = {
        "row_count": 1,
        "date_range": ["2026-01-02", "2026-01-02"],
    }
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.validate_adjusted_ohlc_rows",
        lambda *args, **kwargs: pytest.fail("row validation must not run"),
    )

    result = audit_adjusted_ohlc(context, _manifest([path]), mode="full")

    assert result["status"] == "blocked"
    assert field in " ".join(result["blocked_reasons"])


def test_full_audit_blocks_price_row_whose_date_year_differs_from_partition(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    _write_parquet(context, path, [_ready_row("2026-01-02")])

    result = audit_adjusted_ohlc(context, _manifest([path]), mode="full")

    assert result["status"] == "blocked"
    assert result["partitions"][0]["status"] == "blocked"
    assert any("partition year" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    ("field", "value"),
    [("row_count", 999), ("date_range", ["2025-01-02", "2026-01-03"])],
)
def test_audit_blocks_manifest_partition_aggregate_mismatch(tmp_path, field, value):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest[field] = value

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert any("manifest aggregate" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize("invalid_row_count", [True, 2.0, "2", -1])
def test_audit_rejects_nonexact_manifest_row_count_before_scanning(
    tmp_path, monkeypatch, invalid_row_count
):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest["row_count"] = invalid_row_count
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"audit scanned invalid manifest: {path}"),
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("manifest aggregate metadata" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    "invalid_date_range",
    [
        ("2025-01-02", "2026-01-02"),
        ["20250102", "2026-01-02"],
        ["2025-01-02"],
        ["2025-01-02", "2026-01-02", "2027-01-02"],
        ["2026-01-02", "2025-01-02"],
    ],
)
def test_audit_rejects_nonexact_manifest_date_range_before_scanning(
    tmp_path, monkeypatch, invalid_date_range
):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest["date_range"] = invalid_date_range
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"audit scanned invalid manifest: {path}"),
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("manifest aggregate metadata" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    "paths",
    [
        ["canonical/raw/daily_price_volume/part.parquet"],
        [
            "canonical/raw/daily_price_volume/year=2025/a.parquet",
            "canonical/raw/daily_price_volume/year=2025/b.parquet",
        ],
    ],
)
def test_full_audit_fails_closed_on_unknown_or_duplicate_year_paths(tmp_path, paths):
    context = DataAnalystsContext.from_paths(tmp_path)
    for path in paths:
        _write_parquet(context, path, [_ready_row("2025-01-02")])

    result = audit_adjusted_ohlc(context, _manifest(paths), mode="full")

    assert result["status"] == "blocked"
    assert result["blocked_reasons"]
    assert result["partitions"] == []


def test_full_audit_detects_partition_change_during_scan(tmp_path, monkeypatch):
    context, manifest, _ = _prepared_store(tmp_path)
    original = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["_content_sha256"]
    )._content_sha256
    calls = {}

    def changing_hash(path):
        count = calls.get(path, 0)
        calls[path] = count + 1
        digest = original(path)
        return digest if count == 0 else "0" * 64

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence._content_sha256", changing_hash
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert result["stale_evidence_count"] == 2
    assert result["stale_artifact_paths"] == sorted(manifest["artifact_paths"])


def test_full_audit_fails_closed_when_manifest_partition_is_missing(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2025/part.parquet"

    result = audit_adjusted_ohlc(context, _manifest([path]), mode="full")

    assert result["status"] == "blocked"
    assert result["stale_artifact_paths"] == [path]
    assert result["partitions"][0]["status"] == "blocked"


def test_incremental_audit_scans_only_changed_and_reuses_exact_unchanged_record(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    _write_parquet(context, paths[0], [_ready_row("2026-01-03")])
    manifest = _manifest(paths)
    original = pq.ParquetFile
    opened = []

    class TrackingParquetFile:
        def __init__(self, path):
            opened.append(Path(path))
            self._delegate = original(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, *, batch_size):
            yield from self._delegate.iter_batches(batch_size=batch_size)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile", TrackingParquetFile
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={paths[0]},
        previous_evidence=previous,
    )

    assert result["status"] == "ready"
    assert [path.parent.name for path in opened] == ["year=2026"]
    assert result["partitions"][0] == previous["partitions"][0]
    assert result["partitions"][1]["artifact_path"] == paths[0]


def test_incremental_audit_blocks_stale_unchanged_hash_without_scanning(tmp_path, monkeypatch):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    _write_parquet(context, paths[0], [_ready_row("2026-01-03")])
    manifest = _manifest(paths)
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"unexpected row scan: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["stale_evidence_count"] == 1
    assert result["stale_artifact_paths"] == [paths[0]]


def test_incremental_audit_blocks_missing_unchanged_partition(tmp_path):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    context.artifact_path(paths[0]).unlink()

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["stale_artifact_paths"] == [paths[0]]
    assert result["partitions"][1]["status"] == "blocked"
    assert any(paths[0] in reason for reason in result["blocked_reasons"])


def test_incremental_audit_rescans_event_drift_suffix_and_blocks_bad_factor(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2025-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", [event_path])
        ),
        encoding="utf-8",
    )
    original = pq.ParquetFile
    opened_price_paths = []

    def tracking_parquet_file(path):
        if "daily_price_volume" in Path(path).as_posix():
            opened_price_paths.append(Path(path))
        return original(path)

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        tracking_parquet_file,
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["violation_totals"]["factor_transition_violation_count"] == 1
    ordered_paths = sorted(paths)
    assert opened_price_paths == [context.artifact_path(ordered_paths[0])]
    assert [record["artifact_path"] for record in result["partitions"]] == ordered_paths
    assert result["partitions"][1]["status"] == "blocked"
    assert any(
        "boundary state unavailable" in reason for reason in result["blocked_reasons"]
    )


def test_incremental_audit_fails_closed_when_reused_boundary_state_is_missing(tmp_path):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    previous["partitions"][0].pop("ending_state_by_ticker")

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={paths[0]},
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("full audit required" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    "invalid_previous",
    [
        "missing",
        "blocked",
        "wrong_schema",
        "wrong_policy",
        "incomplete_partitions",
        "malformed_manifest_fingerprint",
        "malformed_event_dependencies",
    ],
)
def test_incremental_audit_requires_complete_verifiable_previous_evidence_before_scanning(
    tmp_path, monkeypatch, invalid_previous
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    if invalid_previous == "missing":
        previous = None
    elif invalid_previous == "blocked":
        previous["status"] = "blocked"
    elif invalid_previous == "wrong_schema":
        previous["schema_version"] = "unknown"
    elif invalid_previous == "wrong_policy":
        previous["adjustment_policy_id"] = "unknown"
    elif invalid_previous == "incomplete_partitions":
        previous["partitions"].pop()
    elif invalid_previous == "malformed_manifest_fingerprint":
        previous["manifest_fingerprint"] = "not-a-sha256"
    else:
        previous["event_dependencies"] = []

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"incremental audit scanned without baseline: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(paths),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("full audit required" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    ("mutation", "counter_value"),
    [
        ("extra", 0),
        ("missing", 0),
        ("value", True),
        ("value", 0.0),
        ("value", "0"),
        ("value", -1),
        ("value", 1),
    ],
)
def test_incremental_audit_rejects_invalid_previous_top_level_violation_totals_before_scanning(
    tmp_path, monkeypatch, mutation, counter_value
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    totals = previous["violation_totals"]
    if mutation == "extra":
        totals["unexpected_counter"] = counter_value
    elif mutation == "missing":
        totals.pop("row_order_violation_count")
    else:
        totals["row_order_violation_count"] = counter_value
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"incremental audit scanned invalid baseline: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(paths),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("full audit required" in reason for reason in result["blocked_reasons"])


def test_incremental_audit_rejects_previous_partition_violation_aggregate_mismatch_before_scanning(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    previous["partitions"][0]["violation_counts"]["row_order_violation_count"] = 1
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"incremental audit scanned invalid baseline: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(paths),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("full audit required" in reason for reason in result["blocked_reasons"])


def test_incremental_audit_accepts_compatible_previous_evidence_from_prior_manifest(
    tmp_path,
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    updated_manifest = dict(manifest)
    updated_manifest["created_at"] = "2026-07-17T00:00:00Z"

    result = audit_adjusted_ohlc(
        context,
        updated_manifest,
        mode="incremental",
        changed_paths=set(paths),
        previous_evidence=previous,
    )

    assert result["status"] == "ready"
    assert result["manifest_fingerprint"] == manifest_fingerprint(updated_manifest)


def test_incremental_audit_accepts_new_declared_year_without_rescanning_baseline(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    new_path = "canonical/raw/daily_price_volume/year=2027/part.parquet"
    _write_parquet(context, new_path, [_ready_row("2027-01-04")])
    current_manifest = _manifest([*paths, new_path])
    original = pq.ParquetFile
    opened = []

    def tracking_parquet_file(path):
        opened.append(Path(path))
        return original(path)

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        tracking_parquet_file,
    )

    result = audit_adjusted_ohlc(
        context,
        current_manifest,
        mode="incremental",
        changed_paths={new_path},
        previous_evidence=previous,
    )

    assert result["status"] == "ready"
    assert [path.parent.name for path in opened] == ["year=2027"]
    assert result["partitions"][:2] == previous["partitions"]
    assert result["partitions"][2]["artifact_path"] == new_path


def test_incremental_audit_blocks_new_year_missing_from_changed_paths_without_row_scan(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    new_path = "canonical/raw/daily_price_volume/year=2027/part.parquet"
    _write_parquet(context, new_path, [_ready_row("2027-01-04")])
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"undeclared partition was row-scanned: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        _manifest([*paths, new_path]),
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert result["partitions"] == []
    assert any("full audit required" in reason for reason in result["blocked_reasons"])


def test_noop_incremental_hashes_event_dependencies_without_opening_event_rows(
    tmp_path, monkeypatch
):
    context, manifest, _ = _prepared_store(tmp_path)
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2025-01-02",
                "ticker": "1111",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", [event_path])
        ),
        encoding="utf-8",
    )
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"no-op incremental opened parquet rows: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "ready"
    assert result["event_dependencies"] == previous["event_dependencies"]


def test_incremental_reads_events_only_through_changed_price_horizon(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    event_paths = []
    for year in (2025, 2026, 2027):
        event_path = (
            "canonical/derived/events/dividend_events/"
            f"event_year={year}/part.parquet"
        )
        event_paths.append(event_path)
        _write_parquet(
            context,
            event_path,
            [
                {
                    "event_date": f"{year}-06-01",
                    "ticker": "1111",
                    "cash_dividend_per_share": 1.0,
                    "stock_dividend_ratio": 0.0,
                }
            ],
        )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", event_paths)
        ),
        encoding="utf-8",
    )
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    _write_parquet(context, paths[0], [_ready_row("2026-01-03")])
    manifest = _manifest(paths)
    original = pq.ParquetFile
    opened_event_years = []
    batch_sizes = []

    class TrackingParquetFile:
        def __init__(self, path):
            self._path = Path(path)
            self._delegate = original(path)
            if "/derived/events/" in self._path.as_posix():
                opened_event_years.append(self._path.parent.name)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, *, batch_size):
            if "/derived/events/" in self._path.as_posix():
                batch_sizes.append(batch_size)
            yield from self._delegate.iter_batches(batch_size=batch_size)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        TrackingParquetFile,
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={paths[0]},
        previous_evidence=previous,
    )

    assert result["status"] == "ready"
    assert opened_event_years == ["event_year=2025", "event_year=2026"]
    assert batch_sizes == [65_536, 65_536]
    assert len(result["event_dependencies"]["dividend_events"]["partitions"]) == 3


def test_latest_year_price_only_incremental_reuses_certified_event_prefix(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_paths = []
    event_paths = []
    for year in range(2022, 2027):
        price_path = (
            "canonical/raw/daily_price_volume/"
            f"year={year}/part.parquet"
        )
        event_path = (
            "canonical/derived/events/dividend_events/"
            f"event_year={year}/part.parquet"
        )
        price_paths.append(price_path)
        event_paths.append(event_path)
        _write_parquet(context, price_path, [_ready_row(f"{year}-01-02")])
        _write_parquet(
            context,
            event_path,
            [
                {
                    "event_date": f"{year}-06-01",
                    "ticker": "2330",
                    "cash_dividend_per_share": 0.0,
                    "stock_dividend_ratio": 0.0,
                }
            ],
        )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", event_paths)
        ),
        encoding="utf-8",
    )
    baseline_manifest = _manifest(price_paths)
    previous = audit_adjusted_ohlc(context, baseline_manifest, mode="full")
    assert previous["status"] == "ready"

    _write_parquet(
        context,
        price_paths[-1],
        [
            _ready_row("2026-01-02"),
            _ready_row("2026-01-05", close=11.0),
        ],
    )
    changed_manifest = _manifest(price_paths)
    evidence_module = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["_content_sha256"]
    )
    original_hash = evidence_module._content_sha256
    original_parquet_file = pq.ParquetFile
    hash_calls = Counter()
    event_row_scans = []

    def tracking_hash(path):
        hash_calls[Path(path).resolve()] += 1
        return original_hash(path)

    class TrackingParquetFile:
        def __init__(self, path):
            self._path = Path(path).resolve()
            self._delegate = original_parquet_file(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            if "/derived/events/" in self._path.as_posix():
                event_row_scans.append(self._path.parent.name)
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(evidence_module, "_content_sha256", tracking_hash)
    monkeypatch.setattr(evidence_module.pq, "ParquetFile", TrackingParquetFile)

    incremental = audit_adjusted_ohlc(
        context,
        changed_manifest,
        mode="incremental",
        changed_paths={price_paths[-1]},
        previous_evidence=previous,
    )

    assert incremental["status"] == "ready"
    assert event_row_scans == ["event_year=2025", "event_year=2026"]
    for event_path in event_paths[:3]:
        assert hash_calls[context.artifact_path(event_path).resolve()] == 1
    for event_path in event_paths[3:]:
        assert hash_calls[context.artifact_path(event_path).resolve()] == 2

    monkeypatch.setattr(evidence_module, "_content_sha256", original_hash)
    monkeypatch.setattr(evidence_module.pq, "ParquetFile", original_parquet_file)
    full = audit_adjusted_ohlc(context, changed_manifest, mode="full")
    assert full["status"] == "ready"
    assert incremental["violation_totals"] == full["violation_totals"]
    assert incremental["event_dependencies"] == full["event_dependencies"]
    assert incremental["ending_state_by_ticker"] == full["ending_state_by_ticker"]
    assert incremental["ending_date_by_ticker"] == full["ending_date_by_ticker"]
    for incremental_record, full_record in zip(
        incremental["partitions"], full["partitions"], strict=True
    ):
        assert {
            key: value
            for key, value in incremental_record.items()
            if key != "verified_at"
        } == {
            key: value
            for key, value in full_record.items()
            if key != "verified_at"
        }


def test_price_only_incremental_scans_older_events_for_new_ticker_and_matches_full(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    first_price_path = "canonical/raw/daily_price_volume/year=2022/part.parquet"
    old_price_path = "canonical/raw/daily_price_volume/year=2024/part.parquet"
    new_price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(
        context,
        first_price_path,
        [_ready_row("2022-01-02", ticker="1111")],
    )
    _write_parquet(
        context,
        old_price_path,
        [_ready_row("2024-01-02", ticker="1111", factor=2.0)],
    )

    event_paths = []
    for year in range(2022, 2027):
        event_path = (
            "canonical/derived/events/dividend_events/"
            f"event_year={year}/part.parquet"
        )
        event_paths.append(event_path)
        rows = [{
            "event_date": f"{year}-06-01",
            "ticker": "UNUSED",
            "cash_dividend_per_share": 0.0,
            "stock_dividend_ratio": 0.0,
        }]
        if year == 2023:
            rows = [
                {
                    "event_date": "2023-06-01",
                    "ticker": ticker,
                    "cash_dividend_per_share": 0.0,
                    "stock_dividend_ratio": 1.0,
                }
                for ticker in ("1111", "2330")
            ]
        _write_parquet(context, event_path, rows)

    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", event_paths)
        ),
        encoding="utf-8",
    )
    previous = audit_adjusted_ohlc(
        context,
        _manifest([first_price_path, old_price_path]),
        mode="full",
    )
    assert previous["status"] == "ready"

    _write_parquet(
        context,
        new_price_path,
        [
            _ready_row("2026-01-02", ticker="1111", factor=2.0),
            _ready_row("2026-01-02", ticker="2330"),
        ],
    )
    changed_manifest = _manifest(
        [first_price_path, old_price_path, new_price_path]
    )
    evidence_module = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["audit_adjusted_ohlc"]
    )
    original_parquet_file = pq.ParquetFile
    event_row_scans = []

    class TrackingParquetFile:
        def __init__(self, path):
            self._path = Path(path).resolve()
            self._delegate = original_parquet_file(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            if "/derived/events/" in self._path.as_posix():
                event_row_scans.append(self._path.parent.name)
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(evidence_module.pq, "ParquetFile", TrackingParquetFile)
    incremental = audit_adjusted_ohlc(
        context,
        changed_manifest,
        mode="incremental",
        changed_paths={new_price_path},
        previous_evidence=previous,
    )

    assert event_row_scans == [f"event_year={year}" for year in range(2022, 2027)]
    assert incremental["status"] == "blocked"
    assert incremental["violation_totals"]["factor_transition_violation_count"] == 1

    monkeypatch.setattr(evidence_module.pq, "ParquetFile", original_parquet_file)
    full = audit_adjusted_ohlc(context, changed_manifest, mode="full")

    assert full["status"] == "blocked"
    assert incremental["violation_totals"] == full["violation_totals"]
    assert incremental["event_dependencies"] == full["event_dependencies"]
    assert incremental["ending_state_by_ticker"] == full["ending_state_by_ticker"]
    assert incremental["ending_date_by_ticker"] == full["ending_date_by_ticker"]


def test_prospective_event_hash_cache_is_keyed_by_resolved_physical_path(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    formal_event_target = _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2026-06-01",
                "ticker": "2330",
                "cash_dividend_per_share": 0.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest = _official_event_manifest(
        context, "dividend_events", [event_path]
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    assert previous["status"] == "ready"

    staged_event_target = tmp_path / "staged-event-2026.parquet"
    staged_event_target.write_bytes(formal_event_target.read_bytes())
    _write_parquet(
        context,
        paths[0],
        [
            _ready_row("2026-01-02"),
            _ready_row("2026-01-05", close=11.0),
        ],
    )
    evidence_module = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["_content_sha256"]
    )
    original_hash = evidence_module._content_sha256
    hash_calls = Counter()

    def tracking_hash(path):
        hash_calls[Path(path).resolve()] += 1
        return original_hash(path)

    monkeypatch.setattr(evidence_module, "_content_sha256", tracking_hash)

    result = audit_adjusted_ohlc(
        context,
        _manifest(paths),
        mode="incremental",
        changed_paths={paths[0]},
        previous_evidence=previous,
        path_overrides={event_path: staged_event_target},
        manifest_overrides={"dividend_events": event_manifest},
        formal_event_manifest_overrides={"dividend_events": event_manifest},
        changed_event_paths={event_path},
    )

    assert result["status"] == "ready"
    assert hash_calls[formal_event_target.resolve()] == 1
    assert hash_calls[staged_event_target.resolve()] == 2


def test_full_audit_hashes_future_events_without_opening_their_rows(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2025-01-02")])
    event_paths = []
    for year in (2025, 2026):
        event_path = (
            "canonical/derived/events/dividend_events/"
            f"event_year={year}/part.parquet"
        )
        event_paths.append(event_path)
        _write_parquet(
            context,
            event_path,
            [{
                "event_date": f"{year}-06-01",
                "ticker": "1111",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }],
        )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(
                context, "dividend_events", list(reversed(event_paths))
            )
        ),
        encoding="utf-8",
    )
    original = pq.ParquetFile
    opened_event_years = []

    class TrackingParquetFile:
        def __init__(self, path):
            self._path = Path(path)
            self._delegate = original(path)
            if "/derived/events/" in self._path.as_posix():
                opened_event_years.append(self._path.parent.name)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, *, batch_size):
            yield from self._delegate.iter_batches(batch_size=batch_size)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile", TrackingParquetFile
    )

    result = audit_adjusted_ohlc(context, _manifest([price_path]), mode="full")

    assert result["status"] == "ready"
    assert opened_event_years == ["event_year=2025"]
    assert list(result["event_dependencies"]) == sorted(result["event_dependencies"])
    assert [
        item["artifact_path"]
        for item in result["event_dependencies"]["dividend_events"]["partitions"]
    ] == event_paths
    summaries = result["event_dependencies"]["dividend_events"]["partitions"]
    assert summaries[0]["row_count"] == 1
    assert summaries[0]["date_range"] == ["2025-06-01", "2025-06-01"]
    assert summaries[1]["row_count"] is None
    assert summaries[1]["date_range"] is None


@pytest.mark.parametrize(
    ("field", "arrow_type", "value"),
    [
        ("ticker", pa.bool_(), True),
        ("event_date", pa.timestamp("ns"), 0),
        ("cash_dividend_per_share", pa.bool_(), True),
        ("stock_dividend_ratio", pa.string(), "0.0"),
    ],
)
def test_event_rows_are_not_loaded_before_physical_schema_is_validated(
    tmp_path, monkeypatch, field, arrow_type, value
):
    context, price_manifest, _ = _prepared_store(tmp_path)
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    row = {
        "event_date": "2025-01-02",
        "ticker": "1111",
        "cash_dividend_per_share": 1.0,
        "stock_dividend_ratio": 0.0,
    }
    fields = []
    values = {}
    for name, current in row.items():
        if name == field:
            fields.append(pa.field(name, arrow_type))
            values[name] = [value]
        elif name in {"event_date", "ticker"}:
            fields.append(pa.field(name, pa.string()))
            values[name] = [current]
        else:
            fields.append(pa.field(name, pa.float64()))
            values[name] = [current]
    target = context.artifact_path(event_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(values, schema=pa.schema(fields)), target)
    event_manifest = {
        "artifact_id": "dividend_events",
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": [event_path],
        "row_count": 1,
        "date_range": ["2025-01-02", "2025-01-02"],
        "columns": list(row),
    }
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    original = pq.ParquetFile
    closed = []

    class SchemaOnlyParquetFile:
        def __init__(self, path):
            self._path = Path(path)
            self._delegate = original(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            if "/derived/events/" in self._path.as_posix():
                pytest.fail("invalid event schema reached row loading")
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()
            closed.append(self._path)

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        SchemaOnlyParquetFile,
    )

    result = audit_adjusted_ohlc(context, price_manifest, mode="full")

    assert result["status"] == "blocked"
    assert "event Arrow schema" in " ".join(result["blocked_reasons"])
    assert target in closed


@pytest.mark.parametrize(
    ("field", "value"),
    [("row_count", 2), ("date_range", ["2025-01-01", "2025-01-02"])],
)
def test_scanned_event_summary_must_exactly_match_official_manifest(
    tmp_path, field, value
):
    context, price_manifest, _ = _prepared_store(tmp_path)
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2025-01-02",
            "ticker": "1111",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest = _official_event_manifest(
        context, "dividend_events", [event_path]
    )
    event_manifest[field] = value
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(event_manifest), encoding="utf-8")

    result = audit_adjusted_ohlc(context, price_manifest, mode="full")

    assert result["status"] == "blocked"
    assert "event manifest aggregate" in " ".join(result["blocked_reasons"])


@pytest.mark.parametrize("invalid_ticker", [None, "", " ", " 2330", 2330])
def test_official_event_ticker_must_be_canonical_nonempty_string(
    tmp_path, invalid_ticker
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    event_path = (
        "canonical/derived/events/dividend_events/event_year=2026/part.parquet"
    )
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2026-06-01",
            "ticker": invalid_ticker,
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_official_event_manifest(context, "dividend_events", [event_path])),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, _manifest([price_path]), mode="full")

    assert result["status"] == "blocked"
    assert any("ticker" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize("invalid_event_date", ["not-a-date", "2025-06-01"])
def test_invalid_event_date_blocks_full_and_incremental_audits(
    tmp_path, invalid_event_date
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    event_path = (
        "canonical/derived/events/dividend_events/event_year=2026/part.parquet"
    )
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2026-06-01",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", [event_path])
        ),
        encoding="utf-8",
    )
    manifest = _manifest([price_path])
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    assert previous["status"] == "ready"

    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": invalid_event_date,
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    previous["event_dependencies"]["dividend_events"]["partitions"][0][
        "content_sha256"
    ] = _sha256(context.artifact_path(event_path))
    _write_parquet(context, price_path, [_ready_row("2026-01-03")])

    incremental = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={price_path},
        previous_evidence=previous,
    )
    full = audit_adjusted_ohlc(context, manifest, mode="full")

    assert incremental["status"] == "blocked"
    assert full["status"] == "blocked"
    assert any("event_date" in reason for reason in incremental["blocked_reasons"])
    assert any("event_date" in reason for reason in full["blocked_reasons"])


def test_factor_transition_is_approved_only_by_official_event_manifest(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(
        context,
        price_path,
        [
            _ready_row("2026-01-01", close=10.0),
            _ready_row("2026-01-02", close=9.0, factor=10.0 / 9.0),
        ],
    )
    manifest = _manifest([price_path])
    assert audit_adjusted_ohlc(context, manifest, mode="full")["status"] == "blocked"

    event_path = "canonical/derived/events/dividend_events/event_year=2026/part.parquet"
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2026-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest = _official_event_manifest(context, "dividend_events", [event_path])
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(event_manifest), encoding="utf-8")

    assert audit_adjusted_ohlc(context, manifest, mode="full")["status"] == "ready"


def test_cross_year_event_uses_each_tickers_own_boundary_date(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    old_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    new_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(
        context,
        old_path,
        [
            _ready_row("2025-12-31", ticker="1111", close=5.0),
            _ready_row("2025-12-29", ticker="2330", close=10.0),
        ],
    )
    _write_parquet(
        context,
        new_path,
        [_ready_row("2026-01-02", ticker="2330", close=9.0, factor=10.0 / 9.0)],
    )
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2025-12-30",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest = _official_event_manifest(context, "dividend_events", [event_path])
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(event_manifest), encoding="utf-8")

    result = audit_adjusted_ohlc(
        context, _manifest([new_path, old_path]), mode="full"
    )

    assert result["status"] == "ready"
    assert result["partitions"][0]["ending_date_by_ticker"] == {
        "1111": "2025-12-31",
        "2330": "2025-12-29",
    }


def test_cash_event_boundary_survives_a_gap_year_for_absent_ticker(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    paths = [
        "canonical/raw/daily_price_volume/year=2024/part.parquet",
        "canonical/raw/daily_price_volume/year=2025/part.parquet",
        "canonical/raw/daily_price_volume/year=2026/part.parquet",
    ]
    _write_parquet(
        context,
        paths[0],
        [
            _ready_row("2024-01-01", ticker="2330", close=10.0),
            _ready_row("2024-01-02", ticker="2330", close=9.0, factor=10.0 / 9.0),
        ],
    )
    _write_parquet(
        context, paths[1], [_ready_row("2025-01-02", ticker="1111", close=5.0)]
    )
    _write_parquet(
        context,
        paths[2],
        [_ready_row("2026-01-02", ticker="2330", close=8.0, factor=10.0 / 9.0)],
    )
    event_path = "canonical/derived/events/dividend_events/event_year=2024/part.parquet"
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2024-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", [event_path])
        ),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, _manifest(paths), mode="full")

    assert result["status"] == "ready"
    assert set(result["partitions"][1]["ending_state_by_ticker"]) == {"1111", "2330"}
    assert result["partitions"][1]["ending_date_by_ticker"]["2330"] == "2024-01-02"


def test_malformed_official_event_manifest_fails_closed_before_price_scan(tmp_path, monkeypatch):
    context, manifest, _ = _prepared_store(tmp_path)
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "artifact_id": "dividend_events",
                "schema_version": "unknown",
                "status": "ready",
                "artifact_paths": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"unexpected price scan: {path}"),
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert "event manifest" in " ".join(result["blocked_reasons"])


def test_empty_official_event_manifest_fails_closed(tmp_path):
    context, manifest, _ = _prepared_store(tmp_path)
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "artifact_id": "dividend_events",
                "schema_version": "1.0",
                "status": "ready",
                "artifact_paths": [],
            }
        ),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert "event manifest" in " ".join(result["blocked_reasons"])


def test_candidate_write_only_writes_candidate_path(tmp_path):
    context, manifest, _ = _prepared_store(tmp_path)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")

    target = write_candidate_audit(context, evidence)

    assert target == context.store_path("jobs", "adjusted_ohlc_audit_candidate.json")
    assert json.loads(target.read_text(encoding="utf-8")) == evidence
    assert not context.store_path("diagnostics", "adjusted_ohlc_verification.json").exists()
    assert not context.store_path("manifests", "daily_price_volume.json").exists()


def test_promotion_streams_rows_before_lock_and_never_opens_parquet_under_lock(
    tmp_path, monkeypatch
):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    write_candidate_audit(context, evidence)
    original = pq.ParquetFile
    opened = []
    closed = []
    batch_sizes = []
    lock_path = context.store_path("jobs", "publish.lock")

    class TrackingParquetFile:
        def __init__(self, path):
            assert not lock_path.exists(), f"opened parquet under publish lock: {path}"
            self._path = Path(path)
            self._delegate = original(path)
            opened.append(self._path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            assert not lock_path.exists()
            batch_sizes.append(kwargs["batch_size"])
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()
            closed.append(self._path)

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        TrackingParquetFile,
    )

    formal = promote_audit_candidate(context)

    prospective = json.loads(manifest_path.read_text(encoding="utf-8"))
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    assert prospective["adjustment_policy_id"] == ADJUSTMENT_POLICY_ID
    assert formal == json.loads(formal_path.read_text(encoding="utf-8"))
    assert formal["manifest_fingerprint"] == manifest_fingerprint(prospective)
    assert formal["status"] == "ready"
    assert opened
    assert closed == opened
    assert batch_sizes and set(batch_sizes) == {65_536}


def test_promotion_rejects_price_footer_schema_mismatch_without_row_scan(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    candidate_path = write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')

    bad_rows = [_ready_row("2025-01-02")]
    bad_rows[0]["adj_close"] = "10.0"
    _write_parquet(context, paths[1], bad_rows)
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate["partitions"][0].__setitem__(
            "content_sha256", _sha256(context.artifact_path(paths[1]))
        ),
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    original = pq.ParquetFile

    class FooterOnlyParquetFile:
        def __init__(self, path):
            self._delegate = original(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            pytest.fail("promotion must not read price rows")

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        FooterOnlyParquetFile,
    )

    with pytest.raises(ValueError, match="price Arrow schema"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_uses_the_same_price_manifest_columns_validator(tmp_path):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_manifest["columns"].append(current_manifest["columns"][0])
    manifest_path.write_text(json.dumps(current_manifest, sort_keys=True), encoding="utf-8")
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate.__setitem__(
            "manifest_fingerprint", manifest_fingerprint(current_manifest)
        ),
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="manifest columns"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_streams_certified_event_rows_and_closes_handles(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2025-01-02")])
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2025-01-02",
            "ticker": "1111",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, "dividend_events", [event_path])),
        encoding="utf-8",
    )
    price_manifest = _manifest([price_path], policy=None)
    _write_current_manifest(context, price_manifest)
    evidence = audit_adjusted_ohlc(context, price_manifest, mode="full")
    write_candidate_audit(context, evidence)
    original = pq.ParquetFile
    opened = []
    closed = []
    streamed = []

    class TrackingParquetFile:
        def __init__(self, path):
            self._path = Path(path)
            self._delegate = original(path)
            opened.append(self._path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            streamed.append((self._path, kwargs["batch_size"]))
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()
            closed.append(self._path)

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        TrackingParquetFile,
    )

    formal = promote_audit_candidate(context)

    assert formal["status"] == "ready"
    assert any("/derived/events/" in path.as_posix() for path in opened)
    assert closed == opened
    assert any("/derived/events/" in path.as_posix() for path, _ in streamed)
    assert {batch_size for _, batch_size in streamed} == {65_536}


def test_promotion_streams_and_lock_hashes_each_applicable_source_once(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    current_manifest_path = _write_current_manifest(context, manifest)
    event_path = "canonical/derived/events/dividend_events/event_year=2025/part.parquet"
    event_target = _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2025-01-02",
                "ticker": "1111",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest = _official_event_manifest(context, "dividend_events", [event_path])
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    assert evidence["status"] == "ready"
    write_candidate_audit(context, evidence)

    evidence_module = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["_content_sha256"]
    )
    transaction_module = __import__(
        "data_analysts.partition_transactions", fromlist=["_content_sha256"]
    )
    original_transaction_hash = transaction_module._content_sha256
    original_parquet_file = pq.ParquetFile
    transaction_hash_calls = Counter()
    row_stream_calls = Counter()

    def tracking_transaction_hash(path):
        transaction_hash_calls[Path(path).resolve()] += 1
        return original_transaction_hash(path)

    class TrackingParquetFile:
        def __init__(self, path):
            self._path = Path(path).resolve()
            self._delegate = original_parquet_file(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            row_stream_calls[self._path] += 1
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        transaction_module, "_content_sha256", tracking_transaction_hash
    )
    monkeypatch.setattr(evidence_module.pq, "ParquetFile", TrackingParquetFile)

    promote_audit_candidate(context)

    partition_targets = [
        *(context.artifact_path(path).resolve() for path in paths),
        event_target.resolve(),
    ]
    for target in partition_targets:
        assert row_stream_calls[target] == 1
        assert transaction_hash_calls[target] == 1
    candidate_path = context.store_path("jobs", "adjusted_ohlc_audit_candidate.json")
    for target in [
        candidate_path.resolve(),
        current_manifest_path.resolve(),
        event_manifest_path.resolve(),
    ]:
        assert transaction_hash_calls[target] == 1


def test_stale_candidate_promotion_leaves_formal_metadata_unchanged(tmp_path):
    context, manifest, paths = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_text('{"status":"old"}', encoding="utf-8")
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _write_parquet(context, paths[0], [_ready_row("2026-01-03")])

    with pytest.raises(ValueError, match="stale candidate"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_price_replaced_after_row_scan_before_publish_lock(
    tmp_path, monkeypatch
):
    context, manifest, paths = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    price_target = context.artifact_path(paths[0])
    original_commit = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["commit_publish_transaction"]
    ).commit_publish_transaction

    def mutate_immediately_before_commit(
        transaction_context,
        staged_partitions,
        metadata_payloads,
        *,
        source_preconditions=None,
    ):
        price_target.write_bytes(b"changed after row validation")
        return original_commit(
            transaction_context,
            staged_partitions,
            metadata_payloads,
            source_preconditions=source_preconditions,
        )

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.commit_publish_transaction",
        mutate_immediately_before_commit,
    )

    with pytest.raises(ValueError, match="source precondition hash"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_manifest_hash_and_payload_come_from_one_byte_snapshot(
    tmp_path, monkeypatch
):
    context, manifest_a, _ = _prepared_store(tmp_path)
    manifest_a.pop("adjustment_policy_id")
    manifest_b = dict(manifest_a)
    manifest_b["created_at"] = "2026-07-16T00:00:01Z"
    manifest_path = _write_current_manifest(context, manifest_a)
    manifest_a_bytes = manifest_path.read_bytes()
    manifest_b_bytes = json.dumps(manifest_b, sort_keys=True).encode("utf-8")
    write_candidate_audit(
        context, audit_adjusted_ohlc(context, manifest_b, mode="full")
    )
    formal_path = context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    )
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')
    before_formal = formal_path.read_bytes()
    original_hash = evidence_module._content_sha256
    original_commit = evidence_module.commit_publish_transaction

    def hash_a_then_expose_b(path):
        content_hash = original_hash(path)
        if Path(path).resolve() == manifest_path.resolve():
            manifest_path.write_bytes(manifest_b_bytes)
        return content_hash

    def restore_a_before_publish(
        transaction_context,
        staged_partitions,
        metadata_payloads,
        *,
        source_preconditions=None,
    ):
        manifest_path.write_bytes(manifest_a_bytes)
        return original_commit(
            transaction_context,
            staged_partitions,
            metadata_payloads,
            source_preconditions=source_preconditions,
        )

    monkeypatch.setattr(evidence_module, "_content_sha256", hash_a_then_expose_b)
    monkeypatch.setattr(
        evidence_module, "commit_publish_transaction", restore_a_before_publish
    )

    with pytest.raises(ValueError, match="stale candidate manifest fingerprint"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == manifest_a_bytes
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize("replacement", ["blocked", "stale"])
def test_candidate_replaced_during_row_scan_blocks_publish(
    tmp_path, monkeypatch, replacement
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    original_parquet_file = pq.ParquetFile
    replaced = False

    class ReplacingParquetFile:
        def __init__(self, path):
            self._delegate = original_parquet_file(path)

        @property
        def schema_arrow(self):
            return self._delegate.schema_arrow

        def iter_batches(self, **kwargs):
            nonlocal replaced
            if not replaced:
                replaced = True

                def mutate(candidate):
                    if replacement == "blocked":
                        candidate["status"] = "blocked"
                        candidate["blocked_reasons"] = ["superseded during scan"]
                    else:
                        candidate["manifest_fingerprint"] = "0" * 64

                _rewrite_candidate(candidate_path, mutate)
            yield from self._delegate.iter_batches(**kwargs)

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(evidence_module.pq, "ParquetFile", ReplacingParquetFile)

    with pytest.raises(ValueError, match="stale candidate.*source precondition"):
        promote_audit_candidate(context)

    assert replaced
    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_candidate_content_sha256_mismatch_without_writes(tmp_path):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate["partitions"][0].__setitem__(
            "content_sha256", "0" * 64
        ),
    )

    with pytest.raises(ValueError, match="source precondition hash mismatch"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_stale_event_partition_blocks_candidate_promotion(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(
        context,
        price_path,
        [
            _ready_row("2026-01-01", close=10.0),
            _ready_row("2026-01-02", close=9.0, factor=10.0 / 9.0),
        ],
    )
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    event_path = "canonical/derived/events/dividend_events/event_year=2026/part.parquet"
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2026-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )
    event_manifest = _official_event_manifest(context, "dividend_events", [event_path])
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    assert evidence["status"] == "ready"
    write_candidate_audit(context, evidence)
    before_manifest = manifest_path.read_bytes()
    _write_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2026-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 2.0,
                "stock_dividend_ratio": 0.0,
            }
        ],
    )

    with pytest.raises(ValueError, match="stale candidate event"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert not context.store_path("diagnostics", "adjusted_ohlc_verification.json").exists()


def test_promotion_rejects_tampered_candidate_summary_without_writes(tmp_path):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    evidence["partition_count"] += 1
    write_candidate_audit(context, evidence)
    before_manifest = manifest_path.read_bytes()

    with pytest.raises(ValueError, match="candidate"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert not context.store_path("diagnostics", "adjusted_ohlc_verification.json").exists()


def test_promotion_rejects_tampered_boundary_chain_without_row_rescan(tmp_path):
    context, manifest, _ = _prepared_store(tmp_path)
    manifest.pop("adjustment_policy_id")
    _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    evidence["partitions"][1]["initial_state_fingerprint"] = "0" * 64
    write_candidate_audit(context, evidence)

    with pytest.raises(ValueError, match="boundary"):
        promote_audit_candidate(context)


def test_promotion_rejects_coherently_forged_event_summary_without_writes(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2025-01-02")])
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2025/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2025-06-01",
            "ticker": "2330",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest = _official_event_manifest(
        context, "dividend_events", [event_path]
    )
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    assert evidence["status"] == "ready"
    candidate_path = write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"trusted-old"}\n')

    forged_range = ["2000-01-01", "2000-12-31"]
    event_manifest["row_count"] = 999
    event_manifest["date_range"] = forged_range
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")

    def forge(candidate):
        dependency = candidate["event_dependencies"]["dividend_events"]
        dependency["manifest_fingerprint"] = manifest_fingerprint(event_manifest)
        dependency["row_count"] = 999
        dependency["date_range"] = forged_range
        dependency["partitions"][0]["row_count"] = 999
        dependency["partitions"][0]["date_range"] = forged_range

    _rewrite_candidate(candidate_path, forge)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="event partition summary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_future_event_summary_claim_without_writes(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2025-01-02")])
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2026-06-01",
            "ticker": "2330",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, "dividend_events", [event_path])),
        encoding="utf-8",
    )
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    assert evidence["event_dependencies"]["dividend_events"]["partitions"][0][
        "row_count"
    ] is None
    candidate_path = write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"trusted-old"}\n')

    def claim_future_summary(candidate):
        record = candidate["event_dependencies"]["dividend_events"]["partitions"][0]
        record["row_count"] = 1
        record["date_range"] = ["2026-06-01", "2026-06-01"]

    _rewrite_candidate(candidate_path, claim_future_summary)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="future event partition must remain hash-only"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_coherently_forged_boundary_and_incremental_keeps_event(
    tmp_path
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_2025 = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    price_2026 = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    expected_factor = 10.0 / 9.0
    _write_parquet(context, price_2025, [_ready_row("2025-01-02", close=10.0)])
    _write_parquet(
        context,
        price_2026,
        [_ready_row("2026-01-02", close=10.0, factor=expected_factor)],
    )
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2025/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2025-06-01",
            "ticker": "2330",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, "dividend_events", [event_path])),
        encoding="utf-8",
    )
    manifest = _manifest([price_2026, price_2025], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    trusted_evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    assert trusted_evidence["status"] == "ready"
    write_candidate_audit(context, trusted_evidence)
    trusted_evidence = promote_audit_candidate(context)
    candidate_path = write_candidate_audit(context, trusted_evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    evidence_module = __import__(
        "data_analysts.adjusted_ohlc_evidence", fromlist=["_state_fingerprint"]
    )

    def forge(candidate):
        first, second = candidate["partitions"]
        first["ending_date_by_ticker"]["2330"] = "2025-12-31"
        second["initial_state_fingerprint"] = evidence_module._state_fingerprint(
            evidence_module._decode_state(first["ending_state_by_ticker"]),
            evidence_module._decode_boundary_dates(first["ending_date_by_ticker"]),
        )

    _rewrite_candidate(candidate_path, forge)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="price partition boundary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal

    _write_parquet(
        context,
        price_2026,
        [_ready_row("2026-01-02", close=10.0, factor=1.0)],
    )
    changed_manifest = _manifest([price_2026, price_2025])
    incremental = audit_adjusted_ohlc(
        context,
        changed_manifest,
        mode="incremental",
        changed_paths=[price_2026],
        previous_evidence=json.loads(formal_path.read_text(encoding="utf-8")),
    )
    assert incremental["status"] == "blocked"
    assert incremental["violation_totals"]["factor_transition_violation_count"] == 1, incremental


@pytest.mark.parametrize("identity_field", ["ticker", "date"])
def test_full_audit_blocks_null_price_identity_with_consistent_boundary_keys(
    tmp_path, identity_field
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, path, [_ready_row("2026-01-02") | {identity_field: None}])
    manifest = _manifest([path])
    if identity_field == "date":
        manifest["date_range"] = ["2026-01-02", "2026-01-02"]

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    record = result["partitions"][0]
    assert record["violation_counts"]["missing_required_column_count"] == 1
    assert record["ending_state_by_ticker"] == {}
    assert set(record["ending_state_by_ticker"]) == set(record["ending_date_by_ticker"])


@pytest.mark.parametrize("identity_field", ["ticker", "date"])
def test_incremental_audit_blocks_null_price_identity_without_certifying_evidence(
    tmp_path, identity_field
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, path, [_ready_row("2026-01-02")])
    manifest = _manifest([path])
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    _write_parquet(context, path, [_ready_row("2026-01-03") | {identity_field: None}])

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={path},
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    record = result["partitions"][0]
    assert record["violation_counts"]["missing_required_column_count"] == 1
    assert record["ending_state_by_ticker"] == {}
    assert set(record["ending_state_by_ticker"]) == set(record["ending_date_by_ticker"])


@pytest.mark.parametrize(
    ("identity_field", "invalid_value"),
    [("date", "not-a-date"), ("ticker", "23 30")],
)
def test_full_audit_blocks_malformed_nonempty_price_identity_in_partition_record(
    tmp_path, identity_field, invalid_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(
        context,
        path,
        [_ready_row("2026-01-02") | {identity_field: invalid_value}],
    )
    manifest = _manifest([path])
    if identity_field == "date":
        manifest["date_range"] = ["2026-01-02", "2026-01-02"]

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    record = result["partitions"][0]
    assert record["status"] == "blocked"
    assert record["violation_counts"]["missing_required_column_count"] == 1
    assert any("core adjusted OHLC violations" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    ("identity_field", "invalid_value"),
    [("date", "not-a-date"), ("ticker", "23 30")],
)
def test_incremental_audit_blocks_malformed_nonempty_price_identity_in_partition_record(
    tmp_path, identity_field, invalid_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, path, [_ready_row("2026-01-02")])
    manifest = _manifest([path])
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    _write_parquet(
        context,
        path,
        [_ready_row("2026-01-03") | {identity_field: invalid_value}],
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={path},
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    record = result["partitions"][0]
    assert record["status"] == "blocked"
    assert record["violation_counts"]["missing_required_column_count"] == 1
    assert any("core adjusted OHLC violations" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize("schema_mutation", ["unknown", "missing"])
def test_promotion_rejects_nonexact_top_level_schema_without_writes(
    tmp_path, schema_mutation
):
    _, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(tmp_path)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    def mutate(candidate):
        if schema_mutation == "unknown":
            candidate["unexpected_field"] = {"untrusted": True}
        else:
            candidate.pop("verified_at")

    _rewrite_candidate(candidate_path, mutate)

    with pytest.raises(ValueError, match="candidate.*schema"):
        promote_audit_candidate(DataAnalystsContext.from_paths(tmp_path))

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("partition_count", 2.0),
        ("ready_partition_count", 2.0),
        ("blocked_partition_count", False),
        ("stale_evidence_count", False),
        ("stale_evidence_count", -1),
    ],
)
def test_promotion_rejects_nonexact_or_negative_summary_counts_without_writes(
    tmp_path, field, invalid_value
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate.__setitem__(field, invalid_value),
    )

    with pytest.raises(ValueError, match="candidate summary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_noncanonical_partition_order_without_writes(tmp_path):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate["partitions"].reverse(),
    )

    with pytest.raises(ValueError, match="partition order"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize("schema_mutation", ["unknown", "missing"])
def test_promotion_rejects_nonexact_partition_schema_without_writes(
    tmp_path, schema_mutation
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(tmp_path)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    def mutate(candidate):
        if schema_mutation == "unknown":
            candidate["partitions"][0]["unexpected_field"] = True
        else:
            candidate["partitions"][0].pop("verified_at")

    _rewrite_candidate(candidate_path, mutate)

    with pytest.raises(ValueError, match="partition schema"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize("schema_mutation", ["unknown", "missing"])
def test_promotion_rejects_nonexact_event_dependency_schema_without_writes(
    tmp_path, schema_mutation
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(tmp_path)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    def mutate(candidate):
        dependency = candidate["event_dependencies"]["dividend_events"]
        if schema_mutation == "unknown":
            dependency["unexpected_field"] = True
        else:
            dependency.pop("partitions")

    _rewrite_candidate(candidate_path, mutate)

    with pytest.raises(ValueError, match="event dependenc"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_noncanonical_event_dependency_order_without_writes(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    event_paths = []
    for year in (2024, 2025):
        event_path = (
            "canonical/derived/events/dividend_events/"
            f"event_year={year}/part.parquet"
        )
        event_paths.append(event_path)
        _write_parquet(
            context,
            event_path,
            [{
                "event_date": f"{year}-06-01",
                "ticker": "1111",
                "cash_dividend_per_share": 1.0,
                "stock_dividend_ratio": 0.0,
            }],
        )
    event_manifest = context.store_path("manifests", "dividend_events.json")
    event_manifest.parent.mkdir(parents=True, exist_ok=True)
    event_manifest.write_text(
        json.dumps(_official_event_manifest(context, "dividend_events", event_paths)),
        encoding="utf-8",
    )
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    candidate_path = write_candidate_audit(context, evidence)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate["event_dependencies"]["dividend_events"][
            "partitions"
        ].reverse(),
    )

    with pytest.raises(ValueError, match="event dependency order"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_revalidates_manifest_partition_aggregate_without_writes(tmp_path):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate["partitions"][0].__setitem__(
            "row_count", candidate["partitions"][0]["row_count"] + 1
        ),
    )

    with pytest.raises(ValueError, match="manifest aggregate"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize("invalid_row_count", [True, 2.0, "2", -1])
def test_promotion_rejects_nonexact_manifest_row_count_without_writes(
    tmp_path, invalid_row_count
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_manifest["row_count"] = invalid_row_count
    manifest_path.write_text(json.dumps(current_manifest, sort_keys=True), encoding="utf-8")
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate.__setitem__(
            "manifest_fingerprint", manifest_fingerprint(current_manifest)
        ),
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="manifest aggregate metadata"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize(
    "invalid_date_range",
    [
        ["20250102", "2026-01-02"],
        ["2025-01-02"],
        ["2025-01-02", "2026-01-02", "2027-01-02"],
        ["2026-01-02", "2025-01-02"],
    ],
)
def test_promotion_rejects_nonexact_manifest_date_range_without_writes(
    tmp_path, invalid_date_range
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_manifest["date_range"] = invalid_date_range
    manifest_path.write_text(json.dumps(current_manifest, sort_keys=True), encoding="utf-8")
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate.__setitem__(
            "manifest_fingerprint", manifest_fingerprint(current_manifest)
        ),
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="manifest aggregate metadata"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_final_boundary_summary_mismatch_without_writes(tmp_path):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(tmp_path)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    def mutate(candidate):
        candidate["ending_date_by_ticker"] = {}

    _rewrite_candidate(candidate_path, mutate)

    with pytest.raises(ValueError, match="boundary summary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize("record_mutation", ["future_timestamp", "incoherent_range"])
def test_promotion_rejects_incoherent_partition_metadata_without_writes(
    tmp_path, record_mutation
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(tmp_path)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    def mutate(candidate):
        record = candidate["partitions"][0]
        if record_mutation == "future_timestamp":
            record["verified_at"] = "9999-01-01T00:00:00Z"
        else:
            record["row_count"] = 0

    _rewrite_candidate(candidate_path, mutate)

    with pytest.raises(ValueError, match="candidate partition"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_derives_gap_year_carry_boundary_summary_in_partition_order(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    paths = [
        "canonical/raw/daily_price_volume/year=2024/part.parquet",
        "canonical/raw/daily_price_volume/year=2025/part.parquet",
        "canonical/raw/daily_price_volume/year=2026/part.parquet",
    ]
    _write_parquet(context, paths[0], [_ready_row("2024-01-02", ticker="2330")])
    _write_parquet(context, paths[1], [_ready_row("2025-01-02", ticker="1111")])
    _write_parquet(context, paths[2], [_ready_row("2026-01-02", ticker="1111")])
    manifest = _manifest(paths, policy=None)
    _write_current_manifest(context, manifest)
    evidence = audit_adjusted_ohlc(context, manifest, mode="full")
    write_candidate_audit(context, evidence)

    formal = promote_audit_candidate(context)

    assert set(formal["ending_state_by_ticker"]) == {"1111", "2330"}
    assert formal["ending_date_by_ticker"] == {
        "1111": "2026-01-02",
        "2330": "2024-01-02",
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("adj_factor", True),
        ("adj_factor", float("nan")),
        ("adj_factor", float("inf")),
        ("previous_close", False),
        ("previous_close", float("nan")),
        ("previous_close", float("-inf")),
    ],
)
def test_promotion_rejects_noncanonical_boundary_numbers_without_writes(
    tmp_path, field, invalid_value
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()
    _rewrite_candidate(
        candidate_path,
        lambda candidate: candidate["partitions"][0]["ending_state_by_ticker"][
            "2330"
        ].__setitem__(field, invalid_value),
    )

    with pytest.raises(ValueError, match="boundary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("adj_factor", True),
        ("adj_factor", float("nan")),
        ("previous_close", False),
        ("previous_close", float("inf")),
    ],
)
def test_incremental_reuse_rejects_noncanonical_boundary_numbers_before_row_scan(
    tmp_path, monkeypatch, field, invalid_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, path, [_ready_row("2026-01-02")])
    manifest = _manifest([path])
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    previous["partitions"][0]["ending_state_by_ticker"]["2330"][field] = invalid_value
    previous["ending_state_by_ticker"]["2330"][field] = invalid_value
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"unexpected row scan: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert "full audit required" in " ".join(result["blocked_reasons"])


@pytest.mark.parametrize("mutation", ["ticker", "ending_date"])
def test_promotion_rejects_noncanonical_boundary_identity_without_writes(
    tmp_path, mutation
):
    context, manifest_path, candidate_path, formal_path = _promotion_tamper_fixture(
        tmp_path
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    def mutate(candidate):
        record = candidate["partitions"][0]
        if mutation == "ticker":
            record["ending_state_by_ticker"]["23 30"] = (
                record["ending_state_by_ticker"].pop("2330")
            )
            record["ending_date_by_ticker"]["23 30"] = (
                record["ending_date_by_ticker"].pop("2330")
            )
        else:
            record["ending_date_by_ticker"]["2330"] = "20260102"

    _rewrite_candidate(candidate_path, mutate)

    with pytest.raises(ValueError, match="boundary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize("mutation", ["ticker", "ending_date"])
def test_incremental_reuse_rejects_noncanonical_boundary_identity(
    tmp_path, mutation
):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, path, [_ready_row("2026-01-02")])
    manifest = _manifest([path])
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    record = previous["partitions"][0]
    if mutation == "ticker":
        record["ending_state_by_ticker"]["23 30"] = (
            record["ending_state_by_ticker"].pop("2330")
        )
        record["ending_date_by_ticker"]["23 30"] = (
            record["ending_date_by_ticker"].pop("2330")
        )
    else:
        record["ending_date_by_ticker"]["2330"] = "20260102"

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert "full audit required" in " ".join(result["blocked_reasons"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.__setitem__("row_count", True),
        lambda manifest: manifest.__setitem__("row_count", -1),
        lambda manifest: manifest.__setitem__("date_range", ["bad", "bad"]),
        lambda manifest: manifest.__setitem__("date_range", ["2026-01-02"]),
        lambda manifest: manifest.__setitem__("columns", ["event_date", 123]),
        lambda manifest: manifest.__setitem__(
            "columns", ["event_date", "ticker", "ticker"]
        ),
        lambda manifest: manifest.__setitem__("columns", ["event_date", "ticker"]),
    ],
)
def test_official_event_manifest_structural_schema_blocks_full_before_price_scan(
    tmp_path, monkeypatch, mutation
):
    context, price_manifest, _ = _prepared_store(tmp_path)
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2026-01-02",
            "ticker": "1111",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest = _official_event_manifest(
        context, "dividend_events", [event_path]
    )
    mutation(event_manifest)
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"unexpected price scan: {path}"),
    )

    result = audit_adjusted_ohlc(context, price_manifest, mode="full")

    assert result["status"] == "blocked"
    assert "official event manifest" in " ".join(result["blocked_reasons"])


def test_malformed_event_manifest_blocks_incremental_before_dependency_reuse(
    tmp_path, monkeypatch
):
    context, price_manifest, _ = _prepared_store(tmp_path)
    previous = audit_adjusted_ohlc(context, price_manifest, mode="full")
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2026-01-02",
            "ticker": "1111",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest = _official_event_manifest(
        context, "dividend_events", [event_path]
    )
    event_manifest["row_count"] = True
    target = context.store_path("manifests", "dividend_events.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(event_manifest), encoding="utf-8")
    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile",
        lambda path: pytest.fail(f"unexpected row scan: {path}"),
    )

    result = audit_adjusted_ohlc(
        context,
        price_manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert "official event manifest" in " ".join(result["blocked_reasons"])


def test_promotion_revalidates_official_event_manifest_structure_without_writes(
    tmp_path
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2026-01-02",
            "ticker": "1111",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest = _official_event_manifest(
        context, "dividend_events", [event_path]
    )
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    price_manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, price_manifest)
    candidate = audit_adjusted_ohlc(context, price_manifest, mode="full")
    candidate_path = write_candidate_audit(context, candidate)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')

    event_manifest["row_count"] = True
    event_manifest_path.write_text(json.dumps(event_manifest), encoding="utf-8")
    _rewrite_candidate(
        candidate_path,
        lambda payload: payload["event_dependencies"]["dividend_events"].__setitem__(
            "manifest_fingerprint", manifest_fingerprint(event_manifest)
        ),
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="event manifest"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_tolerated_raw_factor_uses_canonical_boundary_through_promotion_and_reuse(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(
        context,
        price_path,
        [_ready_row("2026-01-02", factor=1.000000005)],
    )
    manifest = _manifest([price_path], policy=None)
    _write_current_manifest(context, manifest)

    candidate = audit_adjusted_ohlc(context, manifest, mode="full")

    assert candidate["status"] == "ready"
    assert candidate["ending_state_by_ticker"]["2330"]["adj_factor"] == 1.0
    write_candidate_audit(context, candidate)
    formal = promote_audit_candidate(context)
    promoted_manifest = json.loads(
        context.store_path("manifests", "daily_price_volume.json").read_text(
            encoding="utf-8"
        )
    )
    incremental = audit_adjusted_ohlc(
        context,
        promoted_manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=formal,
    )

    assert formal["status"] == "ready"
    assert formal["ending_state_by_ticker"]["2330"]["adj_factor"] == 1.0
    assert incremental["status"] == "ready"
    assert incremental["stale_evidence_count"] == 0


def test_promotion_rejects_coherent_price_factor_forgery_outside_tolerance(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    candidate = audit_adjusted_ohlc(context, manifest, mode="full")
    candidate_path = write_candidate_audit(context, candidate)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')

    _write_parquet(
        context,
        price_path,
        [_ready_row("2026-01-02", factor=1.00000002)],
    )
    forged_hash = _sha256(context.artifact_path(price_path))
    _rewrite_candidate(
        candidate_path,
        lambda payload: payload["partitions"][0].__setitem__(
            "content_sha256", forged_hash
        ),
    )
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="price partition boundary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize(
    ("artifact_id", "row"),
    [
        (
            "dividend_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "cash_dividend_per_share": float("nan"),
                "stock_dividend_ratio": 0.0,
            },
        ),
        (
            "dividend_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "cash_dividend_per_share": -1.0,
                "stock_dividend_ratio": 0.0,
            },
        ),
        (
            "dividend_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "cash_dividend_per_share": 0.0,
                "stock_dividend_ratio": float("inf"),
            },
        ),
        (
            "dividend_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "cash_dividend_per_share": 0.0,
                "stock_dividend_ratio": -1.0,
            },
        ),
        (
            "capital_action_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "action_type": "stock_price_adjustment",
                "share_multiplier": 1.0,
                "cash_return_per_share": 0.0,
                "price_adjustment_reference": float("nan"),
            },
        ),
        (
            "capital_action_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "action_type": "stock_price_adjustment",
                "share_multiplier": 1.0,
                "cash_return_per_share": 0.0,
                "price_adjustment_reference": 0.0,
            },
        ),
    ],
)
def test_unconsumed_invalid_event_semantics_block_full_audit(
    tmp_path, artifact_id, row
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    event_path = (
        f"canonical/derived/events/{artifact_id}/event_year=2026/part.parquet"
    )
    _write_parquet(context, event_path, [row])
    event_manifest_path = context.store_path("manifests", f"{artifact_id}.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, artifact_id, [event_path])),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert result["blocked_reasons"]


@pytest.mark.parametrize(
    ("action_type", "price_adjustment_reference"),
    [
        ("capital_reduction", None),
        ("split", None),
        ("stock_price_adjustment", 1.0),
    ],
)
def test_full_audit_accepts_every_supported_capital_action_type(
    tmp_path, action_type, price_adjustment_reference
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    event_path = (
        "canonical/derived/events/capital_action_events/"
        "event_year=2026/part.parquet"
    )
    _write_capital_action_parquet(
        context,
        event_path,
        [
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "action_type": action_type,
                "share_multiplier": 1.0,
                "cash_return_per_share": 0.0,
                "price_adjustment_reference": price_adjustment_reference,
            }
        ],
    )
    event_manifest_path = context.store_path(
        "manifests", "capital_action_events.json"
    )
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(
                context, "capital_action_events", [event_path]
            )
        ),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "ready", result


@pytest.mark.parametrize(
    "action_type",
    [None, "", " ", "STOCK_PRICE_ADJUSTMENT", "merger", 1],
)
def test_full_audit_blocks_invalid_capital_action_types(tmp_path, action_type):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    event_path = (
        "canonical/derived/events/capital_action_events/"
        "event_year=2026/part.parquet"
    )
    row = {
        "event_date": "2026-06-01",
        "ticker": "UNUSED",
        "action_type": action_type,
        "share_multiplier": 1.0,
        "cash_return_per_share": 0.0,
        "price_adjustment_reference": None,
    }
    if isinstance(action_type, str) or action_type is None:
        _write_capital_action_parquet(context, event_path, [row])
    else:
        _write_parquet(
            context,
            event_path,
            [{**row, "price_adjustment_reference": 1.0}],
        )
    event_manifest_path = context.store_path(
        "manifests", "capital_action_events.json"
    )
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(
                context, "capital_action_events", [event_path]
            )
        ),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert "action_type" in " ".join(result["blocked_reasons"])


def test_incremental_event_scan_blocks_legacy_unconsumed_invalid_semantics(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path])
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2026/part.parquet"
    )
    valid_row = {
        "event_date": "2026-06-01",
        "ticker": "UNUSED",
        "cash_dividend_per_share": 0.0,
        "stock_dividend_ratio": 0.0,
    }
    _write_parquet(context, event_path, [valid_row])
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(context, "dividend_events", [event_path])
        ),
        encoding="utf-8",
    )
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    assert previous["status"] == "ready"

    _write_parquet(
        context,
        event_path,
        [{**valid_row, "cash_dividend_per_share": float("nan")}],
    )
    previous["event_dependencies"]["dividend_events"]["partitions"][0][
        "content_sha256"
    ] = _sha256(context.artifact_path(event_path))

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={price_path},
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert "official cash dividend" in " ".join(result["blocked_reasons"])


def test_incremental_event_scan_blocks_invalid_capital_action_type(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path])
    event_path = (
        "canonical/derived/events/capital_action_events/"
        "event_year=2026/part.parquet"
    )
    valid_row = {
        "event_date": "2026-06-01",
        "ticker": "UNUSED",
        "action_type": "split",
        "share_multiplier": 2.0,
        "cash_return_per_share": 0.0,
        "price_adjustment_reference": None,
    }
    _write_capital_action_parquet(context, event_path, [valid_row])
    event_manifest_path = context.store_path(
        "manifests", "capital_action_events.json"
    )
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(
                context, "capital_action_events", [event_path]
            )
        ),
        encoding="utf-8",
    )
    previous = audit_adjusted_ohlc(context, manifest, mode="full")
    assert previous["status"] == "ready"

    _write_capital_action_parquet(
        context,
        event_path,
        [{**valid_row, "action_type": "SPLIT"}],
    )
    previous["event_dependencies"]["capital_action_events"]["partitions"][0][
        "content_sha256"
    ] = _sha256(context.artifact_path(event_path))

    result = audit_adjusted_ohlc(
        context,
        manifest,
        mode="incremental",
        changed_paths={price_path},
        previous_evidence=previous,
    )

    assert result["status"] == "blocked"
    assert "action_type" in " ".join(result["blocked_reasons"])


@pytest.mark.parametrize(
    ("artifact_id", "valid_row", "invalid_value_field", "invalid_value"),
    [
        (
            "dividend_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "cash_dividend_per_share": 0.0,
                "stock_dividend_ratio": 0.0,
            },
            "cash_dividend_per_share",
            float("inf"),
        ),
        (
            "capital_action_events",
            {
                "event_date": "2026-06-01",
                "ticker": "UNUSED",
                "action_type": "stock_price_adjustment",
                "share_multiplier": 1.0,
                "cash_return_per_share": 0.0,
                "price_adjustment_reference": 1.0,
            },
            "price_adjustment_reference",
            -1.0,
        ),
    ],
)
def test_promotion_lock_stream_rejects_unconsumed_invalid_event_without_writes(
    tmp_path, artifact_id, valid_row, invalid_value_field, invalid_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    event_path = (
        f"canonical/derived/events/{artifact_id}/event_year=2026/part.parquet"
    )
    _write_parquet(context, event_path, [valid_row])
    event_manifest_path = context.store_path("manifests", f"{artifact_id}.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, artifact_id, [event_path])),
        encoding="utf-8",
    )
    candidate = audit_adjusted_ohlc(context, manifest, mode="full")
    assert candidate["status"] == "ready"
    candidate_path = write_candidate_audit(context, candidate)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')

    invalid_row = {**valid_row, invalid_value_field: invalid_value}
    _write_parquet(context, event_path, [invalid_row])
    forged_hash = _sha256(context.artifact_path(event_path))

    def update_event_hash(payload):
        dependency = payload["event_dependencies"][artifact_id]
        dependency["partitions"][0]["content_sha256"] = forged_hash

    _rewrite_candidate(candidate_path, update_event_hash)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="official"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_lock_stream_rejects_invalid_capital_action_type_without_writes(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    event_path = (
        "canonical/derived/events/capital_action_events/"
        "event_year=2026/part.parquet"
    )
    valid_row = {
        "event_date": "2026-06-01",
        "ticker": "UNUSED",
        "action_type": "capital_reduction",
        "share_multiplier": 0.8,
        "cash_return_per_share": 0.0,
        "price_adjustment_reference": None,
    }
    _write_capital_action_parquet(context, event_path, [valid_row])
    event_manifest_path = context.store_path(
        "manifests", "capital_action_events.json"
    )
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(
                context, "capital_action_events", [event_path]
            )
        ),
        encoding="utf-8",
    )
    candidate = audit_adjusted_ohlc(context, manifest, mode="full")
    assert candidate["status"] == "ready"
    candidate_path = write_candidate_audit(context, candidate)
    formal_path = context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    )
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')

    _write_capital_action_parquet(
        context,
        event_path,
        [{**valid_row, "action_type": "unknown_action"}],
    )
    forged_hash = _sha256(context.artifact_path(event_path))

    def update_event_hash(payload):
        dependency = payload["event_dependencies"]["capital_action_events"]
        dependency["partitions"][0]["content_sha256"] = forged_hash

    _rewrite_candidate(candidate_path, update_event_hash)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="action_type"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


def test_promotion_rejects_tolerance_close_candidate_factor_forgery_without_writes(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    manifest_path = _write_current_manifest(context, manifest)
    candidate = audit_adjusted_ohlc(context, manifest, mode="full")
    candidate_path = write_candidate_audit(context, candidate)
    formal_path = context.store_path("diagnostics", "adjusted_ohlc_verification.json")
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    formal_path.write_bytes(b'{"status":"old"}\n')

    def forge_factor(payload):
        payload["partitions"][0]["ending_state_by_ticker"]["2330"][
            "adj_factor"
        ] = 1.000000005
        payload["ending_state_by_ticker"]["2330"]["adj_factor"] = 1.000000005

    _rewrite_candidate(candidate_path, forge_factor)
    before_manifest = manifest_path.read_bytes()
    before_formal = formal_path.read_bytes()

    with pytest.raises(ValueError, match="price partition boundary"):
        promote_audit_candidate(context)

    assert manifest_path.read_bytes() == before_manifest
    assert formal_path.read_bytes() == before_formal


@pytest.mark.parametrize(
    ("artifact_id", "field", "invalid_value"),
    [
        (artifact_id, field, invalid_value)
        for artifact_id, fields in (
            (
                "dividend_events",
                ("cash_dividend_per_share", "stock_dividend_ratio"),
            ),
            (
                "capital_action_events",
                (
                    "share_multiplier",
                    "cash_return_per_share",
                    "price_adjustment_reference",
                ),
            ),
        )
        for field in fields
        for invalid_value in (True, float("nan"), float("inf"))
    ]
    + [
        ("dividend_events", "cash_dividend_per_share", -0.1),
        ("dividend_events", "stock_dividend_ratio", -0.1),
        ("capital_action_events", "share_multiplier", 0.0),
        ("capital_action_events", "cash_return_per_share", -0.1),
        ("capital_action_events", "price_adjustment_reference", 0.0),
    ],
)
def test_every_required_event_numeric_field_fails_closed_on_invalid_semantics(
    tmp_path, artifact_id, field, invalid_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    _write_parquet(context, price_path, [_ready_row("2026-01-02")])
    manifest = _manifest([price_path], policy=None)
    valid_rows = {
        "dividend_events": {
            "event_date": "2026-06-01",
            "ticker": "UNUSED",
            "cash_dividend_per_share": 0.0,
            "stock_dividend_ratio": 0.0,
        },
        "capital_action_events": {
            "event_date": "2026-06-01",
            "ticker": "UNUSED",
            "action_type": "stock_price_adjustment",
            "share_multiplier": 1.0,
            "cash_return_per_share": 0.0,
            "price_adjustment_reference": 1.0,
        },
    }
    event_path = (
        f"canonical/derived/events/{artifact_id}/event_year=2026/part.parquet"
    )
    _write_parquet(
        context,
        event_path,
        [{**valid_rows[artifact_id], field: invalid_value}],
    )
    event_manifest_path = context.store_path("manifests", f"{artifact_id}.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, artifact_id, [event_path])),
        encoding="utf-8",
    )

    result = audit_adjusted_ohlc(context, manifest, mode="full")

    assert result["status"] == "blocked"
    assert result["blocked_reasons"]


def test_future_event_dependency_stays_hash_only_in_full_incremental_and_promotion(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    future_event_path = (
        "canonical/derived/events/capital_action_events/"
        "event_year=2026/part.parquet"
    )
    _write_parquet(context, price_path, [_ready_row("2025-01-02")])
    _write_parquet(
        context,
        future_event_path,
        [{
            "event_date": "2026-06-01",
            "ticker": "2330",
            "action_type": "stock_price_adjustment",
            "share_multiplier": 1.0,
            "cash_return_per_share": 0.0,
            "price_adjustment_reference": "not-inspected-before-horizon",
        }],
    )
    event_manifest_path = context.store_path(
        "manifests", "capital_action_events.json"
    )
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(
            _official_event_manifest(
                context, "capital_action_events", [future_event_path]
            )
        ),
        encoding="utf-8",
    )
    manifest = _manifest([price_path], policy=None)
    _write_current_manifest(context, manifest)
    candidate = audit_adjusted_ohlc(context, manifest, mode="full")
    assert candidate["status"] == "ready"
    future_record = candidate["event_dependencies"]["capital_action_events"][
        "partitions"
    ][0]
    assert future_record["row_count"] is None
    assert future_record["date_range"] is None
    write_candidate_audit(context, candidate)

    original = pq.ParquetFile

    def reject_future_open(path, *args, **kwargs):
        if Path(path) == context.artifact_path(future_event_path):
            pytest.fail("future event parquet must remain hash-only")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(
        "data_analysts.adjusted_ohlc_evidence.pq.ParquetFile", reject_future_open
    )

    formal = promote_audit_candidate(context)
    promoted_manifest = json.loads(
        context.store_path("manifests", "daily_price_volume.json").read_text(
            encoding="utf-8"
        )
    )
    incremental = audit_adjusted_ohlc(
        context,
        promoted_manifest,
        mode="incremental",
        changed_paths=set(),
        previous_evidence=formal,
    )
    changed_incremental = audit_adjusted_ohlc(
        context,
        promoted_manifest,
        mode="incremental",
        changed_paths={price_path},
        previous_evidence=formal,
    )

    assert formal["status"] == "ready"
    assert incremental["status"] == "ready"
    assert changed_incremental["status"] == "ready"
    assert incremental["event_dependencies"]["capital_action_events"][
        "partitions"
    ][0]["row_count"] is None


@pytest.mark.parametrize(
    ("cash_dividend", "stock_ratio", "raw_factor"),
    [(0.0, 1.0, 2.0), (1.0, 0.0, 1.0)],
)
def test_event_before_first_artifact_price_requires_verified_seed_in_all_paths(
    tmp_path, cash_dividend, stock_ratio, raw_factor
):
    context = DataAnalystsContext.from_paths(tmp_path)
    price_path = "canonical/raw/daily_price_volume/year=2026/part.parquet"
    event_path = (
        "canonical/derived/events/dividend_events/"
        "event_year=2025/part.parquet"
    )
    _write_parquet(
        context,
        price_path,
        [_ready_row("2026-01-02", factor=raw_factor)],
    )
    _write_parquet(
        context,
        event_path,
        [{
            "event_date": "2025-06-01",
            "ticker": "2330",
            "cash_dividend_per_share": cash_dividend,
            "stock_dividend_ratio": stock_ratio,
        }],
    )
    event_manifest_path = context.store_path("manifests", "dividend_events.json")
    event_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    event_manifest_path.write_text(
        json.dumps(_official_event_manifest(context, "dividend_events", [event_path])),
        encoding="utf-8",
    )
    manifest = _manifest([price_path], policy=None)
    _write_current_manifest(context, manifest)

    full = audit_adjusted_ohlc(context, manifest, mode="full")
    assert full["status"] == "blocked"
    assert full["violation_totals"]["factor_transition_violation_count"] == 1

    forced_candidate = json.loads(json.dumps(full))
    forced_candidate["status"] = "ready"
    forced_candidate["ready_partition_count"] = 1
    forced_candidate["blocked_partition_count"] = 0
    forced_candidate["stale_evidence_count"] = 0
    forced_candidate["stale_artifact_paths"] = []
    forced_candidate["blocked_reasons"] = []
    forced_candidate["violation_totals"] = empty_violation_counts()
    forced_candidate["partitions"][0]["status"] = "ready"
    forced_candidate["partitions"][0]["violation_counts"] = empty_violation_counts()
    forced_candidate["ending_state_by_ticker"] = forced_candidate["partitions"][0][
        "ending_state_by_ticker"
    ]
    forced_candidate["ending_date_by_ticker"] = forced_candidate["partitions"][0][
        "ending_date_by_ticker"
    ]
    write_candidate_audit(context, forced_candidate)

    with pytest.raises(ValueError, match="price partition boundary"):
        promote_audit_candidate(context)

    incremental_manifest = dict(manifest)
    incremental_manifest["adjustment_policy_id"] = ADJUSTMENT_POLICY_ID
    incremental = audit_adjusted_ohlc(
        context,
        incremental_manifest,
        mode="incremental",
        changed_paths={price_path},
        previous_evidence=forced_candidate,
    )
    assert incremental["status"] == "blocked"
    assert incremental["violation_totals"]["factor_transition_violation_count"] == 1
