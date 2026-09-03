from __future__ import annotations

import json
from datetime import datetime, timezone
import math
from typing import Any, Iterable

import pyarrow.parquet as pq

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.paths import DataAnalystsContext


class MaterializationError(ValueError):
    """Raised when incremental state cannot be reconstructed safely."""


def load_canonical_rows(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    start_date: str | None = None,
    end_date: str | None = None,
    tickers: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Load canonical history exclusively through the active ready manifest."""
    requires_explicit_identity = (
        contract.contract_key != contract.artifact_id or contract.variant != "default"
    )
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    if not manifest_path.exists():
        legacy_path = context.store_path("manifests", f"{contract.artifact_id}.json")
        if requires_explicit_identity and legacy_path.exists():
            raise ArtifactError(
                f"{contract.artifact_id} legacy variant manifest requires migration: "
                f"{legacy_path}"
            )
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            f"cannot read {contract.artifact_id} manifest: {exc}"
        ) from exc
    if manifest.get("artifact_id") != contract.artifact_id:
        raise ArtifactError(f"{contract.artifact_id} manifest artifact_id mismatch")
    if requires_explicit_identity and "contract_key" not in manifest:
        raise ArtifactError(f"{contract.artifact_id} manifest missing contract_key")
    if requires_explicit_identity and "variant" not in manifest:
        raise ArtifactError(f"{contract.artifact_id} manifest missing variant")
    if manifest.get("contract_key", contract.contract_key) != contract.contract_key:
        raise ArtifactError(f"{contract.artifact_id} manifest contract_key mismatch")
    if manifest.get("variant", contract.variant) != contract.variant:
        raise ArtifactError(f"{contract.artifact_id} manifest variant mismatch")
    if manifest.get("status") != "ready":
        raise ArtifactError(f"{contract.artifact_id} manifest is not ready")
    artifact_paths = manifest.get("artifact_paths")
    if not isinstance(artifact_paths, list):
        raise ArtifactError(f"{contract.artifact_id} manifest has no artifact_paths")
    if not artifact_paths:
        if contract.allow_empty and manifest.get("row_count") == 0:
            return []
        raise ArtifactError(f"{contract.artifact_id} manifest has no artifact_paths")

    ticker_filter = {str(ticker) for ticker in tickers} if tickers is not None else None
    rows: list[dict[str, Any]] = []
    for relative_path in artifact_paths:
        if not isinstance(relative_path, str):
            raise ArtifactError(f"{contract.artifact_id} manifest path must be a string")
        path = context.artifact_path(relative_path)
        if not path.is_file():
            raise ArtifactError(
                f"{contract.artifact_id} manifest-listed path is missing: {relative_path}"
            )
        parquet = pq.ParquetFile(path)
        try:
            for batch in parquet.iter_batches(batch_size=65536):
                for row in batch.to_pylist():
                    row_date = _row_date(row, contract)
                    if start_date and row_date is not None and row_date < start_date:
                        continue
                    if end_date and row_date is not None and row_date > end_date:
                        continue
                    if ticker_filter is not None and str(row.get("ticker")) not in ticker_filter:
                        continue
                    rows.append(row)
        finally:
            parquet.close()
    return sorted(rows, key=lambda row: (_row_date(row, contract) or "", str(row.get("ticker") or "")))


def rematerialization_start(
    changed_rows: Iterable[dict[str, Any]],
    changed_actions: Iterable[dict[str, Any]],
) -> str | None:
    starts = rematerialization_starts(changed_rows, changed_actions)
    return min(starts.values()) if starts else None


def rematerialization_starts(
    changed_rows: Iterable[dict[str, Any]],
    changed_actions: Iterable[dict[str, Any]],
) -> dict[str, str]:
    starts: dict[str, str] = {}
    for row in [*changed_rows, *changed_actions]:
        ticker = row.get("ticker")
        row_date = _first_date(row)
        if ticker is None or row_date is None:
            continue
        key = str(ticker)
        starts[key] = min(starts.get(key, row_date), row_date)
    return starts


def changed_tickers(*row_groups: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(row["ticker"])
        for rows in row_groups
        for row in rows
        if row.get("ticker") is not None
    }


def max_data_cutoff(*values: Any) -> str | None:
    candidates = [value for value in values if value is not None and str(value)]
    if not candidates:
        return None

    def timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    latest = max(candidates, key=timestamp)
    if isinstance(latest, datetime):
        return latest.isoformat().replace("+00:00", "Z")
    return str(latest)


def initial_adjustment_state(
    rows: Iterable[dict[str, Any]],
    start_date: str,
    tickers: Iterable[str],
) -> dict[str, dict[str, Any]]:
    return initial_adjustment_state_by_ticker_boundaries(
        rows, {str(ticker): start_date for ticker in tickers}
    )


def initial_adjustment_state_by_ticker_boundaries(
    rows: Iterable[dict[str, Any]],
    boundaries: dict[str, str],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        row_date = _first_date(row)
        boundary = boundaries.get(ticker)
        if boundary is None or row_date is None or row_date >= boundary:
            continue
        if ticker not in latest or row_date > str(latest[ticker].get("date")):
            latest[ticker] = row
    state: dict[str, dict[str, Any]] = {}
    for ticker, row in latest.items():
        values: dict[str, Any] = {
            "adj_factor": float(row.get("adj_factor") or 1.0),
            "last_materialized_date": str(row.get("date")),
        }
        if row.get("close") is not None:
            values["prev_close"] = float(row["close"])
        if row.get("data_cutoff_at") is not None:
            values["data_cutoff_at"] = str(row["data_cutoff_at"])
        state[ticker] = values
    return state


def validate_adjustment_seeds(
    *,
    run_scope: str,
    boundaries: dict[str, str],
    existing_prices: Iterable[dict[str, Any]],
) -> None:
    if run_scope == "full_history":
        return
    existing = list(existing_prices)
    for ticker, boundary in sorted(boundaries.items()):
        prior_rows = [
            row
            for row in existing
            if str(row.get("ticker")) == ticker
            and _first_date(row) is not None
            and str(_first_date(row)) < boundary
        ]
        if prior_rows:
            prior = max(prior_rows, key=lambda row: str(_first_date(row)))
            factor = prior.get("adj_factor")
            try:
                parsed_factor = float(factor)
                valid_factor = math.isfinite(parsed_factor) and parsed_factor > 0.0
            except (TypeError, ValueError):
                valid_factor = False
            if not valid_factor:
                raise MaterializationError(
                    f"ticker={ticker} boundary={boundary} prior adj_factor seed is missing or invalid"
                )
            close = prior.get("close")
            try:
                parsed_close = float(close)
                valid_close = math.isfinite(parsed_close) and parsed_close > 0.0
            except (TypeError, ValueError):
                valid_close = False
            if not valid_close:
                raise MaterializationError(
                    f"ticker={ticker} boundary={boundary} prior prev_close seed is missing or invalid"
                )
            continue
        raise MaterializationError(
            f"ticker={ticker} boundary={boundary} has no prior adjustment seed"
        )


def rows_at_or_after_boundaries(
    rows: Iterable[dict[str, Any]],
    boundaries: dict[str, str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "")
        row_date = _first_date(row)
        boundary = boundaries.get(ticker)
        if boundary is not None and row_date is not None and row_date >= boundary:
            output.append(row)
    return output


def events_after_adjustment_boundary(
    events: Iterable[dict[str, Any]],
    affected_tickers: Iterable[str],
    initial_state_by_ticker: dict[str, dict[str, Any]],
    fallback_start_dates: dict[str, str],
) -> list[dict[str, Any]]:
    wanted = {str(ticker) for ticker in affected_tickers}
    output: list[dict[str, Any]] = []
    for row in events:
        ticker = str(row.get("ticker") or "")
        event_date = _first_date(row)
        if ticker not in wanted or event_date is None:
            continue
        boundary = initial_state_by_ticker.get(ticker, {}).get(
            "last_materialized_date"
        )
        if boundary is not None:
            if event_date <= str(boundary):
                continue
        elif event_date < fallback_start_dates[ticker]:
            continue
        output.append(row)
    return output


def with_membership_exclusions(
    existing_rows: Iterable[dict[str, Any]],
    recalculated_rows: Iterable[dict[str, Any]],
    cutoff_by_as_of_date: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Represent memberships removed by rematerialization as audited exclusions."""
    output_by_key = {
        (
            str(row.get("as_of_date")),
            str(row.get("universe_id")),
            str(row.get("ticker")),
        ): dict(row)
        for row in recalculated_rows
    }
    for row in existing_rows:
        key = (
            str(row.get("as_of_date")),
            str(row.get("universe_id")),
            str(row.get("ticker")),
        )
        if key in output_by_key:
            continue
        cutoff = (cutoff_by_as_of_date or {}).get(str(row.get("as_of_date")))
        replacement_cutoff = max_data_cutoff(row.get("data_cutoff_at"), cutoff)
        if row.get("included") is False and replacement_cutoff == row.get(
            "data_cutoff_at"
        ):
            continue
        excluded = dict(row)
        excluded["included"] = False
        excluded["reason"] = "excluded_after_rematerialization"
        if replacement_cutoff is not None:
            excluded["data_cutoff_at"] = replacement_cutoff
        output_by_key[key] = excluded
    return sorted(
        output_by_key.values(),
        key=lambda row: (
            str(row.get("as_of_date")),
            str(row.get("universe_id")),
            str(row.get("ticker")),
        ),
    )


def _row_date(row: dict[str, Any], contract: ArtifactContract) -> str | None:
    if contract.date_field and row.get(contract.date_field) is not None:
        return str(row[contract.date_field])[:10]
    return _first_date(row)


def _first_date(row: dict[str, Any]) -> str | None:
    for field in ("date", "event_date", "ex_date", "as_of_date", "source_available_date"):
        value = row.get(field)
        if value is not None and str(value).strip():
            return str(value)[:10]
    return None
