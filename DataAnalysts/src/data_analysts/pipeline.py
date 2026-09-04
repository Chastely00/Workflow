from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.adjusted_ohlc import (
    ADJUSTMENT_POLICY_ID,
    REQUIRED_ADJUSTED_OHLC_COLUMNS,
    AdjustmentSeed,
)
from data_analysts.adjusted_ohlc_evidence import (
    audit_adjusted_ohlc,
    manifest_fingerprint,
    _contract_partition_year,
)
from data_analysts.adjusted_prices import AdjustmentError, build_adjusted_daily_prices
from data_analysts.artifacts import (
    ArtifactError,
    ArtifactPublisher,
    build_manifest_payload,
    validate_manifest_fingerprint_structure,
)
from data_analysts.artifact_contracts import (
    ArtifactContract,
    RunScope,
    expected_contract_outputs,
    versioned_partition_value,
)
from data_analysts.config import CONFIG_FILENAMES, RuntimeConfig
from data_analysts.diagnostics import write_diagnostic
from data_analysts.dataset_publication import (
    migrate_legacy_variant_manifests,
    publish_dataset,
)
from data_analysts.daily_market_state import build_daily_market_state_rows
from data_analysts.daily_market_state_publication import (
    publish_daily_market_state_partitions,
)
from data_analysts.events import (
    build_capital_action_events,
    build_corporate_actions,
    build_dividend_events,
)
from data_analysts.extract import DatabaseLike, ExtractError, extract_family_rows_from_database, open_mongo_databases
from data_analysts.metadata import publish_data_store_metadata
from data_analysts.materialization import (
    load_canonical_rows,
    max_data_cutoff,
    rematerialization_starts,
    validate_adjustment_seeds,
    with_membership_exclusions,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.partition_transactions import (
    PartitionSpec,
    PublishTransactionError,
    StagedPartition,
    capture_partition_source,
    commit_publish_transaction,
    snapshot_partition_sources,
    stage_partition_rows,
)
from data_analysts.progress import RunProgress
from data_analysts.raw_families import normalize_raw_family
from data_analysts.security_panel import build_historical_security_panel, build_security_panel
from data_analysts.universe import build_historical_universe_memberships, build_universe_memberships


RAW_EXPANSION_FAMILIES = {
    "trading_calendar",
    "daily_tradability",
    "daily_chip",
    "monthly_sales",
    "financial_statement_raw",
    "self_reported_numbers_raw",
    "director_supervisor_holdings",
    "board_reelection_statistics",
    "executive_change_events",
    "merger_acquisition_events",
    "private_placement_relation_events",
    "insider_transfer_completed",
    "insider_transfer_declared_not_completed",
    "treasury_stock_events",
    "taiwan_index_futures_near_month",
}

SELECTED_OUTPUT_BY_RAW_FAMILY = {
    "financial_statement_raw": "financial_statement_pit_selected",
    "self_reported_numbers_raw": "self_reported_numbers_pit_selected",
}

RAW_DEPENDENCY_FAMILIES = {"trading_calendar", "daily_tradability"}

_DAILY_PRICE_MANIFEST_PATH = "manifests/daily_price_volume.json"
_ADJUSTED_OHLC_EVIDENCE_PATH = "diagnostics/adjusted_ohlc_verification.json"
_OFFICIAL_EVENT_IDS = ("capital_action_events", "dividend_events")
_LOAD_FORMAL_EVIDENCE = object()
_DAILY_PRICE_REQUIRED_COLUMNS = tuple(
    dict.fromkeys(
        (
            "date",
            "ticker",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "traded_value",
            "data_cutoff_at",
            *REQUIRED_ADJUSTED_OHLC_COLUMNS,
        )
    )
)
_EVENT_REQUIRED_COLUMNS = {
    "dividend_events": (
            "event_date",
            "ex_date",
            "ticker",
            "cash_dividend_per_share",
            "stock_dividend_ratio",
            "source_dataset_id",
            "source_row_id",
            "data_cutoff_at",
        ),
    "capital_action_events": (
            "event_date",
            "ex_date",
            "ticker",
            "action_type",
            "share_multiplier",
            "cash_return_per_share",
            "price_adjustment_reference",
            "source_dataset_id",
            "source_row_id",
            "data_cutoff_at",
        ),
}
_EVENT_COLUMN_TYPES = {
    "dividend_events": {
            "event_date": pa.string(),
            "ex_date": pa.string(),
            "ticker": pa.string(),
            "cash_dividend_per_share": pa.float64(),
            "stock_dividend_ratio": pa.float64(),
            "source_dataset_id": pa.string(),
            "source_row_id": pa.string(),
            "data_cutoff_at": pa.string(),
        },
    "capital_action_events": {
            "event_date": pa.string(),
            "ex_date": pa.string(),
            "ticker": pa.string(),
            "action_type": pa.string(),
            "share_multiplier": pa.float64(),
            "cash_return_per_share": pa.float64(),
            "price_adjustment_reference": pa.float64(),
            "source_dataset_id": pa.string(),
            "source_row_id": pa.string(),
            "data_cutoff_at": pa.string(),
        },
}


@dataclass(frozen=True)
class DailyPricePublishResult:
    rows_for_downstream: list[dict[str, Any]]
    changed_paths: tuple[str, ...]
    manifest_payload: Mapping[str, Any]
    evidence_payload: Mapping[str, Any]


@dataclass(frozen=True)
class FormalMetadataSnapshot:
    sha256: str | None
    payload: Mapping[str, Any] | None


def _selected_family_count(config: RuntimeConfig, families: set[str] | None) -> int:
    return sum(
        1
        for family in config.source_family_profiles.get("families", [])
        if family.get("enabled", True) is not False
        and (not families or family["family_id"] in families)
    )


def run_pipeline(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    *,
    families: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
    mongo_databases: dict[str, DatabaseLike] | None = None,
    run_scope: str | None = None,
    publish_ready_state: bool = False,
    pre_publication_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if publish_ready_state:
        raise ValueError(
            "ready state requires fresh verification; direct publication is forbidden"
        )
    if run_scope not in {None, "full_history", "bounded_backfill", "daily"}:
        raise ValueError(f"unsupported run scope: {run_scope}")
    effective_run_scope: RunScope = (
        run_scope
        if run_scope is not None
        else "bounded_backfill"
    )
    run_id = uuid.uuid4().hex
    if run_scope == "full_history" and any(
        value is not None for value in (start_date, end_date, as_of_date)
    ):
        raise ValueError("full_history run scope does not accept date boundaries")
    publisher = ArtifactPublisher(context)
    contracts = config.artifact_contracts
    selected_family_ids = _selected_family_ids(config, families)
    expected_outputs_by_family = expected_contract_outputs(
        contracts, selected_family_ids
    )
    expected_contract_keys = sorted(
        {
            key
            for keys in expected_outputs_by_family.values()
            for key in keys
        }
    )
    intent_config_hashes = {
        name: hashlib.sha256(context.config_path(name).read_bytes()).hexdigest()
        for name in CONFIG_FILENAMES
    }
    progress = RunProgress(context)
    if as_of_date:
        start_date = start_date or as_of_date
        end_date = end_date or as_of_date
    total_families = _selected_family_count(config, families)
    try:
        # Legacy migration is a publication side effect.  Restrict it to the
        # current run's declared outputs so an unrelated stale artifact cannot
        # prevent a bounded source repair from even starting.
        migrate_legacy_variant_manifests(
            context, (contracts[key] for key in expected_contract_keys)
        )
        pre_run_manifest_hashes = _manifest_hashes_by_contract(
            context, contracts
        )
        prefetched_family_rows: dict[str, list[dict[str, Any]]] = {}
        full_history_pit_families = {
            family_id
            for family_id, selected_output in SELECTED_OUTPUT_BY_RAW_FAMILY.items()
            if family_id in selected_family_ids
            and selected_output in expected_contract_keys
            and effective_run_scope == "full_history"
        }
        if full_history_pit_families:
            calendar_profile = next(
                (
                    profile
                    for profile in config.source_family_profiles.get("families", [])
                    if profile.get("family_id") == "trading_calendar"
                    and profile.get("enabled", True) is not False
                ),
                None,
            )
            if calendar_profile is None:
                outputs = sorted(
                    SELECTED_OUTPUT_BY_RAW_FAMILY[family_id]
                    for family_id in full_history_pit_families
                )
                raise ExtractError(
                    f"{', '.join(outputs)} requires fresh trading calendar "
                    "decision-date evidence for full_history"
                )
            if mongo_databases is None and "fixture_rows" not in calendar_profile:
                mongo_databases = open_mongo_databases(config.mongodb_sources)
            prefetched_family_rows["trading_calendar"] = _prepared_family_rows(
                calendar_profile,
                mongo_databases,
                None,
                None,
                effective_run_scope,
            )
        progress.update(phase="extract", total_families=total_families)
        family_rows: dict[str, list[dict[str, Any]]] = {}
        completed_families = 0
        extracted_family_ids: list[str] = []
        for family_id, extracted_rows in _iter_family_rows(
            config,
            families,
            start_date,
            end_date,
            mongo_databases,
            effective_run_scope,
            prefetched_family_rows=prefetched_family_rows,
        ):
            extracted_family_ids.append(family_id)
            _fail_on_empty_full_replace_snapshots(
                config, {family_id: extracted_rows}
            )
            _fail_on_illegal_empty_full_history_family(
                config, family_id, extracted_rows, effective_run_scope
            )
            if family_id not in RAW_EXPANSION_FAMILIES:
                family_rows[family_id] = extracted_rows
                completed_families += 1
                progress.update(
                    phase="source_family",
                    current_family=family_id,
                    completed_families=completed_families,
                    total_families=total_families,
                    extra={"row_count": len(extracted_rows)},
                )
                continue
            progress.update(
                phase="raw_family",
                current_family=family_id,
                completed_families=completed_families,
                total_families=total_families,
                extra={"row_count": len(extracted_rows)},
            )
            decision_dates = _decision_dates(
                start_date=start_date,
                end_date=end_date,
                as_of_date=as_of_date,
            )
            if family_id in full_history_pit_families:
                decision_dates = _full_history_pit_decision_dates(
                    family_id,
                    SELECTED_OUTPUT_BY_RAW_FAMILY[family_id],
                    extracted_rows,
                    prefetched_family_rows["trading_calendar"],
                )
            normalized = normalize_raw_family(
                family_id,
                extracted_rows,
                config.pit_registry,
                decision_dates=decision_dates,
            )
            source_collections = sorted(
                {str(row.get("source_collection")) for row in extracted_rows if row.get("source_collection")}
            )
            normalized["diagnostics"]["source_collection_count"] = len(source_collections)  # type: ignore[index]
            normalized["diagnostics"]["source_collections"] = source_collections[:200]  # type: ignore[index]
            normalized["diagnostics"]["source_collection_sample_truncated"] = len(source_collections) > 200  # type: ignore[index]
            published = _publish_raw_family_outputs(
                context,
                contracts,
                family_id,
                normalized,
                effective_run_scope,
            )
            if family_id in RAW_DEPENDENCY_FAMILIES:
                family_rows[family_id] = extracted_rows
            completed_families += 1
            progress.update(
                phase="raw_family",
                current_family=family_id,
                completed_families=completed_families,
                total_families=total_families,
                extra={"row_count": len(extracted_rows), "published": len(published)},
            )
            if family_id not in RAW_DEPENDENCY_FAMILIES:
                del normalized, extracted_rows

        if set(extracted_family_ids) != selected_family_ids:
            raise ExtractError(
                "extracted family set does not match selected run intent"
            )

        progress.update(phase="publish", current_family=None, completed_families=completed_families)
        incoming_security_master = family_rows.get("security_master", [])
        if incoming_security_master:
            _publish_registered(
                context,
                contracts["security_master"],
                incoming_security_master,
                effective_run_scope,
            )

        raw_daily_prices = family_rows.get("daily_price_volume", [])
        dividend_events = _filter_event_rows_by_date(
            build_dividend_events(family_rows.get("dividend_policy", [])),
            start_date=start_date,
            end_date=end_date,
        )
        capital_action_events = _filter_event_rows_by_date(
            build_capital_action_events(family_rows.get("capital_formation", [])),
            start_date=start_date,
            end_date=end_date,
        )
        replace_event_ids = {
            artifact_id
            for artifact_id, source_family in (
                ("dividend_events", "dividend_policy"),
                ("capital_action_events", "capital_formation"),
            )
            if source_family in extracted_family_ids
            and effective_run_scope == "full_history"
        }
        if replace_event_ids and not raw_daily_prices:
            raw_daily_prices = load_canonical_rows(
                context, contracts["daily_price_volume"]
            )
        daily_price_result: DailyPricePublishResult | None = None
        derived_start_date = start_date
        derived_end_date = end_date
        if raw_daily_prices or dividend_events or capital_action_events:
            adjustment_boundaries = rematerialization_starts(
                raw_daily_prices, [*dividend_events, *capital_action_events]
            )
            if dividend_events or capital_action_events:
                derived_start_date = min(adjustment_boundaries.values())
                derived_end_date = None
            validate_adjustment_seeds(
                run_scope=effective_run_scope,
                boundaries=adjustment_boundaries,
                existing_prices=load_canonical_rows(
                    context, contracts["daily_price_volume"]
                ),
            )
            daily_price_result = _publish_daily_price_volume(
                context,
                publisher,
                raw_daily_prices,
                dividend_events,
                capital_action_events,
                contracts=contracts,
                replace_event_ids=replace_event_ids,
                full_rebuild=(
                    effective_run_scope == "full_history"
                ),
            )
        daily_prices = load_canonical_rows(
            context, contracts["daily_price_volume"]
        )
        security_master = load_canonical_rows(
            context, contracts["security_master"]
        )

        trading_calendar_rows = load_canonical_rows(
            context, contracts["trading_calendar"]
        )
        daily_tradability_rows = load_canonical_rows(
            context, contracts["daily_tradability"]
        )
        if "daily_market_state" in expected_contract_keys:
            if not (
                daily_prices
                and security_master
                and trading_calendar_rows
                and daily_tradability_rows
            ):
                raise ArtifactError(
                    "daily_market_state requires non-empty canonical security_master, "
                    "trading_calendar, daily_price_volume, and daily_tradability"
                )
            _publish_daily_market_state(
                context,
                config,
                daily_prices=daily_prices,
                security_master=security_master,
                trading_calendar_rows=trading_calendar_rows,
                daily_tradability_rows=daily_tradability_rows,
                run_scope=effective_run_scope,
                scope_start=start_date,
                scope_end=end_date,
            )
        security_panel_history: list[dict[str, Any]] = []
        history_rebuild_requested = (
            "security_panel_history" in expected_contract_keys
        )
        if (
            history_rebuild_requested
            and daily_prices
            and security_master
            and trading_calendar_rows
        ):
            progress.update(phase="security_panel", completed_families=completed_families)
            panel_trading_calendar_rows = trading_calendar_rows
            if derived_end_date:
                panel_trading_calendar_rows = _rows_by_family(
                    config,
                    {"trading_calendar"},
                    derived_start_date,
                    None,
                    mongo_databases,
                    effective_run_scope,
                ).get("trading_calendar", trading_calendar_rows)
            security_panel_history, security_panel_diagnostics = build_historical_security_panel(
                daily_prices,
                security_master,
                panel_trading_calendar_rows,
                daily_tradability_rows,
                start_date=derived_start_date,
                end_date=derived_end_date,
            )
            if security_panel_history:
                _publish_registered(
                    context,
                    contracts["security_panel_history"],
                    security_panel_history,
                    effective_run_scope,
                )
                write_diagnostic(context, "historical_universe/security_panel_history", security_panel_diagnostics)

        corporate_actions = build_corporate_actions(dividend_events, capital_action_events)
        if corporate_actions and "corporate_actions" in expected_contract_keys:
            _publish_registered(
                context,
                contracts["corporate_actions"],
                corporate_actions,
                effective_run_scope,
            )

        effective_as_of_date, panel = build_security_panel(daily_prices, security_master, as_of_date=as_of_date)
        if panel and "security_panel" in expected_contract_keys:
            _publish_registered(
                context,
                contracts["security_panel"],
                panel,
                effective_run_scope,
            )

        if security_panel_history:
            progress.update(phase="universe", completed_families=completed_families)
            historical_memberships, universe_diagnostics = build_historical_universe_memberships(
                security_panel_history,
                config.universe_specs,
            )
            cutoff_by_as_of_date = _max_cutoff_by_as_of_date(
                security_panel_history
            )
            for universe_id, rows in historical_memberships.items():
                membership_contract = contracts[
                    f"universe_{universe_id}:historical"
                ]
                rows = with_membership_exclusions(
                    (
                        []
                        if effective_run_scope == "full_history"
                        else load_canonical_rows(context, membership_contract)
                    ),
                    rows,
                    cutoff_by_as_of_date,
                )
                if not rows:
                    if (
                        effective_run_scope == "full_history"
                        and membership_contract.allow_empty
                    ):
                        _publish_registered(
                            context,
                            membership_contract,
                            [],
                            effective_run_scope,
                        )
                    continue
                _publish_registered(
                    context,
                    membership_contract,
                    rows,
                    effective_run_scope,
                )
                write_diagnostic(
                    context,
                    f"historical_universe/{universe_id}",
                    universe_diagnostics[universe_id],
                )
        if panel and (
            as_of_date
            or any(
                key.endswith(":exact_date")
                for key in expected_contract_keys
            )
        ):
            progress.update(phase="universe", completed_families=completed_families)
            memberships = build_universe_memberships(panel, config.universe_specs)
            for universe_id, membership_rows in memberships.items():
                membership_contract = contracts[
                    f"universe_{universe_id}:exact_date"
                ]
                if membership_rows:
                    _publish_registered(
                        context,
                        membership_contract,
                        membership_rows,
                        effective_run_scope,
                    )
                elif membership_contract.allow_empty:
                    publish_dataset(
                        context,
                        membership_contract,
                        [],
                        effective_run_scope,
                        snapshot_value=effective_as_of_date,
                    )

        if effective_run_scope == "full_history":
            current_hashes = _manifest_hashes_by_contract(context, contracts)
            for contract_key in expected_contract_keys:
                contract = contracts[contract_key]
                if (
                    current_hashes[contract_key]
                    == pre_run_manifest_hashes[contract_key]
                    and contract.allow_empty
                ):
                    if contract.publication_mode == "snapshot_by_value":
                        publish_dataset(
                            context,
                            contract,
                            [],
                            effective_run_scope,
                            snapshot_value=effective_as_of_date,
                        )
                    else:
                        publish_dataset(
                            context, contract, [], effective_run_scope
                        )
            current_hashes = _manifest_hashes_by_contract(context, contracts)
            unchanged_required = [
                key
                for key in expected_contract_keys
                if current_hashes[key] == pre_run_manifest_hashes[key]
                and not contracts[key].allow_empty
            ]
            if unchanged_required:
                raise ArtifactError(
                    "full_history required outputs were not freshly published: "
                    + ", ".join(unchanged_required)
                )

        post_run_manifest_hashes = _manifest_hashes_by_contract(
            context, contracts
        )
        changed_contract_keys = sorted(
            key
            for key in contracts
            if post_run_manifest_hashes[key] != pre_run_manifest_hashes[key]
        )
        if effective_run_scope == "full_history" and set(
            expected_contract_keys
        ) != set(changed_contract_keys):
            raise ArtifactError(
                "full_history changed outputs do not match registry expectations"
            )

        progress.update(phase="metadata", completed_families=completed_families)
        result = {
            "status": "verifying",
            "phase": "verify",
            "as_of_date": effective_as_of_date,
            "families": sorted(extracted_family_ids),
        }
        if daily_price_result is not None:
            result["daily_price_volume"] = {
                "changed_partition_count": len(daily_price_result.changed_paths),
                "verification_status": daily_price_result.evidence_payload["status"],
                "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
            }
        metadata = publish_data_store_metadata(context, config)
        if metadata.get("config_hashes") != intent_config_hashes:
            raise ValueError("project config changed during pipeline run")
        attestation = _run_attestation(
            context,
            run_id,
            effective_run_scope,
            sorted(selected_family_ids),
            config.family_ids,
            metadata,
            pre_publication_audit,
            expected_outputs_by_family,
            expected_contract_keys,
            changed_contract_keys,
        )
        result["run_id"] = run_id
        result["run_attestation"] = attestation
        result_path = context.store_path("jobs", "pipeline_result.json")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        persisted_result = dict(result)
        result_path.write_text(
            json.dumps(persisted_result, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        progress.update(
            phase="verify",
            status="verifying",
            current_family=None,
            completed_families=completed_families,
            total_families=total_families,
            message="awaiting fresh verification",
            extra={
                "run_id": run_id,
                "selected_families": sorted(selected_family_ids),
                "run_attestation": attestation,
            },
        )
        return result
    except Exception as exc:
        progress.block(exc)
        raise


def _run_attestation(
    context: DataAnalystsContext,
    run_id: str,
    run_scope: RunScope,
    selected_families: list[str],
    enabled_families: set[str],
    metadata: dict[str, Any],
    pre_publication_audit: dict[str, Any] | None,
    expected_outputs_by_family: Mapping[str, tuple[str, ...]],
    expected_contract_keys: list[str],
    changed_contract_keys: list[str],
) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    manifest_dir = context.store_path("manifests")
    for path in sorted(manifest_dir.glob("*.json")) if manifest_dir.exists() else []:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        identities.append({
            "path": path.relative_to(context.data_store).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "artifact_id": payload.get("artifact_id"),
            "contract_key": payload.get("contract_key", payload.get("artifact_id")),
            "variant": payload.get("variant", "default"),
        })
    return {
        "schema_version": "1.0", "run_id": run_id, "status": "verifying",
        "run_scope": run_scope,
        "selected_families": sorted(selected_families),
        "enabled_families": sorted(enabled_families),
        "expected_outputs_by_family": {
            family_id: list(keys)
            for family_id, keys in sorted(expected_outputs_by_family.items())
        },
        "expected_contract_keys": sorted(expected_contract_keys),
        "changed_contract_keys": sorted(changed_contract_keys),
        "config_hashes": metadata.get("config_hashes"),
        "metadata_sha256": hashlib.sha256(
            context.store_path("metadata", "data_store_manifest.json").read_bytes()
        ).hexdigest(),
        "pre_publication_audit_sha256": (
            hashlib.sha256(
                json.dumps(
                    pre_publication_audit, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            if pre_publication_audit is not None else None
        ),
        "manifest_identities": identities,
    }


def _decision_dates(*, start_date: str | None, end_date: str | None, as_of_date: str | None) -> list[str] | None:
    if as_of_date:
        return [as_of_date]
    if start_date and end_date and start_date == end_date:
        return [start_date]
    if start_date and end_date:
        return [end_date]
    return None


def _full_history_pit_decision_dates(
    family_id: str,
    selected_artifact_id: str,
    source_rows: list[dict[str, Any]],
    calendar_rows: list[dict[str, Any]],
) -> list[str]:
    """Derive the complete PIT decision domain only from fresh source evidence."""
    active_dates: set[str] = set()
    for row in calendar_rows:
        raw_date = row.get("date") or row.get("zdate") or row.get("source_date")
        try:
            calendar_date = date.fromisoformat(str(raw_date)[:10]).isoformat()
        except (TypeError, ValueError):
            continue
        if "is_trading_day" in row:
            active = bool(row.get("is_trading_day"))
        else:
            active = not str(row.get("date_rmk") or "").strip()
        if active:
            active_dates.add(calendar_date)
    if not active_dates:
        raise ExtractError(
            f"{selected_artifact_id} requires fresh trading calendar active-date "
            "evidence for full_history"
        )
    availability_dates: list[str] = []
    for row in source_rows:
        raw_date = row.get("source_available_date") or row.get("key3")
        try:
            availability_dates.append(
                date.fromisoformat(str(raw_date)[:10]).isoformat()
            )
        except (TypeError, ValueError):
            continue
    if not availability_dates:
        raise ExtractError(
            f"{selected_artifact_id} has no fresh source/PIT availability "
            f"evidence from {family_id} for full_history"
        )
    first_available = min(availability_dates)
    decision_dates = sorted(value for value in active_dates if value >= first_available)
    if not decision_dates:
        raise ExtractError(
            f"{selected_artifact_id} has no legal full_history decision dates: "
            "fresh trading calendar dates precede all source/PIT availability"
        )
    return decision_dates


def publish_raw_family_outputs(
    context: DataAnalystsContext,
    contracts: dict[str, ArtifactContract],
    family_id: str,
    normalized: dict[str, object],
    run_scope: RunScope,
) -> list[str]:
    return _publish_raw_family_outputs(
        context, contracts, family_id, normalized, run_scope
    )


def _selected_family_ids(
    config: RuntimeConfig, families: set[str] | None
) -> set[str]:
    return {
        str(family["family_id"])
        for family in config.source_family_profiles.get("families", [])
        if family.get("enabled", True) is not False
        and (not families or family["family_id"] in families)
    }


def _manifest_hashes_by_contract(
    context: DataAnalystsContext,
    contracts: Mapping[str, ArtifactContract],
) -> dict[str, str | None]:
    output: dict[str, str | None] = {}
    for key, contract in contracts.items():
        path = context.store_path("manifests", contract.manifest_file_name)
        output[key] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
    return output


def _publish_daily_market_state(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    *,
    daily_prices: list[dict[str, Any]],
    security_master: list[dict[str, Any]],
    trading_calendar_rows: list[dict[str, Any]],
    daily_tradability_rows: list[dict[str, Any]],
    run_scope: RunScope,
    scope_start: str | None,
    scope_end: str | None,
) -> None:
    """Build DMS only after all four freshly declared dependencies exist."""
    dependency_ids = (
        "security_master",
        "trading_calendar",
        "daily_price_volume",
        "daily_tradability",
    )
    manifests = {
        artifact_id: _ready_manifest_payload(context, artifact_id)
        for artifact_id in dependency_ids
    }
    hashes = _manifest_hashes_by_contract(context, config.artifact_contracts)
    dependency_hashes = {
        artifact_id: hashes[artifact_id]
        for artifact_id in dependency_ids
    }
    if any(value is None for value in dependency_hashes.values()):
        raise ArtifactError("daily_market_state dependency manifest hash is missing")
    # security_master is a snapshot without a date range.  It contributes
    # lifecycle identity and cutoff provenance, but cannot constrain the
    # daily overlap interval.
    build_start, build_end = _common_daily_coverage(
        {
            artifact_id: manifests[artifact_id]
            for artifact_id in (
                "trading_calendar",
                "daily_price_volume",
                "daily_tradability",
            )
        }
    )
    data_cutoff_at = max_data_cutoff(
        *(manifest.get("data_cutoff_at") for manifest in manifests.values())
    )
    if data_cutoff_at is None:
        raise ArtifactError("daily_market_state dependency manifests lack data_cutoff_at")
    processing_start = build_start if run_scope == "full_history" else (scope_start or build_start)
    processing_end = build_end if run_scope == "full_history" else (scope_end or build_end)
    processing_start = max(build_start, processing_start)
    processing_end = min(build_end, processing_end)
    if processing_start > processing_end:
        raise ArtifactError("daily_market_state requested scope lies outside dependency coverage")
    rows = build_daily_market_state_rows(
        trading_calendar_rows=trading_calendar_rows,
        price_rows=daily_prices,
        security_master_rows=security_master,
        attribute_rows=daily_tradability_rows,
        manifest_hashes={
            artifact_id: str(dependency_hashes[artifact_id])
            for artifact_id in dependency_ids
        },
        build_start=build_start,
        build_end=build_end,
        data_cutoff_at=data_cutoff_at,
        certified_source_start=build_start,
        scope_start=processing_start,
        scope_end=processing_end,
    )
    if not rows:
        raise ArtifactError("daily_market_state builder returned no rows")
    by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[str(row["date"])[:4]].append(row)
    publish_daily_market_state_partitions(
        context,
        config,
        tuple((year, sorted(year_rows, key=lambda row: (str(row["date"]), str(row["ticker"])))) for year, year_rows in sorted(by_year.items())),
        build_start=processing_start,
        build_end=processing_end,
        certified_source_start=build_start,
        run_scope=run_scope,
    )


def _ready_manifest_payload(
    context: DataAnalystsContext, artifact_id: str
) -> dict[str, Any]:
    path = context.store_path("manifests", f"{artifact_id}.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            f"daily_market_state cannot read dependency manifest {artifact_id}: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("status") != "ready":
        raise ArtifactError(
            f"daily_market_state dependency manifest is not ready: {artifact_id}"
        )
    return payload


def _common_daily_coverage(
    manifests: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    ranges: list[tuple[str, str]] = []
    for artifact_id, manifest in manifests.items():
        date_range = manifest.get("date_range")
        if (
            not isinstance(date_range, list)
            or len(date_range) != 2
            or not all(isinstance(value, str) and value for value in date_range)
        ):
            raise ArtifactError(
                f"daily_market_state dependency lacks a valid date_range: {artifact_id}"
            )
        ranges.append((date_range[0], date_range[1]))
    start = max(value[0] for value in ranges)
    end = min(value[1] for value in ranges)
    if start > end:
        raise ArtifactError("daily_market_state dependency date ranges do not overlap")
    return start, end


def _publish_raw_family_outputs(
    context: DataAnalystsContext,
    contracts: dict[str, ArtifactContract],
    family_id: str,
    normalized: dict[str, object],
    run_scope: RunScope,
) -> list[str]:
    raw_rows = list(normalized["raw_rows"])  # type: ignore[arg-type]
    selected_rows = list(normalized.get("selected_rows") or [])
    diagnostics = dict(normalized["diagnostics"])  # type: ignore[arg-type]
    published: list[str] = []
    write_diagnostic(context, f"raw_families/{family_id}", diagnostics)
    if raw_rows or (
        run_scope == "full_history" and contracts[family_id].allow_empty
    ):
        _publish_registered(
            context, contracts[family_id], raw_rows, run_scope
        )
        published.append(family_id)
    selected_family_id = SELECTED_OUTPUT_BY_RAW_FAMILY.get(family_id)
    if selected_family_id and selected_rows:
        _publish_registered(
            context,
            contracts[selected_family_id],
            selected_rows,
            run_scope,
        )
        published.append(selected_family_id)
    return published


def _publish_registered(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    rows: list[dict[str, Any]],
    run_scope: RunScope,
) -> None:
    publication_scope: RunScope = (
        "full_history"
        if contract.publication_mode == "full_replace"
        else run_scope
    )
    publish_dataset(context, contract, rows, publication_scope)


def _rows_by_family(
    config: RuntimeConfig,
    families: set[str] | None,
    start_date: str | None,
    end_date: str | None,
    mongo_databases: dict[str, DatabaseLike] | None,
    run_scope: RunScope,
) -> dict[str, list[dict[str, Any]]]:
    return dict(
        _iter_family_rows(
            config, families, start_date, end_date, mongo_databases, run_scope
        )
    )


def _iter_family_rows(
    config: RuntimeConfig,
    families: set[str] | None,
    start_date: str | None,
    end_date: str | None,
    mongo_databases: dict[str, DatabaseLike] | None,
    run_scope: RunScope,
    *,
    prefetched_family_rows: Mapping[str, list[dict[str, Any]]] | None = None,
):
    if mongo_databases is None and _needs_mongo(config, families):
        mongo_databases = open_mongo_databases(config.mongodb_sources)
    for family in config.source_family_profiles.get("families", []):
        family_id = family["family_id"]
        if family.get("enabled", True) is False:
            continue
        if families and family_id not in families:
            continue
        prefetched = (prefetched_family_rows or {}).get(family_id)
        source_rows = (
            list(prefetched)
            if prefetched is not None
            else _prepared_family_rows(
                family, mongo_databases, start_date, end_date, run_scope
            )
        )
        yield family_id, source_rows


def _prepared_family_rows(
    family: dict[str, Any],
    mongo_databases: dict[str, DatabaseLike] | None,
    start_date: str | None,
    end_date: str | None,
    run_scope: RunScope,
) -> list[dict[str, Any]]:
    family_id = str(family["family_id"])
    source_rows = _rows_for_family(
        family, mongo_databases, start_date, end_date, run_scope
    )
    retained_count = 0
    for index, source_row in enumerate(source_rows):
        normalized = _normalize_source_row(
            family_id, _apply_field_map(family, source_row), index
        )
        if family.get("source_profile") != "small_snapshot":
            normalized = _filter_date(normalized, start_date, end_date)
        if normalized is not None:
            source_rows[retained_count] = normalized
            retained_count += 1
    del source_rows[retained_count:]
    return source_rows


def _needs_mongo(config: RuntimeConfig, families: set[str] | None) -> bool:
    for family in config.source_family_profiles.get("families", []):
        if family.get("enabled", True) is False:
            continue
        if families and family["family_id"] not in families:
            continue
        if "fixture_rows" not in family:
            return True
    return False


def _fail_on_empty_full_replace_snapshots(
    config: RuntimeConfig,
    family_rows: dict[str, list[dict[str, Any]]],
) -> None:
    profiles = {
        str(profile.get("family_id")): profile
        for profile in config.source_family_profiles.get("families", [])
        if isinstance(profile, dict)
    }
    for family_id, rows in family_rows.items():
        contract = config.artifact_contracts.get(family_id)
        if (
            profiles.get(family_id, {}).get("source_profile")
            == "small_snapshot"
            and contract is not None
            and contract.publication_mode == "full_replace"
            and not rows
        ):
            raise ExtractError(
                f"{family_id} returned an empty full-replace small snapshot"
            )


def _fail_on_illegal_empty_full_history_family(
    config: RuntimeConfig,
    family_id: str,
    rows: list[dict[str, Any]],
    run_scope: RunScope,
) -> None:
    if run_scope != "full_history" or rows:
        return
    owned = [
        contract
        for contract in config.artifact_contracts.values()
        if contract.contract_key == family_id
        and family_id in contract.source_families
    ]
    if owned and any(not contract.allow_empty for contract in owned):
        blocked = sorted(
            contract.contract_key
            for contract in owned
            if not contract.allow_empty
        )
        raise ExtractError(
            f"{family_id} returned empty complete source domain for required "
            f"artifacts: {blocked}"
        )


def _max_cutoff_by_as_of_date(
    rows: list[dict[str, Any]],
) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in rows:
        as_of_date = row.get("as_of_date")
        cutoff = row.get("data_cutoff_at")
        if as_of_date is None or cutoff is None:
            continue
        key = str(as_of_date)
        output[key] = max_data_cutoff(output.get(key), cutoff) or str(cutoff)
    return output


def _rows_for_family(
    family: dict[str, Any],
    mongo_databases: dict[str, DatabaseLike] | None,
    start_date: str | None,
    end_date: str | None,
    run_scope: RunScope,
) -> list[dict[str, Any]]:
    if "fixture_rows" in family:
        return [dict(row) for row in family.get("fixture_rows", [])]

    if mongo_databases is None:
        raise ExtractError(f"{family['family_id']} has no fixture_rows and no MongoDB database")
    connection = family.get("connection")
    database = mongo_databases.get(connection)
    if database is None:
        raise ExtractError(f"missing MongoDB database for connection: {connection}")
    return extract_family_rows_from_database(
        database,
        family,
        start_date=start_date,
        end_date=end_date,
        run_scope=run_scope,
    )


def _normalize_source_row(family_id: str, row: dict[str, Any], index: int) -> dict[str, Any]:
    row.setdefault("source_dataset_id", family_id)
    row.setdefault("source_collection", f"fixture.{family_id}")
    row.setdefault("source_row_id", f"{family_id}:{index}")
    if not _is_real_source_cutoff(row.get("data_cutoff_at")):
        raise ExtractError(
            f"family_id={family_id} source_collection={row['source_collection']} "
            f"source_row_id={row['source_row_id']} missing real data_cutoff_at"
        )
    return row


def _is_real_source_cutoff(value: Any) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if "T" not in text and " " not in text:
            return False
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() != 0.0


def _apply_field_map(family: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    field_map = family.get("field_map")
    if not isinstance(field_map, dict) or not field_map:
        return row
    mapped = {
        key: row[key]
        for key in [
            "source_collection",
            "source_row_id",
            "source_dataset_id",
            "data_cutoff_at",
        ]
        if key in row
    }
    for canonical_field, source_field in field_map.items():
        if source_field in row:
            mapped[canonical_field] = (
                row[source_field]
                if canonical_field == "data_cutoff_at"
                else _normalize_source_value(row[source_field])
            )
    row.clear()
    row.update(mapped)
    return row


def _normalize_source_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value):
        return value[:10]
    return value


def _filter_date(row: dict[str, Any], start_date: str | None, end_date: str | None) -> dict[str, Any] | None:
    row_date = row.get("date") or row.get("event_date") or row.get("ex_date")
    if row_date is None:
        return row
    row_date = str(row_date)
    if start_date and row_date < start_date:
        return None
    if end_date and row_date > end_date:
        return None
    return row


def _filter_event_rows_by_date(
    rows: list[dict[str, Any]],
    *,
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        event_date = row.get("event_date")
        if event_date is None:
            output.append(row)
            continue
        event_date_text = str(event_date)
        if start_date and event_date_text < start_date:
            continue
        if end_date and event_date_text > end_date:
            continue
        output.append(row)
    return output


def _spec_for_contract(
    contract: ArtifactContract,
    version: str,
    *,
    required_columns: tuple[str, ...],
    column_types: Mapping[str, pa.DataType] | None = None,
) -> PartitionSpec:
    if contract.partition_name is None or contract.partition_field is None:
        raise AdjustmentError(
            f"{contract.artifact_id} requires a partitioned publication contract"
        )
    return PartitionSpec(
        base_path=f"{contract.base_path}/versions/{version}",
        partition_field=contract.partition_field,
        partition_name=contract.partition_name,
        key_fields=contract.logical_key,
        required_columns=tuple(
            dict.fromkeys([*contract.required_columns, *required_columns])
        ),
        column_types=column_types,
    )


def _contracts_for_explicit_specs(
    price_spec: PartitionSpec,
    event_specs: Mapping[str, PartitionSpec],
) -> dict[str, ArtifactContract]:
    specs = {"daily_price_volume": price_spec, **event_specs}
    contracts: dict[str, ArtifactContract] = {}
    for artifact_id, spec in specs.items():
        is_price = artifact_id == "daily_price_volume"
        contracts[artifact_id] = ArtifactContract(
            contract_key=artifact_id,
            artifact_id=artifact_id,
            variant="explicit_spec",
            layer="raw" if is_price else "derived",
            base_path=spec.base_path,
            file_name="part.parquet",
            required_columns=tuple(spec.required_columns),
            logical_key=tuple(spec.key_fields),
            publication_mode="partition_upsert",
            partition_name=spec.partition_name,
            partition_field=spec.partition_field,
            date_field=spec.partition_field,
            availability_field=spec.partition_field,
            pit_policy="explicit_test_spec",
            source_families=(artifact_id,),
            allow_empty=not is_price,
        )
    return contracts


def _clone_inventory_to_version(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    manifest: Mapping[str, Any] | None,
    version: str,
    *,
    include_existing: bool,
) -> tuple[dict[str, str], list[Path], list[dict[str, Any]]]:
    if not isinstance(manifest, Mapping):
        return {}, [], []
    raw_paths = manifest.get("artifact_paths")
    if not isinstance(raw_paths, list):
        raise AdjustmentError(
            f"invalid formal manifest paths: {contract.artifact_id}"
        )
    remap: dict[str, str] = {}
    roots: list[Path] = []
    superseded = list(manifest.get("superseded_paths", []))
    retained_version = f"legacy-{uuid.uuid4().hex}"
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            raise AdjustmentError(
                f"invalid formal manifest path: {contract.artifact_id}"
            )
        partition_value = _partition_value_from_contract_path(contract, raw_path)
        source = context.artifact_path(raw_path)
        if not source.is_file():
            raise AdjustmentError(f"missing formal partition: {raw_path}")
        if include_existing:
            target_path = contract.path_for_partition(
                partition_value, version=version
            )
            target = context.artifact_path(target_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
            remap[raw_path] = target_path
            roots.append(
                context.artifact_path(f"{contract.base_path}/versions/{version}")
            )
        if "/versions/" not in raw_path.replace("\\", "/"):
            retained_path = contract.path_for_partition(
                partition_value, version=retained_version
            )
            retained = context.artifact_path(retained_path)
            retained.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, retained)
            except OSError:
                shutil.copy2(source, retained)
            digest = _sha256(source)
            superseded.append(
                {
                    "path": raw_path,
                    "size": source.stat().st_size,
                    "sha256": digest,
                    "retained_path": retained_path,
                    "state": "retained",
                }
            )
            roots.append(
                context.artifact_path(
                    f"{contract.base_path}/versions/{retained_version}"
                )
            )
    return remap, roots, superseded


def _partition_value_from_contract_path(
    contract: ArtifactContract, path: str
) -> str:
    if contract.partition_name is None:
        raise AdjustmentError(
            f"{contract.artifact_id} has no partition name"
        )
    prefix = f"{contract.partition_name}="
    values = [
        part[len(prefix) :]
        for part in Path(path.replace("/", os.sep)).parts
        if part.startswith(prefix)
    ]
    if len(values) != 1 or not values[0]:
        raise AdjustmentError(
            f"invalid {contract.artifact_id} partition path: {path}"
        )
    return values[0]


def _remap_formal_paths(
    payload: Mapping[str, Any] | None,
    replacements: Mapping[str, str],
) -> dict[str, Any] | None:
    if payload is None:
        return None

    def remap(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        return value

    return remap(dict(payload))


def _publish_daily_price_volume(
    context: DataAnalystsContext,
    publisher: ArtifactPublisher,
    raw_rows: list[dict[str, Any]],
    dividend_events: list[dict[str, Any]],
    capital_action_events: list[dict[str, Any]],
    *,
    contracts: Mapping[str, ArtifactContract] | None = None,
    price_spec: PartitionSpec | None = None,
    event_specs: Mapping[str, PartitionSpec] | None = None,
    replace_event_ids: set[str] | None = None,
    full_rebuild: bool,
) -> DailyPricePublishResult:
    if publisher.context != context:
        raise AdjustmentError("daily price publisher context mismatch")
    if not raw_rows and not dividend_events and not capital_action_events:
        raise AdjustmentError("daily price publish requires price or event rows")
    if full_rebuild and not raw_rows:
        raise AdjustmentError("full daily price rebuild requires rows")
    if None in {row.get("ticker") for row in raw_rows} or None in {
        row.get("date") for row in raw_rows
    }:
        raise AdjustmentError("daily price rows require date and ticker")
    all_incoming_events = [*dividend_events, *capital_action_events]
    if any(
        row.get("ticker") is None or row.get("event_date") is None
        for row in all_incoming_events
    ):
        raise AdjustmentError("official event rows require event_date and ticker")

    metadata_snapshot = _formal_metadata_snapshot(context)
    current_manifest = metadata_snapshot[_DAILY_PRICE_MANIFEST_PATH].payload
    previous_evidence = metadata_snapshot[_ADJUSTED_OHLC_EVIDENCE_PATH].payload
    captured_event_manifests = {
        artifact_id: metadata_snapshot[f"manifests/{artifact_id}.json"].payload
        for artifact_id in _OFFICIAL_EVENT_IDS
    }
    publication_version: str | None = None
    version_roots: list[Path] = []
    superseded_paths: dict[str, list[dict[str, Any]]] = {}
    if contracts is not None:
        publication_version = uuid.uuid4().hex
        remap: dict[str, str] = {}
        for artifact_id in ("daily_price_volume", *_OFFICIAL_EVENT_IDS):
            contract = contracts[artifact_id]
            manifest = (
                current_manifest
                if artifact_id == "daily_price_volume"
                else captured_event_manifests[artifact_id]
            )
            cloned, roots, superseded = _clone_inventory_to_version(
                context,
                contract,
                manifest,
                publication_version,
                include_existing=not full_rebuild,
            )
            remap.update(cloned)
            version_roots.extend(roots)
            superseded_paths[artifact_id] = superseded
        current_manifest = _remap_formal_paths(current_manifest, remap)
        previous_evidence = _remap_formal_paths(previous_evidence, remap)
        if current_manifest is not None:
            current_manifest["active_version"] = publication_version
        if current_manifest is not None and previous_evidence is not None:
            previous_evidence["manifest_fingerprint"] = manifest_fingerprint(
                current_manifest
            )
        captured_event_manifests = {
            artifact_id: _remap_formal_paths(manifest, remap)
            for artifact_id, manifest in captured_event_manifests.items()
        }
        for manifest in captured_event_manifests.values():
            if manifest is not None:
                manifest["active_version"] = publication_version
        price_spec = _spec_for_contract(
            contracts["daily_price_volume"],
            publication_version,
            required_columns=_DAILY_PRICE_REQUIRED_COLUMNS,
        )
        event_specs = {
            artifact_id: _spec_for_contract(
                contracts[artifact_id],
                publication_version,
                required_columns=_EVENT_REQUIRED_COLUMNS[artifact_id],
                column_types=_EVENT_COLUMN_TYPES[artifact_id],
            )
            for artifact_id in _OFFICIAL_EVENT_IDS
        }
    elif price_spec is None or event_specs is None:
        raise AdjustmentError(
                "daily price publication requires registry contracts or explicit specs"
            )
    formal_contracts = (
        contracts
        if contracts is not None
        else _contracts_for_explicit_specs(price_spec, event_specs)
    )
    entry_partition_sha256 = snapshot_partition_sources(
        context,
        _entry_partition_paths(
            raw_rows,
            {
                "dividend_events": dividend_events,
                "capital_action_events": capital_action_events,
            },
            price_spec=price_spec,
            event_specs=event_specs,
        ),
    )
    initial_certification = current_manifest is None and previous_evidence is None
    full_audit = full_rebuild or initial_certification
    transaction_root: Path | None = None
    commit_started = False
    try:
        event_rows_by_id = {
            "dividend_events": dividend_events,
            "capital_action_events": capital_action_events,
        }
        event_partitions: list[StagedPartition] = []
        event_manifests: dict[str, Mapping[str, Any]] = {}
        for artifact_id in _OFFICIAL_EVENT_IDS:
            event_rows = event_rows_by_id[artifact_id]
            if not event_rows and artifact_id not in (replace_event_ids or set()):
                continue
            current_event_manifest = captured_event_manifests[artifact_id]
            staged_events = stage_partition_rows(
                context,
                event_rows,
                event_specs[artifact_id],
                mode="replace" if full_rebuild else "upsert",
                transaction_root=transaction_root,
                source_snapshot=entry_partition_sha256,
            )
            if transaction_root is None and staged_events:
                transaction_root = _staged_transaction_root(context, staged_events)
            event_partitions.extend(staged_events)
            event_manifests[artifact_id] = _build_event_manifest(
                context,
                artifact_id,
                formal_contracts[artifact_id],
                current_event_manifest,
                staged_events,
                event_rows,
                full_rebuild=full_rebuild,
                active_version=publication_version,
                superseded_paths=superseded_paths.get(artifact_id),
            )

        prospective_event_manifests = dict(captured_event_manifests)
        prospective_event_manifests.update(event_manifests)
        event_path_overrides = {
            partition.artifact_path: partition.staged_path
            for partition in event_partitions
        }
        incoming_horizons = _ticker_horizons(raw_rows, date_field="date")
        event_horizons = _ticker_horizons(
            all_incoming_events, date_field="event_date"
        )
        calculation_horizons = dict(incoming_horizons)
        for ticker, event_date in event_horizons.items():
            calculation_horizons[ticker] = min(
                event_date, calculation_horizons.get(ticker, event_date)
            )

        existing_suffix_rows: list[dict[str, Any]] = []
        if not full_rebuild and event_horizons:
            if current_manifest is None or previous_evidence is None:
                raise AdjustmentError(
                    "event refresh requires certified daily price history"
                )
            existing_suffix_rows = _load_price_suffix_rows(
                context,
                current_manifest,
                previous_evidence,
                formal_contracts["daily_price_volume"],
                horizons=event_horizons,
                entry_partition_sha256=entry_partition_sha256,
            )
        calculation_rows = _merge_price_calculation_rows(
            existing_suffix_rows, raw_rows
        )
        active_tickers = {str(row["ticker"]) for row in calculation_rows}
        active_horizons = {
            ticker: horizon
            for ticker, horizon in calculation_horizons.items()
            if ticker in active_tickers
        }

        seeds: dict[str, AdjustmentSeed] = {}
        seed_dates: dict[str, str] = {}
        proven_new_series: set[str] = set()
        if not full_rebuild and active_horizons:
            if current_manifest is not None:
                seeds, seed_dates = _load_adjustment_seeds_by_horizon(
                    context,
                    current_manifest,
                    formal_contracts["daily_price_volume"],
                    evidence=previous_evidence,
                    horizons=active_horizons,
                    entry_partition_sha256=entry_partition_sha256,
                )
            missing_horizons = {
                ticker: horizon
                for ticker, horizon in active_horizons.items()
                if ticker not in seeds
            }
            if missing_horizons:
                proven_new_series = _prove_new_series_by_horizon(
                    context,
                    horizons=missing_horizons,
                    price_manifest=current_manifest,
                    price_evidence=previous_evidence,
                    event_manifests=prospective_event_manifests,
                    event_path_overrides=event_path_overrides,
                    entry_partition_sha256=entry_partition_sha256,
                    contracts=formal_contracts,
                )
            unproven = set(missing_horizons).difference(proven_new_series)
            if unproven:
                raise AdjustmentError(
                    "cannot prove new adjustment series for tickers: "
                    + ", ".join(sorted(unproven))
                )

        event_read_horizons = {
            ticker: seed_dates.get(ticker, horizon)
            for ticker, horizon in active_horizons.items()
        }
        prospective_events = _load_prospective_events(
            context,
            prospective_event_manifests,
            event_path_overrides,
            horizons=event_read_horizons,
            ending_dates=_ticker_ending_dates(calculation_rows),
            entry_partition_sha256=entry_partition_sha256,
            exclusive_lower_bound_tickers=set(seed_dates),
            contracts=formal_contracts,
        )
        adjusted_rows = build_adjusted_daily_prices(
            calculation_rows,
            dividend_events=_retarget_events_for_calculation(
                prospective_events["dividend_events"], calculation_rows
            ),
            capital_action_events=_retarget_events_for_calculation(
                prospective_events["capital_action_events"], calculation_rows
            ),
            initial_state_by_ticker=seeds,
            proven_new_series_tickers=proven_new_series,
            require_seed=not full_rebuild,
        )
        staged_partitions = stage_partition_rows(
            context,
            adjusted_rows,
            price_spec,
            mode="replace" if full_rebuild else "upsert",
            transaction_root=transaction_root,
            source_snapshot=entry_partition_sha256,
        )
        if transaction_root is None:
            transaction_root = _staged_transaction_root(context, staged_partitions)
        changed_paths = tuple(
            sorted(
                (partition.artifact_path for partition in staged_partitions),
                key=lambda path: _contract_partition_year(
                    formal_contracts["daily_price_volume"],
                    path,
                    active_version=publication_version,
                ),
            )
        )
        manifest_payload = _build_daily_price_manifest(
            context,
            formal_contracts["daily_price_volume"],
            current_manifest,
            previous_evidence,
            staged_partitions,
            adjusted_rows,
            full_rebuild=full_rebuild,
            active_version=publication_version,
            superseded_paths=superseded_paths.get("daily_price_volume"),
        )
        all_partitions = [*event_partitions, *staged_partitions]
        path_overrides = {
            partition.artifact_path: partition.staged_path
            for partition in all_partitions
        }
        changed_event_paths = {
            partition.artifact_path for partition in event_partitions
        }
        evidence_payload = audit_adjusted_ohlc(
            context,
            manifest_payload,
            contracts=formal_contracts,
            mode="full" if full_audit else "incremental",
            changed_paths=None if full_audit else set(changed_paths),
            previous_evidence=None if full_audit else previous_evidence,
            path_overrides=path_overrides,
            manifest_overrides=prospective_event_manifests,
            formal_event_manifest_overrides=captured_event_manifests,
            changed_event_paths=None if full_audit else changed_event_paths,
            entry_content_sha256=entry_partition_sha256,
        )
        if evidence_payload.get("status") != "ready":
            reasons = evidence_payload.get("blocked_reasons") or []
            entry_drift = [
                str(reason)
                for reason in reasons
                if "changed since entry snapshot" in str(reason)
            ]
            if entry_drift:
                raise PublishTransactionError(
                    "source precondition hash mismatch after entry snapshot: "
                    + "; ".join(entry_drift)
                )
            raise AdjustmentError(
                "staged adjusted OHLC validation blocked: "
                + ("; ".join(str(reason) for reason in reasons) or "unknown reason")
            )

        source_preconditions = _daily_price_source_preconditions(
            context,
            evidence_payload,
            all_partitions,
            changed_paths=set(changed_paths),
            changed_event_paths=changed_event_paths,
            metadata_snapshot=metadata_snapshot,
            entry_partition_sha256=entry_partition_sha256,
        )
        metadata_payloads: dict[str, Mapping[str, Any]] = {
            f"manifests/{artifact_id}.json": event_manifests[artifact_id]
            for artifact_id in _OFFICIAL_EVENT_IDS
            if artifact_id in event_manifests
        }
        metadata_payloads[_DAILY_PRICE_MANIFEST_PATH] = manifest_payload
        metadata_payloads[_ADJUSTED_OHLC_EVIDENCE_PATH] = evidence_payload
        commit_started = True
        commit_publish_transaction(
            context,
            all_partitions,
            metadata_payloads,
            source_preconditions=source_preconditions,
        )
    finally:
        if not commit_started:
            if transaction_root is not None:
                shutil.rmtree(transaction_root, ignore_errors=True)
            for root in dict.fromkeys(version_roots):
                shutil.rmtree(root, ignore_errors=True)

    incoming_keys = {
        (str(row["date"]), str(row["ticker"])) for row in raw_rows
    }
    return DailyPricePublishResult(
        rows_for_downstream=[
            row
            for row in adjusted_rows
            if (str(row["date"]), str(row["ticker"])) in incoming_keys
        ],
        changed_paths=changed_paths,
        manifest_payload=manifest_payload,
        evidence_payload=evidence_payload,
    )


def _entry_partition_paths(
    price_rows: list[dict[str, Any]],
    event_rows_by_id: Mapping[str, list[dict[str, Any]]],
    *,
    price_spec: PartitionSpec,
    event_specs: Mapping[str, PartitionSpec],
) -> set[str]:
    paths = set(
        f"{price_spec.base_path}/{price_spec.partition_name}={str(row['date'])[:4]}/part.parquet"
        for row in price_rows
    )
    for artifact_id, rows in event_rows_by_id.items():
        spec = event_specs[artifact_id]
        paths.update(
            f"{spec.base_path}/event_year={str(row['event_date'])[:4]}/part.parquet"
            for row in rows
        )
    return paths


def _ticker_horizons(
    rows: list[dict[str, Any]], *, date_field: str
) -> dict[str, str]:
    horizons: dict[str, str] = {}
    for row in rows:
        ticker = str(row["ticker"])
        row_date = str(row[date_field])
        horizons[ticker] = min(row_date, horizons.get(ticker, row_date))
    return horizons


def _ticker_ending_dates(rows: list[dict[str, Any]]) -> dict[str, str]:
    endings: dict[str, str] = {}
    for row in rows:
        ticker = str(row["ticker"])
        row_date = str(row["date"])
        endings[ticker] = max(row_date, endings.get(ticker, row_date))
    return endings


def _merge_price_calculation_rows(
    existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = {
        (str(row["date"]), str(row["ticker"])): dict(row)
        for row in existing_rows
    }
    merged.update(
        {
            (str(row["date"]), str(row["ticker"])): dict(row)
            for row in incoming_rows
        }
    )
    return [merged[key] for key in sorted(merged, key=lambda item: (item[1], item[0]))]


def _assert_entry_partition_unchanged(
    context: DataAnalystsContext,
    artifact_path: str,
    entry_partition_sha256: Mapping[str, str | None],
) -> None:
    expected = capture_partition_source(
        context, entry_partition_sha256, artifact_path
    )
    actual = _sha256_if_present(context.artifact_path(artifact_path))
    if actual != expected:
        raise PublishTransactionError(
            f"entry source changed after snapshot: {artifact_path}; "
            f"expected_sha256={expected}, actual_sha256={actual}"
        )


def _load_price_suffix_rows(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    price_contract: ArtifactContract,
    *,
    horizons: Mapping[str, str],
    entry_partition_sha256: Mapping[str, str | None],
) -> list[dict[str, Any]]:
    verified = _validated_formal_evidence(manifest, evidence, price_contract)
    if verified is None:
        raise AdjustmentError("event refresh requires valid formal price evidence")
    records = {record["artifact_path"]: record for record in verified["partitions"]}
    tickers = sorted(horizons)
    minimum_date = min(horizons.values())
    minimum_year = int(minimum_date[:4])
    columns = [str(column) for column in manifest.get("columns", [])]
    rows: list[dict[str, Any]] = []
    for artifact_path in sorted(
        (str(path) for path in manifest.get("artifact_paths", [])),
        key=lambda path: _contract_partition_year(
            price_contract, path, active_version=manifest.get("active_version")
        ),
    ):
        if _contract_partition_year(
            price_contract,
            artifact_path,
            active_version=manifest.get("active_version"),
        ) < minimum_year:
            continue
        record = records.get(artifact_path)
        _assert_entry_partition_unchanged(
            context, artifact_path, entry_partition_sha256
        )
        if (
            not isinstance(record, Mapping)
            or record.get("content_sha256") != entry_partition_sha256.get(artifact_path)
        ):
            raise AdjustmentError(
                f"price suffix lacks trusted entry evidence: {artifact_path}"
            )
        partition_rows = pq.read_table(
            context.artifact_path(artifact_path),
            columns=columns,
            filters=[("ticker", "in", tickers), ("date", ">=", minimum_date)],
        ).to_pylist()
        _assert_entry_partition_unchanged(
            context, artifact_path, entry_partition_sha256
        )
        rows.extend(
            row
            for row in partition_rows
            if str(row.get("ticker")) in horizons
            and str(row.get("date")) >= horizons[str(row["ticker"])]
        )
    return rows


def _load_adjustment_seeds_by_horizon(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    price_contract: ArtifactContract,
    *,
    evidence: Mapping[str, Any] | None,
    horizons: Mapping[str, str],
    entry_partition_sha256: Mapping[str, str | None],
) -> tuple[dict[str, AdjustmentSeed], dict[str, str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for ticker, before_date in horizons.items():
        grouped[before_date].add(ticker)
    seeds: dict[str, AdjustmentSeed] = {}
    seed_dates: dict[str, str] = {}
    for before_date, tickers in sorted(grouped.items()):
        seeds.update(
            _load_adjustment_seeds(
                context,
                manifest,
                price_contract=price_contract,
                evidence=evidence,
                tickers=tickers,
                before_date=before_date,
                entry_partition_sha256=entry_partition_sha256,
                seed_dates=seed_dates,
            )
        )
    return seeds, seed_dates


def _prove_new_series_by_horizon(
    context: DataAnalystsContext,
    *,
    horizons: Mapping[str, str],
    price_manifest: Mapping[str, Any] | None,
    price_evidence: Mapping[str, Any] | None,
    event_manifests: Mapping[str, Mapping[str, Any] | None],
    event_path_overrides: Mapping[str, Path],
    entry_partition_sha256: Mapping[str, str | None],
    contracts: Mapping[str, ArtifactContract],
) -> set[str]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for ticker, before_date in horizons.items():
        grouped[before_date].add(ticker)
    proven: set[str] = set()
    for before_date, tickers in sorted(grouped.items()):
        proven.update(
            _prove_new_series_tickers(
                context,
                tickers=tickers,
                before_date=before_date,
                price_manifest=price_manifest,
                price_evidence=price_evidence,
                event_manifests=event_manifests,
                event_path_overrides=event_path_overrides,
                entry_partition_sha256=entry_partition_sha256,
                contracts=contracts,
            )
        )
    return proven


def _load_prospective_events(
    context: DataAnalystsContext,
    manifests: Mapping[str, Mapping[str, Any] | None],
    path_overrides: Mapping[str, Path],
    *,
    horizons: Mapping[str, str],
    ending_dates: Mapping[str, str],
    entry_partition_sha256: Mapping[str, str | None],
    exclusive_lower_bound_tickers: set[str],
    contracts: Mapping[str, ArtifactContract],
) -> dict[str, list[dict[str, Any]]]:
    output = {artifact_id: [] for artifact_id in _OFFICIAL_EVENT_IDS}
    if not horizons:
        return output
    minimum_date = min(horizons.values())
    maximum_date = max(ending_dates.values())
    tickers = sorted(horizons)
    for artifact_id in _OFFICIAL_EVENT_IDS:
        manifest = manifests.get(artifact_id)
        if manifest is None:
            continue
        paths = _validated_event_paths(
            context,
            manifest,
            contracts[artifact_id],
            path_overrides=path_overrides,
        )
        if paths is None:
            raise AdjustmentError(f"invalid prospective event manifest: {artifact_id}")
        columns = list(_EVENT_REQUIRED_COLUMNS[artifact_id])
        for artifact_path in paths:
            year = _bound_event_path_year(
                manifest, contracts[artifact_id], artifact_path
            )
            if year < int(minimum_date[:4]) or year > int(maximum_date[:4]):
                continue
            target = path_overrides.get(
                artifact_path, context.artifact_path(artifact_path)
            )
            if artifact_path not in path_overrides:
                _assert_entry_partition_unchanged(
                    context, artifact_path, entry_partition_sha256
                )
            rows = pq.read_table(
                target,
                columns=columns,
                filters=[
                    ("ticker", "in", tickers),
                    ("event_date", ">=", minimum_date),
                    ("event_date", "<=", maximum_date),
                ],
            ).to_pylist()
            if artifact_path not in path_overrides:
                _assert_entry_partition_unchanged(
                    context, artifact_path, entry_partition_sha256
                )
            for row in rows:
                ticker = str(row.get("ticker"))
                event_date = str(row.get("event_date"))
                if ticker not in horizons or event_date > ending_dates[ticker]:
                    continue
                lower_bound = horizons[ticker]
                if ticker in exclusive_lower_bound_tickers:
                    if event_date <= lower_bound:
                        continue
                elif event_date < lower_bound:
                    continue
                output[artifact_id].append(row)
    return output


def _retarget_events_for_calculation(
    events: list[dict[str, Any]], price_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    first_dates = _ticker_horizons(price_rows, date_field="date")
    retargeted: list[dict[str, Any]] = []
    for event in events:
        ticker = str(event["ticker"])
        first_date = first_dates.get(ticker)
        if first_date is None:
            continue
        output = dict(event)
        if str(output["event_date"]) < first_date:
            output["event_date"] = first_date
        retargeted.append(output)
    return retargeted


def _load_adjustment_seeds(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    *,
    price_contract: ArtifactContract,
    evidence: Mapping[str, Any] | None | object = _LOAD_FORMAL_EVIDENCE,
    tickers: set[str],
    before_date: str,
    entry_partition_sha256: Mapping[str, str | None] | None = None,
    seed_dates: dict[str, str] | None = None,
) -> dict[str, AdjustmentSeed]:
    if not tickers:
        return {}
    evidence = (
        _verified_formal_evidence(context, manifest, price_contract)
        if evidence is _LOAD_FORMAL_EVIDENCE
        else _validated_formal_evidence(manifest, evidence, price_contract)
    )
    if evidence is None:
        return {}
    records = {
        record["artifact_path"]: record
        for record in evidence["partitions"]
        if isinstance(record, Mapping) and isinstance(record.get("artifact_path"), str)
    }
    paths = _price_paths_before_date(manifest, before_date, price_contract)
    seeds: dict[str, tuple[str, AdjustmentSeed]] = {}
    for artifact_path in reversed(paths):
        remaining = tickers.difference(seeds)
        if not remaining:
            break
        record = records.get(artifact_path)
        target = context.artifact_path(artifact_path)
        if entry_partition_sha256 is not None:
            _assert_entry_partition_unchanged(
                context, artifact_path, entry_partition_sha256
            )
        source_sha256 = (
            entry_partition_sha256.get(artifact_path)
            if entry_partition_sha256 is not None
            else _sha256(target)
        )
        if (
            not isinstance(record, Mapping)
            or record.get("status") != "ready"
            or record.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
            or record.get("content_sha256") != source_sha256
        ):
            continue
        rows = pq.read_table(
            target,
            columns=["date", "ticker", "adj_factor", "close"],
            filters=[
                ("ticker", "in", sorted(remaining)),
                ("date", "<", before_date),
            ],
        ).to_pylist()
        if entry_partition_sha256 is not None:
            _assert_entry_partition_unchanged(
                context, artifact_path, entry_partition_sha256
            )
        for row in rows:
            row_date = str(row.get("date"))
            ticker = str(row.get("ticker"))
            if row_date >= before_date or ticker not in remaining:
                continue
            current = seeds.get(ticker)
            if current is not None and current[0] >= row_date:
                continue
            factor = _positive_finite(row.get("adj_factor"))
            close = _positive_finite(row.get("close"), allow_none=True)
            if factor is None:
                raise AdjustmentError(
                    f"verified adjustment seed has invalid factor: {ticker} {row_date}"
                )
            seeds[ticker] = (
                row_date,
                AdjustmentSeed(adj_factor=factor, previous_close=close),
            )
    if seed_dates is not None:
        seed_dates.update({ticker: row_date for ticker, (row_date, _) in seeds.items()})
    return {ticker: seed for ticker, (_, seed) in seeds.items()}


def _prove_new_series_tickers(
    context: DataAnalystsContext,
    *,
    tickers: set[str],
    before_date: str,
    price_manifest: Mapping[str, Any] | None = None,
    price_evidence: Mapping[str, Any] | None = None,
    event_manifests: Mapping[str, Mapping[str, Any] | None] | None = None,
    event_path_overrides: Mapping[str, Path] | None = None,
    entry_partition_sha256: Mapping[str, str | None] | None = None,
    contracts: Mapping[str, ArtifactContract] | None = None,
) -> set[str]:
    remaining = set(tickers)
    if not remaining:
        return set()

    captured_metadata = event_manifests is not None
    if not captured_metadata:
        price_manifest = _load_json_if_present(
            context.store_path(*_DAILY_PRICE_MANIFEST_PATH.split("/"))
        )
        price_evidence = None
    if price_manifest is not None:
        if contracts is None:
            raise AdjustmentError("new series proof requires artifact contracts")
        price_contract = contracts["daily_price_volume"]
        evidence = (
            _validated_formal_evidence(price_manifest, price_evidence, price_contract)
            if captured_metadata
            else _verified_formal_evidence(context, price_manifest, price_contract)
        )
        if evidence is None:
            return set()
        price_paths = _price_paths_before_date(
            price_manifest, before_date, price_contract
        )
        records_by_path = {
            record["artifact_path"]: record
            for record in evidence["partitions"]
        }
        if entry_partition_sha256 is None and any(
            records_by_path[path]["content_sha256"]
            != _sha256(context.artifact_path(path))
            for path in price_paths
        ):
            return set()
        remaining.difference_update(
            _tickers_with_prior_rows(
                context,
                price_paths,
                tickers=remaining,
                date_column="date",
                before_date=before_date,
                entry_partition_sha256=entry_partition_sha256,
                expected_sha256={
                    path: records_by_path[path]["content_sha256"]
                    for path in price_paths
                },
            )
        )

    for artifact_id in _OFFICIAL_EVENT_IDS:
        if not remaining:
            break
        manifest = (
            event_manifests.get(artifact_id)
            if event_manifests is not None
            else _load_json_if_present(
                context.store_path("manifests", f"{artifact_id}.json")
            )
        )
        if contracts is None:
            raise AdjustmentError("new-series proof requires formal artifact contracts")
        event_contract = contracts[artifact_id]
        event_root = context.artifact_path(event_contract.base_path)
        if manifest is None:
            inventory_patterns = (
                event_contract.inventory_glob(),
                event_contract.legacy_inventory_glob(),
            )
            if event_root.exists() and any(
                path.is_file()
                for pattern in inventory_patterns
                for path in event_root.glob(
                    pattern.removeprefix(f"{event_contract.base_path}/")
                )
            ):
                return set()
            continue
        paths = _validated_event_paths(
            context,
            manifest,
            event_contract,
            path_overrides=event_path_overrides,
        )
        if paths is None:
            return set()
        before_year = int(before_date[:4])
        paths = [
            path
            for path in paths
            if _bound_event_path_year(manifest, event_contract, path) <= before_year
        ]
        remaining.difference_update(
            _tickers_with_prior_rows(
                context,
                paths,
                tickers=remaining,
                date_column="event_date",
                before_date=before_date,
                path_overrides=event_path_overrides,
                entry_partition_sha256=entry_partition_sha256,
            )
        )
    return remaining


def _build_daily_price_manifest(
    context: DataAnalystsContext,
    price_contract: ArtifactContract,
    current_manifest: Mapping[str, Any] | None,
    previous_evidence: Mapping[str, Any] | None,
    staged_partitions: list[StagedPartition],
    adjusted_rows: list[dict[str, Any]],
    *,
    full_rebuild: bool,
    active_version: str | None = None,
    superseded_paths: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    staged_by_path = {
        partition.artifact_path: partition for partition in staged_partitions
    }
    path_year = lambda path: _contract_partition_year(
        price_contract, path, active_version=active_version
    )
    if full_rebuild:
        artifact_paths = sorted(staged_by_path, key=path_year)
    else:
        existing_paths = (
            current_manifest.get("artifact_paths", [])
            if isinstance(current_manifest, Mapping)
            else []
        )
        artifact_paths = sorted(
            {str(path) for path in existing_paths}.union(staged_by_path),
            key=path_year,
        )

    evidence_records = {
        str(record.get("artifact_path")): record
        for record in (
            previous_evidence.get("partitions", [])
            if isinstance(previous_evidence, Mapping)
            else []
        )
        if isinstance(record, Mapping)
    }
    row_count = 0
    date_ranges: list[tuple[str, str]] = []
    artifact_fingerprints: list[dict[str, str]] = []
    for artifact_path in artifact_paths:
        staged = staged_by_path.get(artifact_path)
        if staged is not None:
            row_count += staged.row_count
            if staged.date_range is not None:
                date_ranges.append(staged.date_range)
            artifact_fingerprints.append(
                {
                    "artifact_path": artifact_path,
                    "sha256": staged.content_sha256,
                }
            )
            continue
        record = evidence_records.get(artifact_path)
        if (
            not isinstance(record, Mapping)
            or type(record.get("row_count")) is not int
            or not isinstance(record.get("date_range"), list)
            or len(record["date_range"]) != 2
            or not isinstance(record.get("content_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", record["content_sha256"]) is None
        ):
            raise AdjustmentError(
                f"missing verified manifest aggregate for {artifact_path}"
            )
        row_count += record["row_count"]
        date_ranges.append((str(record["date_range"][0]), str(record["date_range"][1])))
        artifact_fingerprints.append(
            {
                "artifact_path": artifact_path,
                "sha256": record["content_sha256"],
            }
        )

    old_columns = (
        current_manifest.get("columns", [])
        if isinstance(current_manifest, Mapping)
        else []
    )
    columns = list(
        dict.fromkeys(
            [
                *(str(column) for column in old_columns),
                *(column for row in adjusted_rows for column in row),
            ]
        )
    )
    old_collections = (
        current_manifest.get("source_collections", [])
        if isinstance(current_manifest, Mapping)
        else []
    )
    source_collections = sorted(
        {str(value) for value in old_collections}.union(
            str(row["source_collection"])
            for row in adjusted_rows
            if row.get("source_collection")
        )
    )
    minimum_date = min(item[0] for item in date_ranges)
    maximum_date = max(item[1] for item in date_ranges)
    cutoff_candidates = (
        [_max_cutoff(adjusted_rows)] if adjusted_rows else []
    )
    if isinstance(current_manifest, Mapping) and current_manifest.get(
        "data_cutoff_at"
    ):
        cutoff_candidates.append(str(current_manifest["data_cutoff_at"]))
    if not cutoff_candidates:
        raise ValueError("daily price manifest requires a real data_cutoff_at")
    data_cutoff_at = max(cutoff_candidates)
    inventory_schema = (
        _inventory_schema(context, artifact_paths, staged_by_path)
        if active_version is not None
        else None
    )
    if inventory_schema is not None:
        columns = list(inventory_schema.names)
    manifest_payload = build_manifest_payload(
        context,
        artifact_id="daily_price_volume",
        layer="raw",
        source_families=["daily_price_volume"],
        source_collections=source_collections,
        columns=columns,
        artifact_paths=artifact_paths,
        row_count=row_count,
        date_range=[minimum_date, maximum_date],
        availability_date_range=[minimum_date, maximum_date],
        partitioning=["year"],
        pit_policy="source_date_lagged_to_decision_date",
        data_cutoff_at=data_cutoff_at,
        duplicate_count=0,
        omitted_row_count=0,
        status="ready",
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        extension_fields={"adjustment_policy_id": ADJUSTMENT_POLICY_ID},
    )
    manifest_payload["schema_version"] = "1.1"
    manifest_payload["artifact_fingerprints"] = artifact_fingerprints
    if active_version is not None:
        manifest_payload.update(
            {
                "active_version": active_version,
                "contract_key": "daily_price_volume",
                "variant": "default",
            }
        )
    if superseded_paths:
        manifest_payload["superseded_paths"] = superseded_paths
    if inventory_schema is not None:
        manifest_payload["schema_fingerprint"] = hashlib.sha256(
            inventory_schema.serialize().to_pybytes()
        ).hexdigest()
    validate_manifest_fingerprint_structure(manifest_payload)
    return manifest_payload


def _build_event_manifest(
    context: DataAnalystsContext,
    artifact_id: str,
    contract: ArtifactContract,
    current_manifest: Mapping[str, Any] | None,
    staged_partitions: list[StagedPartition],
    incoming_rows: list[dict[str, Any]],
    *,
    full_rebuild: bool,
    active_version: str | None = None,
    superseded_paths: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    staged_by_path = {
        partition.artifact_path: partition for partition in staged_partitions
    }
    existing_paths = (
        []
        if full_rebuild or not isinstance(current_manifest, Mapping)
        else [str(path) for path in current_manifest.get("artifact_paths", [])]
    )
    artifact_paths = sorted(
        set(existing_paths).union(staged_by_path),
        key=lambda path: _contract_partition_year(
            contract, path, active_version=active_version
        ),
    )
    if full_rebuild or not isinstance(current_manifest, Mapping):
        row_count = sum(partition.row_count for partition in staged_partitions)
        date_ranges = [
            partition.date_range
            for partition in staged_partitions
            if partition.date_range is not None
        ]
    else:
        old_row_count = current_manifest.get("row_count")
        old_date_range = current_manifest.get("date_range")
        if type(old_row_count) is not int:
            raise AdjustmentError(f"invalid formal event manifest: {artifact_id}")
        row_count = old_row_count + sum(
            partition.row_count - (partition.source_row_count or 0)
            for partition in staged_partitions
        )
        date_ranges = [
            tuple(old_date_range)
            if isinstance(old_date_range, list) and len(old_date_range) == 2
            else None,
            *(
                partition.date_range
                for partition in staged_partitions
                if partition.date_range is not None
            ),
        ]
        date_ranges = [item for item in date_ranges if item is not None]

    old_columns = (
        current_manifest.get("columns", [])
        if isinstance(current_manifest, Mapping)
        else []
    )
    columns = list(
        dict.fromkeys(
            [
                *(str(column) for column in old_columns),
                *_EVENT_REQUIRED_COLUMNS[artifact_id],
                *(column for row in incoming_rows for column in row),
            ]
        )
    )
    minimum_date = min((item[0] for item in date_ranges), default=None)
    maximum_date = max((item[1] for item in date_ranges), default=None)
    inventory_schema = (
        _inventory_schema(context, artifact_paths, staged_by_path)
        if active_version is not None
        else None
    )
    if inventory_schema is not None:
        columns = list(inventory_schema.names)
    manifest = build_manifest_payload(
        context,
        artifact_id=artifact_id,
        layer="derived",
        source_families=[
            "dividend_policy"
            if artifact_id == "dividend_events"
            else "capital_formation"
        ],
        source_collections=list(
            current_manifest.get("source_collections", [])
            if isinstance(current_manifest, Mapping)
            else []
        ),
        columns=columns,
        artifact_paths=artifact_paths,
        row_count=row_count,
        date_range=(
            [minimum_date, maximum_date]
            if minimum_date is not None and maximum_date is not None
            else None
        ),
        availability_date_range=(
            [minimum_date, maximum_date]
            if minimum_date is not None and maximum_date is not None
            else None
        ),
        partitioning=["event_year"],
        pit_policy="event_date",
        data_cutoff_at=(
            max(
                [
                    *(
                        [_max_cutoff(incoming_rows)] if incoming_rows else []
                    ),
                    *(
                        [str(current_manifest["data_cutoff_at"])]
                        if isinstance(current_manifest, Mapping)
                        and not full_rebuild
                        and current_manifest.get("data_cutoff_at")
                        else []
                    ),
                ]
            )
            if incoming_rows
            or (
                isinstance(current_manifest, Mapping)
                and not full_rebuild
                and current_manifest.get("data_cutoff_at")
            )
            else None
        ),
        duplicate_count=0,
        omitted_row_count=0,
        status="ready",
        created_at=(
            str(current_manifest.get("created_at"))
            if isinstance(current_manifest, Mapping)
            and current_manifest.get("created_at")
            else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ),
    )
    if active_version is not None:
        manifest.update(
            {
                "active_version": active_version,
                "contract_key": artifact_id,
                "variant": "default",
            }
        )
    if superseded_paths:
        manifest["superseded_paths"] = superseded_paths
    if inventory_schema is not None:
        manifest["schema_fingerprint"] = hashlib.sha256(
            inventory_schema.serialize().to_pybytes()
        ).hexdigest()
    return manifest


def _inventory_schema(
    context: DataAnalystsContext,
    artifact_paths: list[str],
    staged_by_path: Mapping[str, StagedPartition],
) -> pa.Schema:
    schema: pa.Schema | None = None
    for artifact_path in artifact_paths:
        staged = staged_by_path.get(artifact_path)
        path = staged.staged_path if staged is not None else context.artifact_path(
            artifact_path
        )
        current = pq.read_schema(path)
        if schema is None:
            schema = current
        elif not current.equals(schema, check_metadata=False):
            raise AdjustmentError(
                f"schema mismatch across formal partitions: {artifact_path}"
            )
    if schema is None:
        raise AdjustmentError("formal partition inventory has no schema")
    return schema


def _formal_metadata_snapshot(
    context: DataAnalystsContext,
) -> dict[str, FormalMetadataSnapshot]:
    paths = [
        _DAILY_PRICE_MANIFEST_PATH,
        _ADJUSTED_OHLC_EVIDENCE_PATH,
        *(f"manifests/{artifact_id}.json" for artifact_id in _OFFICIAL_EVENT_IDS),
    ]
    snapshots: dict[str, FormalMetadataSnapshot] = {}
    for path in paths:
        target = context.artifact_path(path)
        if not target.is_file():
            snapshots[path] = FormalMetadataSnapshot(sha256=None, payload=None)
            continue
        with target.open("rb") as handle:
            content = handle.read()
        payload = json.loads(content)
        if not isinstance(payload, Mapping):
            raise AdjustmentError(f"formal metadata must be a JSON object: {path}")
        snapshots[path] = FormalMetadataSnapshot(
            sha256=hashlib.sha256(content).hexdigest(), payload=payload
        )
    return snapshots


def _daily_price_source_preconditions(
    context: DataAnalystsContext,
    prospective_evidence: Mapping[str, Any],
    staged_partitions: list[StagedPartition],
    *,
    changed_paths: set[str],
    changed_event_paths: set[str],
    metadata_snapshot: Mapping[str, FormalMetadataSnapshot],
    entry_partition_sha256: Mapping[str, str | None] | None = None,
) -> dict[str, str | None]:
    preconditions = {
        path: snapshot.sha256 for path, snapshot in metadata_snapshot.items()
    }
    if entry_partition_sha256 is not None:
        preconditions.update(entry_partition_sha256)
    for partition in staged_partitions:
        if partition.source_exists is None:
            preconditions[partition.artifact_path] = _sha256_if_present(
                context.artifact_path(partition.artifact_path)
            )
    for record in prospective_evidence.get("partitions", []):
        artifact_path = record.get("artifact_path")
        if artifact_path not in changed_paths:
            preconditions[str(artifact_path)] = str(record.get("content_sha256"))
    dependencies = prospective_evidence.get("event_dependencies", {})
    for artifact_id in _OFFICIAL_EVENT_IDS:
        dependency = dependencies.get(artifact_id, {})
        for record in dependency.get("partitions", []):
            artifact_path = str(record["artifact_path"])
            if artifact_path not in changed_event_paths:
                preconditions[artifact_path] = str(record["content_sha256"])
    return preconditions


def _verified_formal_evidence(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    price_contract: ArtifactContract,
) -> dict[str, Any] | None:
    evidence = _load_json_if_present(
        context.store_path(*_ADJUSTED_OHLC_EVIDENCE_PATH.split("/"))
    )
    return _validated_formal_evidence(manifest, evidence, price_contract)


def _validated_formal_evidence(
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    price_contract: ArtifactContract,
) -> dict[str, Any] | None:
    if (
        evidence is None
        or evidence.get("status") != "ready"
        or evidence.get("artifact_id") != "daily_price_volume"
        or evidence.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
        or manifest.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
        or evidence.get("manifest_fingerprint") != manifest_fingerprint(manifest)
    ):
        return None
    manifest_paths = manifest.get("artifact_paths")
    records = evidence.get("partitions")
    if not isinstance(manifest_paths, list) or not isinstance(records, list):
        return None
    ordered_paths = sorted(
        (str(path) for path in manifest_paths),
        key=lambda path: _contract_partition_year(
            price_contract, path, active_version=manifest.get("active_version")
        ),
    )
    if len(records) != len(ordered_paths) or [
        record.get("artifact_path")
        for record in records
        if isinstance(record, Mapping)
    ] != ordered_paths:
        return None
    if any(
        not isinstance(record, Mapping)
        or record.get("status") != "ready"
        or record.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID
        or not isinstance(record.get("violation_counts"), Mapping)
        or any(record["violation_counts"].values())
        or not isinstance(record.get("content_sha256"), str)
        for record in records
    ):
        return None
    return evidence


def _price_paths_before_date(
    manifest: Mapping[str, Any],
    before_date: str,
    price_contract: ArtifactContract,
) -> list[str]:
    before_year = int(before_date[:4])
    paths = [str(path) for path in manifest.get("artifact_paths", [])]
    return sorted(
        [
            path
            for path in paths
            if _contract_partition_year(
                price_contract,
                path,
                active_version=manifest.get("active_version"),
            )
            <= before_year
        ],
        key=lambda path: _contract_partition_year(
            price_contract, path, active_version=manifest.get("active_version")
        ),
    )


def _tickers_with_prior_rows(
    context: DataAnalystsContext,
    paths: list[str],
    *,
    tickers: set[str],
    date_column: str,
    before_date: str,
    path_overrides: Mapping[str, Path] | None = None,
    entry_partition_sha256: Mapping[str, str | None] | None = None,
    expected_sha256: Mapping[str, str] | None = None,
) -> set[str]:
    found: set[str] = set()
    for artifact_path in paths:
        remaining = tickers.difference(found)
        if not remaining:
            break
        target = (
            path_overrides[artifact_path]
            if path_overrides is not None and artifact_path in path_overrides
            else context.artifact_path(artifact_path)
        )
        if entry_partition_sha256 is not None and not (
            path_overrides is not None and artifact_path in path_overrides
        ):
            _assert_entry_partition_unchanged(
                context, artifact_path, entry_partition_sha256
            )
            if (
                expected_sha256 is not None
                and expected_sha256.get(artifact_path)
                != entry_partition_sha256.get(artifact_path)
            ):
                raise PublishTransactionError(
                    f"entry source does not match ready evidence: {artifact_path}"
                )
        rows = pq.read_table(
            target,
            columns=[date_column, "ticker"],
            filters=[
                ("ticker", "in", sorted(remaining)),
                (date_column, "<", before_date),
            ],
        ).to_pylist()
        if entry_partition_sha256 is not None and not (
            path_overrides is not None and artifact_path in path_overrides
        ):
            _assert_entry_partition_unchanged(
                context, artifact_path, entry_partition_sha256
            )
        found.update(
            str(row["ticker"])
            for row in rows
            if row.get("ticker") in remaining
            and str(row.get(date_column)) < before_date
        )
    return found


def _validated_event_paths(
    context: DataAnalystsContext,
    manifest: Mapping[str, Any],
    contract: ArtifactContract,
    *,
    path_overrides: Mapping[str, Path] | None = None,
) -> list[str] | None:
    artifact_id = contract.artifact_id
    active_version = manifest.get("active_version")
    if (
        manifest.get("artifact_id") != artifact_id
        or manifest.get("schema_version") not in {"1.0", "1.1"}
        or manifest.get("status") != "ready"
        or not isinstance(manifest.get("artifact_paths"), list)
    ):
        return None
    paths: list[str] = []
    for raw_path in manifest["artifact_paths"]:
        if not isinstance(raw_path, str):
            return None
        path = context.validate_artifact_path(raw_path)
        try:
            _contract_partition_year(
                contract, path, active_version=active_version
            )
        except Exception:
            return None
        target = (
            path_overrides[path]
            if path_overrides is not None and path in path_overrides
            else context.artifact_path(path)
        )
        if not target.is_file():
            return None
        paths.append(path)
    return sorted(paths)


def _staged_transaction_root(
    context: DataAnalystsContext, staged_partitions: list[StagedPartition]
) -> Path:
    if not staged_partitions:
        raise AdjustmentError("daily price publish staged no partitions")
    staging_base = context.store_path("jobs", ".publish-staging")
    roots = {
        staging_base / path.relative_to(staging_base).parts[0]
        for path in (partition.staged_path for partition in staged_partitions)
    }
    if len(roots) != 1:
        raise AdjustmentError("daily price partitions use multiple transactions")
    return roots.pop()


def _bound_event_path_year(
    manifest: Mapping[str, Any],
    contract: ArtifactContract,
    path: str,
) -> int:
    try:
        return _contract_partition_year(
            contract, path, active_version=manifest.get("active_version")
        )
    except Exception as exc:
        raise AdjustmentError(f"invalid event partition path: {path}") from exc


def _load_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdjustmentError(f"cannot load formal JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise AdjustmentError(f"formal JSON must be an object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_if_present(path: Path) -> str | None:
    return _sha256(path) if path.exists() else None


def _positive_finite(value: Any, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0.0 else None


def _max_cutoff(rows: list[dict[str, Any]]) -> str:
    cutoffs = [str(row.get("data_cutoff_at")) for row in rows if row.get("data_cutoff_at")]
    if not cutoffs:
        raise ValueError("published rows require a real data_cutoff_at")
    return max(cutoffs)
