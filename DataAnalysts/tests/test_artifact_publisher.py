import json
import hashlib
from copy import deepcopy

import pytest

import data_analysts.artifacts as artifacts_module
from data_analysts.artifacts import (
    ArtifactError,
    ArtifactPublisher,
    build_manifest_payload,
)
from data_analysts.paths import DataAnalystsContext


def test_artifact_publisher_writes_manifest_under_data_store(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)

    publisher.publish_parquet(
        "canonical/raw/sample/part.parquet",
        rows=[{"date": "2025-01-01", "ticker": "2330"}],
        required_columns=["date", "ticker"],
    )
    manifest_path = publisher.publish_manifest(
        artifact_id="sample",
        layer="raw",
        source_families=["sample"],
        source_collections=[],
        columns=["date", "ticker"],
        artifact_paths=["canonical/raw/sample/part.parquet"],
        row_count=1,
        date_range=["2025-01-01", "2025-01-01"],
        availability_date_range=["2025-01-01", "2025-01-01"],
        partitioning=["single_file"],
        pit_policy="test",
        data_cutoff_at="2025-01-01T00:00:00Z",
        duplicate_count=0,
        omitted_row_count=0,
        status="ready",
    )

    assert (
        tmp_path / "data_store" / "canonical" / "raw" / "sample" / "part.parquet"
    ).exists()
    assert manifest_path == tmp_path / "data_store" / "manifests" / "sample.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    parquet_path = tmp_path / "data_store" / "canonical" / "raw" / "sample" / "part.parquet"
    expected_sha256 = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    assert payload["schema_version"] == "1.1"
    assert payload["artifact_fingerprints"] == [
        {
            "artifact_path": "canonical/raw/sample/part.parquet",
            "sha256": expected_sha256,
        }
    ]
    assert payload["artifact_paths"] == ["canonical/raw/sample/part.parquet"]
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize(
    "path",
    [
        "runtime/canonical/raw/sample/part.parquet",
        "canonical/runs/sample/part.parquet",
        "canonical/real_all_products/sample/part.parquet",
    ],
)
def test_artifact_publisher_rejects_forbidden_path_segments(tmp_path, path):
    publisher = ArtifactPublisher(DataAnalystsContext.from_paths(tmp_path))

    with pytest.raises(ArtifactError, match="forbidden segments"):
        publisher.publish_parquet(
            path,
            rows=[{"date": "2025-01-01"}],
            required_columns=["date"],
        )


def test_artifact_publisher_rejects_absolute_artifact_paths(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)
    absolute_path = tmp_path / "data_store" / "canonical" / "raw" / "sample" / "part.parquet"

    with pytest.raises(ArtifactError, match="artifact path must be relative"):
        publisher.publish_parquet(
            absolute_path,
            rows=[{"date": "2025-01-01"}],
            required_columns=["date"],
        )


def test_publish_manifest_fails_before_writing_when_final_parquet_is_missing(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)

    with pytest.raises(ArtifactError, match="artifact fingerprint source missing"):
        publisher.publish_manifest(
            artifact_id="sample",
            layer="raw",
            source_families=["sample"],
            source_collections=[],
            columns=["date", "ticker"],
            artifact_paths=["canonical/raw/sample/part.parquet"],
            row_count=0,
            date_range=None,
            availability_date_range=None,
            partitioning=["single_file"],
            pit_policy="test",
            data_cutoff_at="2025-01-01T00:00:00Z",
            duplicate_count=0,
            omitted_row_count=0,
            status="ready",
        )

    assert not (tmp_path / "data_store" / "manifests" / "sample.json").exists()


def test_build_artifact_fingerprints_rejects_duplicate_paths_before_hashing(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    relative = "canonical/raw/sample/part.parquet"
    path = context.artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(
        artifacts_module,
        "sha256_file",
        lambda path, chunk_size=1024 * 1024: calls.append(path) or "a" * 64,
    )

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.build_artifact_fingerprints(
            context,
            [relative, relative],
        )

    assert calls == []


def _sample_manifest_args():
    return {
        "artifact_id": "sample",
        "layer": "raw",
        "source_families": ["sample"],
        "source_collections": [],
        "columns": ["date", "ticker"],
        "artifact_paths": ["canonical/raw/sample/year=2025/part.parquet"],
        "row_count": 1,
        "date_range": ["2025-01-01", "2025-01-01"],
        "availability_date_range": ["2025-01-01", "2025-01-01"],
        "partitioning": ["year"],
        "pit_policy": "test",
        "data_cutoff_at": "2025-01-01T00:00:00Z",
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": "2025-01-02T00:00:00Z",
    }


def test_manifest_payload_is_deterministic_write_free_and_accepts_extensions(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    args = _sample_manifest_args()
    original = deepcopy(args)

    first = build_manifest_payload(
        context=context,
        **args,
        extension_fields={"adjustment_policy_id": "event_based_adjusted_ohlc_v1"},
    )
    second = build_manifest_payload(
        context=context,
        **args,
        extension_fields={"adjustment_policy_id": "event_based_adjusted_ohlc_v1"},
    )

    assert first == second
    assert first["adjustment_policy_id"] == "event_based_adjusted_ohlc_v1"
    assert args == original
    assert not (tmp_path / "data_store").exists()


@pytest.mark.parametrize("reserved_field", ["status", "artifact_paths", "created_at"])
def test_manifest_extension_cannot_override_reserved_fields(tmp_path, reserved_field):
    with pytest.raises(ArtifactError, match="reserved manifest field"):
        build_manifest_payload(
            context=DataAnalystsContext.from_paths(tmp_path),
            **_sample_manifest_args(),
            extension_fields={reserved_field: "replacement"},
        )


def test_manifest_extension_must_be_json_compatible(tmp_path):
    with pytest.raises(ArtifactError, match="JSON-compatible"):
        build_manifest_payload(
            context=DataAnalystsContext.from_paths(tmp_path),
            **_sample_manifest_args(),
            extension_fields={"unsupported": {"not-a-string-key": {1, 2}}},
        )


def test_manifest_extension_rejects_non_string_json_object_keys(tmp_path):
    with pytest.raises(ArtifactError, match="JSON-compatible"):
        build_manifest_payload(
            context=DataAnalystsContext.from_paths(tmp_path),
            **_sample_manifest_args(),
            extension_fields={"unsupported": {1: "coerced-by-json-dumps"}},
        )


@pytest.mark.parametrize("extension_fields", [[], 0, ""])
def test_manifest_extension_rejects_falsey_non_mappings(tmp_path, extension_fields):
    with pytest.raises(ArtifactError, match="must be a mapping"):
        build_manifest_payload(
            context=DataAnalystsContext.from_paths(tmp_path),
            **_sample_manifest_args(),
            extension_fields=extension_fields,
        )


def test_publish_manifest_preserves_extensions_and_portable_fingerprints(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    publisher = ArtifactPublisher(context)
    args = _sample_manifest_args()
    args.pop("created_at")
    artifact_path = context.artifact_path(args["artifact_paths"][0])
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"fixture parquet bytes")
    monkeypatch.setattr("data_analysts.artifacts._utc_now", lambda: "2025-01-02T00:00:00Z")

    target = publisher.publish_manifest(
        **args,
        extension_fields={"adjustment_policy_id": "event_based_adjusted_ohlc_v1"},
    )

    expected = build_manifest_payload(
        context=context,
        **args,
        created_at="2025-01-02T00:00:00Z",
        extension_fields={"adjustment_policy_id": "event_based_adjusted_ohlc_v1"},
    )
    expected["schema_version"] = "1.1"
    expected["artifact_fingerprints"] = artifacts_module.build_artifact_fingerprints(
        context, args["artifact_paths"]
    )
    assert json.loads(target.read_text(encoding="utf-8")) == expected
