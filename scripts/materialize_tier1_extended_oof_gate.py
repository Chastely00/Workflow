"""Materialize ETF-local Tier 1 gates from immutable extended OOF hand-offs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.diagnostics import evaluate_etf_local_gate, summarize_oof_handoff_outcomes


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        etf_id, separator, path = value.partition("=")
        if not separator or not etf_id or not path or etf_id in result:
            raise ValueError("--oof-root requires unique ETF_ID=PATH values")
        result[etf_id] = Path(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--oof-root", action="append", required=True)
    parser.add_argument("--trial-prefix", required=True)
    parser.add_argument("--effective-trial-count", type=float, required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing gate artifact: {output}")
    if args.effective_trial_count <= 0:
        raise ValueError("effective trial count must be positive")
    target_root = Path(args.target_root)
    target_manifest = target_root / "manifest.json"
    targets_path = target_root / "targets.parquet"
    if not target_manifest.is_file() or not targets_path.is_file():
        raise ValueError("target root is incomplete")
    targets = pd.read_parquet(targets_path)
    mappings = _parse_mapping(args.oof_root)
    rows: list[dict[str, object]] = []
    upstream: dict[str, str] = {"target_manifest": _sha256(target_manifest)}
    for etf_id, root in sorted(mappings.items()):
        manifest = root / "manifest.json"
        handoff_path = root / "oof_handoff.parquet"
        if not manifest.is_file() or not handoff_path.is_file():
            raise ValueError(f"OOF root is incomplete: {root}")
        handoff = pd.read_parquet(handoff_path)
        metrics = summarize_oof_handoff_outcomes(handoff, targets, etf_id=etf_id)
        report = evaluate_etf_local_gate(
            metrics,
            etf_id,
            f"{args.trial_prefix}-{etf_id}",
            args.effective_trial_count,
        )
        # Tier 1 may admit its own Tier 2 research only; it never bypasses Tier 2.
        report["tier3_permitted"] = False
        report["tier3_reason"] = "Tier 3 requires an independently passing Tier 2 lineage."
        rows.append({
            "etf_id": etf_id,
            "status": report["status"],
            "tier2_permitted": report["tier2_permitted"],
            "tier3_permitted": False,
            "reasons_json": json.dumps(report["reasons"], ensure_ascii=False),
            **{f"metric_{key}": value for key, value in metrics.items()},
        })
        upstream[f"oof_{etf_id}_manifest"] = _sha256(manifest)
    output.mkdir(parents=True)
    table = output / "per_etf_gate.parquet"
    frame = pd.DataFrame(rows).sort_values("etf_id", kind="stable").reset_index(drop=True)
    frame.to_parquet(table, index=False)
    manifest = {
        "schema_version": "tier1-extended-oof-gate-v1",
        "reporting_only": True,
        "model_or_threshold_refit": False,
        "tier3_permitted": False,
        "effective_independent_trial_count": args.effective_trial_count,
        "upstream_manifest_sha256": upstream,
        "tables": {"per_etf_gate": {"path": table.name, "row_count": len(frame), "sha256": _sha256(table)}},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
