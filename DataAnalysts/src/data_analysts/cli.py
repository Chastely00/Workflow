from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Sequence

from data_analysts.artifacts import atomic_write_text, repair_manifest_fingerprints
from data_analysts.adjusted_ohlc_evidence import (
    audit_adjusted_ohlc,
    promote_audit_candidate,
    write_candidate_audit,
)
from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.daily import (
    DailyRefreshError,
    plan_daily_refresh_dates,
    write_daily_refresh_blocked,
    write_daily_refresh_success,
)
from data_analysts.inspect import inspect_artifacts
from data_analysts.metadata import (
    load_audit_runtime_config,
    publish_data_store_metadata,
    repair_data_store_metadata_paths,
)
from data_analysts.paths import DataAnalystsContext, PathBoundaryError
from data_analysts.pipeline import run_pipeline
from data_analysts.artifact_contracts import expected_contract_outputs
from data_analysts.run_transaction import FormalStoreTransaction
from data_analysts.store_audit import audit_store
from data_analysts.dataset_publication import archive_superseded_paths
from data_analysts.verify import verify_runtime


def main(argv: Sequence[str] | None = None) -> int:
    rejected_message = _reject_removed_root(argv)
    if rejected_message is not None:
        print(rejected_message, file=sys.stderr)
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        context = DataAnalystsContext.from_paths(args.project_root, args.data_store)
        if args.command == "verify":
            result = verify_runtime(context, getattr(args, "as_of_date", None))
            if result["status"] == "ready":
                print("ready")
                return 0
            print(result["message"], file=sys.stderr)
            return 1
        if args.command == "inspect-artifacts":
            result = inspect_artifacts(context, getattr(args, "as_of_date", None))
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "ready" else 1

        if args.command == "repair-metadata":
            repair_data_store_metadata_paths(context)
            print("ready")
            return 0
        if args.command == "repair-manifest-fingerprints":
            repair_manifest_fingerprints(context, args.artifact_ids)
            print("ready")
            return 0

        if args.command == "certify-adjusted-ohlc":
            config = load_runtime_config(context)
            if args.publish_candidate:
                result = promote_audit_candidate(context, config.artifact_contracts)
            else:
                manifest_path = context.store_path(
                    "manifests", "daily_price_volume.json"
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("daily_price_volume manifest must be a JSON object")
                result = audit_adjusted_ohlc(
                    context,
                    manifest,
                    contracts=config.artifact_contracts,
                    mode=args.mode,
                )
                write_candidate_audit(context, result)
            print(result["status"])
            return 0 if result["status"] == "ready" else 1

        audit_config_evidence = None
        if args.command == "audit-store" and context.store_path(
            "metadata", "data_store_manifest.json"
        ).exists():
            config, audit_config_evidence = load_audit_runtime_config(context)
        else:
            config = load_runtime_config(context)
        _validate_date_range(args)
        _validate_families(args, config.family_ids)
        if args.command == "audit-store":
            result = audit_store(context, config.artifact_contracts)
            if audit_config_evidence is not None:
                result["config_registry_evidence"] = audit_config_evidence
                if not audit_config_evidence["active_snapshot_complete"]:
                    result["status"] = "blocked"
                    result["issues"].append(
                        {
                            "kind": "legacy_config_snapshot",
                            "artifact_id": "<config>",
                            "path": audit_config_evidence["active_snapshot_path"],
                            "message": "active config snapshot is incomplete; audit used project registry fallback",
                        }
                    )
                    result["metrics"]["artifact_issue_count"] = len(result["issues"])
            if args.output:
                _write_audit_result(context, args.output, result)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "ready" else 1
        if args.command == "archive-superseded":
            contract = config.artifact_contracts.get(args.contract_key)
            if contract is None:
                raise ValueError(f"unknown contract key: {args.contract_key}")
            receipt = archive_superseded_paths(
                context, contract,
                expected_manifest_sha256=args.expected_manifest_sha256,
                confirm_no_legacy_readers=args.confirm_no_legacy_readers,
            )
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0
        scope = {
            "run-full-history": "full_history",
            "run-backfill": "bounded_backfill",
            "run-daily": "daily",
        }.get(args.command)

        if args.command == "run-daily":
            try:
                daily_dates = plan_daily_refresh_dates(
                    context,
                    config.artifact_contracts["trading_calendar"],
                    as_of_date=args.as_of_date,
                    from_date=args.from_date,
                    to_date=args.to_date,
                )
            except Exception as exc:
                write_daily_refresh_blocked(
                    context.data_store,
                    as_of_date=None,
                    message=str(exc),
                )
                _write_blocked_pipeline_result(context, str(exc))
                print(str(exc), file=sys.stderr)
                return 1
            if not daily_dates:
                print("ready no-op")
                return 0
            attempted_date: str | None = None
            try:
                latest_result: dict[str, object] | None = None
                for daily_date in daily_dates:
                    attempted_date = daily_date
                    pre_audit = audit_store(context, config.artifact_contracts)
                    with FormalStoreTransaction(context) as transaction:
                        result = run_pipeline(
                            context,
                            config,
                            families=None,
                            start_date=None,
                            end_date=None,
                            as_of_date=daily_date,
                            run_scope=scope,
                            publish_ready_state=False,
                            pre_publication_audit=pre_audit,
                        )
                        verification = verify_runtime(
                            context,
                            daily_date,
                            pre_publication_audit=pre_audit,
                            run_scope=scope,
                        )
                        if verification["status"] != "ready":
                            raise DailyRefreshError(verification["message"])
                        result = _verified_pipeline_result(context, result)
                        transaction.commit()
                    write_daily_refresh_success(context.data_store, as_of_date=daily_date, result=result)
                    latest_result = result
            except Exception as exc:
                write_daily_refresh_blocked(
                    context.data_store,
                    as_of_date=attempted_date,
                    message=str(exc),
                )
                _write_blocked_pipeline_result(context, str(exc))
                print(str(exc), file=sys.stderr)
                return 1
            print((latest_result or {"status": "ready"})["status"])
            return 0

        if args.command in {"run-full-history", "run-backfill"}:
            try:
                selected_families = _parse_families(getattr(args, "families", None))
                audit_contract_keys = _expected_contract_keys(config, selected_families)
                pre_audit = audit_store(
                    context,
                    config.artifact_contracts,
                    contract_keys=audit_contract_keys,
                )
                with FormalStoreTransaction(context) as transaction:
                    result = run_pipeline(
                        context,
                        config,
                        families=selected_families,
                        start_date=getattr(args, "start_date", None),
                        end_date=getattr(args, "end_date", None),
                        as_of_date=getattr(args, "as_of_date", None),
                        run_scope=scope,
                        publish_ready_state=False,
                        pre_publication_audit=pre_audit,
                    )
                    verification = verify_runtime(
                        context,
                        getattr(args, "as_of_date", None),
                        pre_publication_audit=pre_audit,
                        run_scope=scope,
                        audit_contract_keys=audit_contract_keys,
                    )
                    if verification["status"] != "ready":
                        raise ValueError(verification["message"])
                    result = _verified_pipeline_result(context, result)
                    transaction.commit()
            except Exception as exc:
                _write_blocked_pipeline_result(context, str(exc))
                print(str(exc), file=sys.stderr)
                return 1
            print(result["status"])
            return 0
        parser.error(f"unsupported command: {args.command}")
        return 2
    except (ConfigError, FileNotFoundError, PathBoundaryError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _verified_pipeline_result(
    context: DataAnalystsContext, fallback: dict[str, object]
) -> dict[str, object]:
    path = context.store_path("jobs", "pipeline_result.json")
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    # Test/dry adapter compatibility: this branch is reached only after the
    # verification dependency explicitly returned ready.
    return {**fallback, "status": "ready", "phase": "complete"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data_analysts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    full_history = subparsers.add_parser("run-full-history")
    _add_project_and_store(full_history)
    _add_family_arg(full_history)

    backfill = subparsers.add_parser("run-backfill")
    _add_project_and_store(backfill)
    _add_family_arg(backfill)
    _add_date_range(backfill)

    daily = subparsers.add_parser("run-daily")
    _add_project_and_store(daily)
    daily.add_argument("--as-of-date")
    daily.add_argument("--from-date")
    daily.add_argument("--to-date")

    verify = subparsers.add_parser("verify")
    _add_project_and_store(verify)
    verify.add_argument("--as-of-date")

    inspect = subparsers.add_parser("inspect-artifacts")
    _add_project_and_store(inspect)
    inspect.add_argument("--as-of-date")

    repair_metadata = subparsers.add_parser("repair-metadata")
    _add_project_and_store(repair_metadata)

    repair_fingerprints = subparsers.add_parser("repair-manifest-fingerprints")
    _add_project_and_store(repair_fingerprints)
    repair_fingerprints.add_argument(
        "--artifact-id",
        dest="artifact_ids",
        action="append",
        required=True,
    )

    certify = subparsers.add_parser("certify-adjusted-ohlc")
    _add_project_and_store(certify)
    mode = certify.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mode", choices=["full"])
    mode.add_argument("--publish-candidate", action="store_true")

    audit = subparsers.add_parser("audit-store")
    _add_project_and_store(audit)
    audit.add_argument("--output")

    archive = subparsers.add_parser("archive-superseded")
    _add_project_and_store(archive)
    archive.add_argument("--contract-key", required=True)
    archive.add_argument("--expected-manifest-sha256", required=True)
    archive.add_argument("--confirm-no-legacy-readers", action="store_true")

    return parser


def _reject_removed_root(argv: Sequence[str] | None) -> str | None:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if "--root" in raw_args or any(token.startswith("--root=") for token in raw_args):
        return "--root has been removed. Use --project-root and --data-store."
    return None


def _add_project_and_store(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--data-store")


def _add_family_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--families")


def _add_date_range(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")


def _validate_families(args: argparse.Namespace, known_family_ids: set[str]) -> None:
    requested = getattr(args, "families", None)
    if not requested:
        return
    for family_id in [item.strip() for item in requested.split(",") if item.strip()]:
        if family_id not in known_family_ids:
            raise ValueError(f"unknown family: {family_id}")


def _parse_families(requested: str | None) -> set[str] | None:
    if not requested:
        return None
    return {item.strip() for item in requested.split(",") if item.strip()}


def _expected_contract_keys(config, requested_families: set[str] | None) -> set[str]:
    selected = {
        str(family["family_id"])
        for family in config.source_family_profiles.get("families", [])
        if family.get("enabled", True) is not False
        and (not requested_families or family["family_id"] in requested_families)
    }
    matrix = expected_contract_outputs(config.artifact_contracts, selected)
    return {key for keys in matrix.values() for key in keys}


def _validate_date_range(args: argparse.Namespace) -> None:
    start = getattr(args, "start_date", None)
    end = getattr(args, "end_date", None)
    if start and end and date.fromisoformat(end) < date.fromisoformat(start):
        raise ValueError("end-date cannot be earlier than start-date")
    from_date = getattr(args, "from_date", None)
    to_date = getattr(args, "to_date", None)
    if from_date and to_date and date.fromisoformat(to_date) < date.fromisoformat(from_date):
        raise ValueError("to-date cannot be earlier than from-date")
    as_of_date = getattr(args, "as_of_date", None)
    if as_of_date and (from_date or to_date):
        raise ValueError("--as-of-date cannot be combined with --from-date or --to-date")


def _write_blocked_pipeline_result(context: DataAnalystsContext, message: str) -> None:
    pipeline_path = context.store_path("jobs", "pipeline_result.json")
    try:
        prior = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        prior = {}
    if not isinstance(prior, dict):
        prior = {}
    attestation = prior.get("run_attestation")
    if isinstance(attestation, dict):
        attestation = {**attestation, "status": "blocked"}
    payload = {**prior,
        "status": "blocked",
        "phase": "verify",
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "blocked_step": "pipeline",
        "message": message,
        "next_actions": ["fix source config or MongoDB availability, then rerun the same command"],
    }
    if isinstance(attestation, dict):
        payload["run_attestation"] = attestation
    _write_job_json(context, "pipeline_result.json", payload)
    current_path = context.store_path("jobs", "current_run.json")
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current.update(
        {
            "status": "blocked",
            "phase": "verify",
            "message": message,
            "error": message,
            "updated_at": payload["checked_at"],
        }
    )
    current_attestation = current.get("run_attestation")
    if isinstance(current_attestation, dict):
        current["run_attestation"] = {
            **current_attestation, "status": "blocked"
        }
    _write_job_json(context, "current_run.json", current)


def _write_ready_pipeline_state(
    context: DataAnalystsContext, result: dict[str, object]
) -> None:
    ready_result = {**result, "status": "ready"}
    _write_job_json(context, "pipeline_result.json", ready_result)
    current_path = context.store_path("jobs", "current_run.json")
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current.update(
        {
            "status": "ready",
            "phase": "complete",
            "current_family": None,
            "message": "pipeline ready after fresh verification",
            "updated_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    current.pop("error", None)
    _write_job_json(context, "current_run.json", current)


def _write_job_json(
    context: DataAnalystsContext, name: str, payload: dict[str, object]
) -> None:
    atomic_write_text(
        context.store_path("jobs", name), json.dumps(payload, indent=2, sort_keys=True)
    )


def _write_audit_result(
    context: DataAnalystsContext,
    relative_job_path: str,
    result: dict[str, object],
) -> None:
    normalized = relative_job_path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if (
        pure.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(":" in part or bool(PureWindowsPath(part).drive) for part in pure.parts)
    ):
        raise ValueError("--output must be a safe path relative to data_store/jobs")
    parts = pure.parts
    if parts[0] == "jobs":
        parts = parts[1:]
    elif len(parts) != 1:
        raise ValueError(
            "--output must be a bare filename or a safe path under data_store/jobs"
        )
    if not parts:
        raise ValueError(
            "--output must name a file under data_store/jobs"
        )
    if parts[0].casefold() == "jobs":
        raise ValueError("--output must not repeat the data_store/jobs prefix")
    target = context.store_path("jobs", *parts)
    atomic_write_text(target, json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
