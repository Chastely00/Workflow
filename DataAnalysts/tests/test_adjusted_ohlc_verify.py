import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

import data_analysts.adjusted_ohlc_evidence as evidence_module
import data_analysts.verify as verify_module
from data_analysts.adjusted_ohlc import ADJUSTMENT_POLICY_ID, empty_violation_counts
from data_analysts.adjusted_ohlc_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    manifest_fingerprint,
)
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.paths import DataAnalystsContext
from data_analysts.verify import verify_runtime


ARTIFACT_PATH = "canonical/raw/daily_price_volume/year=2026/part.parquet"


def _formal_contracts() -> dict[str, ArtifactContract]:
    def contract(artifact_id: str, base_path: str, partition_field: str):
        return ArtifactContract(
            contract_key=artifact_id,
            artifact_id=artifact_id,
            variant="test",
            layer="raw" if artifact_id == "daily_price_volume" else "derived",
            base_path=base_path,
            file_name="part.parquet",
            required_columns=(partition_field, "ticker"),
            logical_key=(partition_field, "ticker"),
            publication_mode="partition_upsert",
            partition_name="year" if artifact_id == "daily_price_volume" else "event_year",
            partition_field=partition_field,
            date_field=partition_field,
            availability_field=partition_field,
            pit_policy="test",
            source_families=(artifact_id,),
            allow_empty=artifact_id != "daily_price_volume",
        )

    return {
        "daily_price_volume": contract(
            "daily_price_volume", "canonical/raw/daily_price_volume", "date"
        ),
        "dividend_events": contract(
            "dividend_events",
            "canonical/derived/events/dividend_events",
            "event_date",
        ),
        "capital_action_events": contract(
            "capital_action_events",
            "canonical/derived/events/capital_action_events",
            "event_date",
        ),
    }


def _manifest() -> dict[str, object]:
    return {
        "artifact_id": "daily_price_volume",
        "schema_version": "1.0",
        "status": "ready",
        "artifact_paths": [ARTIFACT_PATH],
        "columns": [
            "ticker",
            "date",
            "open",
            "high",
            "low",
            "close",
            "adj_factor",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
            "price_adjustment_status",
        ],
        "row_count": 1,
        "date_range": ["2026-01-02", "2026-01-02"],
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
    }


def _formal_evidence(manifest: dict[str, object]) -> dict[str, object]:
    violations = empty_violation_counts()
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "artifact_id": "daily_price_volume",
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
        "verification_mode": "full",
        "manifest_fingerprint": manifest_fingerprint(manifest),
        "verified_at": "2026-07-16T00:00:00Z",
        "status": "ready",
        "partition_count": 1,
        "ready_partition_count": 1,
        "blocked_partition_count": 0,
        "stale_evidence_count": 0,
        "stale_artifact_paths": [],
        "violation_totals": dict(violations),
        "partitions": [
            {
                "artifact_path": ARTIFACT_PATH,
                "content_sha256": "a" * 64,
                "row_count": 1,
                "date_range": ["2026-01-02", "2026-01-02"],
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
                "verified_at": "2026-07-16T00:00:00Z",
                "status": "ready",
                "violation_counts": dict(violations),
                "initial_state_fingerprint": manifest_fingerprint(
                    {"state": {}, "ending_date_by_ticker": {}}
                ),
                "ending_state_by_ticker": {},
                "ending_date_by_ticker": {},
            }
        ],
        "blocked_reasons": [],
        "event_dependencies": {
            artifact_id: {
                "manifest_fingerprint": None,
                "row_count": None,
                "date_range": None,
                "partitions": [],
            }
            for artifact_id in ("capital_action_events", "dividend_events")
        },
        "ending_state_by_ticker": {},
        "ending_date_by_ticker": {},
    }


def _run_verify(
    tmp_path,
    monkeypatch,
    *,
    manifest: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    manifest = deepcopy(manifest or _manifest())
    for artifact_path in manifest.get("artifact_paths", []):
        artifact = context.artifact_path(artifact_path)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"not-opened-by-evidence-only-verify")
    if evidence is not None:
        evidence_path = context.store_path(
            "diagnostics", "adjusted_ohlc_verification.json"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    metrics = {
        "manifest_count": 1,
        "absolute_artifact_path_count": 0,
        "artifact_path_escape_count": 0,
        "forbidden_path_segment_count": 0,
        "required_manifest_missing_count": 0,
    }
    config = SimpleNamespace(
        source_catalog={"sources": [], "forbidden_sources": []},
        pit_registry={"families": {}},
        universe_specs={},
        artifact_contracts=_formal_contracts(),
    )
    monkeypatch.setattr(verify_module, "load_manifests", lambda context: [manifest])
    monkeypatch.setattr(
        verify_module,
        "_verification_metrics",
        lambda context, manifests: dict(metrics),
    )
    monkeypatch.setattr(verify_module, "load_runtime_config", lambda context: config)
    monkeypatch.setattr(
        verify_module,
        "audit_store",
        lambda context, contracts: {"status": "ready", "issues": [], "metrics": {}},
    )
    monkeypatch.setattr(verify_module, "_metadata_gate", lambda context, metrics: (None, []))
    monkeypatch.setattr(
        verify_module,
        "check_raw_family_diagnostics",
        lambda context: (None, {}),
    )
    monkeypatch.setattr(
        verify_module.pq,
        "ParquetFile",
        lambda *args, **kwargs: pytest.fail("general verify must not open price parquet"),
    )
    monkeypatch.setattr(
        evidence_module,
        "_content_sha256",
        lambda *args, **kwargs: pytest.fail("general verify must not hash content"),
    )
    return verify_runtime(context)


def test_verify_blocks_missing_formal_adjusted_ohlc_evidence(tmp_path, monkeypatch):
    result = _run_verify(tmp_path, monkeypatch)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "adjusted_ohlc"


def test_verify_does_not_treat_missing_manifest_schema_as_legacy(
    tmp_path, monkeypatch
):
    manifest = _manifest()
    manifest.pop("schema_version")

    result = _run_verify(tmp_path, monkeypatch, manifest=manifest)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifest_fingerprints"
    assert result["message"] == "unsupported artifact manifest schema"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_manifest_policy",
        "missing_partition_record",
        "manifest_fingerprint_mismatch",
        "partition_schema_mismatch",
        "partition_policy_mismatch",
        "partition_status_blocked",
        "partition_path_mismatch",
        "partition_path_missing",
        "nonzero_core_violation",
    ],
)
def test_verify_blocks_inconsistent_adjusted_ohlc_evidence(
    tmp_path, monkeypatch, mutation
):
    manifest = _manifest()
    evidence = _formal_evidence(manifest)
    if mutation == "missing_manifest_policy":
        manifest.pop("adjustment_policy_id")
    elif mutation == "missing_partition_record":
        evidence["partitions"] = []
    elif mutation == "manifest_fingerprint_mismatch":
        evidence["manifest_fingerprint"] = "0" * 64
    elif mutation == "partition_schema_mismatch":
        evidence["partitions"][0]["schema_version"] = "0.9"
    elif mutation == "partition_policy_mismatch":
        evidence["partitions"][0]["adjustment_policy_id"] = "unknown"
    elif mutation == "partition_status_blocked":
        evidence["partitions"][0]["status"] = "blocked"
    elif mutation == "partition_path_mismatch":
        evidence["partitions"][0]["artifact_path"] = ARTIFACT_PATH.replace(
            "2026", "2025"
        )
    elif mutation == "partition_path_missing":
        second_path = ARTIFACT_PATH.replace("2026", "2025")
        manifest["artifact_paths"].append(second_path)
        evidence["manifest_fingerprint"] = manifest_fingerprint(manifest)
        second_record = deepcopy(evidence["partitions"][0])
        second_record["artifact_path"] = second_path
        evidence["partitions"].append(second_record)
        evidence["partitions"][0].pop("artifact_path")
    elif mutation == "nonzero_core_violation":
        evidence["violation_totals"]["adjusted_value_mismatch_count"] = 1

    result = _run_verify(
        tmp_path,
        monkeypatch,
        manifest=manifest,
        evidence=evidence,
    )

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "adjusted_ohlc"


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_top_level_field",
        "partition_count_mismatch",
        "contradictory_blocked_count",
        "contradictory_blocked_reasons",
        "missing_partition_field",
        "extra_partition_field",
        "invalid_partition_verified_at",
        "invalid_boundary_fingerprint",
        "partition_row_count_mismatch",
    ],
)
def test_verify_blocks_nonexact_or_contradictory_ready_evidence(
    tmp_path, monkeypatch, mutation
):
    manifest = _manifest()
    evidence = _formal_evidence(manifest)
    record = evidence["partitions"][0]
    if mutation == "extra_top_level_field":
        evidence["unexpected"] = True
    elif mutation == "partition_count_mismatch":
        evidence["partition_count"] = 2
    elif mutation == "contradictory_blocked_count":
        evidence["blocked_partition_count"] = 1
    elif mutation == "contradictory_blocked_reasons":
        evidence["blocked_reasons"] = ["contradicts ready status"]
    elif mutation == "missing_partition_field":
        record.pop("row_count")
    elif mutation == "extra_partition_field":
        record["unexpected"] = True
    elif mutation == "invalid_partition_verified_at":
        record["verified_at"] = "not-a-timestamp"
    elif mutation == "invalid_boundary_fingerprint":
        record["initial_state_fingerprint"] = 7
    elif mutation == "partition_row_count_mismatch":
        record["row_count"] = 2

    result = _run_verify(
        tmp_path,
        monkeypatch,
        manifest=manifest,
        evidence=evidence,
    )

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "adjusted_ohlc"


def test_verify_blocks_partition_order_that_differs_from_manifest(tmp_path, monkeypatch):
    manifest = _manifest()
    earlier_path = ARTIFACT_PATH.replace("2026", "2025")
    manifest["artifact_paths"] = [earlier_path, ARTIFACT_PATH]
    manifest["row_count"] = 2
    manifest["date_range"] = ["2025-01-02", "2026-01-02"]
    evidence = _formal_evidence(manifest)
    earlier = deepcopy(evidence["partitions"][0])
    earlier["artifact_path"] = earlier_path
    earlier["date_range"] = ["2025-01-02", "2025-01-02"]
    later = deepcopy(evidence["partitions"][0])
    evidence["partitions"] = [later, earlier]
    evidence["partition_count"] = 2
    evidence["ready_partition_count"] = 2

    result = _run_verify(
        tmp_path,
        monkeypatch,
        manifest=manifest,
        evidence=evidence,
    )

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "adjusted_ohlc"


def test_verify_accepts_consistent_adjusted_ohlc_evidence_without_scanning_parquet(
    tmp_path, monkeypatch
):
    manifest = _manifest()
    result = _run_verify(
        tmp_path,
        monkeypatch,
        manifest=manifest,
        evidence=_formal_evidence(manifest),
    )

    assert result["status"] == "ready"
    assert {
        "check": "adjusted_ohlc",
        "status": "ready",
        "artifact_id": "daily_price_volume",
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
        "formal_evidence_pointer": "diagnostics/adjusted_ohlc_verification.json",
        "formal_evidence_sha256": sha256(
            json.dumps(_formal_evidence(manifest)).encode("utf-8")
        ).hexdigest(),
        "verified_partition_count": 1,
        "violation_totals": empty_violation_counts(),
    } in result["checks"]


def test_verify_uses_one_formal_evidence_byte_snapshot(tmp_path, monkeypatch):
    evidence_path = (
        tmp_path / "data_store/diagnostics/adjusted_ohlc_verification.json"
    ).resolve()
    reads = []
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def tracked_read_bytes(path):
        if path.resolve() == evidence_path:
            reads.append(path.resolve())
        return original_read_bytes(path)

    def reject_read_text(path, *args, **kwargs):
        if path.resolve() == evidence_path:
            pytest.fail("formal evidence must be parsed from the byte snapshot")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(Path, "read_text", reject_read_text)
    manifest = _manifest()
    evidence = _formal_evidence(manifest)
    result = _run_verify(tmp_path, monkeypatch, manifest=manifest, evidence=evidence)
    check = next(item for item in result["checks"] if item["check"] == "adjusted_ohlc")

    assert reads == [evidence_path]
    assert check["formal_evidence_sha256"] == sha256(
        json.dumps(evidence).encode("utf-8")
    ).hexdigest()
