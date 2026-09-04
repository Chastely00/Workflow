"""Materialize immutable PIT daily return history for Tier 3 allocation research."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier3.return_history import build_pit_daily_return_history


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    afml_root = Path(args.afml_root)
    source = afml_root / "tables" / "bar_daily_membership.parquet"
    input_manifest = afml_root / "manifest.json"
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output root already exists: {output}")
    if not source.is_file() or not input_manifest.is_file():
        raise ValueError("AFML root requires manifest and bar_daily_membership table")
    returns = build_pit_daily_return_history(pd.read_parquet(source))
    output.mkdir(parents=True)
    table = output / "daily_returns.parquet"
    returns.to_parquet(table, index=False)
    manifest = {
        "schema_version": "tier3-daily-return-history-v1",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "upstream": {
            "afml_manifest_sha256": _sha256(input_manifest),
            "bar_daily_membership_sha256": _sha256(source),
        },
        "return_semantics": "close_to_close_nav_return_available_at_current_close_source",
        "table": {"path": table.name, "row_count": len(returns), "sha256": _sha256(table)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    print({"rows": len(returns), "etf_count": int(returns["etf_id"].nunique())})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
