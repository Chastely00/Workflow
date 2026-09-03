from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_analysts.filesystem import replace_file
from data_analysts.paths import DataAnalystsContext


@dataclass
class RunProgress:
    context: DataAnalystsContext
    phase: str = "starting"
    current_family: str | None = None
    completed_families: int = 0
    total_families: int = 0

    def update(
        self,
        *,
        phase: str,
        status: str = "running",
        current_family: str | None = None,
        completed_families: int | None = None,
        total_families: int | None = None,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.phase = phase
        self.current_family = current_family
        if completed_families is not None:
            self.completed_families = completed_families
        if total_families is not None:
            self.total_families = total_families
        payload = self._payload(status=status, message=message, extra=extra)
        _atomic_write_json(self.context.store_path("jobs", "current_run.json"), payload)
        print(_format_progress_line(payload), flush=True)

    def block(self, error: Exception) -> None:
        payload = self._payload(status="blocked", message=str(error), extra={"error": str(error)})
        _atomic_write_json(self.context.store_path("jobs", "current_run.json"), payload)
        print(_format_progress_line(payload), flush=True)

    def _payload(
        self,
        *,
        status: str,
        message: str | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "phase": self.phase,
            "current_family": self.current_family,
            "completed_families": self.completed_families,
            "total_families": self.total_families,
            "updated_at": _utc_now(),
        }
        if message is not None:
            payload["message"] = message
        if extra:
            payload.update(extra)
        return payload


def _format_progress_line(payload: dict[str, Any]) -> str:
    parts = [
        "[progress]",
        f"phase={payload['phase']}",
        f"status={payload['status']}",
    ]
    if payload.get("current_family"):
        parts.append(f"family={payload['current_family']}")
    parts.append(f"families={payload['completed_families']}/{payload['total_families']}")
    if payload.get("row_count") is not None:
        parts.append(f"rows={payload['row_count']}")
    if payload.get("published") is not None:
        parts.append(f"published={payload['published']}")
    if payload.get("message"):
        parts.append(f"message={payload['message']}")
    return " ".join(parts)


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
