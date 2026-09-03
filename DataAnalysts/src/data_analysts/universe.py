from __future__ import annotations

from typing import Any


def build_universe_memberships(
    security_panel: list[dict[str, Any]],
    universe_specs: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    memberships: dict[str, list[dict[str, Any]]] = {}
    for spec in universe_specs.get("universes", []):
        if spec.get("enabled", True) is False:
            continue
        universe_id = spec["universe_id"]
        candidates = [row for row in security_panel if _passes_filters(row, spec.get("filters", []))]
        candidates = _sort_candidates(candidates, spec.get("rank_by", []))
        limit = spec.get("limit")
        if isinstance(limit, int):
            candidates = candidates[:limit]
        memberships[universe_id] = [
            {
                "as_of_date": row["as_of_date"],
                "universe_id": universe_id,
                "ticker": row["ticker"],
                "rank": index + 1,
                "data_cutoff_at": row.get("data_cutoff_at"),
            }
            for index, row in enumerate(candidates)
        ]
    return memberships


def build_historical_universe_memberships(
    security_panel_history: list[dict[str, Any]],
    universe_specs: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    memberships: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, Any] = {}
    rows_by_as_of: dict[str, list[dict[str, Any]]] = {}
    for row in security_panel_history:
        effective_date = row.get("effective_date")
        if not effective_date:
            continue
        rows_by_as_of.setdefault(str(row["as_of_date"]), []).append(row)

    for spec in universe_specs.get("universes", []):
        if spec.get("enabled", True) is False:
            continue
        universe_id = spec["universe_id"]
        output: list[dict[str, Any]] = []
        included_counts: list[int] = []
        eligible_counts: list[int] = []
        duplicate_membership_count = 0
        duplicate_rank_count = 0
        top_n_underfilled_date_count = 0
        seen_memberships: set[tuple[str, str, str]] = set()
        seen_ranks: set[tuple[str, str, int]] = set()
        for as_of_date in sorted(rows_by_as_of):
            candidates = [
                row for row in rows_by_as_of[as_of_date] if _passes_filters(row, spec.get("filters", []))
            ]
            eligible_counts.append(len(candidates))
            candidates = _sort_candidates(candidates, spec.get("rank_by", []))
            limit = spec.get("limit")
            if isinstance(limit, int):
                candidates = candidates[:limit]
            included_for_date = 0
            for index, row in enumerate(candidates, start=1):
                effective_date = str(row["effective_date"])
                membership_key = (effective_date, universe_id, str(row["ticker"]))
                rank_key = (effective_date, universe_id, index)
                membership_seen = membership_key in seen_memberships
                rank_seen = rank_key in seen_ranks
                if membership_seen:
                    duplicate_membership_count += 1
                if rank_seen:
                    duplicate_rank_count += 1
                if membership_seen or rank_seen:
                    continue
                seen_memberships.add(membership_key)
                seen_ranks.add(rank_key)
                output.append(_membership_row(row, universe_id, index))
                included_for_date += 1
            included_counts.append(included_for_date)
            if isinstance(limit, int) and eligible_counts[-1] >= limit and included_for_date != limit:
                top_n_underfilled_date_count += 1
        memberships[universe_id] = output
        diagnostics[universe_id] = {
            "universe_id": universe_id,
            "as_of_date_count": len(rows_by_as_of),
            "candidate_count": sum(eligible_counts),
            "included_count": len(output),
            "excluded_count": sum(eligible_counts) - len(output),
            "top_n_limit": spec.get("limit"),
            "max_included_count": max(included_counts) if included_counts else 0,
            "top_n_underfilled_date_count": top_n_underfilled_date_count,
            "duplicate_universe_effective_ticker_count": duplicate_membership_count,
            "duplicate_universe_effective_rank_count": duplicate_rank_count,
        }
    return memberships, diagnostics


def _passes_filters(row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
    for rule in filters:
        field = rule["field"]
        op = rule["op"]
        value = rule.get("value")
        row_value = row.get(field)
        if op == "eq" and row_value != value:
            return False
        if op == "gte" and (row_value is None or row_value < value):
            return False
        if op == "not_null" and row_value is None:
            return False
    return True


def _sort_candidates(rows: list[dict[str, Any]], rank_by: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = list(rows)
    for rule in reversed(rank_by):
        field = rule["field"]
        reverse = rule.get("direction") == "desc"
        sorted_rows.sort(key=lambda row: (row.get(field) is None, row.get(field)), reverse=reverse)
    return sorted_rows


def _membership_row(row: dict[str, Any], universe_id: str, rank: int) -> dict[str, Any]:
    return {
        "as_of_date": row["as_of_date"],
        "effective_date": row["effective_date"],
        "universe_id": universe_id,
        "ticker": row["ticker"],
        "rank": rank,
        "included": True,
        "reason": "selected",
        "market": row.get("market"),
        "security_type": row.get("security_type"),
        "listed": row.get("listed"),
        "tradable": row.get("tradable"),
        "close": row.get("close"),
        "adj_close": row.get("adj_close"),
        "market_cap": row.get("market_cap"),
        "adv20": row.get("adv20"),
        "data_cutoff_at": row.get("data_cutoff_at"),
    }
