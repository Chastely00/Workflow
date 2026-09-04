"""Materialize one hash-linked Tier 1 chip-feature extension artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.artifact import write_feature_extension_artifact
from etf_tricks.tier1.chip_feature_artifact import merge_chip_feature_extension
from etf_tricks.tier1.chip_feature_extension import Tier1ChipFeatureExtensionBuilder


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read manifest: {path}") from error


def _read_manifest_table(root: Path, manifest: dict[str, object]) -> pd.DataFrame:
    paths = manifest.get("artifact_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise ValueError("chip manifest requires nonempty artifact_paths")
    safe_paths: list[Path] = []
    resolved_root = root.resolve()
    for relative in paths:
        path = (root / relative).resolve()
        if resolved_root not in path.parents or not path.is_file():
            raise ValueError(f"chip manifest path is missing or escapes data store: {relative}")
        safe_paths.append(path)
    return pd.concat([pd.read_parquet(path) for path in safe_paths], ignore_index=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--base-extension-root", required=True)
    parser.add_argument("--holdings-path", required=True)
    parser.add_argument("--data-store-root", default="DataAnalysts/data_store")
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    afml_root = Path(args.afml_root)
    base_root = Path(args.base_extension_root)
    holdings_path = Path(args.holdings_path)
    data_store_root = Path(args.data_store_root)
    output_root = Path(args.output_root)
    afml_manifest = _read_manifest(afml_root / "manifest.json")
    base_manifest_path = base_root / "manifest.json"
    base_manifest = _read_manifest(base_manifest_path)
    chip_manifest_path = data_store_root / "manifests" / "daily_chip_etf_constituents.json"
    chip_manifest = _read_manifest(chip_manifest_path)
    if chip_manifest.get("status") != "ready":
        raise ValueError("daily_chip_etf_constituents is not ready")
    if chip_manifest.get("revision_status") != "PIT_REVISION_UNVERIFIED":
        raise ValueError("daily_chip_etf_constituents revision status is unexpected")
    if not holdings_path.is_file():
        raise ValueError(f"holdings path is missing: {holdings_path}")
    for path in (afml_root / "tables" / "dollar_bars.parquet", base_root / "features.parquet"):
        if not path.is_file():
            raise ValueError(f"required input is missing: {path}")

    bars = pd.read_parquet(afml_root / "tables" / "dollar_bars.parquet")
    holdings = pd.read_parquet(holdings_path)
    chip = _read_manifest_table(data_store_root, chip_manifest)
    base_features = pd.read_parquet(base_root / "features.parquet")
    chip_features = Tier1ChipFeatureExtensionBuilder().build(bars, holdings, chip)
    features = merge_chip_feature_extension(base_features, chip_features)
    manifest = write_feature_extension_artifact(
        features,
        output_root,
        {
            "afml_manifest_sha256": _sha256(afml_root / "manifest.json"),
            "base_extension_manifest_sha256": _sha256(base_manifest_path),
            "base_extension_features_sha256": base_manifest["tables"]["features"]["sha256"],
            "daily_chip_etf_constituents_manifest_sha256": _sha256(chip_manifest_path),
            "daily_chip_revision_status": chip_manifest["revision_status"],
            "chip_feature": {
                "formula": "sum(actual_weight*(qfii_examt+fund_examt+dlrp_examt))",
                "window_sessions": 20,
                "availability_assumption": "AFTER_CLOSE_DATE_ONLY",
                "missing_policy": "missing any held constituent chip row keeps daily flow missing",
            },
            "holdings_path": str(holdings_path),
            "holdings_sha256": _sha256(holdings_path),
        },
    )
    print({"row_count": len(features), "features_sha256": manifest["tables"]["features"]["sha256"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
