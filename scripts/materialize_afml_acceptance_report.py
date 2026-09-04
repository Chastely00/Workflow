"""Write a hash-linked NOT_READY AFML acceptance report without inventing DSR."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.acceptance import build_not_ready_acceptance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier1-gate-report", required=True)
    parser.add_argument("--sealed-report", required=True)
    parser.add_argument("--trial-registry", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    gate_path = Path(args.tier1_gate_report)
    sealed_path = Path(args.sealed_report)
    registry_path = Path(args.trial_registry)
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"acceptance output already exists: {output}")
    gate = _read_json(gate_path)
    sealed = _read_json(sealed_path)
    rows = [json.loads(line) for line in registry_path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise ValueError("trial registry is empty")
    trial_count = max(float(row["effective_independent_trial_count"]) for row in rows)
    report = build_not_ready_acceptance(
        trial_count=trial_count,
        tier1_gate=gate,
        sealed_summary={
            "selected_etf_id": sealed["selected_etf_id"],
            "sealed_auc": sealed["metrics"]["auc"],
            "sealed_candidate_mean_net_log_return": sealed["metrics"]["candidate_mean_net_log_return"],
            "sealed_base_mean_net_log_return": sealed["metrics"]["base_mean_net_log_return"],
        },
    )
    report["input_hashes"] = {"tier1_gate_report": _sha256(gate_path), "sealed_report": _sha256(sealed_path), "trial_registry": _sha256(registry_path)}
    output.mkdir(parents=True)
    path = output / "report.json"
    path.write_text(json.dumps(report, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    manifest = {"schema_version": "afml-acceptance-v1", "report": {"path": path.name, "sha256": _sha256(path)}}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print({"status": report["status"], "effective_trial_count": trial_count, "report_sha256": manifest["report"]["sha256"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
