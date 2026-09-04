"""Materialize read-only fold evidence for already-published Tier 1 OOF artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.lab import Tier1Lab
from etf_tricks.tier1.splits import chronological_purged_folds, fold_audit_records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--feature-extension-root", required=True)
    parser.add_argument("--oof-root", action="append", required=True)
    parser.add_argument("--output-root", required=True)
    return parser.parse_args()


def _oof_manifests(roots: list[str]) -> list[Path]:
    manifests: list[Path] = []
    for root_text in roots:
        root = Path(root_text)
        found = sorted(root.glob("*/oof/manifest.json"))
        if not found:
            raise ValueError(f"OOF root has no ETF-local manifests: {root}")
        manifests.extend(found)
    return manifests


def _config(manifest_path: Path) -> tuple[str, dict[str, object]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "tier1-oof-v1":
        raise ValueError(f"unsupported OOF manifest schema: {manifest_path}")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("config"), dict):
        raise ValueError(f"OOF manifest lacks config: {manifest_path}")
    etf_id = metadata.get("etf_scope")
    if not isinstance(etf_id, str) or not etf_id:
        raise ValueError(f"OOF manifest lacks ETF scope: {manifest_path}")
    return etf_id, metadata["config"]


def main() -> int:
    args = _arguments()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output root already exists: {output}")
    manifests = _oof_manifests(args.oof_root)
    lab = Tier1Lab.from_artifacts(
        args.afml_root, args.target_root, args.feature_extension_root
    )
    rows: list[pd.DataFrame] = []
    source_hashes: dict[str, str] = {}
    seen_etfs: set[str] = set()
    for manifest_path in manifests:
        etf_id, config = _config(manifest_path)
        if etf_id in seen_etfs:
            raise ValueError(f"duplicate ETF OOF lineage supplied: {etf_id}")
        seen_etfs.add(etf_id)
        required = {
            "feature_columns", "research_t0_start", "research_t0_end",
            "research_outcome_before", "outer_splits",
        }
        if missing := required.difference(config):
            raise ValueError(f"OOF config missing fields for {etf_id}: {sorted(missing)}")
        feature_columns = config["feature_columns"]
        if not isinstance(feature_columns, list) or not all(isinstance(x, str) for x in feature_columns):
            raise ValueError(f"invalid feature columns for {etf_id}")
        frame = lab.load_training_frame(
            feature_columns,
            config["research_t0_start"],
            config["research_t0_end"],
            config["research_outcome_before"],
        )
        local = frame.loc[frame["etf_id"].astype(str).eq(etf_id)].reset_index(drop=True)
        availability = local.loc[:, feature_columns].notna().all(axis=1).to_numpy()
        if not availability.any():
            raise ValueError(f"ETF-local feature coverage absent: {etf_id}")
        local = local.iloc[int(np.flatnonzero(availability)[0]):].reset_index(drop=True)
        folds = chronological_purged_folds(local[["t0", "t1"]], n_splits=int(config["outer_splits"]))
        audit = fold_audit_records(local, folds)
        expected_by_fold = {
            fold_number: set(local.iloc[valid]["event_id"].astype(str))
            for fold_number, (_, valid) in enumerate(folds)
        }
        expected_ids = set().union(*expected_by_fold.values())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        table = manifest_path.parent / manifest["tables"]["oof_handoff"]["path"]
        actual_ids = set(pd.read_parquet(table, columns=["event_id"])["event_id"].astype(str))
        if not actual_ids.issubset(expected_ids):
            raise ValueError(f"OOF handoff contains non-validation event for {etf_id}")
        calibrated_rows: list[int] = []
        coverage_statuses: list[str] = []
        for fold_number, expected in expected_by_fold.items():
            calibrated = len(actual_ids.intersection(expected))
            calibrated_rows.append(calibrated)
            if calibrated == len(expected):
                coverage_statuses.append("EXACT_CALIBRATED")
            elif calibrated == 0:
                coverage_statuses.append("NO_CALIBRATED_PREDICTION")
            else:
                coverage_statuses.append("PARTIAL_CALIBRATED")
        audit.insert(0, "etf_id", etf_id)
        audit["eligible_validation_event_rows"] = [len(expected_by_fold[i]) for i in audit["outer_fold"]]
        audit["calibrated_oof_event_rows"] = calibrated_rows
        audit["oof_event_coverage_status"] = coverage_statuses
        rows.append(audit)
        source_hashes[str(manifest_path)] = _sha256(manifest_path)
    result = pd.concat(rows, ignore_index=True).sort_values(["etf_id", "outer_fold"])
    output.mkdir(parents=True)
    table = output / "fold_audit.parquet"
    result.to_parquet(table, index=False)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tier1-fold-audit-v1",
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "source_oof_manifest_sha256": source_hashes,
                "table": {"path": table.name, "row_count": len(result), "sha256": _sha256(table)},
                "coverage_status": "ACCOUNTED_NONFUTURE_VALIDATION_ONLY",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print({"etf_count": len(seen_etfs), "fold_rows": len(result), "coverage_status": "ACCOUNTED_NONFUTURE_VALIDATION_ONLY"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
