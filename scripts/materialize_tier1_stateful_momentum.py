"""Materialize the pre-registered stateful Momentum Tier 1 OOF slice."""

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
from etf_tricks.tier1.barrier_diagnostics import summarize_barriers
from etf_tricks.tier1.market_snapshot import ExecutionMarketSnapshot
from etf_tricks.tier1.stateful_ledger import execute_stateful_transitions
from etf_tricks.tier1.stateful_policy import build_stateful_transitions


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--etf-result-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--oof-root", required=True)
    parser.add_argument("--data-store-root", default="DataAnalysts/data_store")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--registry-path", default=".artifacts/afml_governance/tier1_trials.jsonl")
    parser.add_argument("--trial-id", default="tier1-stateful-momentum-oof-v1")
    parser.add_argument("--effective-trial-count-base", required=True, type=float)
    parser.add_argument("--initial-capital", type=float, default=10_000_000.0)
    parser.add_argument("--entry-score", type=float, default=0.20)
    parser.add_argument("--exit-score", type=float, default=-0.10)
    return parser.parse_args()


def _barrier_inputs(targets: pd.DataFrame, membership: pd.DataFrame, event_ids: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = targets.loc[targets["event_id"].isin(event_ids)].copy()
    selected = selected.loc[selected["target_status"].astype(str).str.startswith("resolved_")].copy()
    if selected.empty:
        raise ValueError("Momentum OOF has no mature target rows for barrier diagnostics")
    members = membership.loc[membership["etf_id"].eq("momentum"), ["bar_id", "date", "nav"]].copy()
    members["date"] = pd.to_datetime(members["date"]).dt.normalize()
    selected["trigger_date"] = pd.to_datetime(selected["trigger_date"]).dt.normalize()
    trigger_bar = members.rename(columns={"bar_id": "first_touch_bar_id", "date": "trigger_date"})[["trigger_date", "first_touch_bar_id"]].drop_duplicates()
    events = selected.merge(trigger_bar, on="trigger_date", how="left", validate="many_to_one")
    if events["first_touch_bar_id"].isna().any():
        raise ValueError("cannot map a resolved target touch date to an immutable Dollar bar")
    events = events.rename(columns={"t0_date": "t0_date", "entry_raw_open": "entry_price", "trigger_type": "first_touch_type"})[
        ["event_id", "etf_id", "t0_bar_id", "t0_date", "entry_price", "first_touch_type", "first_touch_bar_id", "target_status"]
    ]
    paths = selected[["event_id", "t0_bar_id"]].merge(members, how="cross")
    paths = paths.loc[(paths["bar_id"] > paths["t0_bar_id"]) & (paths["bar_id"] <= paths["t0_bar_id"] + 60), ["event_id", "bar_id", "date", "nav"]]
    return events, paths.rename(columns={"nav": "close_nav"})


def main() -> int:
    args = _args()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    roots = {name: Path(getattr(args, f"{name}_root")) for name in ("afml", "target", "oof")}
    etf_root, data_store = Path(args.etf_result_root), Path(args.data_store_root)
    manifest_paths = {name: root / "manifest.json" for name, root in roots.items()} | {"etf": etf_root / "result_manifest.json"}
    if missing := [str(path) for path in manifest_paths.values() if not path.is_file()]:
        raise ValueError(f"missing immutable input manifest: {missing}")
    config = {"etf_id": "momentum", "policy": "one_sided_cusum_p1_minus_half_v1", "entry_score": args.entry_score, "exit_score": args.exit_score, "execution": {"price": "next_legal_raw_open", "buy_cost_rate": 0.001425, "sell_cost_rate": 0.003, "minimum_ticket_fee": 1.0}, "diagnostics": "outcome_only_barrier_path_v1"}
    upstream = {name: _sha256(path) for name, path in manifest_paths.items()}
    trial_id = args.trial_id
    registry = TrialRegistry(args.registry_path)
    registered = {"trial_id": trial_id, "parent_trial_id": "tier1-etf-local-hgb-base15-longhistory-2005-2024-v1-momentum-result", "created_at": _now(), "completed_at": None, "research_question": "Does a pre-registered non-overlapping state policy improve executable Momentum Tier 1 OOF economics?", "hypothesis": "Accumulating calibrated OOF directional evidence and charging only real state changes removes the artificial per-event round-trip penalty.", "code_commit": _commit(), "upstream_artifact_hashes": upstream, "feature_set_hash": _hash("hgb_base_15_v1"), "label_config_hash": _hash(json.loads((roots["target"] / "manifest.json").read_text(encoding="utf-8"))["metadata"]["target_config"]), "tier1_config_hash": _hash(config), "tier2_config_hash": None, "allocation_config_hash": None, "execution_cost_policy_hash": _hash(config["execution"]), "fold_definition_hash": _hash("existing_etf_local_expanding_event_end_purged_oof"), "train_validation_test_boundaries": {"research_t0_start": "2005-01-01", "research_t0_end": "2024-12-31", "oof_only": True}, "etf_scope": "momentum", "model_scope": "ETF_LOCAL", "raw_trial_count": int(args.effective_trial_count_base + 1), "effective_independent_trial_count": float(args.effective_trial_count_base + 1), "validation_metrics": {}, "selection_status": "REGISTERED", "selection_reason": "State-policy configuration registered before its OOF ledger outcomes are materialized."}
    registry.append(registered)

    oof = pd.read_parquet(roots["oof"] / "oof_handoff.parquet")
    transitions = build_stateful_transitions(oof, entry_score=args.entry_score, exit_score=args.exit_score)
    holdings = pd.read_parquet(etf_root / "daily_holdings.parquet")
    daily_nav = pd.read_parquet(etf_root / "daily_etf.parquet")
    holdings = holdings.loc[holdings["etf_id"].eq("momentum")]
    daily_nav = daily_nav.loc[daily_nav["etf_id"].eq("momentum")]
    years = list(range(int(pd.to_datetime(oof["decision_available_at"]).min().year), int(pd.to_datetime(oof["decision_available_at"]).max().year) + 1))
    prices, states = ExecutionMarketSnapshot.read_bounded_constituent_snapshot(data_store, years)
    opens = ExecutionMarketSnapshot.from_frames(holdings, ExecutionMarketSnapshot.prepare_prices(prices, states), daily_nav)
    ledger = execute_stateful_transitions(transitions.loc[transitions["transition"].notna()], opens, initial_capital=args.initial_capital)
    targets = pd.read_parquet(roots["target"] / "targets.parquet")
    membership = pd.read_parquet(roots["afml"] / "tables" / "bar_daily_membership.parquet")
    events, paths = _barrier_inputs(targets, membership, oof["event_id"])
    barrier = summarize_barriers(events, oof[["event_id", "candidate_indicator"]], paths)
    output.mkdir(parents=True)
    for name, frame in {"transitions": transitions, "trades": ledger, "barrier_diagnostics": barrier}.items():
        frame.to_parquet(output / f"{name}.parquet", index=False)
    manifest = {"schema_version": "tier1-stateful-oof-v1", "config": config, "upstream": upstream, "tables": {path.stem: {"path": path.name, "row_count": len(pd.read_parquet(path)), "sha256": _sha256(path)} for path in output.glob("*.parquet")}}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    metrics = {"transition_count": int(len(ledger)), "round_trip_count": int((ledger["side"].eq("sell")).sum()), "total_commission": float(ledger["commission"].sum()), "final_cash_after_last_transition": float(ledger["cash_after"].iloc[-1]) if not ledger.empty else None}
    registry.append({**registered, "trial_id": f"{trial_id}-result", "parent_trial_id": trial_id, "created_at": _now(), "completed_at": _now(), "upstream_artifact_hashes": {**upstream, "stateful_manifest": _sha256(output / "manifest.json")}, "validation_metrics": metrics, "selection_status": "RESEARCH_ONLY", "selection_reason": "Stateful OOF ledger materialized; economic gate remains to be evaluated before Tier 2 admission."})
    print({"output": str(output), **metrics})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
