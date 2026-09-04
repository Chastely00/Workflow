from __future__ import annotations

import pandas as pd


_KEY_COLUMNS = ["etf_id", "bar_id", "feature_available_at"]


def merge_chip_feature_extension(
    base_extension: pd.DataFrame,
    chip_extension: pd.DataFrame,
) -> pd.DataFrame:
    """Merge a complete existing Tier 1 extension with its chip sidecar."""
    for label, frame in (("base extension", base_extension), ("chip extension", chip_extension)):
        if missing := set(_KEY_COLUMNS).difference(frame.columns):
            raise ValueError(f"{label} missing columns: {sorted(missing)}")
        if frame.duplicated(["etf_id", "bar_id"]).any():
            raise ValueError(f"{label} has duplicate etf_id-bar_id keys")
    base = base_extension.copy()
    chip = chip_extension.copy()
    for frame in (base, chip):
        frame["feature_available_at"] = pd.to_datetime(
            frame["feature_available_at"], errors="coerce", utc=True
        ).astype("datetime64[ns, UTC]")
        if frame["feature_available_at"].isna().any():
            raise ValueError("feature extension requires valid availability")
    overlap = (set(base.columns) & set(chip.columns)) - set(_KEY_COLUMNS)
    if overlap:
        raise ValueError(f"chip extension overlaps base columns: {sorted(overlap)}")
    clock = base[_KEY_COLUMNS].merge(
        chip[_KEY_COLUMNS],
        on=["etf_id", "bar_id"],
        how="outer",
        suffixes=("_base", "_chip"),
        indicator=True,
        validate="one_to_one",
    )
    if not clock["_merge"].eq("both").all():
        raise ValueError("chip extension must cover exactly the base feature keys")
    if not clock["feature_available_at_base"].equals(
        clock["feature_available_at_chip"]
    ):
        raise ValueError("chip extension availability must equal base feature availability")
    columns = [column for column in chip.columns if column not in _KEY_COLUMNS]
    return base.merge(
        chip[["etf_id", "bar_id", *columns]],
        on=["etf_id", "bar_id"],
        how="inner",
        validate="one_to_one",
    ).sort_values(["etf_id", "bar_id"], kind="stable").reset_index(drop=True)
