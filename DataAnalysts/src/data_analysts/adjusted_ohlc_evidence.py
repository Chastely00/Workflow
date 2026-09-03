from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_right
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.adjusted_ohlc import (
    ADJUSTMENT_POLICY_ID,
    REQUIRED_ADJUSTED_OHLC_COLUMNS,
    AdjustmentFactorStateMachine,
    AdjustmentSeed,
    ExpectedAdjustmentEvent,
    empty_violation_counts,
    normalize_adjusted_ohlc_identity,
    normalize_adjusted_ohlc_ticker,
    validate_adjusted_ohlc_rows,
)
from data_analysts.filesystem import replace_file
from data_analysts.artifact_contracts import (
    ArtifactContract,
    ArtifactContractError,
    contract_partition_value,
    versioned_partition_value,
)
from data_analysts.partition_transactions import (
    PublishTransactionError,
    capture_partition_source,
    commit_publish_transaction,
)
from data_analysts.paths import DataAnalystsContext, PathBoundaryError


EVIDENCE_SCHEMA_VERSION = "1.0"
CAPITAL_ACTION_EVENT_TYPES = frozenset(
    {"capital_reduction", "split", "stock_price_adjustment"}
)
_PARQUET_BATCH_SIZE = 65_536
_HASH_CHUNK_SIZE = 1024 * 1024
_CANDIDATE_PATH = "jobs/adjusted_ohlc_audit_candidate.json"
_FORMAL_EVIDENCE_PATH = "diagnostics/adjusted_ohlc_verification.json"
_MANIFEST_PATH = "manifests/daily_price_volume.json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ARTIFACT_IDS = ("capital_action_events", "dividend_events")
_REQUIRED_PRICE_COLUMNS = frozenset(
    {"ticker", "date", *REQUIRED_ADJUSTED_OHLC_COLUMNS}
)
_PRICE_NUMERIC_COLUMNS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "adj_factor",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
    }
)
_EVENT_REQUIRED_COLUMNS = {
    "dividend_events": frozenset(
        {
            "event_date",
            "ticker",
            "cash_dividend_per_share",
            "stock_dividend_ratio",
        }
    ),
    "capital_action_events": frozenset(
        {
            "event_date",
            "ticker",
            "action_type",
            "share_multiplier",
            "cash_return_per_share",
            "price_adjustment_reference",
        }
    ),
}
_EVENT_NUMERIC_COLUMNS = {
    "dividend_events": frozenset(
        {"cash_dividend_per_share", "stock_dividend_ratio"}
    ),
    "capital_action_events": frozenset(
        {"share_multiplier", "cash_return_per_share", "price_adjustment_reference"}
    ),
}
_EVENT_STRING_COLUMNS = {
    "dividend_events": frozenset({"ticker"}),
    "capital_action_events": frozenset({"ticker", "action_type"}),
}


def _contract_partition_year(
    contract: ArtifactContract,
    artifact_path: str,
    *,
    active_version: str | None = None,
) -> int:
    try:
        value = contract_partition_value(
            contract, artifact_path, active_version=active_version
        )
        if len(value) != 4 or not value.isdigit():
            raise ArtifactContractError("partition must be a four-digit year")
        return int(value)
    except (ArtifactContractError, IndexError) as exc:
        raise AdjustedOhlcEvidenceError(
            f"invalid official event manifest path: {contract.artifact_id}"
        ) from exc


def _embedded_contract_partition_year(
    contract: ArtifactContract, artifact_path: str
) -> int:
    """Parse an already-bound internal evidence path, including its version."""
    try:
        normalized = PurePosixPath(artifact_path).parts
        base = PurePosixPath(contract.base_path).parts
        remainder = normalized[len(base):] if normalized[:len(base)] == base else ()
        if len(remainder) == 2:
            value = contract_partition_value(
                contract, artifact_path, active_version=None
            )
        else:
            version = remainder[1] if len(remainder) == 4 else ""
            value = versioned_partition_value(
                contract, artifact_path, active_version=version
            )
        if len(value) != 4 or not value.isdigit():
            raise ArtifactContractError("partition must be a four-digit year")
        return int(value)
    except (ArtifactContractError, IndexError) as exc:
        raise AdjustedOhlcEvidenceError(
            f"invalid internal evidence path: {contract.artifact_id}"
        ) from exc


_PARTITION_RECORD_FIELDS = frozenset(
    {
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
)
_EVIDENCE_FIELDS = frozenset(
    {
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
)


class AdjustedOhlcEvidenceError(ValueError):
    """Raised when adjusted OHLC evidence cannot be safely promoted."""


class _PriceArrowSchemaError(AdjustedOhlcEvidenceError):
    def __init__(self, message: str, *, invalid_identity_count: int = 0) -> None:
        super().__init__(message)
        self.invalid_identity_count = invalid_identity_count


class _ContentHashCache:
    def __init__(
        self,
        expected_hashes: Mapping[Path, str] | None = None,
        capture_expected_hash: Callable[[Path], str | None] | None = None,
    ) -> None:
        self._entries: dict[Path, tuple[tuple[int, int, int, int, int], str]] = {}
        self._expected_hashes = {
            Path(path).resolve(): content_sha256
            for path, content_sha256 in (expected_hashes or {}).items()
        }
        self._capture_expected_hash = capture_expected_hash

    def get(self, path: Path, *, fresh: bool = False) -> str:
        resolved = Path(path).resolve(strict=True)
        expected_hash = self._expected_hashes.get(resolved)
        if expected_hash is None and self._capture_expected_hash is not None:
            expected_hash = self._capture_expected_hash(resolved)
            if expected_hash is not None:
                self._expected_hashes[resolved] = expected_hash
        if expected_hash is not None and not fresh:
            return expected_hash
        before = self._snapshot(resolved)
        cached = self._entries.get(resolved)
        if not fresh and cached is not None and cached[0] == before:
            return cached[1]
        content_hash = _content_sha256(resolved)
        after = self._snapshot(resolved)
        if before != after:
            raise AdjustedOhlcEvidenceError(
                f"artifact changed while hashing: {resolved}"
            )
        if expected_hash is not None and content_hash != expected_hash:
            raise AdjustedOhlcEvidenceError(
                f"artifact changed since entry snapshot: {resolved}"
            )
        self._entries[resolved] = (after, content_hash)
        return content_hash

    @staticmethod
    def _snapshot(path: Path) -> tuple[int, int, int, int, int]:
        stat = path.stat()
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Return a canonical fingerprint for a JSON-compatible manifest."""
    try:
        serialized = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdjustedOhlcEvidenceError("manifest must be JSON-compatible") from exc
    return hashlib.sha256(serialized).hexdigest()


def _artifact_read_path(
    context: DataAnalystsContext,
    artifact_path: str,
    path_overrides: Mapping[str, Path] | None,
) -> Path:
    if path_overrides is None or artifact_path not in path_overrides:
        return context.artifact_path(artifact_path)
    context.validate_artifact_path(artifact_path)
    override = Path(path_overrides[artifact_path])
    if not override.is_file():
        raise AdjustedOhlcEvidenceError(
            f"prospective artifact override is not a file: {artifact_path}"
        )
    return override


def audit_adjusted_ohlc(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    *,
    contracts: Mapping[str, ArtifactContract],
    mode: Literal["full", "incremental"],
    changed_paths: set[str] | None = None,
    previous_evidence: Mapping[str, Any] | None = None,
    path_overrides: Mapping[str, Path] | None = None,
    manifest_overrides: Mapping[str, Mapping[str, Any] | None] | None = None,
    formal_event_manifest_overrides: Mapping[
        str, Mapping[str, Any] | None
    ] | None = None,
    changed_event_paths: set[str] | None = None,
    entry_content_sha256: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build adjusted OHLC evidence using bounded partition scans."""
    verified_at = _utc_now()
    bounded_entry_snapshot = entry_content_sha256 is not None
    price_contract = contracts["daily_price_volume"]
    event_contracts = {
        artifact_id: contracts[artifact_id]
        for artifact_id in _EVENT_ARTIFACT_IDS
    }

    def capture_entry_hash(target: Path) -> str | None:
        if entry_content_sha256 is None:
            return None
        try:
            artifact_path = target.relative_to(context.data_store.resolve()).as_posix()
        except ValueError:
            return None
        if artifact_path not in entry_content_sha256:
            return None
        return capture_partition_source(
            context, entry_content_sha256, artifact_path
        )

    hash_cache = _ContentHashCache(
        {
            context.artifact_path(path): content_sha256
            for path, content_sha256 in (entry_content_sha256 or {}).items()
            if content_sha256 is not None
        },
        capture_expected_hash=capture_entry_hash,
    )
    try:
        fingerprint = manifest_fingerprint(manifest)
    except AdjustedOhlcEvidenceError as exc:
        return _blocked_evidence(mode, "", verified_at, [str(exc)])

    try:
        ordered_paths = _validate_price_manifest(
            context, manifest, mode, price_contract
        )
        normalized_changed_paths = _validate_changed_paths(
            context, ordered_paths, mode, changed_paths
        )
    except (AdjustedOhlcEvidenceError, OSError, TypeError, ValueError) as exc:
        return _blocked_evidence(mode, fingerprint, verified_at, [str(exc)])

    previous_records, previous_error = _previous_partition_records(
        previous_evidence,
        mode,
        ordered_paths=ordered_paths,
        changed_paths=normalized_changed_paths,
        event_contracts=event_contracts,
    )
    if previous_error is not None:
        return _blocked_evidence(
            mode,
            fingerprint,
            verified_at,
            [f"{previous_error}; full audit required"],
        )

    try:
        normalized_event_paths = {
            context.validate_artifact_path(path)
            for path in (changed_event_paths or set())
        }
        reusable_event_dependencies = (
            previous_evidence.get("event_dependencies")
            if bounded_entry_snapshot
            and mode == "incremental"
            and isinstance(previous_evidence, Mapping)
            else None
        )
        formal_event_dependencies = _load_event_dependencies(
            context,
            manifest_overrides=formal_event_manifest_overrides,
            hash_cache=hash_cache,
            reusable_dependencies=reusable_event_dependencies,
            refresh_paths=normalized_event_paths,
            event_contracts=event_contracts,
        )
        if mode == "incremental" and isinstance(previous_evidence, Mapping):
            _reuse_event_partition_summaries(
                formal_event_dependencies,
                previous_evidence.get("event_dependencies"),
                event_contracts,
            )
        event_dependencies = _load_event_dependencies(
            context,
            path_overrides=path_overrides,
            manifest_overrides=manifest_overrides,
            hash_cache=hash_cache,
            reusable_dependencies=(
                formal_event_dependencies
                if bounded_entry_snapshot and mode == "incremental"
                else None
            ),
            refresh_paths=normalized_event_paths,
            event_contracts=event_contracts,
        )
        if mode == "incremental" and isinstance(previous_evidence, Mapping):
            _reuse_event_partition_summaries(
                event_dependencies, formal_event_dependencies, event_contracts
            )
            _reuse_event_partition_summaries(
                event_dependencies, previous_evidence.get("event_dependencies"),
                event_contracts,
            )
    except (AdjustedOhlcEvidenceError, OSError, TypeError, ValueError) as exc:
        return _blocked_evidence(mode, fingerprint, verified_at, [str(exc)])

    dependency_drift_paths: set[str] = set()
    event_dependencies_unchanged = False
    if mode == "incremental" and isinstance(previous_evidence, Mapping):
        try:
            previous_event_dependencies = previous_evidence.get("event_dependencies")
            formal_drift_paths = _changed_event_dependency_paths(
                previous_event_dependencies, formal_event_dependencies,
                event_contracts,
            )
            prospective_drift_paths = _changed_event_dependency_paths(
                formal_event_dependencies, event_dependencies,
                event_contracts,
            )
            dependency_drift_paths = formal_drift_paths.union(
                prospective_drift_paths
            )
            event_dependencies_unchanged = (
                previous_event_dependencies == formal_event_dependencies
                and formal_event_dependencies == event_dependencies
            )
            if previous_event_dependencies != event_dependencies:
                dependency_drift_paths.update(
                    _changed_event_dependency_paths(
                        previous_event_dependencies, event_dependencies,
                        event_contracts,
                    )
                )
            current_event_paths = {
                record["artifact_path"]
                for dependency in event_dependencies.values()
                for record in dependency["partitions"]
            }
            unknown_event_paths = normalized_event_paths.difference(current_event_paths)
            if unknown_event_paths:
                raise AdjustedOhlcEvidenceError(
                    f"changed event paths are not present in prospective manifests: {sorted(unknown_event_paths)}"
                )
            if previous_event_dependencies != event_dependencies:
                if not dependency_drift_paths:
                    raise AdjustedOhlcEvidenceError(
                        "event dependency drift has no bounded event year; full audit required"
                    )
                earliest_event_year = min(
                    _event_partition_year(event_contracts, path)
                    for path in dependency_drift_paths
                )
                suffix_start = next(
                    (
                        index
                        for index, path in enumerate(ordered_paths)
                        if _partition_year(price_contract, path) >= earliest_event_year
                    ),
                    len(ordered_paths),
                )
                while suffix_start > 0 and not _trusted_previous_partition(
                    context,
                    ordered_paths[suffix_start - 1],
                    previous_records,
                    path_overrides,
                    hash_cache,
                    trust_previous_hash=bounded_entry_snapshot,
                ):
                    suffix_start -= 1
                normalized_changed_paths.update(ordered_paths[suffix_start:])
        except (AdjustedOhlcEvidenceError, TypeError, ValueError) as exc:
            return _blocked_evidence(mode, fingerprint, verified_at, [str(exc)])
    expected_events: dict[str, list[ExpectedAdjustmentEvent]] = {}
    if mode == "full" or normalized_changed_paths:
        maximum_event_year = max(
            _partition_year(price_contract, path)
            for path in (
                ordered_paths if mode == "full" else normalized_changed_paths
            )
        )
        event_lower_bounds: dict[str, str | None] | None = None
        try:
            if event_dependencies_unchanged:
                affected_tickers = _affected_price_tickers(
                    context,
                    normalized_changed_paths,
                    path_overrides,
                    hash_cache,
                    price_contract,
                )
                event_lower_bounds = _trusted_event_scan_boundary(
                    context,
                    ordered_paths,
                    normalized_changed_paths,
                    affected_tickers,
                    previous_records,
                    path_overrides,
                    hash_cache,
                    trust_previous_hash=bounded_entry_snapshot,
                )
            expected_events = _load_expected_events(
                context,
                event_dependencies,
                maximum_event_year=maximum_event_year,
                event_lower_bounds=event_lower_bounds,
                path_overrides=path_overrides,
                hash_cache=hash_cache,
                event_contracts=event_contracts,
            )
            _validate_scanned_event_aggregates(event_dependencies)
        except (AdjustedOhlcEvidenceError, OSError, TypeError, ValueError) as exc:
            return _blocked_evidence(mode, fingerprint, verified_at, [str(exc)])
    records: list[dict[str, Any]] = []
    stale_paths: list[str] = []
    blocked_reasons: list[str] = []
    boundary_state: dict[str, AdjustmentSeed] = {}
    boundary_dates: dict[str, str] = {}
    boundary_valid = True

    for artifact_path in ordered_paths:
        initial_fingerprint = _state_fingerprint(boundary_state, boundary_dates)
        target = _artifact_read_path(context, artifact_path, path_overrides)
        should_scan = mode == "full" or artifact_path in normalized_changed_paths
        if not boundary_valid:
            record = _blocked_partition_record(
                artifact_path,
                target,
                verified_at,
                initial_fingerprint,
                hash_cache=hash_cache,
            )
            records.append(record)
            stale_paths.append(artifact_path)
            blocked_reasons.append(
                f"boundary state unavailable before partition: {artifact_path}"
            )
            continue

        if should_scan:
            events_for_partition = _events_for_partition(
                expected_events,
                boundary_dates,
                _partition_year(price_contract, artifact_path),
            )
            try:
                record, changed_during_scan, partition_reasons = _scan_partition(
                    target,
                    artifact_path,
                    verified_at,
                    boundary_state,
                    boundary_dates,
                    initial_fingerprint,
                    events_for_partition,
                    hash_cache,
                    price_contract,
                )
            except (OSError, ValueError, TypeError) as exc:
                record = _blocked_partition_record(
                    artifact_path,
                    target,
                    verified_at,
                    initial_fingerprint,
                    hash_cache=hash_cache,
                )
                if isinstance(exc, _PriceArrowSchemaError):
                    record["violation_counts"]["missing_required_column_count"] = (
                        exc.invalid_identity_count
                    )
                changed_during_scan = False
                partition_reasons = []
                stale_paths.append(artifact_path)
                blocked_reasons.append(
                    f"partition scan failed: {artifact_path}: {exc}"
                )
            if changed_during_scan:
                stale_paths.append(artifact_path)
                blocked_reasons.append(
                    f"partition changed during scan: {artifact_path}"
                )
            blocked_reasons.extend(partition_reasons)
        else:
            previous_record = previous_records.get(artifact_path)
            record = _reuse_partition_record(
                target,
                artifact_path,
                previous_record,
                initial_fingerprint,
                verified_at,
                previous_error,
                hash_cache,
                trust_previous_hash=bounded_entry_snapshot,
            )
            if record["status"] != "ready":
                stale_paths.append(artifact_path)
                blocked_reasons.append(
                    f"stale or incompatible previous evidence: {artifact_path}"
                )

        if record["status"] == "ready":
            temporal_error = _partition_temporal_error(
                record, artifact_path, boundary_dates, price_contract
            )
            if temporal_error is not None:
                record = dict(record)
                record["status"] = "blocked"
                stale_paths.append(artifact_path)
                blocked_reasons.append(temporal_error)
        records.append(record)
        if record["status"] != "ready":
            boundary_valid = False
            continue
        try:
            boundary_state = _decode_state(record["ending_state_by_ticker"])
            boundary_dates = _decode_boundary_dates(
                record["ending_date_by_ticker"]
            )
        except AdjustedOhlcEvidenceError as exc:
            boundary_valid = False
            stale_paths.append(artifact_path)
            blocked_reasons.append(f"{exc}: {artifact_path}")

    violation_totals = empty_violation_counts()
    for record in records:
        counts = record.get("violation_counts")
        if not _valid_violation_counts(counts):
            blocked_reasons.append(
                f"partition has invalid violation schema: {record.get('artifact_path')}"
            )
            continue
        for counter in violation_totals:
            violation_totals[counter] += counts[counter]

    ready_count = sum(record.get("status") == "ready" for record in records)
    blocked_count = len(records) - ready_count
    if any(violation_totals.values()):
        blocked_reasons.append("one or more core adjusted OHLC violations are nonzero")
    aggregate_error = _manifest_aggregate_error(manifest, records)
    if aggregate_error is not None:
        blocked_reasons.append(aggregate_error)
    stale_paths = sorted(set(stale_paths))
    status = (
        "ready"
        if not blocked_reasons
        and blocked_count == 0
        and ready_count == len(ordered_paths)
        and not stale_paths
        else "blocked"
    )
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "artifact_id": "daily_price_volume",
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
        "verification_mode": mode,
        "manifest_fingerprint": fingerprint,
        "verified_at": verified_at,
        "status": status,
        "partition_count": len(ordered_paths),
        "ready_partition_count": ready_count,
        "blocked_partition_count": blocked_count,
        "stale_evidence_count": len(stale_paths),
        "stale_artifact_paths": stale_paths,
        "violation_totals": violation_totals,
        "partitions": records,
        "blocked_reasons": sorted(set(blocked_reasons)),
        "event_dependencies": event_dependencies,
        "ending_state_by_ticker": _encode_state(boundary_state),
        "ending_date_by_ticker": dict(sorted(boundary_dates.items())),
    }


def write_candidate_audit(
    context: DataAnalystsContext, evidence: Mapping[str, Any]
) -> Path:
    """Atomically write an audit candidate without changing formal metadata."""
    _validate_evidence_identity(evidence, require_ready=False)
    target = context.store_path("jobs", "adjusted_ohlc_audit_candidate.json")
    _atomic_write_json(target, evidence)
    return target


def promote_audit_candidate(
    context: DataAnalystsContext,
    contracts: Mapping[str, ArtifactContract],
) -> dict[str, Any]:
    """Promote a ready candidate after pre-commit row-derived verification."""
    candidate_path = context.store_path(*_CANDIDATE_PATH.split("/"))
    candidate, candidate_hash = _load_json_object_snapshot(candidate_path)
    current_manifest_path = context.store_path(*_MANIFEST_PATH.split("/"))
    current_manifest, current_manifest_hash = _load_json_object_snapshot(
        current_manifest_path
    )
    ordered_paths, records_by_path = validate_ready_formal_evidence(
        context, current_manifest, candidate, contracts
    )
    event_source_preconditions = _validate_candidate_event_dependencies(
        context,
        candidate.get("event_dependencies"),
        {artifact_id: contracts[artifact_id] for artifact_id in _EVENT_ARTIFACT_IDS},
    )
    source_preconditions = dict(event_source_preconditions)
    source_preconditions[_CANDIDATE_PATH] = candidate_hash
    source_preconditions[_MANIFEST_PATH] = current_manifest_hash
    for artifact_path in ordered_paths:
        source_preconditions[artifact_path] = records_by_path[artifact_path][
            "content_sha256"
        ]

    prospective_manifest = dict(current_manifest)
    prospective_manifest["adjustment_policy_id"] = ADJUSTMENT_POLICY_ID
    formal_evidence = dict(candidate)
    formal_evidence["manifest_fingerprint"] = manifest_fingerprint(
        prospective_manifest
    )
    _verify_candidate_rows_before_commit(
        context,
        current_manifest,
        ordered_paths,
        records_by_path,
        candidate["event_dependencies"],
        contracts["daily_price_volume"],
        {artifact_id: contracts[artifact_id] for artifact_id in _EVENT_ARTIFACT_IDS},
    )
    try:
        commit_publish_transaction(
            context,
            [],
            {
                _MANIFEST_PATH: prospective_manifest,
                _FORMAL_EVIDENCE_PATH: formal_evidence,
            },
            source_preconditions=source_preconditions,
        )
    except PublishTransactionError as exc:
        message = str(exc)
        if "source precondition" in message:
            prefix = (
                "stale candidate event "
                if any(path in message for path in event_source_preconditions)
                else "stale candidate "
            )
            message = f"{prefix}{message}"
        raise AdjustedOhlcEvidenceError(message) from exc
    return formal_evidence


def validate_ready_formal_evidence(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    contracts: Mapping[str, ArtifactContract],
) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
    """Validate formal evidence structure without reading Parquet or hashing content."""
    _validate_evidence_identity(evidence, require_ready=True)
    if manifest_fingerprint(manifest) != evidence.get("manifest_fingerprint"):
        raise AdjustedOhlcEvidenceError("stale candidate manifest fingerprint")

    price_contract = contracts["daily_price_volume"]
    event_contracts = {
        artifact_id: contracts[artifact_id] for artifact_id in _EVENT_ARTIFACT_IDS
    }
    ordered_paths = _validate_price_manifest(
        context, manifest, "full", price_contract
    )
    records = evidence.get("partitions")
    if not isinstance(records, list):
        raise AdjustedOhlcEvidenceError("stale candidate partition records")
    records_by_path: dict[str, Mapping[str, Any]] = {}
    candidate_paths: list[str] = []
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _PARTITION_RECORD_FIELDS:
            raise AdjustedOhlcEvidenceError("stale candidate partition schema")
        artifact_path = record.get("artifact_path")
        if not isinstance(artifact_path, str) or artifact_path in records_by_path:
            raise AdjustedOhlcEvidenceError("stale candidate partition paths")
        candidate_paths.append(artifact_path)
        records_by_path[artifact_path] = record
    if set(records_by_path) != set(ordered_paths):
        raise AdjustedOhlcEvidenceError("stale candidate partition paths")
    if candidate_paths != ordered_paths:
        raise AdjustedOhlcEvidenceError("stale candidate partition order")

    _validate_ready_candidate_summary(evidence, len(ordered_paths))
    _validate_event_dependencies_structure(
        evidence.get("event_dependencies"), event_contracts
    )
    boundary_state: dict[str, AdjustmentSeed] = {}
    boundary_dates: dict[str, str] = {}
    evidence_verified_at = _parse_verified_at(evidence.get("verified_at"))
    if evidence_verified_at is None:
        raise AdjustedOhlcEvidenceError("invalid candidate verified_at")
    derived_violation_totals = empty_violation_counts()
    for artifact_path in ordered_paths:
        record = records_by_path[artifact_path]
        if (
            record.get("status") != "ready"
            or record.get("schema_version") != EVIDENCE_SCHEMA_VERSION
            or record.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
        ):
            raise AdjustedOhlcEvidenceError("stale candidate partition record")
        content_sha256 = record.get("content_sha256")
        if not isinstance(content_sha256, str) or _SHA256_PATTERN.fullmatch(
            content_sha256
        ) is None:
            raise AdjustedOhlcEvidenceError("stale candidate partition record")
        if not _valid_violation_counts(record.get("violation_counts")) or any(
            record["violation_counts"].values()
        ):
            raise AdjustedOhlcEvidenceError("stale candidate partition violations")
        if not _valid_encoded_state(record.get("ending_state_by_ticker")):
            raise AdjustedOhlcEvidenceError(
                "stale candidate partition boundary state"
            )
        if not _valid_boundary_dates(record.get("ending_date_by_ticker")):
            raise AdjustedOhlcEvidenceError(
                "stale candidate partition boundary dates"
            )
        if not _valid_partition_metadata(record, evidence_verified_at):
            raise AdjustedOhlcEvidenceError("stale candidate partition metadata")
        temporal_error = _partition_temporal_error(
            record, artifact_path, boundary_dates, price_contract
        )
        if temporal_error is not None:
            raise AdjustedOhlcEvidenceError(temporal_error)
        if record.get("initial_state_fingerprint") != _state_fingerprint(
            boundary_state, boundary_dates
        ):
            raise AdjustedOhlcEvidenceError("stale candidate boundary state")
        boundary_state = _decode_state(record["ending_state_by_ticker"])
        boundary_dates = _decode_boundary_dates(record["ending_date_by_ticker"])
        for counter in derived_violation_totals:
            derived_violation_totals[counter] += record["violation_counts"][counter]

    aggregate_error = _manifest_aggregate_error(
        manifest, [records_by_path[path] for path in ordered_paths]
    )
    if aggregate_error is not None:
        raise AdjustedOhlcEvidenceError(aggregate_error)
    if (
        evidence.get("ending_state_by_ticker") != _encode_state(boundary_state)
        or evidence.get("ending_date_by_ticker")
        != dict(sorted(boundary_dates.items()))
    ):
        raise AdjustedOhlcEvidenceError("stale candidate boundary summary")
    if evidence.get("violation_totals") != derived_violation_totals:
        raise AdjustedOhlcEvidenceError("stale candidate violation summary")
    return ordered_paths, records_by_path


def _validate_price_manifest(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    mode: str,
    contract: ArtifactContract,
) -> list[str]:
    if manifest.get("artifact_id") != contract.artifact_id:
        raise AdjustedOhlcEvidenceError("unknown adjusted OHLC artifact manifest")
    if manifest.get("schema_version") not in {"1.0", "1.1"}:
        raise AdjustedOhlcEvidenceError("unknown daily_price_volume schema")
    if manifest.get("status") != "ready":
        raise AdjustedOhlcEvidenceError("daily_price_volume manifest is not ready")
    policy = manifest.get("adjustment_policy_id")
    if policy not in {None, ADJUSTMENT_POLICY_ID}:
        raise AdjustedOhlcEvidenceError("unknown adjustment policy")
    if mode == "incremental" and policy != ADJUSTMENT_POLICY_ID:
        raise AdjustedOhlcEvidenceError(
            "incremental audit requires the current adjustment policy"
        )
    row_count = manifest.get("row_count")
    date_range = manifest.get("date_range")
    if (
        type(row_count) is not int
        or row_count < 0
        or not _valid_date_range(date_range)
        or ((row_count == 0) != (date_range is None))
    ):
        raise AdjustedOhlcEvidenceError(
            "daily_price_volume manifest aggregate metadata is invalid"
        )
    columns = manifest.get("columns")
    if not _valid_manifest_columns(columns, _REQUIRED_PRICE_COLUMNS):
        raise AdjustedOhlcEvidenceError("daily_price_volume manifest columns are invalid")
    paths = manifest.get("artifact_paths")
    if (
        not isinstance(paths, list)
        or (row_count == 0 and paths)
        or (row_count > 0 and not paths)
    ):
        raise AdjustedOhlcEvidenceError("daily_price_volume manifest has no paths")

    parsed: list[tuple[int, str]] = []
    years: set[int] = set()
    for raw_path in paths:
        if not isinstance(raw_path, str):
            raise AdjustedOhlcEvidenceError("manifest has a non-string artifact path")
        try:
            artifact_path = context.validate_artifact_path(raw_path)
        except PathBoundaryError as exc:
            raise AdjustedOhlcEvidenceError(f"invalid manifest path: {exc}") from exc
        try:
            year = _contract_partition_year(
                contract,
                artifact_path,
                active_version=manifest.get("active_version"),
            )
        except AdjustedOhlcEvidenceError as exc:
            raise AdjustedOhlcEvidenceError(
                f"cannot parse daily_price_volume partition year: {artifact_path}"
            ) from exc
        if year in years:
            raise AdjustedOhlcEvidenceError(f"duplicate manifest partition year: {year}")
        years.add(year)
        parsed.append((year, artifact_path))
    return [path for _, path in sorted(parsed)]


def _validate_changed_paths(
    context: DataAnalystsContext,
    ordered_paths: Sequence[str],
    mode: str,
    changed_paths: set[str] | None,
) -> set[str]:
    if mode not in {"full", "incremental"}:
        raise AdjustedOhlcEvidenceError(f"unknown verification mode: {mode}")
    if mode == "full":
        if changed_paths:
            raise AdjustedOhlcEvidenceError("full audit does not accept changed_paths")
        return set(ordered_paths)
    if changed_paths is None:
        raise AdjustedOhlcEvidenceError("incremental audit requires changed_paths")
    normalized: set[str] = set()
    for raw_path in changed_paths:
        try:
            normalized.add(context.validate_artifact_path(raw_path))
        except PathBoundaryError as exc:
            raise AdjustedOhlcEvidenceError(f"invalid changed path: {exc}") from exc
    unknown = normalized.difference(ordered_paths)
    if unknown:
        raise AdjustedOhlcEvidenceError(
            f"changed_paths are not present in manifest: {sorted(unknown)}"
        )
    return normalized


def _scan_partition(
    target: Path,
    artifact_path: str,
    verified_at: str,
    boundary_state: Mapping[str, AdjustmentSeed],
    boundary_dates: Mapping[str, str],
    initial_state_fingerprint: str,
    expected_events: Mapping[str, Sequence[ExpectedAdjustmentEvent]],
    hash_cache: _ContentHashCache,
    price_contract: ArtifactContract,
) -> tuple[dict[str, Any], bool, list[str]]:
    before_hash = hash_cache.get(target)
    parquet_file = pq.ParquetFile(target)
    ending_dates = dict(boundary_dates)
    partition_year = _partition_year(price_contract, artifact_path)
    partition_reasons: set[str] = set()
    try:
        schema_error = _price_arrow_schema_error(parquet_file.schema_arrow)
        if schema_error is not None:
            raise _PriceArrowSchemaError(
                schema_error,
                invalid_identity_count=_invalid_identity_arrow_type_count(
                    parquet_file.schema_arrow
                ),
            )
        missing_schema_columns = _REQUIRED_PRICE_COLUMNS.difference(
            parquet_file.schema_arrow.names
        )

        def rows() -> Iterable[Mapping[str, Any]]:
            for batch in parquet_file.iter_batches(batch_size=_PARQUET_BATCH_SIZE):
                for row in batch.to_pylist():
                    identity = normalize_adjusted_ohlc_identity(
                        row.get("ticker"), row.get("date")
                    )
                    if identity is not None:
                        ticker, row_date = identity
                        if date.fromisoformat(row_date).year != partition_year:
                            partition_reasons.add(
                                f"price row date does not match partition year: {artifact_path}"
                            )
                        previous_date = ending_dates.get(ticker)
                        if previous_date is not None and row_date < previous_date:
                            partition_reasons.add(
                                "ticker ending_date moved backwards across partitions: "
                                f"{artifact_path}: {ticker}"
                            )
                        if previous_date is None or row_date > previous_date:
                            ending_dates[ticker] = row_date
                    yield row

        result = validate_adjusted_ohlc_rows(
            rows(),
            initial_state_by_ticker=boundary_state,
            expected_events_by_ticker=expected_events,
        )
    finally:
        _close_parquet_file(parquet_file)
    after_hash = hash_cache.get(target, fresh=True)
    violations = dict(result.violation_counts)
    if missing_schema_columns:
        violations["missing_required_column_count"] += len(missing_schema_columns)
    changed_during_scan = before_hash != after_hash
    status = (
        "ready"
        if not changed_during_scan
        and not partition_reasons
        and not any(violations.values())
        else "blocked"
    )
    return (
        {
            "artifact_path": artifact_path,
            "content_sha256": before_hash,
            "row_count": result.row_count,
            "date_range": _serialize_date_range(result.date_range),
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
            "verified_at": verified_at,
            "status": status,
            "violation_counts": violations,
            "initial_state_fingerprint": initial_state_fingerprint,
            "ending_state_by_ticker": _encode_state(
                {**boundary_state, **result.ending_state_by_ticker}
            ),
            "ending_date_by_ticker": dict(sorted(ending_dates.items())),
        },
        changed_during_scan,
        sorted(partition_reasons),
    )


def _price_arrow_schema_error(schema: pa.Schema) -> str | None:
    missing = sorted(_REQUIRED_PRICE_COLUMNS.difference(schema.names))
    if missing:
        return f"invalid price Arrow schema; missing columns: {missing}"

    duplicate_names = sorted(
        name for name in _REQUIRED_PRICE_COLUMNS if len(schema.get_all_field_indices(name)) != 1
    )
    if duplicate_names:
        return f"invalid price Arrow schema; duplicate columns: {duplicate_names}"

    invalid_types: list[str] = []
    for column in sorted(_PRICE_NUMERIC_COLUMNS):
        arrow_type = schema.field(column).type
        if not (
            pa.types.is_integer(arrow_type)
            or pa.types.is_floating(arrow_type)
            or pa.types.is_decimal(arrow_type)
        ):
            invalid_types.append(f"{column}={arrow_type}")

    ticker_type = schema.field("ticker").type
    status_type = schema.field("price_adjustment_status").type
    date_type = schema.field("date").type
    if not (pa.types.is_string(ticker_type) or pa.types.is_large_string(ticker_type)):
        invalid_types.append(f"ticker={ticker_type}")
    if not (pa.types.is_string(status_type) or pa.types.is_large_string(status_type)):
        invalid_types.append(f"price_adjustment_status={status_type}")
    if not (
        pa.types.is_string(date_type)
        or pa.types.is_large_string(date_type)
        or pa.types.is_date32(date_type)
        or pa.types.is_date64(date_type)
    ):
        invalid_types.append(f"date={date_type}")
    if invalid_types:
        return f"invalid price Arrow schema types: {', '.join(invalid_types)}"
    return None


def _event_arrow_schema_error(schema: pa.Schema, artifact_id: str) -> str | None:
    required_columns = _EVENT_REQUIRED_COLUMNS[artifact_id]
    missing = sorted(required_columns.difference(schema.names))
    if missing:
        return f"invalid event Arrow schema; missing columns: {missing}"

    duplicate_names = sorted(
        name for name in required_columns if len(schema.get_all_field_indices(name)) != 1
    )
    if duplicate_names:
        return f"invalid event Arrow schema; duplicate columns: {duplicate_names}"

    invalid_types: list[str] = []
    for column in sorted(_EVENT_NUMERIC_COLUMNS[artifact_id]):
        arrow_type = schema.field(column).type
        if not (
            pa.types.is_integer(arrow_type)
            or pa.types.is_floating(arrow_type)
            or pa.types.is_decimal(arrow_type)
        ) or pa.types.is_boolean(arrow_type):
            invalid_types.append(f"{column}={arrow_type}")
    for column in sorted(_EVENT_STRING_COLUMNS[artifact_id]):
        arrow_type = schema.field(column).type
        if not (pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type)):
            invalid_types.append(f"{column}={arrow_type}")
    if "status" in schema.names:
        status_type = schema.field("status").type
        if not (pa.types.is_string(status_type) or pa.types.is_large_string(status_type)):
            invalid_types.append(f"status={status_type}")
    event_date_type = schema.field("event_date").type
    if not (
        pa.types.is_string(event_date_type)
        or pa.types.is_large_string(event_date_type)
        or pa.types.is_date32(event_date_type)
        or pa.types.is_date64(event_date_type)
    ):
        invalid_types.append(f"event_date={event_date_type}")
    if invalid_types:
        return f"invalid event Arrow schema types: {', '.join(invalid_types)}"
    return None


def _validate_parquet_footer_schema(
    target: Path, *, artifact_id: str | None = None
) -> None:
    parquet_file = pq.ParquetFile(target)
    try:
        error = (
            _price_arrow_schema_error(parquet_file.schema_arrow)
            if artifact_id is None
            else _event_arrow_schema_error(parquet_file.schema_arrow, artifact_id)
        )
        if error is not None:
            raise AdjustedOhlcEvidenceError(error)
    finally:
        _close_parquet_file(parquet_file)


def _verify_candidate_rows_before_commit(
    context: DataAnalystsContext,
    price_manifest: Mapping[str, Any],
    ordered_price_paths: Sequence[str],
    price_records: Mapping[str, Mapping[str, Any]],
    event_dependencies: Mapping[str, Any],
    price_contract: ArtifactContract,
    event_contracts: Mapping[str, ArtifactContract],
) -> None:
    maximum_price_year = max(
        (_partition_year(price_contract, path) for path in ordered_price_paths),
        default=None,
    )
    expected_events: dict[str, list[ExpectedAdjustmentEvent]] = {}
    for artifact_id in _EVENT_ARTIFACT_IDS:
        dependency = event_dependencies[artifact_id]
        records = dependency["partitions"]
        actual_scanned_records: list[dict[str, Any]] = []
        for record in records:
            artifact_path = record["artifact_path"]
            event_year = _embedded_contract_partition_year(
                event_contracts[artifact_id], artifact_path
            )
            is_future = (
                maximum_price_year is not None
                and event_year > maximum_price_year
            )
            if is_future:
                if record["row_count"] is not None:
                    raise AdjustedOhlcEvidenceError(
                        "future event partition must remain hash-only: "
                        f"{artifact_id}: {artifact_path}"
                    )
                continue
            if record["row_count"] is None:
                raise AdjustedOhlcEvidenceError(
                    f"event partition summary missing within price horizon: "
                    f"{artifact_id}: {artifact_path}"
                )
            actual, partition_events = _stream_event_promotion_summary(
                context.artifact_path(artifact_path),
                artifact_path,
                artifact_id,
                event_contracts[artifact_id],
            )
            if (
                record["row_count"] != actual["row_count"]
                or record["date_range"] != actual["date_range"]
            ):
                raise AdjustedOhlcEvidenceError(
                    f"event partition summary mismatch: "
                    f"{artifact_id}: {artifact_path}"
                )
            actual_scanned_records.append(actual)
            for ticker, event in partition_events:
                if event is not None:
                    expected_events.setdefault(ticker, []).append(event)
        if (
            dependency["manifest_fingerprint"] is not None
            and len(actual_scanned_records) == len(records)
        ):
            actual_row_count = sum(
                record["row_count"] for record in actual_scanned_records
            )
            actual_ranges = [
                record["date_range"]
                for record in actual_scanned_records
                if record["date_range"] is not None
            ]
            actual_date_range = (
                None
                if not actual_ranges
                else [
                    min(value[0] for value in actual_ranges),
                    max(value[1] for value in actual_ranges),
                ]
            )
            if (
                dependency["row_count"] != actual_row_count
                or dependency["date_range"] != actual_date_range
            ):
                raise AdjustedOhlcEvidenceError(
                    f"event manifest aggregate mismatch: {artifact_id}"
                )
    for events in expected_events.values():
        events.sort(key=lambda event: str(event.event_date))

    boundary_state: dict[str, AdjustmentSeed] = {}
    boundary_dates: dict[str, str] = {}
    actual_price_records: list[dict[str, Any]] = []
    for artifact_path in ordered_price_paths:
        record = price_records[artifact_path]
        actual = _stream_price_promotion_summary(
            context.artifact_path(artifact_path),
            artifact_path,
            boundary_state,
            boundary_dates,
            _events_for_partition(
                expected_events,
                boundary_dates,
                _partition_year(price_contract, artifact_path),
            ),
            price_contract,
        )
        for field in (
            "row_count",
            "date_range",
            "ending_state_by_ticker",
            "ending_date_by_ticker",
        ):
            if record.get(field) != actual[field]:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate price partition boundary summary mismatch: "
                    f"{artifact_path}: {field}"
                )
        boundary_state = _decode_state(actual["ending_state_by_ticker"])
        boundary_dates = _decode_boundary_dates(actual["ending_date_by_ticker"])
        actual_price_records.append(actual)

    aggregate_error = _manifest_aggregate_error(price_manifest, actual_price_records)
    if aggregate_error is not None:
        raise AdjustedOhlcEvidenceError(aggregate_error)


def _stream_price_promotion_summary(
    target: Path,
    artifact_path: str,
    initial_state: Mapping[str, AdjustmentSeed],
    initial_dates: Mapping[str, str],
    expected_events: Mapping[str, Sequence[ExpectedAdjustmentEvent]],
    price_contract: ArtifactContract,
) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(target)
    partition_year = _partition_year(price_contract, artifact_path)
    factor_machine = AdjustmentFactorStateMachine(
        initial_state_by_ticker=initial_state,
        expected_events_by_ticker=expected_events,
    )
    ending_dates = dict(initial_dates)
    row_count = 0
    minimum_date: str | None = None
    maximum_date: str | None = None
    try:
        schema_error = _price_arrow_schema_error(parquet_file.schema_arrow)
        if schema_error is not None:
            raise AdjustedOhlcEvidenceError(schema_error)
        for batch in parquet_file.iter_batches(batch_size=_PARQUET_BATCH_SIZE):
            for row in batch.to_pylist():
                row_count += 1
                identity = normalize_adjusted_ohlc_identity(
                    row.get("ticker"), row.get("date")
                )
                if identity is None:
                    raise AdjustedOhlcEvidenceError(
                        f"invalid price row identity during promotion: {artifact_path}"
                    )
                ticker, row_date = identity
                if date.fromisoformat(row_date).year != partition_year:
                    raise AdjustedOhlcEvidenceError(
                        f"price row date does not match partition year: {artifact_path}"
                    )
                previous_date = ending_dates.get(ticker)
                if previous_date is not None and row_date < previous_date:
                    raise AdjustedOhlcEvidenceError(
                        "ticker ending_date moved backwards across partitions: "
                        f"{artifact_path}: {ticker}"
                    )
                if previous_date is None or row_date > previous_date:
                    ending_dates[ticker] = row_date
                minimum_date = row_date if minimum_date is None else min(minimum_date, row_date)
                maximum_date = row_date if maximum_date is None else max(maximum_date, row_date)

                transition = factor_machine.apply_row(
                    ticker=ticker,
                    row_date=row_date,
                    raw_factor=row.get("adj_factor"),
                    raw_close=row.get("close"),
                )
                if transition.invalid_raw_factor:
                    raise AdjustedOhlcEvidenceError(
                        f"invalid price adj_factor during promotion: {artifact_path}"
                    )
                if transition.invalid_seed or transition.factor_transition_violation:
                    failure_label = (
                        "stale candidate event or price partition boundary factor"
                        if expected_events
                        else "stale candidate price partition boundary factor"
                    )
                    raise AdjustedOhlcEvidenceError(
                        f"{failure_label}: {artifact_path}: {ticker}"
                    )
    finally:
        _close_parquet_file(parquet_file)
    states = {**initial_state, **factor_machine.states}
    return {
        "row_count": row_count,
        "date_range": (
            None if minimum_date is None else [minimum_date, maximum_date]
        ),
        "ending_state_by_ticker": _encode_state(states),
        "ending_date_by_ticker": dict(sorted(ending_dates.items())),
    }


def _stream_event_promotion_summary(
    target: Path,
    artifact_path: str,
    artifact_id: str,
    contract: ArtifactContract,
) -> tuple[
    dict[str, Any], list[tuple[str, ExpectedAdjustmentEvent | None]]
]:
    parquet_file = pq.ParquetFile(target)
    try:
        schema_error = _event_arrow_schema_error(parquet_file.schema_arrow, artifact_id)
        if schema_error is not None:
            raise AdjustedOhlcEvidenceError(schema_error)
        event_year = _embedded_contract_partition_year(contract, artifact_path)
        row_count = 0
        minimum_date: str | None = None
        maximum_date: str | None = None
        partition_events: list[tuple[str, ExpectedAdjustmentEvent | None]] = []
        for batch in parquet_file.iter_batches(batch_size=_PARQUET_BATCH_SIZE):
            for row in batch.to_pylist():
                partition_events.append(
                    _expected_event(artifact_id, row, event_year=event_year)
                )
                event_date = _canonical_event_date(row.get("event_date"))
                if event_date is None:
                    raise AdjustedOhlcEvidenceError(
                        f"official event row has invalid event_date: {artifact_id}"
                    )
                row_count += 1
                minimum_date = (
                    event_date if minimum_date is None else min(minimum_date, event_date)
                )
                maximum_date = (
                    event_date if maximum_date is None else max(maximum_date, event_date)
                )
        return (
            {
                "row_count": row_count,
                "date_range": (
                    None if minimum_date is None else [minimum_date, maximum_date]
                ),
            },
            partition_events,
        )
    finally:
        _close_parquet_file(parquet_file)


def _valid_manifest_columns(columns: Any, required: frozenset[str]) -> bool:
    return (
        isinstance(columns, list)
        and all(isinstance(column, str) and bool(column.strip()) for column in columns)
        and len(columns) == len(set(columns))
        and required.issubset(columns)
    )


def _invalid_identity_arrow_type_count(schema: pa.Schema) -> int:
    count = 0
    if "ticker" in schema.names:
        ticker_type = schema.field("ticker").type
        if not (
            pa.types.is_string(ticker_type) or pa.types.is_large_string(ticker_type)
        ):
            count += 1
    if "date" in schema.names:
        date_type = schema.field("date").type
        if not (
            pa.types.is_string(date_type)
            or pa.types.is_large_string(date_type)
            or pa.types.is_date32(date_type)
            or pa.types.is_date64(date_type)
        ):
            count += 1
    return count


def _reuse_partition_record(
    target: Path,
    artifact_path: str,
    previous_record: Mapping[str, Any] | None,
    initial_state_fingerprint: str,
    verified_at: str,
    previous_error: str | None,
    hash_cache: _ContentHashCache,
    *,
    trust_previous_hash: bool,
) -> dict[str, Any]:
    current_hash: str | None = None
    if not trust_previous_hash:
        try:
            current_hash = hash_cache.get(target)
        except OSError:
            return _blocked_partition_record(
                artifact_path,
                target,
                verified_at,
                initial_state_fingerprint,
                content_hash="",
                hash_cache=hash_cache,
            )
    if (
        previous_error is None
        and target.is_file()
        and isinstance(previous_record, Mapping)
        and set(previous_record) == _PARTITION_RECORD_FIELDS
        and previous_record.get("artifact_path") == artifact_path
        and isinstance(previous_record.get("content_sha256"), str)
        and _SHA256_PATTERN.fullmatch(previous_record["content_sha256"]) is not None
        and (
            trust_previous_hash
            or previous_record.get("content_sha256") == current_hash
        )
        and previous_record.get("schema_version") == EVIDENCE_SCHEMA_VERSION
        and previous_record.get("adjustment_policy_id") == ADJUSTMENT_POLICY_ID
        and previous_record.get("status") == "ready"
        and previous_record.get("initial_state_fingerprint")
        == initial_state_fingerprint
        and _valid_violation_counts(previous_record.get("violation_counts"))
        and not any(previous_record["violation_counts"].values())
        and _valid_encoded_state(previous_record.get("ending_state_by_ticker"))
        and _valid_boundary_dates(previous_record.get("ending_date_by_ticker"))
        and _valid_date_range(previous_record.get("date_range"))
    ):
        return dict(previous_record)
    return _blocked_partition_record(
        artifact_path,
        target,
        verified_at,
        initial_state_fingerprint,
        hash_cache=hash_cache,
    )


def _blocked_partition_record(
    artifact_path: str,
    target: Path,
    verified_at: str,
    initial_state_fingerprint: str,
    *,
    content_hash: str | None = None,
    hash_cache: _ContentHashCache,
) -> dict[str, Any]:
    if content_hash is None:
        try:
            content_hash = hash_cache.get(target)
        except OSError:
            content_hash = ""
    return {
        "artifact_path": artifact_path,
        "content_sha256": content_hash,
        "row_count": 0,
        "date_range": None,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
        "verified_at": verified_at,
        "status": "blocked",
        "violation_counts": empty_violation_counts(),
        "initial_state_fingerprint": initial_state_fingerprint,
        "ending_state_by_ticker": {},
        "ending_date_by_ticker": {},
    }


def _previous_partition_records(
    previous_evidence: Mapping[str, Any] | None,
    mode: str,
    *,
    ordered_paths: Sequence[str],
    changed_paths: set[str],
    event_contracts: Mapping[str, ArtifactContract],
) -> tuple[dict[str, Mapping[str, Any]], str | None]:
    if mode == "full":
        return {}, None
    if not isinstance(previous_evidence, Mapping):
        return {}, "missing previous evidence"
    if set(previous_evidence) != _EVIDENCE_FIELDS:
        return {}, "invalid previous evidence schema"
    if (
        previous_evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or previous_evidence.get("artifact_id") != "daily_price_volume"
        or previous_evidence.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
        or previous_evidence.get("status") != "ready"
    ):
        return {}, "incompatible previous evidence"
    previous_manifest_fingerprint = previous_evidence.get("manifest_fingerprint")
    if (
        not isinstance(previous_manifest_fingerprint, str)
        or _SHA256_PATTERN.fullmatch(previous_manifest_fingerprint) is None
    ):
        return {}, "invalid previous evidence manifest fingerprint"
    if not _valid_event_dependencies(
        previous_evidence.get("event_dependencies"), event_contracts
    ):
        return {}, "invalid previous event dependencies"
    previous_totals = previous_evidence.get("violation_totals")
    if not _valid_violation_counts(previous_totals) or any(previous_totals.values()):
        return {}, "invalid previous violation totals"
    records = previous_evidence.get("partitions")
    if not isinstance(records, list):
        return {}, "invalid previous partition records"
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _PARTITION_RECORD_FIELDS:
            return {}, "invalid previous partition record"
        artifact_path = record.get("artifact_path")
        if not isinstance(artifact_path, str) or artifact_path in result:
            return {}, "duplicate previous partition record"
        result[artifact_path] = record
    try:
        _validate_ready_candidate_summary(previous_evidence, len(result))
    except AdjustedOhlcEvidenceError:
        return {}, "incomplete previous evidence summary"
    current_paths = set(ordered_paths)
    previous_paths = set(result)
    if not previous_paths.issubset(current_paths):
        return {}, "previous partition paths were removed"
    if not current_paths.difference(previous_paths).issubset(changed_paths):
        return {}, "new partition paths are missing from changed_paths"
    previous_ordered_paths = [path for path in ordered_paths if path in previous_paths]
    if [record.get("artifact_path") for record in records] != previous_ordered_paths:
        return {}, "previous partition records are not in manifest order"

    boundary_state: dict[str, AdjustmentSeed] = {}
    boundary_dates: dict[str, str] = {}
    derived_violation_totals = empty_violation_counts()
    previous_verified_at = _parse_verified_at(previous_evidence.get("verified_at"))
    if previous_verified_at is None:
        return {}, "invalid previous evidence verified_at"
    for artifact_path in previous_ordered_paths:
        record = result[artifact_path]
        content_hash = record.get("content_sha256")
        if (
            record.get("status") != "ready"
            or record.get("schema_version") != EVIDENCE_SCHEMA_VERSION
            or record.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
            or not isinstance(content_hash, str)
            or _SHA256_PATTERN.fullmatch(content_hash) is None
            or not _valid_violation_counts(record.get("violation_counts"))
            or any(record["violation_counts"].values())
            or not _valid_partition_metadata(record, previous_verified_at)
            or record.get("initial_state_fingerprint")
            != _state_fingerprint(boundary_state, boundary_dates)
        ):
            return {}, "incompatible previous partition record"
        try:
            boundary_state = _decode_state(record["ending_state_by_ticker"])
            boundary_dates = _decode_boundary_dates(record["ending_date_by_ticker"])
        except AdjustedOhlcEvidenceError:
            return {}, "invalid previous partition boundary state"
        for counter in derived_violation_totals:
            derived_violation_totals[counter] += record["violation_counts"][counter]
    if previous_totals != derived_violation_totals:
        return {}, "previous violation totals do not match partition records"
    if (
        previous_evidence.get("ending_state_by_ticker") != _encode_state(boundary_state)
        or previous_evidence.get("ending_date_by_ticker")
        != dict(sorted(boundary_dates.items()))
    ):
        return {}, "invalid previous boundary summary"
    return result, None


def _valid_event_dependencies(
    dependencies: Any,
    event_contracts: Mapping[str, ArtifactContract],
) -> bool:
    if not isinstance(dependencies, Mapping) or set(dependencies) != set(
        _EVENT_ARTIFACT_IDS
    ) or list(dependencies) != list(_EVENT_ARTIFACT_IDS):
        return False
    for artifact_id in _EVENT_ARTIFACT_IDS:
        dependency = dependencies[artifact_id]
        if not isinstance(dependency, Mapping) or set(dependency) != {
            "manifest_fingerprint",
            "row_count",
            "date_range",
            "partitions",
        }:
            return False
        fingerprint = dependency["manifest_fingerprint"]
        manifest_row_count = dependency["row_count"]
        manifest_date_range = dependency["date_range"]
        records = dependency["partitions"]
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or _SHA256_PATTERN.fullmatch(fingerprint) is None
        ):
            return False
        if fingerprint is None:
            if records or manifest_row_count is not None or manifest_date_range is not None:
                return False
        elif (
            type(manifest_row_count) is not int
            or manifest_row_count < 0
            or not _valid_date_range(manifest_date_range)
            or ((manifest_row_count == 0) != (manifest_date_range is None))
        ):
            return False
        if not isinstance(records, list):
            return False
        seen_paths: set[str] = set()
        seen_years: set[int] = set()
        ordered_keys: list[tuple[int, str]] = []
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                "artifact_path",
                "content_sha256",
                "row_count",
                "date_range",
            }:
                return False
            artifact_path = record.get("artifact_path")
            content_hash = record.get("content_sha256")
            if (
                not isinstance(artifact_path, str)
                or artifact_path in seen_paths
                or not isinstance(content_hash, str)
                or _SHA256_PATTERN.fullmatch(content_hash) is None
                or not _valid_optional_event_partition_summary(record)
            ):
                return False
            seen_paths.add(artifact_path)
            try:
                event_year = _embedded_contract_partition_year(
                    event_contracts[artifact_id], artifact_path
                )
            except AdjustedOhlcEvidenceError:
                return False
            if event_year in seen_years:
                return False
            seen_years.add(event_year)
            ordered_keys.append((event_year, artifact_path))
        if ordered_keys != sorted(ordered_keys):
            return False
    return True


def _changed_event_dependency_paths(
    before: Any,
    after: Any,
    event_contracts: Mapping[str, ArtifactContract],
) -> set[str]:
    if not _valid_event_dependencies(
        before, event_contracts
    ) or not _valid_event_dependencies(after, event_contracts):
        raise AdjustedOhlcEvidenceError("invalid event dependencies for drift comparison")
    changed: set[str] = set()
    for artifact_id in _EVENT_ARTIFACT_IDS:
        before_dependency = before[artifact_id]
        after_dependency = after[artifact_id]
        before_by_path = {
            record["artifact_path"]: record
            for record in before_dependency["partitions"]
        }
        after_by_path = {
            record["artifact_path"]: record
            for record in after_dependency["partitions"]
        }
        artifact_changed = {
            path
            for path in set(before_by_path).union(after_by_path)
            if before_by_path.get(path) != after_by_path.get(path)
        }
        manifest_summary_changed = any(
            before_dependency[field] != after_dependency[field]
            for field in ("manifest_fingerprint", "row_count", "date_range")
        )
        if manifest_summary_changed and not artifact_changed:
            artifact_changed.update(set(before_by_path).union(after_by_path))
        changed.update(artifact_changed)
    return changed


def _event_partition_year(
    event_contracts: Mapping[str, ArtifactContract], artifact_path: str
) -> int:
    for artifact_id, contract in event_contracts.items():
        try:
            return _embedded_contract_partition_year(contract, artifact_path)
        except AdjustedOhlcEvidenceError:
            continue
    raise AdjustedOhlcEvidenceError(
        f"event dependency path has no canonical year: {artifact_path}"
    )


def _trusted_previous_partition(
    context: DataAnalystsContext,
    artifact_path: str,
    previous_records: Mapping[str, Mapping[str, Any]],
    path_overrides: Mapping[str, Path] | None,
    hash_cache: _ContentHashCache,
    *,
    trust_previous_hash: bool,
) -> bool:
    if path_overrides is not None and artifact_path in path_overrides:
        return False
    record = previous_records.get(artifact_path)
    if record is None or record.get("status") != "ready":
        return False
    content_sha256 = record.get("content_sha256")
    metadata_is_trusted = (
        context.artifact_path(artifact_path).is_file()
        and isinstance(content_sha256, str)
        and _SHA256_PATTERN.fullmatch(content_sha256) is not None
    )
    if not metadata_is_trusted or trust_previous_hash:
        return metadata_is_trusted
    try:
        return content_sha256 == hash_cache.get(context.artifact_path(artifact_path))
    except OSError:
        return False


def _trusted_event_scan_boundary(
    context: DataAnalystsContext,
    ordered_paths: Sequence[str],
    changed_paths: set[str],
    affected_tickers: set[str],
    previous_records: Mapping[str, Mapping[str, Any]],
    path_overrides: Mapping[str, Path] | None,
    hash_cache: _ContentHashCache,
    *,
    trust_previous_hash: bool,
) -> dict[str, str | None] | None:
    if not changed_paths:
        return {}
    path_indexes = {path: index for index, path in enumerate(ordered_paths)}
    earliest_changed_index = min(path_indexes[path] for path in changed_paths)
    if earliest_changed_index == 0:
        return None
    boundary_path = ordered_paths[earliest_changed_index - 1]
    if not _trusted_previous_partition(
        context,
        boundary_path,
        previous_records,
        path_overrides,
        hash_cache,
        trust_previous_hash=trust_previous_hash,
    ):
        return None
    boundary_dates = _decode_boundary_dates(
        previous_records[boundary_path]["ending_date_by_ticker"]
    )
    return {
        ticker: boundary_dates.get(ticker)
        for ticker in sorted(affected_tickers)
    }


def _affected_price_tickers(
    context: DataAnalystsContext,
    changed_paths: set[str],
    path_overrides: Mapping[str, Path] | None,
    hash_cache: _ContentHashCache,
    price_contract: ArtifactContract,
) -> set[str]:
    tickers: set[str] = set()
    for artifact_path in sorted(
        changed_paths, key=lambda path: _partition_year(price_contract, path)
    ):
        target = _artifact_read_path(context, artifact_path, path_overrides)
        before_hash = hash_cache.get(target)
        table = pq.read_table(target, columns=["ticker"])
        if hash_cache.get(target, fresh=True) != before_hash:
            raise AdjustedOhlcEvidenceError(
                f"price partition changed during ticker scan: {artifact_path}"
            )
        for value in table.column("ticker").to_pylist():
            ticker = normalize_adjusted_ohlc_ticker(value)
            if ticker is not None:
                tickers.add(ticker)
    return tickers


def _valid_optional_event_partition_summary(record: Mapping[str, Any]) -> bool:
    row_count = record.get("row_count")
    date_range = record.get("date_range")
    if row_count is None:
        return date_range is None
    return (
        type(row_count) is int
        and row_count >= 0
        and _valid_date_range(date_range)
        and ((row_count == 0) == (date_range is None))
    )


def _reuse_event_partition_summaries(
    current: Mapping[str, Any],
    previous: Any,
    event_contracts: Mapping[str, ArtifactContract],
) -> None:
    if not _valid_event_dependencies(previous, event_contracts):
        return
    for artifact_id in _EVENT_ARTIFACT_IDS:
        current_dependency = current[artifact_id]
        previous_dependency = previous[artifact_id]
        if (
            current_dependency["manifest_fingerprint"]
            != previous_dependency["manifest_fingerprint"]
            or current_dependency["row_count"] != previous_dependency["row_count"]
            or current_dependency["date_range"] != previous_dependency["date_range"]
        ):
            continue
        previous_by_path = {
            record["artifact_path"]: record
            for record in previous_dependency["partitions"]
        }
        for record in current_dependency["partitions"]:
            previous_record = previous_by_path.get(record["artifact_path"])
            if (
                previous_record is not None
                and previous_record["content_sha256"] == record["content_sha256"]
            ):
                record["row_count"] = previous_record["row_count"]
                record["date_range"] = previous_record["date_range"]


def _validate_scanned_event_aggregates(dependencies: Mapping[str, Any]) -> None:
    for artifact_id in dependencies:
        dependency = dependencies[artifact_id]
        records = dependency["partitions"]
        if not records or any(record["row_count"] is None for record in records):
            continue
        actual_row_count = sum(record["row_count"] for record in records)
        ranges = [
            record["date_range"]
            for record in records
            if record["date_range"] is not None
        ]
        actual_date_range = (
            None
            if not ranges
            else [min(item[0] for item in ranges), max(item[1] for item in ranges)]
        )
        if (
            dependency["row_count"] != actual_row_count
            or dependency["date_range"] != actual_date_range
        ):
            raise AdjustedOhlcEvidenceError(
                "official event manifest aggregate does not exactly match scanned rows: "
                f"{artifact_id}: expected row_count={actual_row_count}, "
                f"date_range={actual_date_range}"
            )


def _load_event_dependencies(
    context: DataAnalystsContext,
    *,
    path_overrides: Mapping[str, Path] | None = None,
    manifest_overrides: Mapping[str, Mapping[str, Any] | None] | None = None,
    hash_cache: _ContentHashCache,
    reusable_dependencies: Mapping[str, Any] | None = None,
    refresh_paths: set[str] | None = None,
    event_contracts: Mapping[str, ArtifactContract],
) -> dict[str, dict[str, Any]]:
    dependencies = _empty_event_dependencies()
    can_reuse = _valid_event_dependencies(reusable_dependencies, event_contracts)
    refresh_paths = refresh_paths or set()
    for artifact_id in _EVENT_ARTIFACT_IDS:
        if manifest_overrides is not None and artifact_id in manifest_overrides:
            manifest = manifest_overrides[artifact_id]
            if manifest is None:
                continue
        else:
            manifest_path = context.store_path("manifests", f"{artifact_id}.json")
            if not manifest_path.exists():
                continue
            manifest = _load_json_object(manifest_path)
        contract = event_contracts[artifact_id]
        paths = _validate_official_event_manifest(context, manifest, contract)
        dependencies[artifact_id]["manifest_fingerprint"] = manifest_fingerprint(
            manifest
        )
        dependencies[artifact_id]["row_count"] = manifest["row_count"]
        dependencies[artifact_id]["date_range"] = manifest["date_range"]
        reusable_by_path = (
            {
                record["artifact_path"]: record["content_sha256"]
                for record in reusable_dependencies[artifact_id]["partitions"]
            }
            if can_reuse
            else {}
        )
        seen_years: set[int] = set()
        ordered_paths: list[tuple[int, str]] = []
        for raw_path in paths:
            if not isinstance(raw_path, str):
                raise AdjustedOhlcEvidenceError(
                    f"invalid official event manifest path: {artifact_id}"
                )
            try:
                artifact_path = context.validate_artifact_path(raw_path)
            except PathBoundaryError as exc:
                raise AdjustedOhlcEvidenceError(
                    f"invalid official event manifest path: {artifact_id}"
                ) from exc
            year = _contract_partition_year(
                contract,
                artifact_path,
                active_version=manifest.get("active_version"),
            )
            if year in seen_years:
                raise AdjustedOhlcEvidenceError(
                    f"duplicate official event manifest year: {artifact_id}"
                )
            seen_years.add(year)
            ordered_paths.append((year, artifact_path))
        for _, artifact_path in sorted(ordered_paths):
            target = _artifact_read_path(context, artifact_path, path_overrides)
            uses_override = bool(
                path_overrides is not None and artifact_path in path_overrides
            )
            content_sha256 = reusable_by_path.get(artifact_path)
            if (
                uses_override
                or artifact_path in refresh_paths
                or content_sha256 is None
            ):
                content_sha256 = hash_cache.get(target)
            dependencies[artifact_id]["partitions"].append(
                {
                    "artifact_path": artifact_path,
                    "content_sha256": content_sha256,
                    "row_count": None,
                    "date_range": None,
                }
            )
    return dependencies


def _validate_official_event_manifest(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    contract: ArtifactContract,
) -> list[str]:
    artifact_id = contract.artifact_id
    if (
        manifest.get("artifact_id") != artifact_id
        or manifest.get("schema_version") not in {"1.0", "1.1"}
        or manifest.get("status") != "ready"
    ):
        raise AdjustedOhlcEvidenceError(
            f"invalid official event manifest: {artifact_id}"
        )

    row_count = manifest.get("row_count")
    date_range = manifest.get("date_range")
    columns = manifest.get("columns")
    if (
        type(row_count) is not int
        or row_count < 0
        or not _valid_date_range(date_range)
        or ((row_count == 0) != (date_range is None))
    ):
        raise AdjustedOhlcEvidenceError(
            f"invalid official event manifest aggregate metadata: {artifact_id}"
        )
    if not _valid_manifest_columns(columns, _EVENT_REQUIRED_COLUMNS[artifact_id]):
        raise AdjustedOhlcEvidenceError(
            f"invalid official event manifest columns: {artifact_id}"
        )

    paths = manifest.get("artifact_paths")
    if (
        not isinstance(paths, list)
        or (row_count == 0 and paths)
        or (row_count > 0 and not paths)
    ):
        raise AdjustedOhlcEvidenceError(
            f"invalid official event manifest paths: {artifact_id}"
        )
    normalized_paths: list[str] = []
    seen_paths: set[str] = set()
    seen_years: set[int] = set()
    for raw_path in paths:
        if not isinstance(raw_path, str):
            raise AdjustedOhlcEvidenceError(
                f"invalid official event manifest path: {artifact_id}"
            )
        try:
            artifact_path = context.validate_artifact_path(raw_path)
        except PathBoundaryError as exc:
            raise AdjustedOhlcEvidenceError(
                f"invalid official event manifest path: {artifact_id}"
            ) from exc
        if artifact_path in seen_paths:
            raise AdjustedOhlcEvidenceError(
                f"invalid official event manifest path: {artifact_id}"
            )
        year = _contract_partition_year(
            contract,
            artifact_path,
            active_version=manifest.get("active_version"),
        )
        if year in seen_years:
            raise AdjustedOhlcEvidenceError(
                f"duplicate official event manifest year: {artifact_id}"
            )
        seen_paths.add(artifact_path)
        seen_years.add(year)
        normalized_paths.append(artifact_path)
    return normalized_paths


def _load_expected_events(
    context: DataAnalystsContext,
    dependencies: Mapping[str, Any],
    *,
    maximum_event_year: int | None,
    event_lower_bounds: Mapping[str, str | None] | None,
    path_overrides: Mapping[str, Path] | None = None,
    hash_cache: _ContentHashCache,
    event_contracts: Mapping[str, ArtifactContract],
) -> dict[str, list[ExpectedAdjustmentEvent]]:
    if not _valid_event_dependencies(dependencies, event_contracts):
        raise AdjustedOhlcEvidenceError("invalid official event dependencies")
    grouped: dict[str, list[ExpectedAdjustmentEvent]] = {}
    if event_lower_bounds == {}:
        return grouped
    bounded_dates = (
        []
        if event_lower_bounds is None
        else [value for value in event_lower_bounds.values() if value is not None]
    )
    has_unbounded_ticker = (
        event_lower_bounds is not None
        and any(value is None for value in event_lower_bounds.values())
    )
    minimum_event_year = (
        None
        if event_lower_bounds is None or has_unbounded_ticker or not bounded_dates
        else min(date.fromisoformat(value).year for value in bounded_dates)
    )
    for artifact_id in _EVENT_ARTIFACT_IDS:
        for record in dependencies[artifact_id]["partitions"]:
            artifact_path = record["artifact_path"]
            event_year = _embedded_contract_partition_year(
                event_contracts[artifact_id], artifact_path
            )
            if minimum_event_year is not None and event_year < minimum_event_year:
                continue
            if event_year > maximum_event_year:
                continue
            target = _artifact_read_path(context, artifact_path, path_overrides)
            before_hash = record["content_sha256"]
            if hash_cache.get(target) != before_hash:
                raise AdjustedOhlcEvidenceError(
                    f"official event partition changed before scan: {artifact_path}"
                )
            parquet_file = pq.ParquetFile(target)
            actual_row_count = 0
            actual_dates: list[str] = []
            try:
                schema_error = _event_arrow_schema_error(
                    parquet_file.schema_arrow, artifact_id
                )
                if schema_error is not None:
                    raise AdjustedOhlcEvidenceError(schema_error)
                for batch in parquet_file.iter_batches(
                    batch_size=_PARQUET_BATCH_SIZE
                ):
                    for row in batch.to_pylist():
                        ticker, event = _expected_event(
                            artifact_id, row, event_year=event_year
                        )
                        event_date = _canonical_event_date(row.get("event_date"))
                        if event_date is None:
                            raise AdjustedOhlcEvidenceError(
                                f"official event row has invalid event_date: {artifact_id}"
                            )
                        actual_row_count += 1
                        actual_dates.append(event_date)
                        if (
                            event_lower_bounds is not None
                            and ticker not in event_lower_bounds
                        ):
                            continue
                        cursor_date = (
                            None
                            if event_lower_bounds is None
                            else event_lower_bounds[ticker]
                        )
                        if event is not None and (
                            cursor_date is None or event_date > cursor_date
                        ):
                            grouped.setdefault(ticker, []).append(event)
            finally:
                _close_parquet_file(parquet_file)
            if hash_cache.get(target, fresh=True) != before_hash:
                raise AdjustedOhlcEvidenceError(
                    f"official event partition changed during scan: {artifact_path}"
                )
            record["row_count"] = actual_row_count
            record["date_range"] = (
                None
                if not actual_dates
                else [min(actual_dates), max(actual_dates)]
            )
    for events in grouped.values():
        events.sort(key=lambda event: str(event.event_date))
    return grouped


def _empty_event_dependencies() -> dict[str, dict[str, Any]]:
    return {
        artifact_id: {
            "manifest_fingerprint": None,
            "row_count": None,
            "date_range": None,
            "partitions": [],
        }
        for artifact_id in _EVENT_ARTIFACT_IDS
    }


def _validate_event_dependencies_structure(
    dependencies: Any,
    event_contracts: Mapping[str, ArtifactContract],
) -> None:
    if not isinstance(dependencies, Mapping) or set(dependencies) != set(
        _EVENT_ARTIFACT_IDS
    ):
        raise AdjustedOhlcEvidenceError("stale candidate event dependencies")
    if list(dependencies) != list(_EVENT_ARTIFACT_IDS):
        raise AdjustedOhlcEvidenceError("stale candidate event dependency order")
    for artifact_id in _EVENT_ARTIFACT_IDS:
        dependency = dependencies[artifact_id]
        if not isinstance(dependency, Mapping) or set(dependency) != {
            "manifest_fingerprint",
            "row_count",
            "date_range",
            "partitions",
        }:
            raise AdjustedOhlcEvidenceError("stale candidate event dependency schema")
        fingerprint = dependency["manifest_fingerprint"]
        records = dependency["partitions"]
        if fingerprint is None:
            if (
                dependency["row_count"] is not None
                or dependency["date_range"] is not None
                or records != []
            ):
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event manifest summary: {artifact_id}"
                )
            continue
        if not isinstance(fingerprint, str) or _SHA256_PATTERN.fullmatch(
            fingerprint
        ) is None:
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event manifest: {artifact_id}"
            )
        row_count = dependency["row_count"]
        date_range = dependency["date_range"]
        if (
            type(row_count) is not int
            or row_count < 0
            or not _valid_date_range(date_range)
            or ((row_count == 0) != (date_range is None))
            or not isinstance(records, list)
        ):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event manifest summary: {artifact_id}"
            )
        years: set[int] = set()
        order: list[tuple[int, str]] = []
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                "artifact_path",
                "content_sha256",
                "row_count",
                "date_range",
            }:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition schema: {artifact_id}"
                )
            artifact_path = record.get("artifact_path")
            if not isinstance(artifact_path, str):
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition path: {artifact_id}"
                )
            try:
                year = _embedded_contract_partition_year(
                    event_contracts[artifact_id], artifact_path
                )
            except AdjustedOhlcEvidenceError as exc:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition path: {artifact_id}"
                ) from exc
            if year in years:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate duplicate event dependency: {artifact_id}"
                )
            years.add(year)
            order.append((year, artifact_path))
            content_sha256 = record.get("content_sha256")
            if not isinstance(content_sha256, str) or _SHA256_PATTERN.fullmatch(
                content_sha256
            ) is None:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition schema: {artifact_id}"
                )
            if not _valid_optional_event_partition_summary(record):
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition summary: {artifact_id}"
                )
        if order != sorted(order):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event dependency order: {artifact_id}"
            )
        if records and all(record["row_count"] is not None for record in records):
            _validate_scanned_event_aggregates({artifact_id: dependency})


def _validate_candidate_event_dependencies(
    context: DataAnalystsContext,
    dependencies: Any,
    event_contracts: Mapping[str, ArtifactContract],
) -> dict[str, str | None]:
    _validate_event_dependencies_structure(dependencies, event_contracts)
    source_preconditions: dict[str, str | None] = {}
    for artifact_id in _EVENT_ARTIFACT_IDS:
        dependency = dependencies[artifact_id]
        if not isinstance(dependency, Mapping) or set(dependency) != {
            "manifest_fingerprint",
            "row_count",
            "date_range",
            "partitions",
        }:
            raise AdjustedOhlcEvidenceError("stale candidate event dependency schema")
        manifest_path = context.store_path("manifests", f"{artifact_id}.json")
        expected_fingerprint = dependency["manifest_fingerprint"]
        manifest_artifact_path = f"manifests/{artifact_id}.json"
        if expected_fingerprint is None:
            if (
                manifest_path.exists()
                or dependency["partitions"] != []
                or dependency["row_count"] is not None
                or dependency["date_range"] is not None
            ):
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event manifest: {artifact_id}"
                )
            source_preconditions[manifest_artifact_path] = None
            continue
        if not isinstance(expected_fingerprint, str) or not manifest_path.exists():
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event manifest: {artifact_id}"
            )
        manifest, manifest_hash = _load_json_object_snapshot(manifest_path)
        if (
            manifest_fingerprint(manifest) != expected_fingerprint
        ):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event manifest: {artifact_id}"
            )
        try:
            contract = event_contracts[artifact_id]
            paths = _validate_official_event_manifest(
                context, manifest, contract
            )
        except AdjustedOhlcEvidenceError as exc:
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event manifest: {artifact_id}: {exc}"
            ) from exc
        if (
            dependency["row_count"] != manifest["row_count"]
            or dependency["date_range"] != manifest["date_range"]
        ):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event manifest summary: {artifact_id}"
            )
        source_preconditions[manifest_artifact_path] = manifest_hash
        records = dependency["partitions"]
        if not isinstance(records, list):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event partitions: {artifact_id}"
            )
        records_by_path: dict[str, Mapping[str, Any]] = {}
        candidate_years: set[int] = set()
        candidate_order: list[tuple[int, str]] = []
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                "artifact_path",
                "content_sha256",
                "row_count",
                "date_range",
            }:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition schema: {artifact_id}"
                )
            artifact_path = record.get("artifact_path")
            if not isinstance(artifact_path, str) or artifact_path in records_by_path:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition paths: {artifact_id}"
                )
            try:
                event_year = _embedded_contract_partition_year(
                    contract, artifact_path
                )
            except AdjustedOhlcEvidenceError as exc:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition path: {artifact_id}"
                ) from exc
            records_by_path[artifact_path] = record
            if event_year in candidate_years:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate duplicate event dependency: {artifact_id}"
                )
            candidate_years.add(event_year)
            candidate_order.append((event_year, artifact_path))
        if candidate_order != sorted(candidate_order):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event dependency order: {artifact_id}"
            )
        if set(records_by_path) != set(paths):
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event partition paths: {artifact_id}"
            )
        manifest_order: list[tuple[int, str]] = []
        manifest_years: set[int] = set()
        for artifact_path in paths:
            if not isinstance(artifact_path, str):
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition paths: {artifact_id}"
                )
            try:
                event_year = _contract_partition_year(
                    contract,
                    artifact_path,
                    active_version=manifest.get("active_version"),
                )
            except AdjustedOhlcEvidenceError as exc:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition path: {artifact_id}"
                ) from exc
            if event_year in manifest_years:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate duplicate event dependency: {artifact_id}"
                )
            manifest_years.add(event_year)
            manifest_order.append((event_year, artifact_path))
        if sorted(manifest_order) != candidate_order:
            raise AdjustedOhlcEvidenceError(
                f"stale candidate event dependency order: {artifact_id}"
            )
        for artifact_path in sorted(records_by_path):
            expected_hash = records_by_path[artifact_path].get("content_sha256")
            if not isinstance(expected_hash, str) or _SHA256_PATTERN.fullmatch(
                expected_hash
            ) is None:
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition schema: {artifact_id}"
                )
            if not _valid_optional_event_partition_summary(
                records_by_path[artifact_path]
            ):
                raise AdjustedOhlcEvidenceError(
                    f"stale candidate event partition summary: {artifact_id}"
                )
            source_preconditions[artifact_path] = expected_hash
        scanned_records = [
            record for record in records if record.get("row_count") is not None
        ]
        if len(scanned_records) == len(records):
            _validate_scanned_event_aggregates({artifact_id: dependency})
    return source_preconditions


def _expected_event(
    artifact_id: str, row: Mapping[str, Any], *, event_year: int
) -> tuple[str, ExpectedAdjustmentEvent | None]:
    event_date = _canonical_event_date(row.get("event_date"))
    ticker = _canonical_event_ticker(row.get("ticker"))
    if event_date is None:
        raise AdjustedOhlcEvidenceError(
            f"official event row has invalid event_date: {artifact_id}"
        )
    if date.fromisoformat(event_date).year != event_year:
        raise AdjustedOhlcEvidenceError(
            f"official event row event_date does not match event_year={event_year}: "
            f"{artifact_id}"
        )
    if ticker is None:
        raise AdjustedOhlcEvidenceError(
            f"official event row has invalid ticker: {artifact_id}"
        )
    action_type = (
        _validate_capital_action_type(row.get("action_type"))
        if artifact_id == "capital_action_events"
        else None
    )
    numeric = _validate_event_numeric_semantics(artifact_id, row)
    if artifact_id == "dividend_events":
        return (
            ticker,
            ExpectedAdjustmentEvent(
                event_date=event_date,
                cash_dividend=numeric["cash_dividend_per_share"],
                stock_event_factor=1.0 + numeric["stock_dividend_ratio"],
            ),
        )
    if action_type != "stock_price_adjustment":
        return ticker, None
    return (
        ticker,
        ExpectedAdjustmentEvent(
            event_date=event_date,
            stock_event_factor=numeric["price_adjustment_reference"],
        ),
    )


def _validate_capital_action_type(value: Any) -> str:
    if not isinstance(value, str) or value not in CAPITAL_ACTION_EVENT_TYPES:
        raise AdjustedOhlcEvidenceError(
            "official capital action event has invalid action_type"
        )
    return value


def _validate_event_numeric_semantics(
    artifact_id: str, row: Mapping[str, Any]
) -> dict[str, float | None]:
    if artifact_id == "dividend_events":
        return {
            "cash_dividend_per_share": _required_event_number(
                row.get("cash_dividend_per_share"),
                field="cash dividend",
                require_positive=False,
            ),
            "stock_dividend_ratio": _required_event_number(
                row.get("stock_dividend_ratio"),
                field="stock dividend ratio",
                require_positive=False,
            ),
        }

    share_multiplier = _required_event_number(
        row.get("share_multiplier"),
        field="capital action share multiplier",
        require_positive=True,
    )
    cash_return = _required_event_number(
        row.get("cash_return_per_share"),
        field="capital action cash return",
        require_positive=False,
    )
    reference = _optional_event_number(
        row.get("price_adjustment_reference"),
        field="capital action price adjustment reference",
    )
    if reference is not None and reference <= 0.0:
        raise AdjustedOhlcEvidenceError(
            "official capital action price adjustment reference must be positive"
        )
    if row.get("action_type") == "stock_price_adjustment" and reference is None:
        raise AdjustedOhlcEvidenceError(
            "official capital action event is missing price_adjustment_reference"
        )
    return {
        "share_multiplier": share_multiplier,
        "cash_return_per_share": cash_return,
        "price_adjustment_reference": reference,
    }


def _canonical_event_ticker(value: Any) -> str | None:
    return normalize_adjusted_ohlc_ticker(value)


def _canonical_event_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _required_event_number(
    value: Any, *, field: str, require_positive: bool
) -> float:
    parsed = _optional_event_number(value, field=field)
    if parsed is None:
        raise AdjustedOhlcEvidenceError(f"official {field} must be numeric")
    if require_positive and parsed <= 0.0:
        raise AdjustedOhlcEvidenceError(f"official {field} must be positive")
    if not require_positive and parsed < 0.0:
        raise AdjustedOhlcEvidenceError(f"official {field} must be nonnegative")
    return parsed


def _optional_event_number(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise AdjustedOhlcEvidenceError(f"official {field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdjustedOhlcEvidenceError(
            f"official {field} must be numeric"
        ) from exc
    if not math.isfinite(parsed):
        raise AdjustedOhlcEvidenceError(f"official {field} must be finite")
    return parsed


def _events_for_partition(
    events: Mapping[str, Sequence[ExpectedAdjustmentEvent]],
    ending_dates: Mapping[str, str],
    partition_year: int,
) -> dict[str, list[ExpectedAdjustmentEvent]]:
    upper_date = f"{partition_year:04d}-12-31"
    selected: dict[str, list[ExpectedAdjustmentEvent]] = {}
    event_date = lambda event: str(event.event_date)
    for ticker, items in events.items():
        lower_date = ending_dates.get(ticker)
        lower_index = (
            0
            if lower_date is None
            else bisect_right(items, lower_date, key=event_date)
        )
        upper_index = bisect_right(items, upper_date, key=event_date)
        if lower_index < upper_index:
            selected[ticker] = list(items[lower_index:upper_index])
    return selected


def _encode_state(
    state: Mapping[str, AdjustmentSeed],
) -> dict[str, dict[str, float | None]]:
    return {
        ticker: {
            "adj_factor": seed.adj_factor,
            "previous_close": seed.previous_close,
        }
        for ticker, seed in sorted(state.items())
    }


def _decode_state(value: Any) -> dict[str, AdjustmentSeed]:
    if not _valid_encoded_state(value):
        raise AdjustedOhlcEvidenceError("invalid partition boundary state")
    return {
        ticker: AdjustmentSeed(
            adj_factor=float(seed["adj_factor"]),
            previous_close=(
                None
                if seed["previous_close"] is None
                else float(seed["previous_close"])
            ),
        )
        for ticker, seed in value.items()
    }


def _valid_encoded_state(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for ticker, seed in value.items():
        if (
            normalize_adjusted_ohlc_ticker(ticker) is None
            or not isinstance(seed, Mapping)
        ):
            return False
        if set(seed) != {"adj_factor", "previous_close"}:
            return False
        if not _is_finite_real(seed["adj_factor"], require_positive=True):
            return False
        previous_close = seed["previous_close"]
        if previous_close is not None and not _is_finite_real(previous_close):
            return False
    return True


def _is_finite_real(value: Any, *, require_positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(parsed) and (not require_positive or parsed > 0.0)


def _state_fingerprint(
    state: Mapping[str, AdjustmentSeed], ending_dates: Mapping[str, str]
) -> str:
    return manifest_fingerprint(
        {
            "state": _encode_state(state),
            "ending_date_by_ticker": dict(sorted(ending_dates.items())),
        }
    )


def _decode_boundary_dates(value: Any) -> dict[str, str]:
    if not _valid_boundary_dates(value):
        raise AdjustedOhlcEvidenceError("invalid partition boundary dates")
    return dict(value)


def _valid_boundary_dates(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        return all(
            normalize_adjusted_ohlc_ticker(ticker) is not None
            and isinstance(row_date, str)
            and date.fromisoformat(row_date).isoformat() == row_date
            for ticker, row_date in value.items()
        )
    except ValueError:
        return False


def _partition_year(
    price_contract: ArtifactContract, artifact_path: str
) -> int:
    try:
        return _embedded_contract_partition_year(price_contract, artifact_path)
    except AdjustedOhlcEvidenceError as exc:
        raise AdjustedOhlcEvidenceError(
            f"cannot parse daily_price_volume partition year: {artifact_path}"
        ) from exc


def _valid_date_range(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, list) or len(value) != 2:
        return False
    try:
        parsed = [date.fromisoformat(item) for item in value]
    except (TypeError, ValueError):
        return False
    return all(parsed_item.isoformat() == item for parsed_item, item in zip(parsed, value)) and (
        parsed[0] <= parsed[1]
    )


def _serialize_date_range(value: tuple[Any, Any] | None) -> list[str] | None:
    if value is None:
        return None
    return [_date_text(value[0]), _date_text(value[1])]


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _valid_violation_counts(value: Any) -> bool:
    expected = empty_violation_counts()
    return (
        isinstance(value, Mapping)
        and set(value) == set(expected)
        and all(type(value[key]) is int and value[key] >= 0 for key in expected)
    )


def _parse_verified_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _valid_partition_metadata(
    record: Mapping[str, Any], evidence_verified_at: datetime
) -> bool:
    row_count = record.get("row_count")
    date_range = record.get("date_range")
    record_verified_at = _parse_verified_at(record.get("verified_at"))
    state = record.get("ending_state_by_ticker")
    ending_dates = record.get("ending_date_by_ticker")
    return (
        type(row_count) is int
        and row_count >= 0
        and _valid_date_range(date_range)
        and ((row_count == 0) == (date_range is None))
        and record_verified_at is not None
        and record_verified_at <= evidence_verified_at
        and _valid_encoded_state(state)
        and _valid_boundary_dates(ending_dates)
        and set(state) == set(ending_dates)
    )


def _partition_temporal_error(
    record: Mapping[str, Any],
    artifact_path: str,
    previous_ending_dates: Mapping[str, str],
    price_contract: ArtifactContract,
) -> str | None:
    partition_year = _partition_year(price_contract, artifact_path)
    date_range = record.get("date_range")
    if date_range is not None and (
        not _valid_date_range(date_range)
        or any(date.fromisoformat(item).year != partition_year for item in date_range)
    ):
        return f"partition date_range does not match partition year: {artifact_path}"
    ending_dates = record.get("ending_date_by_ticker")
    if not _valid_boundary_dates(ending_dates):
        return f"invalid partition ending_date metadata: {artifact_path}"
    for ticker, previous_date in previous_ending_dates.items():
        current_date = ending_dates.get(ticker)
        if current_date is None or current_date < previous_date:
            return (
                "ticker ending_date moved backwards across partitions: "
                f"{artifact_path}: {ticker}"
            )
    for ticker, current_date in ending_dates.items():
        previous_date = previous_ending_dates.get(ticker)
        current_year = date.fromisoformat(current_date).year
        if current_year > partition_year or (
            current_date != previous_date and current_year != partition_year
        ):
            return (
                f"ticker ending_date does not match partition year: "
                f"{artifact_path}: {ticker}"
            )
    return None


def _manifest_aggregate_error(
    manifest: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> str | None:
    row_count = 0
    ranges: list[list[str]] = []
    for record in records:
        partition_count = record.get("row_count")
        partition_range = record.get("date_range")
        if (
            type(partition_count) is not int
            or partition_count < 0
            or not _valid_date_range(partition_range)
            or ((partition_count == 0) != (partition_range is None))
        ):
            return "manifest aggregate cannot be derived from partition records"
        row_count += partition_count
        if partition_range is not None:
            ranges.append(partition_range)
    date_range = (
        None
        if not ranges
        else [min(item[0] for item in ranges), max(item[1] for item in ranges)]
    )
    if manifest.get("row_count") != row_count or manifest.get("date_range") != date_range:
        return (
            "manifest aggregate does not exactly match partition records: "
            f"expected row_count={row_count}, date_range={date_range}"
        )
    return None


def _validate_evidence_identity(
    evidence: Mapping[str, Any], *, require_ready: bool
) -> None:
    if set(evidence) != _EVIDENCE_FIELDS:
        raise AdjustedOhlcEvidenceError("candidate evidence schema is not exact")
    if (
        evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or evidence.get("artifact_id") != "daily_price_volume"
        or evidence.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
    ):
        raise AdjustedOhlcEvidenceError("unknown adjusted OHLC evidence schema or policy")
    fingerprint = evidence.get("manifest_fingerprint")
    if (
        not isinstance(fingerprint, str)
        or _SHA256_PATTERN.fullmatch(fingerprint) is None
        or _parse_verified_at(evidence.get("verified_at")) is None
        or not _valid_encoded_state(evidence.get("ending_state_by_ticker"))
        or not _valid_boundary_dates(evidence.get("ending_date_by_ticker"))
    ):
        raise AdjustedOhlcEvidenceError("invalid adjusted OHLC evidence metadata")
    if require_ready and evidence.get("status") != "ready":
        raise AdjustedOhlcEvidenceError("candidate evidence is not ready")
    totals = evidence.get("violation_totals")
    if not _valid_violation_counts(totals):
        raise AdjustedOhlcEvidenceError("invalid adjusted OHLC violation schema")
    if require_ready and any(totals.values()):
        raise AdjustedOhlcEvidenceError("candidate has nonzero core violations")


def _validate_ready_candidate_summary(
    candidate: Mapping[str, Any], partition_count: int
) -> None:
    count_fields = (
        "partition_count",
        "ready_partition_count",
        "blocked_partition_count",
        "stale_evidence_count",
    )
    if (
        any(
            type(candidate.get(field)) is not int or candidate[field] < 0
            for field in count_fields
        )
        or candidate.get("verification_mode") not in {"full", "incremental"}
        or candidate.get("partition_count") != partition_count
        or candidate.get("ready_partition_count") != partition_count
        or candidate.get("blocked_partition_count") != 0
        or candidate.get("stale_evidence_count") != 0
        or candidate.get("stale_artifact_paths") != []
        or candidate.get("blocked_reasons") != []
    ):
        raise AdjustedOhlcEvidenceError("stale candidate summary")


def _blocked_evidence(
    mode: str,
    fingerprint: str,
    verified_at: str,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "artifact_id": "daily_price_volume",
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
        "verification_mode": mode,
        "manifest_fingerprint": fingerprint,
        "verified_at": verified_at,
        "status": "blocked",
        "partition_count": 0,
        "ready_partition_count": 0,
        "blocked_partition_count": 0,
        "stale_evidence_count": 0,
        "stale_artifact_paths": [],
        "violation_totals": empty_violation_counts(),
        "partitions": [],
        "blocked_reasons": sorted(set(reasons)),
        "event_dependencies": _empty_event_dependencies(),
        "ending_state_by_ticker": {},
        "ending_date_by_ticker": {},
    }


def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _close_parquet_file(parquet_file: Any) -> None:
    close = getattr(parquet_file, "close", None)
    if callable(close):
        close()


def _load_json_object(path: Path) -> dict[str, Any]:
    return _load_json_object_snapshot(path)[0]


def _load_json_object_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    try:
        source_bytes = path.read_bytes()
        payload = json.loads(source_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdjustedOhlcEvidenceError(f"cannot load JSON evidence: {path}") from exc
    if not isinstance(payload, dict):
        raise AdjustedOhlcEvidenceError(f"JSON payload must be an object: {path}")
    return payload, hashlib.sha256(source_bytes).hexdigest()


def _atomic_write_json(target: Path, payload: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AdjustedOhlcEvidenceError("evidence must be JSON-compatible") from exc
    staging = target.with_name(f".{target.name}.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.write_text(serialized + "\n", encoding="utf-8")
        replace_file(staging, target)
    finally:
        if staging.exists():
            staging.unlink()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
