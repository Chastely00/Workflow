"""Create one immutable descriptive summary of ETF-local Tier 1 stateful OOF ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.stateful_diagnostics import summarize_stateful_ledger


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", action="append", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    inputs = [Path(value) for value in args.input_root]
    rows: list[pd.DataFrame] = []
    upstream: dict[str, str] = {}
    for root in inputs:
        manifest = root / "manifest.json"
        if not manifest.is_file():
            raise ValueError(f"missing stateful artifact manifest: {root}")
        tables = json.loads(manifest.read_text(encoding="utf-8")).get("tables", {})
        for table in ("daily_nav", "trades", "barrier_diagnostics", "transitions"):
            if table not in tables or not (root / tables[table]["path"]).is_file():
                raise ValueError(f"stateful artifact missing table {table}: {root}")
        daily = pd.read_parquet(root / tables["daily_nav"]["path"])
        trades = pd.read_parquet(root / tables["trades"]["path"])
        barrier = pd.read_parquet(root / tables["barrier_diagnostics"]["path"])
        summary = summarize_stateful_ledger(daily, trades)
        if set(barrier["scope"]) != {"ALL_EVENTS", "CANDIDATES"}:
            raise ValueError(f"barrier diagnostic scopes invalid: {root}")
        all_events = barrier.loc[barrier["scope"].eq("ALL_EVENTS")].iloc[0]
        candidates = barrier.loc[barrier["scope"].eq("CANDIDATES")].iloc[0]
        summary["mature_event_count"] = int(all_events["event_count"])
        summary["candidate_event_count"] = int(candidates["event_count"])
        summary["lower_touch_rate_all_events"] = float(all_events["lower_touch_count"] / all_events["event_count"])
        summary["post_upper_continuation_log_return_all_events"] = all_events["mean_post_upper_continuation_log_return"]
        rows.append(summary)
        upstream[root.name] = _sha256(manifest)
    combined = pd.concat(rows, ignore_index=True).sort_values("etf_id", kind="stable").reset_index(drop=True)
    if combined["etf_id"].duplicated().any():
        raise ValueError("input roots must contain unique ETF-local ledgers")
    output.mkdir(parents=True)
    table = output / "per_etf_stateful_oof.parquet"
    combined.to_parquet(table, index=False)
    manifest = {"schema_version": "tier1-stateful-oof-summary-v1", "upstream_stateful_manifest_sha256": upstream, "tables": {"per_etf_stateful_oof": {"path": table.name, "row_count": len(combined), "sha256": _sha256(table)}}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(combined.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
