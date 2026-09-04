"""Rebuild an immutable stateful-barrier diagnostic extension without rewriting a ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.barrier_diagnostics import build_barrier_path_inputs, summarize_barriers


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--etf-id", required=True)
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--oof-root", required=True)
    parser.add_argument("--stateful-root", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    roots = {"afml": Path(args.afml_root), "target": Path(args.target_root), "oof": Path(args.oof_root), "stateful": Path(args.stateful_root)}
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    manifests = {name: root / "manifest.json" for name, root in roots.items()}
    if missing := [str(path) for path in manifests.values() if not path.is_file()]:
        raise ValueError(f"missing input manifest: {missing}")
    oof = pd.read_parquet(roots["oof"] / "oof_handoff.parquet")
    if oof.empty or not oof["etf_id"].eq(args.etf_id).all():
        raise ValueError("OOF hand-off does not match ETF-local diagnostic scope")
    targets = pd.read_parquet(roots["target"] / "targets.parquet")
    membership = pd.read_parquet(roots["afml"] / "tables" / "bar_daily_membership.parquet")
    events, paths = build_barrier_path_inputs(targets, membership, oof["event_id"], args.etf_id)
    diagnostic = summarize_barriers(events, oof[["event_id", "candidate_indicator"]], paths)
    output.mkdir(parents=True)
    table = output / "barrier_diagnostics.parquet"
    diagnostic.to_parquet(table, index=False)
    manifest = {"schema_version": "tier1-stateful-barrier-diagnostics-v2", "etf_id": args.etf_id, "parent_stateful_manifest_sha256": _sha256(manifests["stateful"]), "upstream": {name: _sha256(path) for name, path in manifests.items() if name != "stateful"}, "tables": {"barrier_diagnostics": {"path": table.name, "row_count": len(diagnostic), "sha256": _sha256(table)}}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(diagnostic.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
