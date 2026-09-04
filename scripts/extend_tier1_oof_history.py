"""Extend existing ETF-local OOF hand-offs backward without rewriting prior OOF rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.artifact import write_oof_artifact
from etf_tricks.tier1.extended_oof import prepend_earlier_oof
from etf_tricks.tier1.lab import Tier1Lab
from etf_tricks.tier1.long_history import feature_columns_for
from etf_tricks.tier1.splits import fold_audit_records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        etf_id, separator, path = value.partition("=")
        if not separator or not etf_id or not path or etf_id in result:
            raise ValueError("--existing-oof requires unique ETF_ID=PATH values")
        result[etf_id] = Path(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--feature-extension-root", required=True)
    parser.add_argument("--existing-oof", action="append", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--outer-splits", type=int, default=12)
    args = parser.parse_args()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    mappings = _parse_mapping(args.existing_oof)
    for path in mappings.values():
        if not (path / "manifest.json").is_file() or not (path / "oof_handoff.parquet").is_file():
            raise ValueError(f"existing OOF artifact is incomplete: {path}")
    feature_set = "hgb_base_15_v1"
    lab = Tier1Lab.from_artifacts(args.afml_root, args.target_root, args.feature_extension_root)
    runs = lab.run_oof_per_etf(
        feature_columns_for(feature_set),
        outer_splits=args.outer_splits,
        model_family="hist_gradient_boosting",
        candidate_threshold_objective="economic_net_log_return",
        research_t0_start="2005-01-01",
        research_t0_end="2024-12-31",
        research_outcome_before="2025-01-01",
        etf_ids=tuple(sorted(mappings)),
    )
    output.mkdir(parents=True)
    report: dict[str, object] = {"schema_version": "tier1-oof-history-extension-v1", "outer_splits": args.outer_splits, "policy": "prepend_only_before_existing_min_t0_bar", "etfs": {}}
    for etf_id, old_root in sorted(mappings.items()):
        existing = pd.read_parquet(old_root / "oof_handoff.parquet")
        extension = runs.by_etf[etf_id].handoff
        combined = prepend_earlier_oof(existing, extension)
        etf_output = output / etf_id / "oof"
        manifest = write_oof_artifact(combined, etf_output, {"existing_oof_manifest_sha256": _sha256(old_root / "manifest.json"), "extension_outer_splits": args.outer_splits, "extension_model_scope": "ETF_LOCAL", "policy": "prepend_only_before_existing_min_t0_bar"})
        audit = fold_audit_records(runs.by_etf[etf_id].training_frame, runs.by_etf[etf_id].folds)
        audit.to_parquet(output / etf_id / "extension_fold_audit.parquet", index=False)
        report["etfs"][etf_id] = {"existing_rows": len(existing), "combined_rows": len(combined), "prepended_rows": len(combined) - len(existing), "combined_oof_manifest_sha256": _sha256(etf_output / "manifest.json"), "extension_fold_audit_sha256": _sha256(output / etf_id / "extension_fold_audit.parquet"), "oof_table_sha256": manifest["tables"]["oof_handoff"]["sha256"]}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
