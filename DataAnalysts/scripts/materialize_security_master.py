from __future__ import annotations

import json
from datetime import datetime, timezone
from time import perf_counter

from pymongo import MongoClient

from data_analysts.config import load_runtime_config
from data_analysts.dataset_publication import publish_dataset
from data_analysts.paths import DataAnalystsContext
from data_analysts.security_master_materialization import materialize_security_master_rows


_PROJECTION = {
    "_id": 0, "coid": 1, "stk_name": 1, "enm": 1,
    "list_date": 1, "delist_date": 1, "tse_date": 1, "otc_date": 1,
    "main_ind_c": 1, "main_ind_e": 1, "sub_ind_c": 1, "sub_ind_e": 1,
}


def main() -> None:
    started = perf_counter()
    context = DataAnalystsContext.from_paths("DataAnalysts", "data_store")
    runtime = load_runtime_config(context)
    cutoff = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_rows = list(MongoClient(
        "mongodb://localhost:27017/", serverSelectionTimeoutMS=5_000
    )["TEJ"]["APISTOCK"].find({}, _PROJECTION))
    rows = materialize_security_master_rows(source_rows, data_cutoff_at=cutoff)
    result = publish_dataset(
        context,
        runtime.artifact_contracts["security_master"],
        rows,
        "full_history",
    )
    print(json.dumps({
        "row_count": result.total_row_count,
        "manifest_path": str(result.manifest_path),
        "active_version": result.manifest["active_version"],
        "elapsed_seconds": perf_counter() - started,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
