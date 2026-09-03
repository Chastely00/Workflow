from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.materialization import load_canonical_rows
from data_analysts.paths import DataAnalystsContext

from data_analysts.filesystem import replace_file


class DailyRefreshError(ValueError):
    """Raised when daily refresh planning cannot proceed safely."""


def plan_daily_refresh_dates(
    context: DataAnalystsContext,
    trading_calendar_contract: ArtifactContract,
    *,
    as_of_date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    today: str | None = None,
) -> list[str]:
    today_date = date.fromisoformat(today) if today else date.today()
    if as_of_date:
        _reject_future(as_of_date, today_date)
        if from_date or to_date:
            raise DailyRefreshError("--as-of-date cannot be combined with --from-date or --to-date")
        return [as_of_date]

    store = context.data_store
    trading_dates = _trading_dates(context, trading_calendar_contract)
    target_date = to_date or _latest_trading_date_on_or_before(
        trading_dates, today_date.isoformat()
    )
    _reject_future(target_date, today_date)

    if from_date:
        start_date = from_date
    else:
        last_ready = _last_ready_as_of_date(store)
        start_date = (
            _next_trading_date_after(trading_dates, last_ready, target_date)
            if last_ready
            else target_date
        )

    if date.fromisoformat(target_date) < date.fromisoformat(start_date):
        return []
    return _trading_dates_between(trading_dates, start_date, target_date)


def write_daily_refresh_success(
    data_store: str | Path,
    *,
    as_of_date: str,
    result: dict[str, Any],
) -> None:
    store = Path(data_store)
    payload = dict(result)
    payload["status"] = result.get("status", "ready")
    payload["as_of_date"] = as_of_date
    payload["updated_at"] = _utc_now()

    _atomic_write_json(store / "jobs" / "daily_results" / f"{as_of_date}.json", payload)
    _atomic_write_json(
        store / "jobs" / "daily_state.json",
        {
            "last_ready_as_of_date": as_of_date,
            "last_attempted_as_of_date": as_of_date,
            "status": "ready",
            "updated_at": payload["updated_at"],
        },
    )


def write_daily_refresh_blocked(
    data_store: str | Path,
    *,
    as_of_date: str | None,
    message: str,
) -> None:
    store = Path(data_store)
    updated_at = _utc_now()
    payload = {
        "status": "blocked",
        "as_of_date": as_of_date,
        "message": message,
        "updated_at": updated_at,
    }
    if as_of_date:
        _atomic_write_json(store / "jobs" / "daily_results" / f"{as_of_date}.json", payload)
    _atomic_write_json(
        store / "jobs" / "daily_state.json",
        {
            "last_ready_as_of_date": _last_ready_as_of_date(store),
            "last_attempted_as_of_date": as_of_date,
            "status": "blocked",
            "message": message,
            "updated_at": updated_at,
        },
    )


def _latest_trading_date_on_or_before(
    trading_dates: list[str], target_date: str
) -> str:
    dates = [item for item in trading_dates if item <= target_date]
    if not dates:
        raise DailyRefreshError("no trading calendar date is available on or before target date")
    return dates[-1]


def _next_trading_date_after(
    trading_dates: list[str], last_ready: str, target_date: str
) -> str:
    dates = [item for item in trading_dates if last_ready < item <= target_date]
    return dates[0] if dates else target_date


def _trading_dates_between(
    trading_dates: list[str], start_date: str, end_date: str
) -> list[str]:
    return [item for item in trading_dates if start_date <= item <= end_date]


def _trading_dates(
    context: DataAnalystsContext,
    trading_calendar_contract: ArtifactContract,
) -> list[str]:
    try:
        rows = load_canonical_rows(context, trading_calendar_contract)
    except ArtifactError as exc:
        raise DailyRefreshError(
            f"active trading_calendar artifact is unavailable: {exc}"
        ) from exc
    if not rows:
        raise DailyRefreshError(
            "active trading_calendar manifest and parquet are required before automatic daily refresh"
        )
    return sorted({str(row["date"]) for row in rows if row.get("is_trading_day") is True})


def _last_ready_as_of_date(data_store: Path) -> str | None:
    path = data_store / "jobs" / "daily_state.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get("last_ready_as_of_date")
    return str(value) if value else None


def _reject_future(value: str, today: date) -> None:
    if date.fromisoformat(value) > today:
        raise DailyRefreshError(f"future daily refresh target is not allowed: {value}")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    staging = path.with_name(f".{path.name}.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        replace_file(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
