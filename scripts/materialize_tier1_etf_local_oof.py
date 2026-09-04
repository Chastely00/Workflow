"""Materialize independently fitted, per-ETF Tier 1 OOF evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.trials import TrialRegistry, write_tier1_gate_report
from etf_tricks.tier1.artifact import write_oof_artifact
from etf_tricks.tier1.diagnostics import evaluate_etf_local_gate, summarize_per_etf_oof
from etf_tricks.tier1.lab import Tier1Lab
from etf_tricks.tier1.long_history import feature_columns_for


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _native(value: object) -> object:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _record_metrics(summary: pd.DataFrame, etf_id: str) -> dict[str, object]:
    row = summary.loc[summary["scope"].eq("ALL_WITHIN_ETF")].iloc[0]
    return {column: _native(row[column]) for column in summary.columns if column not in {"etf_id", "scope", "training_rows"}}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--feature-extension-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--registry-path", default=".artifacts/afml_governance/tier1_trials.jsonl")
    parser.add_argument("--trial-prefix", required=True)
    parser.add_argument("--feature-set", default="hgb_base_15_v1")
    parser.add_argument("--model-family", choices=("logistic_regression", "hist_gradient_boosting"), required=True)
    parser.add_argument("--research-t0-end", required=True)
    parser.add_argument("--research-outcome-before", required=True)
    parser.add_argument("--outer-splits", type=int, default=3)
    parser.add_argument("--effective-trial-count-base", type=float, required=True)
    return parser.parse_args()


def main() -> int:
    args = _args()
    root = Path(args.output_root)
    if root.exists():
        raise FileExistsError(f"output root already exists: {root}")
    if args.effective_trial_count_base < 0:
        raise ValueError("effective trial count base must be nonnegative")
    roots = {"afml": Path(args.afml_root), "target": Path(args.target_root), "extension": Path(args.feature_extension_root)}
    manifests = {name: path / "manifest.json" for name, path in roots.items()}
    if missing := [str(path) for path in manifests.values() if not path.is_file()]:
        raise ValueError(f"missing immutable input manifest: {missing}")
    upstream = {name: _sha256(path) for name, path in manifests.items()}
    features = feature_columns_for(args.feature_set)
    expected_etfs = sorted(pd.read_parquet(roots["target"] / "targets.parquet", columns=["etf_id"])["etf_id"].drop_duplicates())
    config = {
        "model_scope": "ETF_LOCAL", "model_family": args.model_family,
        "feature_set": args.feature_set, "feature_columns": features,
        "candidate_threshold_objective": "economic_net_log_return",
        "minimum_candidate_weight_share": 0.10, "outer_splits": args.outer_splits,
        "research_t0_end": args.research_t0_end,
        "research_outcome_before": args.research_outcome_before,
        "fold_policy": "ETF-local chronological expanding event-end purged",
    }
    registry = TrialRegistry(args.registry_path)
    label_config = json.loads((roots["target"] / "manifest.json").read_text(encoding="utf-8"))["metadata"]["target_config"]
    records: dict[str, dict[str, object]] = {}
    for ordinal, etf_id in enumerate(expected_etfs, start=1):
        trial_id = f"{args.trial_prefix}-{etf_id}"
        records[etf_id] = {
            "trial_id": trial_id, "parent_trial_id": "tier1-hgb-base15-cost-audited-2005-2024-v1-result",
            "created_at": _now(), "completed_at": None,
            "research_question": "Does this ETF-local Tier 1 model provide promotable OOF long opportunities?",
            "hypothesis": "ETF-specific FFD, liquidity, portfolio, and regime state may rank this ETF's own cost-aware directional events.",
            "code_commit": _commit(), "upstream_artifact_hashes": upstream,
            "feature_set_hash": _hash(features), "label_config_hash": _hash(label_config),
            "tier1_config_hash": _hash({**config, "etf_scope": etf_id}),
            "tier2_config_hash": None, "allocation_config_hash": None,
            "execution_cost_policy_hash": _hash({"buy_cost_rate": 0.001425, "sell_cost_rate": 0.003, "cost_policy_id": "tier1-proportional-v1"}),
            "fold_definition_hash": _hash({**config, "etf_scope": etf_id}),
            "train_validation_test_boundaries": {"research_t0_end": args.research_t0_end, "research_outcome_before": args.research_outcome_before},
            "etf_scope": etf_id, "model_scope": "ETF_LOCAL",
            "raw_trial_count": int(args.effective_trial_count_base + ordinal),
            "effective_independent_trial_count": float(args.effective_trial_count_base + ordinal),
            "validation_metrics": {}, "selection_status": "REGISTERED",
            "selection_reason": "Registered before ETF-local fitting; no pooled fitted state or pooled gate is used.",
        }
        registry.append(records[etf_id])

    lab = Tier1Lab.from_artifacts(roots["afml"], roots["target"], roots["extension"])
    runs = lab.run_oof_per_etf(
        features, outer_splits=args.outer_splits, model_family=args.model_family,
        candidate_threshold_objective="economic_net_log_return",
        research_t0_end=args.research_t0_end,
        research_outcome_before=args.research_outcome_before,
    )
    root.mkdir(parents=True)
    for etf_id, run in runs.by_etf.items():
        etf_root = root / etf_id
        summary = summarize_per_etf_oof(
            run.training_frame, run.predictions,
            [(train.tolist(), valid.tolist()) for train, valid in run.folds],
            expected_etf_ids=[etf_id], scope_label="ALL_WITHIN_ETF",
        )
        metrics = _record_metrics(summary, etf_id)
        report = evaluate_etf_local_gate(
            metrics, etf_id, str(records[etf_id]["trial_id"]),
            float(records[etf_id]["effective_independent_trial_count"]),
        )
        oof_manifest = write_oof_artifact(run.handoff, etf_root / "oof", {**upstream, "config": config, "etf_scope": etf_id})
        diagnostics_path = etf_root / "diagnostics"
        diagnostics_path.mkdir(parents=True)
        summary.to_parquet(diagnostics_path / "per_etf_oof.parquet", index=False)
        (diagnostics_path / "manifest.json").write_text(json.dumps({"schema_version": "tier1-etf-local-diagnostics-v1", "etf_scope": etf_id, "table_sha256": _sha256(diagnostics_path / "per_etf_oof.parquet")}, sort_keys=True), encoding="utf-8")
        gate_manifest = write_tier1_gate_report(report, etf_root / "gate")
        registry.append({
            **records[etf_id], "trial_id": f"{records[etf_id]['trial_id']}-result", "parent_trial_id": records[etf_id]["trial_id"],
            "created_at": _now(), "completed_at": _now(),
            "upstream_artifact_hashes": {**upstream, "oof_manifest": _sha256(etf_root / "oof" / "manifest.json"), "diagnostics_manifest": _sha256(diagnostics_path / "manifest.json"), "gate_manifest": _sha256(etf_root / "gate" / "manifest.json")},
            "validation_metrics": metrics,
            "selection_status": "SELECTED" if report["status"] == "PASSED" else report["status"],
            "selection_reason": "ETF-local OOF gate " + str(report["status"]),
        })
        print({"etf_id": etf_id, "status": report["status"], "oof_sha256": oof_manifest["tables"]["oof_handoff"]["sha256"], "gate_sha256": gate_manifest["report"]["sha256"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
