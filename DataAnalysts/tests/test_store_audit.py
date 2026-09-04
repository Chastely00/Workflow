from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.paths import DataAnalystsContext
from data_analysts.store_audit import audit_store


def _contract() -> ArtifactContract:
    return ArtifactContract(
        contract_key="daily_price_volume",
        artifact_id="daily_price_volume",
        variant="default",
        layer="raw",
        base_path="canonical/raw/daily_price_volume",
        file_name="part.parquet",
        required_columns=("date", "ticker", "data_cutoff_at"),
        logical_key=("date", "ticker"),
        publication_mode="partition_upsert",
        partition_name="year",
        partition_field="date",
        date_field="date",
        availability_field="date",
        pit_policy="source_date",
        source_families=("daily_price_volume",),
    )


def _row(date: str, ticker: str = "2330", cutoff: str = "2026-07-08T12:00:00Z"):
    return {"date": date, "ticker": ticker, "data_cutoff_at": cutoff}


def _write_parquet(context, year: str, rows: list[dict[str, object]]):
    relative = f"canonical/raw/daily_price_volume/versions/active/year={year}/part.parquet"
    path = context.artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return relative


def _write_manifest(context, paths: list[str], **overrides):
    files = [context.artifact_path(path) for path in paths if context.artifact_path(path).exists()]
    schemas = [pq.read_schema(path) for path in files]
    rows = [row for path in files for row in pq.ParquetFile(path).read().to_pylist()]
    schema = schemas[0]
    payload = {
        "artifact_id": "daily_price_volume",
        "schema_version": "1.0",
        "layer": "raw",
        "source_families": ["daily_price_volume"],
        "source_collections": [],
        "row_count": len(rows),
        "date_range": [min(row["date"] for row in rows), max(row["date"] for row in rows)],
        "availability_date_range": [min(row["date"] for row in rows), max(row["date"] for row in rows)],
        "columns": schema.names,
        "schema_fingerprint": __import__("hashlib").sha256(schema.serialize().to_pybytes()).hexdigest(),
        "partitioning": ["year"],
        "artifact_paths": paths,
        "active_version": "active",
        "pit_policy": "source_date",
        "data_cutoff_at": max(row["data_cutoff_at"] for row in rows),
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": "2026-07-08T00:00:00Z",
    }
    payload.update(overrides)
    target = context.store_path("manifests", "daily_price_volume.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_audit_detects_single_day_manifest_over_older_files(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _write_parquet(context, "2025", [_row("2025-01-02")])
    current = _write_parquet(context, "2026", [_row("2026-07-08")])
    _write_manifest(context, [current])

    result = audit_store(context, {"daily_price_volume": _contract()})

    assert result["status"] == "blocked"
    assert result["metrics"]["orphan_partition_count"] == 1
    assert any("year=2025/part.parquet" in issue["path"] for issue in result["issues"])


def test_audit_finds_contract_bounded_orphan_when_manifest_is_missing(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    orphan = _write_parquet(context, "2025", [_row("2025-01-02")])

    result = audit_store(context, {"daily_price_volume": _contract()})

    assert result["status"] == "blocked"
    assert result["metrics"]["orphan_partition_count"] == 1
    assert result["metrics"]["missing_manifest_count"] == 1
    assert any(issue.get("path") == orphan for issue in result["issues"])


def test_audit_detects_manifest_evidence_mismatches(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = _write_parquet(context, "2026", [_row("2026-07-07"), _row("2026-07-08", "2317")])
    _write_manifest(
        context,
        [path],
        row_count=1,
        date_range=["2026-07-08", "2026-07-08"],
        availability_date_range=["2026-07-08", "2026-07-08"],
        schema_fingerprint="wrong",
        data_cutoff_at="2026-07-07T00:00:00Z",
    )

    result = audit_store(context, {"daily_price_volume": _contract()})

    messages = "\n".join(issue["message"] for issue in result["issues"])
    assert "row_count mismatch" in messages
    assert "date_range mismatch" in messages
    assert "availability_date_range mismatch" in messages
    assert "schema_fingerprint mismatch" in messages
    assert "data_cutoff_at mismatch" in messages


def test_audit_detects_missing_wrong_partition_duplicate_and_malformed_cutoff(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path_2025 = _write_parquet(context, "2025", [_row("2026-01-02", cutoff="not-a-cutoff")])
    path_2026 = _write_parquet(context, "2026", [_row("2026-01-02")])
    missing = "canonical/raw/daily_price_volume/year=2027/part.parquet"
    _write_manifest(context, [path_2025, path_2026, missing])

    result = audit_store(context, {"daily_price_volume": _contract()})

    messages = "\n".join(issue["message"] for issue in result["issues"])
    assert result["metrics"]["missing_partition_count"] == 1
    assert "wrong partition membership" in messages
    assert "duplicate logical key across files" in messages
    assert "malformed data_cutoff_at" in messages


def test_audit_blocks_malformed_configured_partition_date_and_availability_fields(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = _write_parquet(context, "2026", [_row("not-a-date")])
    _write_manifest(
        context,
        [path],
        date_range=None,
        availability_date_range=None,
    )

    result = audit_store(context, {"daily_price_volume": _contract()})

    messages = "\n".join(issue["message"] for issue in result["issues"])
    assert result["status"] == "blocked"
    assert "invalid partition_field date" in messages
    assert "invalid date_field date" in messages
    assert "invalid availability_field date" in messages


def test_audit_allows_retained_versions_but_flags_flat_full_replace_orphans(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = ArtifactContract(
        contract_key="security_master",
        artifact_id="security_master",
        variant="default",
        layer="raw",
        base_path="canonical/raw/security_master",
        file_name="security_master.parquet",
        required_columns=("ticker", "data_cutoff_at"),
        logical_key=("ticker",),
        publication_mode="full_replace",
        partition_name=None,
        partition_field=None,
        date_field=None,
        availability_field=None,
        pit_policy="snapshot_cutoff",
        source_families=("security_master",),
    )
    active = "canonical/raw/security_master/versions/active/security_master.parquet"
    inactive = "canonical/raw/security_master/versions/inactive/security_master.parquet"
    for relative in (active, inactive):
        path = context.artifact_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([{"ticker": "2330", "data_cutoff_at": "2026-07-08T12:00:00Z"}]), path)
    stray = context.artifact_path("canonical/raw/security_master/notes.parquet")
    pq.write_table(pa.Table.from_pylist([{"ignored": 1}]), stray)
    schema = pq.read_schema(context.artifact_path(active))
    manifest = {
        "artifact_id": "security_master", "row_count": 1, "date_range": None,
        "availability_date_range": None, "columns": schema.names,
        "schema_fingerprint": __import__("hashlib").sha256(schema.serialize().to_pybytes()).hexdigest(),
        "partitioning": ["single_file"], "artifact_paths": [active],
        "data_cutoff_at": "2026-07-08T12:00:00Z", "status": "ready",
    }
    target = context.store_path("manifests", "security_master.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest), encoding="utf-8")

    result = audit_store(context, {"security_master": contract})

    assert result["status"] == "blocked"
    assert result["metrics"]["orphan_partition_count"] == 1
    assert result["artifacts"]["security_master"]["inventory_paths"] == [active]
    assert any(issue["path"].endswith("notes.parquet") for issue in result["issues"])

    target.unlink()
    without_manifest = audit_store(context, {"security_master": contract})

    assert without_manifest["status"] == "blocked"
    assert without_manifest["metrics"]["orphan_partition_count"] == 3


def test_audit_records_exact_backup_pointer_hashes_without_parquet_payload(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    for relative, payload in {
        "metadata/data_store_manifest.json": b'{"schema_version":"1.0"}',
        "jobs/current_run.json": b'{"status":"blocked"}',
        "manifests/example.json": b'{"artifact_id":"example"}',
    }.items():
        target = context.store_path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    result = audit_store(context, {})

    pointers = result["backup_evidence"]
    expected = {
        "metadata/data_store_manifest.json",
        "jobs/current_run.json",
        "manifests/example.json",
    }
    assert expected.issubset({item["relative_path"] for item in pointers})
    assert all(item["absolute_path"] == str(__import__("pathlib").Path(item["absolute_path"]).resolve()) for item in pointers)
    selected = [item for item in pointers if item["relative_path"] in expected]
    assert all(len(item["sha256"]) == 64 and item["exists"] for item in selected)
    assert "parquet" not in json.dumps(pointers).lower()


def test_audit_streams_bounded_columns_without_table_read(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = _write_parquet(context, "2026", [_row("2026-07-08")])
    _write_manifest(context, [path])

    monkeypatch.setattr(
        pq.ParquetFile,
        "read",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("whole table read")),
    )
    result = audit_store(context, {"daily_price_volume": _contract()})

    assert result["status"] == "ready"


def test_scoped_audit_ignores_unselected_manifest_but_full_audit_still_blocks(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = _write_parquet(context, "2026", [_row("2026-07-08")])
    _write_manifest(context, [path])
    unrelated = context.store_path("manifests", "unrelated.json")
    unrelated.write_text(json.dumps({"artifact_id": "unrelated"}), encoding="utf-8")

    scoped = audit_store(
        context,
        {"daily_price_volume": _contract()},
        contract_keys={"daily_price_volume"},
    )
    full = audit_store(context, {"daily_price_volume": _contract()})

    assert scoped["status"] == "ready"
    assert full["status"] == "blocked"
    assert any(issue["artifact_id"] == "unrelated" for issue in full["issues"])


def test_audit_treats_universe_snapshot_variants_as_distinct(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical = ArtifactContract(
        contract_key="universe_u:historical", artifact_id="universe_u", variant="historical",
        layer="derived", base_path="canonical/derived/universes/u/membership_by_year",
        file_name="part.parquet", required_columns=("as_of_date", "universe_id", "ticker", "rank"),
        logical_key=("as_of_date", "universe_id", "ticker"), publication_mode="partition_upsert",
        partition_name="as_of_year", partition_field="as_of_date", date_field="as_of_date",
        availability_field="as_of_date", pit_policy="history", source_families=("security_panel",),
    )
    exact = ArtifactContract(
        contract_key="universe_u:exact_date", artifact_id="universe_u", variant="exact_date",
        layer="derived", base_path="canonical/derived/universes/u/membership_by_date",
        file_name="membership.parquet", required_columns=("as_of_date", "universe_id", "ticker", "rank"),
        logical_key=("as_of_date", "universe_id", "ticker"), publication_mode="snapshot_by_value",
        partition_name="as_of_date", partition_field="as_of_date", date_field="as_of_date",
        availability_field="as_of_date", pit_policy="snapshot", source_families=("security_panel",),
    )
    row = {"as_of_date": "2026-07-08", "universe_id": "u", "ticker": "2330", "rank": 1}
    exact_path = exact.path_for_partition("2026-07-08")
    parquet = context.artifact_path(exact_path)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([row]), parquet)
    schema = pq.read_schema(parquet)
    manifest = {
        "artifact_id": "universe_u", "row_count": 1,
        "date_range": ["2026-07-08", "2026-07-08"],
        "availability_date_range": ["2026-07-08", "2026-07-08"],
        "columns": schema.names,
        "schema_fingerprint": __import__("hashlib").sha256(schema.serialize().to_pybytes()).hexdigest(),
        "partitioning": ["as_of_date"], "artifact_paths": [exact_path],
        "data_cutoff_at": None, "status": "ready",
    }
    target = context.store_path("manifests", "universe_u.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest), encoding="utf-8")

    result = audit_store(
        context,
        {historical.contract_key: historical, exact.contract_key: exact},
    )

    assert result["status"] == "blocked"
    assert "legacy ambiguous manifest" in "\n".join(
        issue["message"] for issue in result["issues"]
    )


def test_audit_blocks_null_or_malformed_configured_fields_in_full_replace(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = ArtifactContract(
        contract_key="sample_snapshot", artifact_id="sample_snapshot", variant="default",
        layer="raw", base_path="canonical/raw/sample_snapshot",
        file_name="sample.parquet", required_columns=(
            "ticker", "report_date", "available_date", "data_cutoff_at"
        ), logical_key=("ticker", "report_date"), publication_mode="full_replace",
        partition_name=None, partition_field=None, date_field="report_date",
        availability_field="available_date", pit_policy="snapshot_cutoff",
        source_families=("sample_snapshot",),
    )
    relative = contract.path_for_version("active")
    parquet = context.artifact_path(relative)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist([{
            "ticker": None,
            "report_date": None,
            "available_date": "not-a-date",
            "data_cutoff_at": "2026-07-08T12:00:00Z",
        }]),
        parquet,
    )
    schema = pq.read_schema(parquet)
    manifest = {
        "artifact_id": contract.artifact_id, "row_count": 1,
        "date_range": None, "availability_date_range": None,
        "columns": schema.names,
        "schema_fingerprint": __import__("hashlib").sha256(
            schema.serialize().to_pybytes()
        ).hexdigest(),
        "partitioning": ["single_file"], "artifact_paths": [relative],
        "data_cutoff_at": "2026-07-08T12:00:00Z", "status": "ready",
    }
    target = context.store_path("manifests", "sample_snapshot.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest), encoding="utf-8")

    result = audit_store(context, {contract.contract_key: contract})

    messages = "\n".join(issue["message"] for issue in result["issues"])
    assert result["status"] == "blocked"
    assert "invalid logical_key field ticker" in messages
    assert "invalid date_field report_date" in messages
    assert "invalid availability_field available_date" in messages
