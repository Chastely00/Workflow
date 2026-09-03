import hashlib
import json

import pytest

import data_analysts.artifacts as artifacts_module
from data_analysts.artifacts import ArtifactError
from data_analysts.paths import DataAnalystsContext, PathBoundaryError


def write_legacy_artifact(
    context: DataAnalystsContext,
    artifact_id: str,
    content: bytes = b"parquet-fixture",
):
    relative = f"canonical/raw/{artifact_id}/part.parquet"
    path = context.artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    manifest = {
        "artifact_id": artifact_id,
        "schema_version": "1.0",
        "artifact_paths": [relative],
    }
    manifest_path = context.store_path("manifests", f"{artifact_id}.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return path, manifest_path


def manifest_payload() -> dict:
    return {
        "artifact_id": "sample",
        "schema_version": "1.1",
        "artifact_paths": ["canonical/raw/sample/part.parquet"],
        "artifact_fingerprints": [
            {
                "artifact_path": "canonical/raw/sample/part.parquet",
                "sha256": "a" * 64,
            }
        ],
    }


def test_validate_manifest_fingerprint_structure_accepts_ordered_one_to_one_entries():
    assert artifacts_module.validate_manifest_fingerprint_structure(manifest_payload()) is True


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "reordered", "duplicate", "malformed"],
)
def test_validate_manifest_fingerprint_structure_rejects_invalid_entries(mutation):
    payload = manifest_payload()
    if mutation == "missing":
        payload["artifact_fingerprints"] = []
    elif mutation == "extra":
        payload["artifact_fingerprints"].append(
            {"artifact_path": "canonical/raw/sample/extra.parquet", "sha256": "b" * 64}
        )
    elif mutation == "reordered":
        payload["artifact_paths"] = [
            "canonical/raw/sample/other.parquet",
            "canonical/raw/sample/part.parquet",
        ]
        payload["artifact_fingerprints"] = list(reversed(payload["artifact_fingerprints"] * 2))
    elif mutation == "duplicate":
        payload["artifact_paths"] *= 2
        payload["artifact_fingerprints"] *= 2
    else:
        payload["artifact_fingerprints"][0]["sha256"] = "ABC"

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


@pytest.mark.parametrize("malformed_path", [[], {}])
def test_validate_manifest_fingerprint_structure_rejects_malformed_path_values(
    malformed_path,
):
    payload = manifest_payload()
    payload["artifact_paths"] = [malformed_path]
    payload["artifact_fingerprints"][0]["artifact_path"] = malformed_path

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


def test_build_artifact_fingerprints_preflights_second_physical_escape_before_hashing(
    tmp_path,
    monkeypatch,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    valid_path = "canonical/raw/sample/part.parquet"
    escaping_path = "canonical/raw/sample/escape.parquet"
    final_path = context.artifact_path(valid_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"fixture")
    calls = []
    original_artifact_path = DataAnalystsContext.artifact_path

    def artifact_path_with_escape(self, path):
        if path == escaping_path:
            raise PathBoundaryError("path resolves outside allowed root")
        return original_artifact_path(self, path)

    monkeypatch.setattr(
        DataAnalystsContext,
        "artifact_path",
        artifact_path_with_escape,
    )
    monkeypatch.setattr(
        artifacts_module,
        "sha256_file",
        lambda path, chunk_size=1024 * 1024: calls.append(path) or "a" * 64,
    )

    with pytest.raises(ArtifactError, match="path resolves outside allowed root"):
        artifacts_module.build_artifact_fingerprints(
            context,
            [valid_path, escaping_path],
        )

    assert calls == []


def test_build_artifact_fingerprints_rejects_case_aliases_before_hashing(
    tmp_path,
    monkeypatch,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    relative = "canonical/raw/sample/part.parquet"
    case_alias = "CANONICAL/RAW/SAMPLE/PART.PARQUET"
    final_path = context.artifact_path(relative)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(
        artifacts_module,
        "sha256_file",
        lambda path, chunk_size=1024 * 1024: calls.append(path) or "a" * 64,
    )

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.build_artifact_fingerprints(
            context,
            [relative, case_alias],
        )

    assert calls == []


def test_validate_manifest_fingerprint_structure_rejects_case_aliases():
    payload = manifest_payload()
    case_alias = "CANONICAL/RAW/SAMPLE/PART.PARQUET"
    payload["artifact_paths"].append(case_alias)
    payload["artifact_fingerprints"].append(
        {"artifact_path": case_alias, "sha256": "b" * 64}
    )

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


@pytest.mark.parametrize(
    "alias",
    [
        "CANONICAL/RAW/SAMPLE/PART.PARQUET",
        r"canonical\raw\sample\part.parquet",
        "canonical/raw/./sample/part.parquet",
        "canonical//raw/sample/part.parquet",
    ],
)
def test_portable_artifact_path_identity_is_host_independent(alias):
    canonical = "canonical/raw/sample/part.parquet"

    assert artifacts_module._portable_artifact_path_identity(alias) == (
        artifacts_module._portable_artifact_path_identity(canonical)
    )


def test_portable_artifact_path_identity_rejects_unicode_casefold_duplicates():
    strasse_path = "canonical/raw/strasse/part.parquet"
    sharp_s_path = "canonical/raw/stra\u00dfe/part.parquet"

    assert artifacts_module._portable_artifact_path_identity(sharp_s_path) == (
        artifacts_module._portable_artifact_path_identity(strasse_path)
    )

    payload = manifest_payload()
    payload["artifact_paths"] = [strasse_path, sharp_s_path]
    payload["artifact_fingerprints"] = [
        {"artifact_path": strasse_path, "sha256": "a" * 64},
        {"artifact_path": sharp_s_path, "sha256": "b" * 64},
    ]

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


@pytest.mark.parametrize(
    "alias",
    [
        "canonical/raw/sample/part.parquet.",
        "canonical/raw/sample/part.parquet ",
    ],
)
def test_build_artifact_fingerprints_rejects_nonportable_alias_before_hashing(
    tmp_path,
    monkeypatch,
    alias,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    canonical = "canonical/raw/sample/part.parquet"
    final_path = context.artifact_path(canonical)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(
        artifacts_module,
        "sha256_file",
        lambda path, chunk_size=1024 * 1024: calls.append(path) or "a" * 64,
    )

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.build_artifact_fingerprints(
            context,
            [canonical, alias],
        )

    assert calls == []


@pytest.mark.parametrize(
    "alias",
    [
        "CANONICAL/RAW/SAMPLE/PART.PARQUET",
        r"canonical\raw\sample\part.parquet",
        "canonical/raw/./sample/part.parquet",
        "canonical//raw/sample/part.parquet",
        "canonical/raw/sample/part.parquet.",
        "canonical/raw/sample/part.parquet ",
    ],
)
def test_validate_manifest_fingerprint_structure_rejects_portable_aliases(alias):
    payload = manifest_payload()
    payload["artifact_paths"].append(alias)
    payload["artifact_fingerprints"].append(
        {"artifact_path": alias, "sha256": "b" * 64}
    )

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "",
        "/canonical/raw/sample/part.parquet",
        r"C:\canonical\raw\sample\part.parquet",
        "canonical/raw/../sample/part.parquet",
    ],
)
def test_validate_manifest_fingerprint_structure_rejects_unsafe_paths_with_exact_entries(
    unsafe_path,
):
    payload = manifest_payload()
    payload["artifact_paths"] = [unsafe_path]
    payload["artifact_fingerprints"] = [
        {"artifact_path": unsafe_path, "sha256": "a" * 64}
    ]

    with pytest.raises(ArtifactError, match="manifest fingerprint structure invalid"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


@pytest.mark.parametrize("chunk_size", [0, -1])
def test_sha256_file_rejects_non_positive_chunk_size(tmp_path, chunk_size):
    path = tmp_path / "fixture.parquet"
    path.write_bytes(b"fixture")

    with pytest.raises(ArtifactError, match="chunk_size must be positive"):
        artifacts_module.sha256_file(path, chunk_size=chunk_size)


def test_validate_manifest_fingerprint_structure_allows_legacy_only_when_explicit():
    payload = manifest_payload()
    payload["schema_version"] = "1.0"
    payload.pop("artifact_fingerprints")

    assert artifacts_module.validate_manifest_fingerprint_structure(
        payload,
        allow_legacy=True,
    ) is False
    with pytest.raises(ArtifactError, match="unsupported artifact manifest schema"):
        artifacts_module.validate_manifest_fingerprint_structure(payload)


def test_validate_manifest_fingerprint_structure_rejects_unversioned_manifest():
    payload = manifest_payload()
    payload.pop("schema_version")
    payload.pop("artifact_fingerprints")

    for allow_legacy in (False, True):
        with pytest.raises(ArtifactError, match="unsupported artifact manifest schema"):
            artifacts_module.validate_manifest_fingerprint_structure(
                payload,
                allow_legacy=allow_legacy,
            )


def test_repair_manifest_fingerprints_only_migrates_requested_artifacts(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    selected_parquet, selected_manifest = write_legacy_artifact(context, "selected")
    _, untouched_manifest = write_legacy_artifact(context, "untouched")
    untouched_before = untouched_manifest.read_bytes()

    repaired = artifacts_module.repair_manifest_fingerprints(context, ["selected"])

    payload = json.loads(selected_manifest.read_text(encoding="utf-8"))
    assert repaired == (selected_manifest,)
    assert payload["schema_version"] == "1.1"
    assert payload["artifact_fingerprints"] == [
        {
            "artifact_path": "canonical/raw/selected/part.parquet",
            "sha256": hashlib.sha256(selected_parquet.read_bytes()).hexdigest(),
        }
    ]
    assert untouched_manifest.read_bytes() == untouched_before


def test_repair_manifest_fingerprints_rejects_bare_string_before_manifest_access(
    tmp_path,
    monkeypatch,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    calls = []
    original_store_path = DataAnalystsContext.store_path

    def recording_store_path(self, *parts):
        calls.append(parts)
        return original_store_path(self, *parts)

    monkeypatch.setattr(DataAnalystsContext, "store_path", recording_store_path)

    with pytest.raises(ArtifactError, match="invalid artifact id"):
        artifacts_module.repair_manifest_fingerprints(context, "abc")

    assert calls == []


def test_repair_manifest_fingerprints_preflights_all_targets_before_writing(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _, valid_manifest = write_legacy_artifact(context, "valid")
    valid_before = valid_manifest.read_bytes()
    write_legacy_artifact(context, "broken")
    context.artifact_path("canonical/raw/broken/part.parquet").unlink()

    with pytest.raises(ArtifactError, match="artifact fingerprint source missing"):
        artifacts_module.repair_manifest_fingerprints(context, ["valid", "broken"])

    assert valid_manifest.read_bytes() == valid_before


def test_repair_manifest_fingerprints_rejects_existing_hash_mismatch(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    parquet_path, manifest_path = write_legacy_artifact(context, "sample")
    artifacts_module.repair_manifest_fingerprints(context, ["sample"])
    parquet_path.write_bytes(b"replaced")
    before = manifest_path.read_bytes()

    with pytest.raises(ArtifactError, match="artifact fingerprint mismatch"):
        artifacts_module.repair_manifest_fingerprints(context, ["sample"])

    assert manifest_path.read_bytes() == before


def test_repair_manifest_fingerprints_is_byte_stable_and_does_not_hash_unselected(
    tmp_path,
    monkeypatch,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    selected_parquet, selected_manifest = write_legacy_artifact(context, "selected")
    unselected_parquet, _ = write_legacy_artifact(context, "unselected")
    calls = []
    real_hash = artifacts_module.sha256_file

    def counting_hash(path, chunk_size=1024 * 1024):
        calls.append(path.resolve())
        return real_hash(path, chunk_size)

    monkeypatch.setattr(artifacts_module, "sha256_file", counting_hash)
    artifacts_module.repair_manifest_fingerprints(context, ["selected"])
    first = selected_manifest.read_bytes()
    artifacts_module.repair_manifest_fingerprints(context, ["selected"])

    assert selected_manifest.read_bytes() == first
    assert calls == [selected_parquet.resolve(), selected_parquet.resolve()]
    assert unselected_parquet.resolve() not in calls


@pytest.mark.parametrize("invalid_artifact_id", [[], {}])
def test_repair_manifest_fingerprints_rejects_unhashable_artifact_id(
    tmp_path,
    invalid_artifact_id,
):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ArtifactError, match="invalid artifact id"):
        artifacts_module.repair_manifest_fingerprints(context, [invalid_artifact_id])


def test_repair_manifest_fingerprints_rejects_invalid_pattern_before_duplicates(
    tmp_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ArtifactError, match="invalid artifact id"):
        artifacts_module.repair_manifest_fingerprints(
            context,
            ["valid", "valid", "invalid-id"],
        )


def test_repair_manifest_fingerprints_rejects_duplicate_valid_artifact_ids(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ArtifactError, match="artifact ids must be unique and non-empty"):
        artifacts_module.repair_manifest_fingerprints(context, ["sample", "sample"])
