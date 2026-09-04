"""Write an immutable NOT_READY acceptance report for current gated lineage."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.acceptance import build_current_lineage_acceptance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        etf_id, separator, path = value.partition("=")
        if not separator or not etf_id or not path or etf_id in result:
            raise ValueError("--tier2-metrics requires unique ETF_ID=PATH values")
        result[etf_id] = Path(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier1-gate-root", required=True)
    parser.add_argument("--tier2-metrics", action="append", required=True)
    parser.add_argument("--trial-registry", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"acceptance output already exists: {output}")
    gate_root = Path(args.tier1_gate_root)
    gate_manifest_path = gate_root / "manifest.json"
    if not gate_manifest_path.is_file():
        raise ValueError("Tier 1 gate manifest is missing")
    gate_manifest = json.loads(gate_manifest_path.read_text(encoding="utf-8"))
    gate_spec = gate_manifest.get("tables", {}).get("per_etf_gate", {})
    gate_path = gate_root / str(gate_spec.get("path", ""))
    if not gate_path.is_file():
        raise ValueError("Tier 1 gate table is missing")
    gates = pd.read_parquet(gate_path)
    metrics_by_etf = _parse_mapping(args.tier2_metrics)
    statuses: dict[str, str] = {}
    upstream = {"tier1_gate_manifest": _sha256(gate_manifest_path)}
    for etf_id, path in metrics_by_etf.items():
        if not path.is_file():
            raise ValueError(f"Tier 2 metrics table is missing: {path}")
        metrics = pd.read_parquet(path)
        if len(metrics) != 1 or "oof_rows" not in metrics:
            raise ValueError(f"Tier 2 metrics must contain one oof_rows row: {path}")
        status = "INSUFFICIENT_MATURE_EVENTS" if int(metrics.loc[0, "oof_rows"]) == 0 else "RESEARCH_ONLY"
        statuses[etf_id] = status
        upstream[f"tier2_{etf_id}_metrics"] = _sha256(path)
    registry_path = Path(args.trial_registry)
    records = [json.loads(line) for line in registry_path.read_text(encoding="utf-8").splitlines() if line]
    if not records:
        raise ValueError("trial registry is empty")
    trial_count = max(float(record["effective_independent_trial_count"]) for record in records)
    report = build_current_lineage_acceptance(
        trial_count=trial_count,
        tier1_gate_table=gates,
        tier2_status_by_etf=statuses,
    )
    report["input_hashes"] = {**upstream, "trial_registry": _sha256(registry_path)}
    output.mkdir(parents=True)
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    manifest = {
        "schema_version": "afml-current-lineage-acceptance-v1",
        "report": {"path": report_path.name, "sha256": _sha256(report_path)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print({"status": report["status"], "tier1_passed_etfs": report["tier1_passed_etfs"], "tier2_status": report["tier2_status"], "trial_count": trial_count})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
