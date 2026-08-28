from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from .config import AFMLContractError, LabelConfig, config_sha256


@dataclass(frozen=True)
class LabelTables:
    events: pd.DataFrame
    labels: pd.DataFrame


class TripleBarrierLabeler:
    """Create directional labels from future daily-close paths.

    Event creation time and label realization time remain separate. A terminal
    event without the full configured future-bar horizon stays unresolved even
    if a short partial path happens to cross a horizontal barrier.
    """

    def __init__(self, config: LabelConfig) -> None:
        self.config = config

    def build(
        self,
        features: pd.DataFrame,
        bars: pd.DataFrame,
        memberships: pd.DataFrame,
        split_cutoffs: Mapping[str, Mapping[str, object]],
    ) -> LabelTables:
        _require_unique(features, ("etf_id", "bar_id"), "features")
        _require_unique(bars, ("etf_id", "bar_id"), "bars")
        _require_unique(
            memberships, ("etf_id", "bar_id", "date"), "memberships"
        )
        self._validate_inputs(features, bars, memberships)
        cutoffs = _normalize_split_cutoffs(split_cutoffs)

        completed = bars[bars["bar_status"].eq("FINALIZED")].copy()
        completed["bar_end_date"] = pd.to_datetime(
            completed["bar_end_date"], errors="coerce"
        )
        completed["bar_available_at"] = pd.to_datetime(
            completed["bar_available_at"], errors="coerce"
        )
        completed = completed.sort_values(["etf_id", "bar_id"], kind="stable")
        completed["target_volatility"] = completed.groupby(
            "etf_id", sort=False
        )["log_return"].transform(
            lambda values: pd.to_numeric(values, errors="coerce")
            .ewm(
                span=self.config.volatility_span,
                adjust=False,
                min_periods=self.config.min_obs,
            )
            .std(bias=False)
        )
        feature_rows = features.copy()
        feature_rows["feature_available_at"] = pd.to_datetime(
            feature_rows["feature_available_at"], errors="coerce"
        )
        feature_rows = feature_rows.sort_values(["etf_id", "bar_id"], kind="stable")

        members = memberships.copy()
        members["date"] = pd.to_datetime(members["date"], errors="coerce")
        availability_column = (
            "member_available_at"
            if "member_available_at" in members.columns
            else "source_available_at"
        )
        members[availability_column] = pd.to_datetime(
            members[availability_column], errors="coerce"
        )
        members = members.sort_values(
            ["etf_id", "bar_id", "date"], kind="stable"
        )

        event_rows: list[dict[str, object]] = []
        label_rows: list[dict[str, object]] = []
        for etf_id, candidates in feature_rows.groupby("etf_id", sort=False):
            etf_bars = completed[completed["etf_id"].eq(etf_id)].reset_index(drop=True)
            position_by_id = {
                bar_id: position
                for position, bar_id in enumerate(etf_bars["bar_id"].tolist())
            }
            etf_members = members[members["etf_id"].eq(etf_id)]
            for candidate in candidates.to_dict("records"):
                bar_id = candidate["bar_id"]
                if bar_id not in position_by_id:
                    raise AFMLContractError(
                        f"feature references non-finalized bar: {(etf_id, bar_id)}"
                    )
                position = position_by_id[bar_id]
                bar = etf_bars.iloc[position]
                event, label = self._build_one(
                    str(etf_id),
                    candidate,
                    bar,
                    position,
                    etf_bars,
                    etf_members,
                    availability_column,
                    cutoffs,
                )
                event_rows.append(event)
                label_rows.append(label)

        events = pd.DataFrame(event_rows)
        labels = pd.DataFrame(label_rows)
        events = _add_overlap_evidence(events, labels, members)
        return LabelTables(
            events=events.sort_values(["etf_id", "t0_bar_id"], kind="stable").reset_index(
                drop=True
            ),
            labels=labels.sort_values(["etf_id", "t0_bar_id"], kind="stable").reset_index(
                drop=True
            ),
        )

    def _build_one(
        self,
        etf_id: str,
        feature: dict[str, object],
        bar: pd.Series,
        position: int,
        etf_bars: pd.DataFrame,
        etf_members: pd.DataFrame,
        availability_column: str,
        cutoffs: dict[str, dict[str, pd.Timestamp]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        event_id = f"{etf_id}-{bar['bar_id']}"
        t0_date = pd.Timestamp(bar["bar_end_date"])
        event_available_at = pd.Timestamp(feature["feature_available_at"])
        entry_price = float(bar["close_nav"])
        sigma = float(bar["target_volatility"])
        sigma_valid = np.isfinite(sigma) and sigma > 0
        log_entry = np.log(entry_price)
        upper_log = log_entry + self.config.pt_mult * sigma if sigma_valid else np.nan
        lower_log = log_entry - self.config.sl_mult * sigma if sigma_valid else np.nan
        upper_price = float(np.exp(upper_log)) if sigma_valid else np.nan
        lower_price = float(np.exp(lower_log)) if sigma_valid else np.nan
        vertical_position = position + self.config.vertical_bars
        has_full_horizon = vertical_position < len(etf_bars)
        vertical_bar = etf_bars.iloc[vertical_position] if has_full_horizon else None
        vertical_id = vertical_bar["bar_id"] if has_full_horizon else np.nan
        vertical_date = (
            pd.Timestamp(vertical_bar["bar_end_date"]) if has_full_horizon else pd.NaT
        )

        event = {
            "etf_id": etf_id,
            "event_id": event_id,
            "t0_bar_id": bar["bar_id"],
            "t0_observation_date": t0_date,
            "event_available_at": event_available_at,
            "entry_reference_price": entry_price,
            "target_volatility": sigma if sigma_valid else np.nan,
            "upper_barrier_log": upper_log,
            "lower_barrier_log": lower_log,
            "upper_barrier_price": upper_price,
            "lower_barrier_price": lower_price,
            "vertical_bar_id": vertical_id,
            "vertical_date": vertical_date,
            "pt_mult": self.config.pt_mult,
            "sl_mult": self.config.sl_mult,
            "vertical_bars": self.config.vertical_bars,
            "volatility_method": self.config.volatility_method,
            "volatility_span": self.config.volatility_span,
            "volatility_min_obs": self.config.min_obs,
            "source_path_kind": self.config.source_path_kind,
            "label_config_hash": config_sha256(self.config),
        }
        outcome = {
            "etf_id": etf_id,
            "event_id": event_id,
            "t0_bar_id": bar["bar_id"],
            "t0_observation_date": t0_date,
            "event_available_at": event_available_at,
            "entry_reference_price": entry_price,
            "target_volatility": sigma if sigma_valid else np.nan,
            "upper_barrier_price": upper_price,
            "lower_barrier_price": lower_price,
            "vertical_bar_id": vertical_id,
            "vertical_date": vertical_date,
            "first_touch_type": pd.NA,
            "first_touch_date": pd.NaT,
            "first_touch_source_available_at": pd.NaT,
            "t1": pd.NaT,
            "label_available_at": pd.NaT,
            "realized_log_return": np.nan,
            "label": np.nan,
            "label_status": pd.NA,
            "pt_mult": self.config.pt_mult,
            "sl_mult": self.config.sl_mult,
            "vertical_bars": self.config.vertical_bars,
            "source_path_kind": self.config.source_path_kind,
            "label_config_hash": config_sha256(self.config),
        }
        if not has_full_horizon:
            outcome["label_status"] = "unresolved_tail"
            return event, _add_split_eligibility(outcome, cutoffs)
        if not sigma_valid:
            outcome["label_status"] = "insufficient_target_volatility"
            return event, _add_split_eligibility(outcome, cutoffs)

        future_ids = etf_bars.iloc[position + 1 : vertical_position + 1][
            "bar_id"
        ]
        path = etf_members[
            etf_members["bar_id"].isin(future_ids)
            & etf_members["date"].gt(t0_date)
            & etf_members["date"].le(vertical_date)
        ].sort_values(["date", "bar_id"], kind="stable")
        if path.empty:
            outcome["label_status"] = "missing_daily_close_path"
            return event, _add_split_eligibility(outcome, cutoffs)
        path_log = np.log(pd.to_numeric(path["nav"], errors="coerce"))
        if not np.isfinite(path_log).all():
            outcome["label_status"] = "invalid_daily_close_path"
            return event, _add_split_eligibility(outcome, cutoffs)
        touched = path_log.ge(upper_log) | path_log.le(lower_log)
        if touched.any():
            touch_position = int(np.flatnonzero(touched.to_numpy())[0])
            touch = path.iloc[touch_position]
            touch_log = float(path_log.iloc[touch_position])
            touch_type = "upper" if touch_log >= upper_log else "lower"
            label = 1 if touch_type == "upper" else -1
            touch_available = pd.Timestamp(touch[availability_column])
            label_available = max(event_available_at, touch_available)
            outcome.update(
                {
                    "first_touch_type": touch_type,
                    "first_touch_date": pd.Timestamp(touch["date"]),
                    "first_touch_source_available_at": touch_available,
                    "t1": pd.Timestamp(touch["date"]),
                    "label_available_at": label_available,
                    "realized_log_return": touch_log - log_entry,
                    "label": label,
                    "label_status": "resolved",
                }
            )
            return event, _add_split_eligibility(outcome, cutoffs)

        vertical_price = float(vertical_bar["close_nav"])
        realized = float(np.log(vertical_price / entry_price))
        vertical_available = max(
            pd.Timestamp(vertical_bar["bar_available_at"]),
            pd.Timestamp(path[availability_column].max()),
        )
        label_available = max(event_available_at, vertical_available)
        if realized > 0:
            label: int | float = 1
            status = "resolved"
        elif realized < 0:
            label = -1
            status = "resolved"
        elif self.config.zero_return_policy == "zero_class":
            label = 0
            status = "resolved_zero_class"
        else:
            label = np.nan
            status = "zero_vertical_return"
        outcome.update(
            {
                "first_touch_type": "vertical",
                "first_touch_date": vertical_date,
                "first_touch_source_available_at": vertical_available,
                "t1": vertical_date,
                "label_available_at": label_available,
                "realized_log_return": realized,
                "label": label,
                "label_status": status,
            }
        )
        return event, _add_split_eligibility(outcome, cutoffs)

    @staticmethod
    def _validate_inputs(
        features: pd.DataFrame, bars: pd.DataFrame, memberships: pd.DataFrame
    ) -> None:
        required = {
            "features": {"etf_id", "bar_id", "feature_available_at"},
            "bars": {
                "etf_id",
                "bar_id",
                "bar_status",
                "bar_end_date",
                "close_nav",
                "log_return",
                "bar_available_at",
            },
            "memberships": {"etf_id", "bar_id", "date", "nav"},
        }
        frames = {
            "features": features,
            "bars": bars,
            "memberships": memberships,
        }
        for name, columns in required.items():
            missing = sorted(columns.difference(frames[name].columns))
            if missing:
                raise AFMLContractError(f"{name} missing required columns: {missing}")
        if not {"member_available_at", "source_available_at"}.intersection(
            memberships.columns
        ):
            raise AFMLContractError("memberships missing source availability column")


def _require_unique(frame: pd.DataFrame, key: tuple[str, ...], name: str) -> None:
    missing = sorted(set(key).difference(frame.columns))
    if missing:
        raise AFMLContractError(f"{name} missing key columns: {missing}")
    if frame.duplicated(list(key)).any():
        raise AFMLContractError(f"{name} has duplicate {key} keys")


def _normalize_split_cutoffs(
    raw: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, pd.Timestamp]]:
    result: dict[str, dict[str, pd.Timestamp]] = {}
    for name, values in raw.items():
        missing = {"observation_start", "observation_end", "decision_cutoff"}.difference(
            values
        )
        if missing:
            raise AFMLContractError(f"split {name!r} missing cutoff fields: {sorted(missing)}")
        start = pd.Timestamp(values["observation_start"])
        end = pd.Timestamp(values["observation_end"])
        cutoff = pd.Timestamp(values["decision_cutoff"])
        if start > end:
            raise AFMLContractError(f"split {name!r} has reversed observation bounds")
        result[str(name)] = {
            "observation_start": start,
            "observation_end": end,
            "decision_cutoff": cutoff,
        }
    return result


def _add_split_eligibility(
    row: dict[str, object], cutoffs: dict[str, dict[str, pd.Timestamp]]
) -> dict[str, object]:
    complete = row["label_status"] in {"resolved", "resolved_zero_class"}
    t0 = pd.Timestamp(row["t0_observation_date"])
    t1 = row["t1"]
    available = row["label_available_at"]
    event_available = pd.Timestamp(row["event_available_at"])
    for name, cutoff in cutoffs.items():
        observation_start = cutoff["observation_start"]
        observation_end = cutoff["observation_end"]
        decision_cutoff = cutoff["decision_cutoff"]
        eligible = (
            complete
            and pd.notna(t1)
            and pd.notna(available)
            and observation_start <= t0 <= observation_end
            and pd.Timestamp(t1) <= observation_end
            and event_available <= decision_cutoff
            and pd.Timestamp(available) <= decision_cutoff
        )
        row[f"eligible_for_{name}"] = bool(eligible)
    return row


def _add_overlap_evidence(
    events: pd.DataFrame, labels: pd.DataFrame, memberships: pd.DataFrame
) -> pd.DataFrame:
    output = events.copy()
    output["t1"] = output["event_id"].map(labels.set_index("event_id")["t1"])
    output["event_concurrency_at_t0"] = np.nan
    output["max_event_concurrency"] = np.nan
    output["average_uniqueness"] = np.nan
    for etf_id, indexes in output.groupby("etf_id", sort=False).groups.items():
        member_dates = pd.DatetimeIndex(
            pd.to_datetime(
                memberships[memberships["etf_id"].eq(etf_id)]["date"]
            ).dropna().unique()
        ).sort_values()
        rows = output.loc[indexes]
        resolved = rows[rows["t1"].notna()]
        if resolved.empty or member_dates.empty:
            continue
        concurrency = pd.Series(0, index=member_dates, dtype=int)
        intervals: dict[object, pd.DatetimeIndex] = {}
        for index, row in resolved.iterrows():
            active = member_dates[
                (member_dates >= pd.Timestamp(row["t0_observation_date"]))
                & (member_dates <= pd.Timestamp(row["t1"]))
            ]
            intervals[index] = active
            concurrency.loc[active] += 1
        for index, active in intervals.items():
            if active.empty:
                continue
            counts = concurrency.loc[active]
            output.at[index, "event_concurrency_at_t0"] = int(counts.iloc[0])
            output.at[index, "max_event_concurrency"] = int(counts.max())
            output.at[index, "average_uniqueness"] = float((1.0 / counts).mean())
    return output
