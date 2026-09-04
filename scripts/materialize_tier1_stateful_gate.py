"""Register and materialize the cross-ETF stateful Tier 1 proxy gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.trials import TrialRegistry
from etf_tricks.tier1.stateful_gate import evaluate_stateful_gate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--registry-path", default=".artifacts/afml_governance/tier1_trials.jsonl")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--effective-trial-count-base", type=float, required=True)
    parser.add_argument("--minimum-completed-round-trips", type=int, default=20)
    args = parser.parse_args()
    source, output = Path(args.summary_root), Path(args.output_root)
    source_manifest = source / "manifest.json"
    source_table = source / "per_etf_stateful_oof.parquet"
    if not source_manifest.is_file() or not source_table.is_file():
        raise ValueError("stateful summary artifact is incomplete")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    config = {"minimum_completed_round_trips": args.minimum_completed_round_trips, "require_closed_proxy_ledger": True, "require_positive_proxy_sharpe": True}
    registered = {"trial_id": args.trial_id, "parent_trial_id": "tier1-stateful-etf-local-oof-v1", "created_at": _now(), "completed_at": None, "research_question": "Which ETF-local stateful OOF proxy ledgers have sufficient closed evidence for Tier 2 research?", "hypothesis": "A minimum completed-trade sample prevents proxy Sharpe and a single marked position from becoming an economic admission claim.", "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "upstream_artifact_hashes": {"stateful_summary_manifest": _sha256(source_manifest)}, "feature_set_hash": _hash("hgb_base_15_v1"), "label_config_hash": _hash("tier1-proportional-v1"), "tier1_config_hash": _hash(config), "tier2_config_hash": None, "allocation_config_hash": None, "execution_cost_policy_hash": _hash("raw_open_proxy_ticket_cost_v1"), "fold_definition_hash": _hash("existing_etf_local_expanding_event_end_purged_oof"), "train_validation_test_boundaries": {"research_t0_start": "2005-01-01", "research_t0_end": "2024-12-31", "oof_only": True}, "etf_scope": "13 ETF-local ledgers", "model_scope": "GOVERNANCE", "raw_trial_count": int(args.effective_trial_count_base + 1), "effective_independent_trial_count": float(args.effective_trial_count_base + 1), "validation_metrics": {}, "selection_status": "REGISTERED", "selection_reason": "Stateful economic gate registered before reading the combined summary."}
    registry = TrialRegistry(args.registry_path)
    registry.append(registered)
    summary = pd.read_parquet(source_table)
    rows = [evaluate_stateful_gate(summary.loc[summary["etf_id"].eq(etf_id)], minimum_completed_round_trips=args.minimum_completed_round_trips) for etf_id in summary["etf_id"]]
    gate = pd.DataFrame(rows).sort_values("etf_id", kind="stable").reset_index(drop=True)
    output.mkdir(parents=True)
    table = output / "per_etf_gate.parquet"
    gate.to_parquet(table, index=False)
    manifest = {"schema_version": "tier1-stateful-gate-v1", "config": config, "summary_manifest_sha256": _sha256(source_manifest), "tables": {"per_etf_gate": {"path": table.name, "row_count": len(gate), "sha256": _sha256(table)}}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    counts = gate["status"].value_counts().to_dict()
    registry.append({**registered, "trial_id": f"{args.trial_id}-result", "parent_trial_id": args.trial_id, "created_at": _now(), "completed_at": _now(), "upstream_artifact_hashes": {**registered["upstream_artifact_hashes"], "gate_manifest": _sha256(output / "manifest.json")}, "validation_metrics": {"status_counts": {str(key): int(value) for key, value in counts.items()}, "tier2_permitted_count": int(gate["tier2_permitted"].sum())}, "selection_status": "RESEARCH_ONLY", "selection_reason": "Stateful proxy gate materialized; no proxy ledger may permit Tier 3 or paper trading."})
    print(gate.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
