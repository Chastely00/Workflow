import hashlib
import json
from dataclasses import replace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import os

from data_analysts.artifact_contracts import ArtifactContract, ArtifactContractError
from data_analysts.artifacts import ArtifactError
from data_analysts.dataset_publication import (
    archive_superseded_paths,
    publish_dataset,
    reconstruct_manifest,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.store_audit import audit_store
from data_analysts.config import load_runtime_config
from data_analysts.pipeline import run_pipeline
from data_analysts.verify import verify_runtime
from data_analysts.verify import _consume_run_attestation, _validate_run_attestation
from test_historical_universe_pipeline import _write_configs


def _contract(*, allow_empty=False):
    return ArtifactContract(
        contract_key="daily_tradability", artifact_id="daily_tradability",
        variant="default", layer="raw",
        base_path="canonical/raw/daily_tradability", file_name="part.parquet",
        required_columns=("date", "ticker", "tradable", "data_cutoff_at"),
        logical_key=("date", "ticker"), publication_mode="partition_upsert",
        partition_name="year", partition_field="date", date_field="date",
        availability_field="date", pit_policy="source_available_date",
        source_families=("daily_tradability",), allow_empty=allow_empty,
    )


def _row(value, ticker="2330"):
    return {
        "date": value, "ticker": ticker, "tradable": True,
        "data_cutoff_at": f"{value}T12:00:00Z",
    }


def _full_contract():
    return replace(
        _contract(), contract_key="security_master", artifact_id="security_master",
        base_path="canonical/raw/security_master", file_name="security_master.parquet",
        publication_mode="full_replace", partition_name=None, partition_field=None,
    )


@pytest.mark.parametrize(
    "token",
    ["", " ", ".", "..", " x", "x ", "x/y", "x\\y", "x:y", "x\x00y",
     "CON", "con.txt", "PRN.", "AUX ", "NUL", "COM1", "com9.log", "LPT1"],
)
def test_version_token_rejects_unsafe_windows_and_normalization_forms(token):
    with pytest.raises(ArtifactContractError, match="version"):
        _contract().path_for_partition("2026", version=token)


def test_reconstruct_manifest_publishes_explicit_allowed_empty_contract(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract(allow_empty=True)

    manifest = reconstruct_manifest(context, contract)

    assert manifest["artifact_paths"] == []
    assert manifest["row_count"] == 0
    assert manifest["columns"] == list(contract.required_columns)
    assert manifest["date_range"] is None
    assert manifest["availability_date_range"] is None


def test_reconstruct_manifest_rejects_disallowed_empty_contract(tmp_path):
    with pytest.raises(ArtifactError, match="empty"):
        reconstruct_manifest(DataAnalystsContext.from_paths(tmp_path), _contract())


def test_partition_legacy_switch_retains_old_reader_bytes_and_records_evidence(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy_relative = contract.path_for_partition("2025")
    legacy = context.artifact_path(legacy_relative)
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    old_manifest = {"artifact_paths": [legacy_relative]}
    before = legacy.read_bytes()

    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")

    # A reader that loaded the old manifest before the switch still sees the
    # exact old bytes after the new manifest becomes visible.
    assert context.artifact_path(old_manifest["artifact_paths"][0]).read_bytes() == before
    evidence = result.manifest["superseded_paths"][0]
    assert evidence["path"] == legacy_relative
    assert evidence["size"] == len(before)
    assert evidence["sha256"] == hashlib.sha256(before).hexdigest()
    assert evidence["state"] == "retained"
    assert "/versions/legacy-" in evidence["retained_path"]
    assert audit_store(context, {contract.contract_key: contract})["status"] == "ready"


@pytest.mark.parametrize(
    "publication_mode", ["partition_upsert", "snapshot_by_value", "full_replace"]
)
def test_manifest_requires_one_explicit_normalized_active_version(
    tmp_path, publication_mode
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = (
        _full_contract()
        if publication_mode == "full_replace"
        else replace(_contract(), publication_mode=publication_mode)
    )
    first = publish_dataset(context, contract, [_row("2025-01-02")], "full_history")
    second = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_path = context.store_path("manifests", contract.manifest_file_name)

    cases = []
    missing = dict(second.manifest)
    missing.pop("active_version")
    cases.append(missing)
    mixed = dict(second.manifest)
    mixed["artifact_paths"] = [first.manifest["artifact_paths"][0], second.manifest["artifact_paths"][0]]
    mixed["row_count"] = 2
    cases.append(mixed)
    collapsed = dict(second.manifest)
    collapsed["artifact_paths"] = [second.manifest["artifact_paths"][0].replace("/versions/", "/versions/./")]
    cases.append(collapsed)

    for payload in cases:
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        evidence = audit_store(context, {contract.contract_key: contract})
        assert evidence["status"] == "blocked"


@pytest.mark.parametrize(
    "publication_mode", ["partition_upsert", "snapshot_by_value", "full_replace"]
)
def test_audit_rejects_unsafe_active_version_tokens(tmp_path, publication_mode):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = (
        _full_contract()
        if publication_mode == "full_replace"
        else replace(_contract(), publication_mode=publication_mode)
    )
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest = dict(result.manifest)
    active = manifest["active_version"]
    source_root = context.artifact_path(f"{contract.base_path}/versions/{active}")
    unsafe_root = source_root.with_name("bad slug")
    os.replace(source_root, unsafe_root)
    manifest["active_version"] = "bad slug"
    manifest["artifact_paths"] = [
        path.replace(f"/versions/{active}/", "/versions/bad slug/")
        for path in manifest["artifact_paths"]
    ]
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    evidence = audit_store(context, {contract.contract_key: contract})

    assert evidence["status"] == "blocked"
    assert any("active_version" in str(issue) for issue in evidence["issues"])


def test_snapshot_versions_without_manifest_fail_closed(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = replace(_contract(), publication_mode="snapshot_by_value")
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    result.manifest_path.unlink()

    evidence = audit_store(context, {contract.contract_key: contract})

    assert evidence["status"] == "blocked"
    assert evidence["metrics"]["missing_manifest_count"] == 1


def test_explicit_archive_removes_superseded_state_only_after_hash_bound_confirmation(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy = context.artifact_path(contract.path_for_partition("2025"))
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_bytes = result.manifest_path.read_bytes()

    with pytest.raises(ArtifactError, match="confirm"):
        archive_superseded_paths(
            context, contract,
            expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            confirm_no_legacy_readers=False,
        )
    receipt = archive_superseded_paths(
        context, contract,
        expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        confirm_no_legacy_readers=True,
    )

    assert not legacy.exists()
    assert context.store_path(*receipt["receipt_path"].split("/")).is_file()
    active = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert "superseded_paths" not in active
    evidence = audit_store(context, {contract.contract_key: contract})
    assert evidence["status"] == "ready"
    assert evidence["metrics"]["superseded_retained_count"] == 0


def test_full_replace_first_versioned_switch_retains_manifest_listed_flat_reader(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _full_contract()
    flat_relative = f"{contract.base_path}/{contract.file_name}"
    flat = context.artifact_path(flat_relative)
    flat.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), flat)
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"artifact_paths": [flat_relative]}))
    before = flat.read_bytes()

    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")

    assert flat.read_bytes() == before
    assert result.manifest["active_version"] in result.manifest["artifact_paths"][0]
    assert result.manifest["superseded_paths"][0]["path"] == flat_relative
    assert audit_store(context, {contract.contract_key: contract})["status"] == "ready"


def test_archive_rejects_superseded_hash_drift_without_mutation(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy = context.artifact_path(contract.path_for_partition("2025"))
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    legacy.write_bytes(legacy.read_bytes() + b"drift")

    with pytest.raises(ArtifactError, match="evidence changed"):
        archive_superseded_paths(
            context, contract,
            expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
            confirm_no_legacy_readers=True,
        )

    assert result.manifest_path.read_bytes() == manifest_before
    assert legacy.is_file()


def test_archive_partial_move_failure_restores_every_legacy_path(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy_paths = []
    for year, value in (("2024", "2024-12-31"), ("2025", "2025-01-02")):
        path = context.artifact_path(contract.path_for_partition(year))
        path.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([_row(value)]), path)
        legacy_paths.append(path)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    bytes_before = {path: path.read_bytes() for path in legacy_paths}
    real_replace = os.replace
    moves = 0

    def fail_second_payload_move(source, target):
        nonlocal moves
        if "archives" in str(target) and "payload" in str(target):
            moves += 1
            if moves == 2:
                raise OSError("partial archive move")
        return real_replace(source, target)

    monkeypatch.setattr("data_analysts.dataset_publication.os.replace", fail_second_payload_move)
    with pytest.raises(OSError, match="partial archive move"):
        archive_superseded_paths(
            context, contract,
            expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
            confirm_no_legacy_readers=True,
        )

    assert result.manifest_path.read_bytes() == manifest_before
    assert {path: path.read_bytes() for path in legacy_paths} == bytes_before


def test_archive_rollback_failures_keep_all_payloads_and_write_recovery_receipt(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy_paths = []
    for year, value in (
        ("2023", "2023-12-29"), ("2024", "2024-12-31"), ("2025", "2025-01-02")
    ):
        path = context.artifact_path(contract.path_for_partition(year))
        path.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([_row(value)]), path)
        legacy_paths.append(path)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    real_replace = os.replace
    outbound = 0

    def fail_move_and_every_restore(source, target):
        nonlocal outbound
        source_text, target_text = str(source), str(target)
        if "archives" in target_text and "payload" in target_text:
            outbound += 1
            if outbound == 3:
                raise OSError("third outbound failed")
        if "archives" in source_text and "payload" in source_text:
            raise OSError("restore failed")
        return real_replace(source, target)

    monkeypatch.setattr("data_analysts.dataset_publication.os.replace", fail_move_and_every_restore)
    with pytest.raises(ArtifactError, match="recovery"):
        archive_superseded_paths(
            context, contract,
            expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
            confirm_no_legacy_readers=True,
        )

    assert result.manifest_path.read_bytes() == manifest_before
    # The third source never moved; the first two remain durably recoverable in archive.
    assert legacy_paths[2].is_file()
    payloads = list(context.store_path("archives", "superseded").rglob("*.parquet"))
    assert len(payloads) >= 2
    receipts = list(context.store_path("jobs").glob("archive_recovery_*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "manual_recovery_required"
    assert len(receipt["unrestored"]) == 2
    assert all(item["sha256"] and item["manual_step"] for item in receipt["unrestored"])


def test_archive_primary_receipt_failure_uses_durable_fallback_sidecar(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy = context.artifact_path(contract.path_for_partition("2025"))
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    from data_analysts import dataset_publication as publication
    real_durable = publication._durable_atomic_write

    def fail_primary_receipt(path, payload):
        if str(path).endswith("receipt.json"):
            raise OSError("primary receipt failed")
        return real_durable(path, payload)

    monkeypatch.setattr(publication, "_durable_atomic_write", fail_primary_receipt)
    receipt = archive_superseded_paths(
        context, contract,
        expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
        confirm_no_legacy_readers=True,
    )

    assert receipt["receipt_path"].endswith("receipt.fallback.json")
    assert context.store_path(*receipt["receipt_path"].split("/")).is_file()
    assert not legacy.exists()


def test_archive_recovery_intent_uses_jobs_sink_when_archive_sink_fails(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy = context.artifact_path(contract.path_for_partition("2025"))
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    from data_analysts import dataset_publication as publication
    real_durable_write = publication._durable_atomic_write

    def fail_archive_recovery_sink(path, payload):
        if "archives" in str(path) and "recovery" in path.name:
            raise OSError("archive recovery sink unavailable")
        return real_durable_write(path, payload)

    monkeypatch.setattr(publication, "_durable_atomic_write", fail_archive_recovery_sink)
    receipt = archive_superseded_paths(
        context, contract,
        expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
        confirm_no_legacy_readers=True,
    )

    intent_path = context.store_path("jobs", f"archive_recovery_{receipt['archive_id']}.json")
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    assert intent["status"] == "committed"
    assert intent["manifest_before_b64"]
    assert intent["manifest_sha256_after"]
    assert len(intent["mappings"]) == 1
    assert intent["mappings"][0]["state"] == "archived"
    assert not legacy.exists()


def test_archive_moves_nothing_when_all_recovery_intent_sinks_fail(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy = context.artifact_path(contract.path_for_partition("2025"))
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    payload_before = legacy.read_bytes()
    from data_analysts import dataset_publication as publication

    monkeypatch.setattr(
        publication, "_durable_atomic_write",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("all sinks unavailable")),
    )
    with pytest.raises(ArtifactError, match="no payload moved"):
        archive_superseded_paths(
            context, contract,
            expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
            confirm_no_legacy_readers=True,
        )

    assert result.manifest_path.read_bytes() == manifest_before
    assert legacy.read_bytes() == payload_before


def test_archive_rolls_back_if_a_prepared_sink_cannot_reach_terminal_state(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    legacy = context.artifact_path(contract.path_for_partition("2025"))
    legacy.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row("2025-01-02")]), legacy)
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    manifest_before = result.manifest_path.read_bytes()
    payload_before = legacy.read_bytes()
    from data_analysts import dataset_publication as publication
    real_durable = publication._durable_atomic_write
    jobs_writes = 0

    def fail_jobs_terminal_transition(path, payload):
        nonlocal jobs_writes
        if path.parent == context.store_path("jobs") and "archive_recovery_" in path.name:
            jobs_writes += 1
            if jobs_writes > 1:
                raise OSError("jobs terminal transition failed")
        return real_durable(path, payload)

    monkeypatch.setattr(
        publication, "_durable_atomic_write", fail_jobs_terminal_transition
    )
    with pytest.raises(ArtifactError, match="every prepared sink"):
        archive_superseded_paths(
            context, contract,
            expected_manifest_sha256=hashlib.sha256(manifest_before).hexdigest(),
            confirm_no_legacy_readers=True,
        )

    assert result.manifest_path.read_bytes() == manifest_before
    assert legacy.read_bytes() == payload_before


def test_successful_verification_consumes_attestation_and_blocks_replay(tmp_path):
    _write_configs(tmp_path)
    universe_path = tmp_path / "configs" / "universe_specs.json"
    universe_path.write_text(
        json.dumps({"schema_version": "1.0", "universes": []}), encoding="utf-8"
    )
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    before = audit_store(context, config.artifact_contracts)
    pipeline = run_pipeline(
        context, config, run_scope="full_history", publish_ready_state=False,
        pre_publication_audit=before,
    )

    # Scope authority comes from the persisted attestation, not this caller.
    first = verify_runtime(context, pre_publication_audit=before)
    second = verify_runtime(context, pre_publication_audit=before)

    assert first["status"] == "ready"
    for name in ("pipeline_result.json", "current_run.json"):
        state = json.loads(context.store_path("jobs", name).read_text(encoding="utf-8"))
        assert state["run_id"] == pipeline["run_id"]
        assert state["status"] == "ready"
        assert state["phase"] == "complete"
        assert state["run_attestation"]["status"] == "verified"
    assert second["status"] == "blocked"
    assert second["blocked_step"] == "run_attestation"


def test_pipeline_cannot_publish_ready_without_verification(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    with pytest.raises(ValueError, match="fresh verification"):
        run_pipeline(
            context, config, run_scope="full_history", publish_ready_state=True
        )

    assert not context.store_path("jobs", "pipeline_result.json").exists()
    assert not context.store_path("jobs", "current_run.json").exists()


def test_concurrent_attestation_consumers_allow_exactly_one_success(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _write_configs(tmp_path)
    (tmp_path / "configs" / "universe_specs.json").write_text(
        json.dumps({"schema_version": "1.0", "universes": []}), encoding="utf-8"
    )
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    before = audit_store(context, config.artifact_contracts)
    run_pipeline(
        context, config, run_scope="full_history", pre_publication_audit=before
    )

    def consume():
        return _consume_run_attestation(context, config, before, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: consume(), range(2)))

    assert outcomes.count(None) == 1
    assert sum(outcome is not None for outcome in outcomes) == 1
    lock_owner = json.loads(
        context.store_path("jobs", "run_attestation.lock").read_text(encoding="utf-8")
    )
    assert lock_owner["owner_pid"] == os.getpid()
    assert lock_owner["run_id"] != "unknown"


def test_attestation_consume_failure_atomically_restores_both_job_states(
    tmp_path, monkeypatch
):
    _write_configs(tmp_path)
    (tmp_path / "configs" / "universe_specs.json").write_text(
        json.dumps({"schema_version": "1.0", "universes": []}), encoding="utf-8"
    )
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    before = audit_store(context, config.artifact_contracts)
    run_pipeline(
        context, config, run_scope="full_history", pre_publication_audit=before
    )
    pipeline_path = context.store_path("jobs", "pipeline_result.json")
    current_path = context.store_path("jobs", "current_run.json")
    pipeline_before = pipeline_path.read_bytes()
    current_before = current_path.read_bytes()
    from data_analysts import artifacts
    real_atomic = artifacts.atomic_write_text

    def fail_pipeline_commit(path, payload):
        if path == pipeline_path:
            raise OSError("pipeline commit failed")
        return real_atomic(path, payload)

    monkeypatch.setattr(artifacts, "atomic_write_text", fail_pipeline_commit)
    verification = verify_runtime(context, pre_publication_audit=before)

    assert verification["status"] == "blocked"
    assert "pipeline commit failed" in verification["message"]
    persisted_verification = json.loads(
        context.store_path("jobs", "verification_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted_verification["status"] == "blocked"
    assert pipeline_path.read_bytes() == pipeline_before
    assert current_path.read_bytes() == current_before
    assert not list(context.store_path("jobs").glob("attestation_consume_recovery_*.json"))


def test_parquet_handles_close_when_schema_or_read_raises(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    contract = _contract()
    result = publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    from data_analysts import artifacts, store_audit
    real_audit_parquet = store_audit.pq.ParquetFile
    audit_closed = {"value": False}

    class SchemaFailure:
        def __init__(self, path):
            self.inner = real_audit_parquet(path)

        @property
        def schema_arrow(self):
            raise RuntimeError("schema failure")

        def close(self):
            audit_closed["value"] = True
            self.inner.close()

    monkeypatch.setattr(store_audit.pq, "ParquetFile", SchemaFailure)
    with pytest.raises(RuntimeError, match="schema failure"):
        audit_store(context, {contract.contract_key: contract})
    assert audit_closed["value"] is True

    stage_closed = {"value": False}

    class ReadFailure:
        def __init__(self, path):
            self.inner = real_audit_parquet(path)

        def read(self):
            raise RuntimeError("read failure")

        def close(self):
            stage_closed["value"] = True
            self.inner.close()

    monkeypatch.setattr(artifacts.pq, "ParquetFile", ReadFailure)
    target = context.artifact_path("canonical/raw/test/part.parquet")
    with pytest.raises(ArtifactError, match="read failure"):
        artifacts.stage_parquet(target, pa.Table.from_pylist([{"value": 1}]))
    assert stage_closed["value"] is True


@pytest.mark.parametrize(
    "tamper",
    [
        "ready",
        "empty_manifests",
        "missing_enabled",
        "new_manifest",
        "malformed_selected",
        "expected_matrix",
        "expected_contract_keys",
        "changed_contract_keys",
    ],
)
def test_attestation_rejects_stale_or_incomplete_persisted_state(tmp_path, tamper):
    _write_configs(tmp_path)
    (tmp_path / "configs" / "universe_specs.json").write_text(
        json.dumps({"schema_version": "1.0", "universes": []}), encoding="utf-8"
    )
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    before = audit_store(context, config.artifact_contracts)
    run_pipeline(
        context, config, run_scope="full_history", publish_ready_state=False,
        pre_publication_audit=before,
    )
    pipeline_path = context.store_path("jobs", "pipeline_result.json")
    current_path = context.store_path("jobs", "current_run.json")
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))
    if tamper == "ready":
        pipeline["status"] = "ready"
    elif tamper == "empty_manifests":
        attestation = {**pipeline["run_attestation"], "manifest_identities": []}
        pipeline["run_attestation"] = attestation
        current["run_attestation"] = attestation
    elif tamper == "missing_enabled":
        enabled = pipeline["run_attestation"]["enabled_families"][1:]
        attestation = {**pipeline["run_attestation"], "enabled_families": enabled}
        pipeline["run_attestation"] = attestation
        current["run_attestation"] = attestation
    elif tamper == "new_manifest":
        extra = context.store_path("manifests", "unexpected.json")
        extra.write_text('{"status":"ready"}', encoding="utf-8")
    elif tamper == "malformed_selected":
        attestation = {**pipeline["run_attestation"], "selected_families": "not-a-list"}
        pipeline["run_attestation"] = attestation
        current["run_attestation"] = attestation
    else:
        field = {
            "expected_matrix": "expected_outputs_by_family",
            "expected_contract_keys": "expected_contract_keys",
            "changed_contract_keys": "changed_contract_keys",
        }[tamper]
        attestation = {**pipeline["run_attestation"], field: []}
        pipeline["run_attestation"] = attestation
        current["run_attestation"] = attestation
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")

    error, _ = _validate_run_attestation(
        context, config, before, "full_history"
    )

    assert error is not None
