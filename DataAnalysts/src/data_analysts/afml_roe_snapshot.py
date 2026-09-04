"""PIT-provenance-preserving ROE source snapshot helpers for AFML research."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable


_KEY = (
    "ticker",
    "no",
    "sem",
    "curr",
    "merg",
    "period_end_date",
    "source_available_date",
    "revision_date",
)


def resolve_roe_snapshot_rows(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reduce source duplicates without inventing a disputed `r103` value.

    A source may emit multiple documents for the same financial PIT identity.
    The generic raw artifact must reject such a collision.  This narrow,
    feature-specific snapshot permits a value only where every finite reported
    `r103` agrees; otherwise it preserves the identity with an explicit null
    and conflict flag for downstream as-of joins.
    """
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(_required_text(row, field) for field in _KEY)
        grouped[key].append(dict(row))

    resolved: list[dict[str, Any]] = []
    for key, candidates in sorted(grouped.items()):
        ordered = sorted(candidates, key=lambda row: str(row.get("source_row_id") or ""))
        values = {_finite_roe(row.get("r103")) for row in ordered}
        values.discard(None)
        conflict = len(values) > 1
        chosen = ordered[0]
        resolved.append(
            {
                **{field: key[index] for index, field in enumerate(_KEY)},
                "r103": None if conflict or not values else values.pop(),
                "r103_conflict": conflict,
                "source_row_count": len(ordered),
                "source_row_id": str(chosen.get("source_row_id") or ""),
                "source_collection": str(chosen.get("source_collection") or ""),
                "data_cutoff_at": str(chosen.get("data_cutoff_at") or ""),
                "data_cutoff_origin": str(chosen.get("data_cutoff_origin") or ""),
            }
        )
    return resolved


def _required_text(row: dict[str, Any], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ValueError(f"financial statement row lacks {field}")
    return value


def _finite_roe(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None
