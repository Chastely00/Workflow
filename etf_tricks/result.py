from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from numbers import Number, Real
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .registry import ETF_IDS


_RESULT_TABLE_NAMES = (
    "daily_etf",
    "daily_holdings",
    "trades",
    "monthly_targets",
    "candidate_audit",
    "diagnostics",
)
_RESULT_MANIFEST_KEYS = {"format_version", "metadata", "metadata_sha256", "tables"}
_MANIFEST_HASH_ARTIFACTS = {
    "trading_calendar",
    "daily_price_volume",
    "daily_chip",
    "monthly_sales",
    "financial_statement_raw",
    "security_master",
    "daily_market_state",
}
_MARKET_STATE_CONFIG = {
    "formation_admission": "TRADING_ONLY",
    "execution_admission": "SAME_SESSION_TRADING_AND_EXCHANGE_TRADABLE",
    "amount_source": "PRIOR_SESSION_HOLDINGS_AUTHORITATIVE_TRADED_VALUE",
}
_MARKET_STATE_IDENTITY_KEYS = {
    "artifact_id",
    "manifest_sha256",
    "active_version",
    "classification_policy_version",
    "state_lattice_policy_version",
    "market_identity_policy_version",
    "dependency_certification_fingerprint",
}
_MARKET_STATE_IDENTITY_FIXED = {
    "artifact_id": "daily_market_state",
    "classification_policy_version": "daily_market_state_v3",
}
_MARKET_STATE_IDENTITY_DYNAMIC_VERSIONS = {
    "active_version",
    "state_lattice_policy_version",
    "market_identity_policy_version",
}
_LIFECYCLE_KEYS = {
    "state_row_count",
    "lifecycle_active_row_count",
    "lifecycle_inactive_row_count",
    "lifecycle_conflict_count",
    "identity_conflict_count",
    "formation_state_counts",
    "formation_exclusion_reason_counts",
}
_LIFECYCLE_EVIDENCE = "market_state_lifecycle_evidence"


class ResultMetadataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("result metadata is not canonically JSON serializable") from exc


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def market_state_identity_sha256(identity: object) -> str:
    if not isinstance(identity, dict) or set(identity) != _MARKET_STATE_IDENTITY_KEYS:
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_identity has missing or extra policy fields",
        )
    if any(
        identity[key] != expected
        for key, expected in _MARKET_STATE_IDENTITY_FIXED.items()
    ):
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_identity contains an ungoverned fixed policy value",
        )
    if not _is_sha256(identity["manifest_sha256"]) or not _is_sha256(
        identity["dependency_certification_fingerprint"]
    ):
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_identity contains an invalid digest",
        )
    if any(
        not isinstance(identity[key], str) or not identity[key].strip()
        for key in _MARKET_STATE_IDENTITY_DYNAMIC_VERSIONS
    ):
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_identity contains an invalid dynamic version",
        )
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def _validate_lifecycle_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _LIFECYCLE_KEYS:
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "lifecycle diagnostics have missing or extra fields",
        )
    scalar_keys = _LIFECYCLE_KEYS.difference(
        {"formation_state_counts", "formation_exclusion_reason_counts"}
    )
    if any(not _strict_nonnegative_int(value[key]) for key in scalar_keys):
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "lifecycle diagnostics counts must be nonnegative integers",
        )
    if (
        value["lifecycle_active_row_count"]
        + value["lifecycle_inactive_row_count"]
        != value["state_row_count"]
        or value["lifecycle_conflict_count"] != 0
        or value["identity_conflict_count"] != 0
    ):
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "lifecycle diagnostics violate governed row-count invariants",
        )
    mappings = (
        ("formation_state_counts", {"TRADING", "HALTED", "MISSING"}),
        (
            "formation_exclusion_reason_counts",
            {"formation_market_halted", "formation_market_state_missing"},
        ),
    )
    for key, allowed in mappings:
        counts = value[key]
        if (
            not isinstance(counts, dict)
            or not set(counts).issubset(allowed)
            or any(not _strict_nonnegative_int(count) for count in counts.values())
        ):
            raise ResultMetadataError(
                "lifecycle_diagnostics_mismatch",
                f"lifecycle diagnostics {key} are invalid",
            )
    return value


def lifecycle_diagnostics_from_tables(
    candidate_audit: pd.DataFrame, diagnostics: pd.DataFrame
) -> dict[str, object]:
    if "diagnostic" not in diagnostics or "lifecycle_evidence_json" not in diagnostics:
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "diagnostics table lacks canonical lifecycle evidence",
        )
    evidence = diagnostics[diagnostics["diagnostic"].eq(_LIFECYCLE_EVIDENCE)]
    if len(evidence) != 1:
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "diagnostics table must contain exactly one lifecycle evidence row",
        )
    raw = evidence.iloc[0]["lifecycle_evidence_json"]
    if not isinstance(raw, str):
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "lifecycle evidence must be canonical JSON text",
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "lifecycle evidence is malformed JSON",
        ) from exc
    if canonical_json_bytes(parsed).decode("utf-8") != raw:
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "lifecycle evidence is not canonically serialized",
        )
    payload = _validate_lifecycle_payload(parsed)

    if {
        "formation_date",
        "ticker",
        "formation_market_state",
    }.issubset(candidate_audit.columns):
        unique_states = candidate_audit.loc[
            :, ["formation_date", "ticker", "formation_market_state"]
        ].drop_duplicates()
        state_counts = unique_states["formation_market_state"].value_counts(sort=False)
        expected_states = {
            str(key): int(count) for key, count in state_counts.items() if pd.notna(key)
        }
        if payload["formation_state_counts"] != expected_states:
            raise ResultMetadataError(
                "lifecycle_diagnostics_mismatch",
                "formation-state diagnostics do not match candidate_audit",
            )
    if "exclusion_reason" in candidate_audit.columns:
        governed_reasons = {
            "formation_market_halted",
            "formation_market_state_missing",
        }
        reason_counts = candidate_audit.loc[
            candidate_audit["exclusion_reason"].isin(governed_reasons),
            "exclusion_reason",
        ].value_counts(sort=False)
        expected_reasons = {
            str(key): int(count) for key, count in reason_counts.items()
        }
        if payload["formation_exclusion_reason_counts"] != expected_reasons:
            raise ResultMetadataError(
                "lifecycle_diagnostics_mismatch",
                "formation exclusion diagnostics do not match candidate_audit",
            )
    return payload


def append_lifecycle_evidence(
    diagnostics: pd.DataFrame, lifecycle_diagnostics: dict[str, object]
) -> pd.DataFrame:
    payload = _validate_lifecycle_payload(lifecycle_diagnostics)
    if "diagnostic" in diagnostics and diagnostics["diagnostic"].eq(
        _LIFECYCLE_EVIDENCE
    ).any():
        raise ValueError("diagnostics already contain lifecycle evidence")
    row = {
        column: (
            pd.NaT
            if pd.api.types.is_datetime64_any_dtype(diagnostics[column].dtype)
            else (
                np.nan
                if pd.api.types.is_numeric_dtype(diagnostics[column].dtype)
                else None
            )
        )
        for column in diagnostics.columns
    }
    row.update(
        {
            "etf_id": "__MARKET_STATE__",
            "diagnostic": _LIFECYCLE_EVIDENCE,
            "lifecycle_evidence_json": canonical_json_bytes(payload).decode("utf-8"),
        }
    )
    return pd.concat([diagnostics, pd.DataFrame([row])], ignore_index=True, sort=False)


def validate_governed_result_metadata(
    metadata: object,
    candidate_audit: pd.DataFrame,
    diagnostics: pd.DataFrame,
) -> None:
    required_top = {
        "run_config",
        "manifest_hashes",
        "spec_hash",
        "market_state_identity",
        "market_state_config",
        "lifecycle_diagnostics",
    }
    if not isinstance(metadata, dict) or not required_top.issubset(metadata):
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "result metadata lacks governed market-state fields",
        )
    config = metadata["market_state_config"]
    expected_config_keys = {"scan_start_date", "scan_end_date", *_MARKET_STATE_CONFIG}
    if not isinstance(config, dict) or set(config) != expected_config_keys:
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_config has missing or extra policy fields",
        )
    if any(config[key] != expected for key, expected in _MARKET_STATE_CONFIG.items()):
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_config contains an ungoverned policy value",
        )
    try:
        scan_start = pd.Timestamp(config["scan_start_date"])
        scan_end = pd.Timestamp(config["scan_end_date"])
    except (TypeError, ValueError) as exc:
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_config contains malformed scan bounds",
        ) from exc
    if pd.isna(scan_start) or pd.isna(scan_end) or scan_start > scan_end:
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "market_state_config contains invalid scan bounds",
        )

    identity = metadata["market_state_identity"]
    market_state_identity_sha256(identity)
    manifest_hashes = metadata["manifest_hashes"]
    if (
        not isinstance(manifest_hashes, dict)
        or set(manifest_hashes) != _MANIFEST_HASH_ARTIFACTS
        or any(not _is_sha256(value) for value in manifest_hashes.values())
        or not _is_sha256(metadata["spec_hash"])
        or identity["manifest_sha256"] != manifest_hashes["daily_market_state"]
    ):
        raise ResultMetadataError(
            "market_state_metadata_mismatch",
            "result source and market-state identity hashes are invalid",
        )
    reconstructed = lifecycle_diagnostics_from_tables(candidate_audit, diagnostics)
    if metadata["lifecycle_diagnostics"] != reconstructed:
        raise ResultMetadataError(
            "lifecycle_diagnostics_mismatch",
            "metadata lifecycle diagnostics do not match hashed result tables",
        )


@dataclass(frozen=True)
class ETFTrickResultHandle:
    output_dir: Path
    manifest_sha256: str
    market_state_identity_sha256: str
    manifest: dict[str, Any] | None = None

    def __getitem__(self, key: str) -> Any:
        if self.manifest is None:
            raise KeyError("result handle has no embedded manifest inventory")
        return self.manifest[key]


def _sequential_float_sum(values: pd.Series) -> float:
    total = 0.0
    for value in values:
        total += float(value)
    return total


def _strict_boolean_mask(values: pd.Series, expected: bool) -> pd.Series:
    return values.map(
        lambda value: isinstance(value, (bool, np.bool_))
        and bool(value) is expected
    )


def _finite_nonnegative_number_mask(values: pd.Series) -> pd.Series:
    return values.map(
        lambda value: not isinstance(value, (bool, np.bool_))
        and isinstance(value, Real)
        and np.isfinite(float(value))
        and float(value) >= 0.0
    )


def _normalize_security_master(security_master: pd.DataFrame) -> pd.DataFrame:
    master = security_master.loc[:, ["ticker", "delist_date"]].copy()
    if master["ticker"].isna().any() or not master["ticker"].map(
        lambda value: isinstance(value, str) and bool(value)
    ).all():
        raise ValueError("security_master ticker dtype is invalid")
    if master["ticker"].duplicated().any():
        raise ValueError("security_master contains duplicate ticker keys")

    raw_dates = master["delist_date"]
    non_null = raw_dates.notna()
    valid_date_type = raw_dates.map(
        lambda value: pd.isna(value)
        or (
            not isinstance(value, (bool, np.bool_, Number))
            and isinstance(value, (str, pd.Timestamp, datetime, date))
        )
    )
    if not valid_date_type.all():
        raise ValueError("security_master delist_date dtype is invalid")
    parsed = pd.to_datetime(raw_dates, errors="coerce")
    if (non_null & parsed.isna()).any():
        raise ValueError("security_master contains invalid delist_date")
    master["delist_date"] = parsed.dt.normalize()
    return master


def _validate_market_state_amounts(market_state: pd.DataFrame) -> pd.DataFrame:
    amounts = market_state.copy()
    if amounts["date"].isna().any():
        raise ValueError("market_state contains invalid date")
    amounts["date"] = pd.to_datetime(amounts["date"], errors="coerce")
    if amounts["date"].isna().any():
        raise ValueError("market_state contains invalid date")
    if amounts["ticker"].isna().any() or not amounts["ticker"].map(
        lambda value: isinstance(value, str) and bool(value)
    ).all():
        raise ValueError("market_state ticker dtype is invalid")
    if amounts.duplicated(["date", "ticker"]).any():
        raise ValueError("market_state contains duplicate date-ticker keys")

    state = amounts["market_state"]
    amount_state = amounts["amount_state"]
    raw_amount = amounts["authoritative_traded_value"]
    amount_valid = _finite_nonnegative_number_mask(raw_amount)
    amount_null = raw_amount.isna()
    zero_false = _strict_boolean_mask(amounts["amount_zero_authorized"], False)
    zero_true = _strict_boolean_mask(amounts["amount_zero_authorized"], True)
    exchange_true = _strict_boolean_mask(amounts["exchange_tradable"], True)
    exchange_false = _strict_boolean_mask(amounts["exchange_tradable"], False)
    exchange_null = amounts["exchange_tradable"].isna()

    trading_observed = (
        state.eq("TRADING")
        & amount_state.eq("OBSERVED")
        & zero_false
        & amount_valid
        & exchange_true
    )
    halted_observed = (
        state.eq("HALTED")
        & amount_state.eq("OBSERVED")
        & zero_false
        & amount_valid
        & exchange_false
    )
    halted_zero = (
        state.eq("HALTED")
        & amount_state.eq("ZERO_AUTHORIZED")
        & zero_true
        & amount_valid
        & raw_amount.eq(0)
        & exchange_false
    )
    missing = (
        state.eq("MISSING")
        & amount_state.eq("MISSING")
        & zero_false
        & amount_null
        & exchange_null
    )
    if not (trading_observed | halted_observed | halted_zero | missing).all():
        raise ValueError("market_state amount cross-field or dtype invariant failed")
    amounts["authoritative_traded_value"] = pd.to_numeric(
        raw_amount, errors="coerce"
    )
    return amounts


def attach_etf_amount(
    daily_etf: pd.DataFrame,
    daily_holdings: pd.DataFrame,
    market_state: pd.DataFrame,
    security_master: pd.DataFrame,
) -> pd.DataFrame:
    daily = daily_etf.copy()
    required_daily = {"date", "etf_id"}
    required_holdings = {"date", "etf_id", "ticker", "actual_weight"}
    required_market = {
        "date", "ticker", "market_state", "amount_state",
        "amount_zero_authorized", "authoritative_traded_value",
        "exchange_tradable",
    }
    required_master = {"ticker", "delist_date"}
    for name, frame, required in (
        ("daily_etf", daily, required_daily),
        ("daily_holdings", daily_holdings, required_holdings),
        ("market_state", market_state, required_market),
        ("security_master", security_master, required_master),
    ):
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"{name} missing columns: {missing}")

    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    if daily[["date", "etf_id"]].isna().any().any():
        raise ValueError("daily_etf contains invalid date or etf_id")
    daily["etf_id"] = daily["etf_id"].astype(str)
    holdings = daily_holdings.copy()
    holdings["date"] = pd.to_datetime(holdings["date"], errors="coerce")
    if holdings[["date", "etf_id", "ticker"]].isna().any().any():
        raise ValueError("daily_holdings contains invalid keys")
    if not holdings["ticker"].map(
        lambda value: isinstance(value, str) and bool(value)
    ).all():
        raise ValueError("daily_holdings ticker dtype is invalid")
    holdings["etf_id"] = holdings["etf_id"].astype(str)
    amounts = _validate_market_state_amounts(market_state)
    if daily.duplicated(["date", "etf_id"]).any():
        raise ValueError("daily_etf contains duplicate date-etf_id keys")
    if holdings.duplicated(["date", "etf_id", "ticker"]).any():
        raise ValueError("daily_holdings contains duplicate keys")
    master = _normalize_security_master(security_master)
    unknown_lifecycle = set(holdings["ticker"]).difference(master["ticker"])
    if unknown_lifecycle:
        raise ValueError("daily_holdings ticker is absent from security_master")

    if daily.empty:
        daily["etf_amount"] = pd.Series(dtype="float64")
        daily["missing_traded_value_count"] = pd.Series(dtype="int64")
        daily["status_missing_count"] = pd.Series(dtype="int64")
        daily["status_zero_authorized_count"] = pd.Series(dtype="int64")
        daily["amount_quality_state"] = pd.Series(dtype="object")
    else:
        daily["_result_row"] = np.arange(len(daily), dtype=np.int64)
        pairs = daily.loc[:, ["_result_row", "date", "etf_id"]].sort_values(
            ["etf_id", "date"], kind="stable"
        )
        pairs["holding_date"] = pairs.groupby("etf_id", sort=False)["date"].shift()
        previous_holdings = holdings.loc[
            :, ["date", "etf_id", "ticker", "actual_weight"]
        ].rename(columns={"date": "holding_date"})
        previous_holdings["_holding_order"] = np.arange(
            len(previous_holdings), dtype=np.int64
        )
        aligned = pairs.merge(
            previous_holdings,
            on=["holding_date", "etf_id"],
            how="left",
            sort=False,
            validate="one_to_many",
        )
        aligned = aligned.merge(master, on="ticker", how="left", validate="many_to_one")

        has_holding = aligned["ticker"].notna()
        valid_weight = _finite_nonnegative_number_mask(aligned["actual_weight"])
        invalid_weight = has_holding & ~valid_weight
        if invalid_weight.any():
            raise ValueError(
                "daily_holdings actual_weight dtype must be finite and non-negative"
            )
        weights = pd.to_numeric(aligned["actual_weight"], errors="coerce")
        delisted = (
            has_holding
            & aligned["delist_date"].notna()
            & aligned["delist_date"].le(aligned["date"])
        )
        aligned.loc[delisted, ["ticker", "actual_weight"]] = pd.NA
        aligned = aligned.merge(
            amounts.loc[:, list(required_market)],
            on=["date", "ticker"],
            how="left",
            sort=False,
            validate="many_to_one",
        )
        aligned = aligned.sort_values(
            ["_result_row", "_holding_order"], kind="stable", na_position="last"
        )
        has_holding = aligned["ticker"].notna()
        weights = pd.to_numeric(aligned["actual_weight"], errors="coerce")
        amount = pd.to_numeric(
            aligned["authoritative_traded_value"], errors="coerce"
        )
        observed = has_holding & aligned["amount_state"].eq("OBSERVED")
        zero = has_holding & aligned["amount_state"].eq("ZERO_AUTHORIZED")
        missing_amount = has_holding & (
            aligned["market_state"].isna()
            | aligned["market_state"].eq("MISSING")
        )
        aligned["_missing_amount"] = missing_amount.astype("int64")
        aligned["_zero_authorized"] = zero.astype("int64")
        aligned["_amount_contribution"] = (
            amount.where(observed, 0.0) * weights.where(has_holding, 0.0)
        )

        grouped = aligned.groupby("_result_row", sort=False)
        amount_by_row = grouped["_amount_contribution"].agg(
            _sequential_float_sum
        )
        missing_by_row = grouped["_missing_amount"].sum()
        zero_by_row = grouped["_zero_authorized"].sum()
        daily["etf_amount"] = daily["_result_row"].map(amount_by_row).fillna(0.0)
        daily["missing_traded_value_count"] = (
            daily["_result_row"].map(missing_by_row).fillna(0).astype("int64")
        )
        daily["status_missing_count"] = daily["missing_traded_value_count"]
        daily["status_zero_authorized_count"] = (
            daily["_result_row"].map(zero_by_row).fillna(0).astype("int64")
        )
        daily["amount_quality_state"] = np.where(daily["status_missing_count"].gt(0), "MISSING", "READY")
        daily = daily.drop(columns="_result_row")
    if "has_data_quality_flag" not in daily.columns:
        daily["has_data_quality_flag"] = False
    daily["has_data_quality_flag"] = (
        daily["has_data_quality_flag"].fillna(False).astype(bool)
        | daily["missing_traded_value_count"].gt(0)
    )
    return daily.sort_values(["date", "etf_id"], kind="stable").reset_index(drop=True)


@dataclass
class ETFTrickResult:
    daily_etf: pd.DataFrame
    daily_holdings: pd.DataFrame
    trades: pd.DataFrame
    monthly_targets: pd.DataFrame
    candidate_audit: pd.DataFrame
    diagnostics: pd.DataFrame
    metadata: dict[str, Any]
    result_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        required = {"date", "etf_id", "nav", "daily_return", "etf_amount"}
        missing = sorted(required.difference(self.daily_etf.columns))
        if missing:
            raise ValueError(f"daily_etf missing columns: {missing}")
        frame = self.daily_etf.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["etf_id"] = frame["etf_id"].astype(str)
        if frame[["date", "etf_id"]].isna().any().any():
            raise ValueError("daily_etf contains invalid date or etf_id")
        if frame.duplicated(["date", "etf_id"]).any():
            raise ValueError("daily_etf contains duplicate date-etf_id keys")
        nav = pd.to_numeric(frame["nav"], errors="coerce")
        if (~np.isfinite(nav) | nav.le(0)).any():
            raise ValueError("daily_etf nav must be finite and positive")
        frame["nav"] = nav
        self.daily_etf = frame.sort_values(["date", "etf_id"], kind="stable").reset_index(drop=True)

    @property
    def daily(self) -> pd.DataFrame:
        return self.daily_etf

    @property
    def holdings(self) -> pd.DataFrame:
        return self.daily_holdings

    @property
    def targets(self) -> pd.DataFrame:
        return self.monthly_targets

    @property
    def candidates(self) -> pd.DataFrame:
        return self.candidate_audit

    @property
    def nav(self) -> pd.DataFrame:
        return self._wide("nav")

    @property
    def returns(self) -> pd.DataFrame:
        return self._wide("daily_return")

    @property
    def amount(self) -> pd.DataFrame:
        return self._wide("etf_amount")

    def for_ffd(self, etf_id: str) -> pd.DataFrame:
        if etf_id not in ETF_IDS:
            raise KeyError(f"unknown ETF ID: {etf_id}")
        columns = ["date", "etf_id", "nav", "daily_return", "etf_amount"]
        return (
            self.daily_etf[self.daily_etf["etf_id"].eq(etf_id)]
            .loc[:, columns]
            .sort_values("date", kind="stable")
            .reset_index(drop=True)
        )

    def write(self, output_dir: str | Path) -> ETFTrickResultHandle:
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        validate_governed_result_metadata(
            self.metadata, self.candidate_audit, self.diagnostics
        )
        tables = {
            "daily_etf": self.daily_etf,
            "daily_holdings": self.daily_holdings,
            "trades": self.trades,
            "monthly_targets": self.monthly_targets,
            "candidate_audit": self.candidate_audit,
            "diagnostics": self.diagnostics,
        }
        table_manifest: dict[str, dict[str, Any]] = {}
        for name, frame in tables.items():
            final_path = output / f"{name}.parquet"
            temporary_path = output / f".{name}.tmp.parquet"
            frame.to_parquet(temporary_path, index=False)
            temporary_path.replace(final_path)
            table_manifest[name] = {
                "path": final_path.name,
                "rows": len(frame),
                "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
            }
        manifest = {
            "format_version": 1,
            "metadata": self.metadata,
            "metadata_sha256": hashlib.sha256(
                canonical_json_bytes(self.metadata)
            ).hexdigest(),
            "tables": table_manifest,
        }
        manifest_path = output / "result_manifest.json"
        temporary_manifest = output / ".result_manifest.tmp.json"
        manifest_bytes = canonical_json_bytes(manifest)
        temporary_manifest.write_bytes(manifest_bytes)
        temporary_manifest.replace(manifest_path)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        self.result_manifest_sha256 = manifest_sha256
        return ETFTrickResultHandle(
            output_dir=output,
            manifest_sha256=manifest_sha256,
            market_state_identity_sha256=market_state_identity_sha256(
                self.metadata["market_state_identity"]
            ),
            manifest=manifest,
        )

    @classmethod
    def read(
        cls,
        output_dir: str | Path,
        *,
        expected_handle: ETFTrickResultHandle,
    ) -> "ETFTrickResult":
        output = Path(output_dir).resolve()
        manifest_path = output / "result_manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"missing ETF Trick result manifest: {manifest_path}")
        if not isinstance(expected_handle, ETFTrickResultHandle):
            raise TypeError("expected_handle must be an ETFTrickResultHandle")
        if expected_handle.output_dir.resolve() != output:
            raise ValueError("result handle output directory mismatch")
        if not _is_sha256(expected_handle.manifest_sha256):
            raise ValueError("expected result manifest SHA-256 is invalid")
        if not _is_sha256(expected_handle.market_state_identity_sha256):
            raise ValueError("expected market-state identity SHA-256 is invalid")
        manifest_bytes = manifest_path.read_bytes()
        observed_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if observed_manifest_sha256 != expected_handle.manifest_sha256:
            raise ValueError("result manifest hash mismatch")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("result manifest is not valid canonical JSON") from exc
        if canonical_json_bytes(manifest) != manifest_bytes:
            raise ValueError("result manifest is not canonically serialized")
        if not isinstance(manifest, dict) or set(manifest) != _RESULT_MANIFEST_KEYS:
            raise ValueError("result manifest has missing or extra fields")
        if manifest.get("format_version") != 1:
            raise ValueError("unsupported ETF Trick result format version")
        metadata = manifest.get("metadata")
        if (
            not _is_sha256(manifest.get("metadata_sha256"))
            or hashlib.sha256(canonical_json_bytes(metadata)).hexdigest()
            != manifest["metadata_sha256"]
        ):
            raise ValueError("result metadata hash mismatch")
        if not isinstance(metadata, dict) or (
            market_state_identity_sha256(metadata.get("market_state_identity"))
            != expected_handle.market_state_identity_sha256
        ):
            raise ValueError("market-state identity authority mismatch")
        table_entries = manifest.get("tables")
        if not isinstance(table_entries, dict) or set(table_entries) != set(
            _RESULT_TABLE_NAMES
        ):
            raise ValueError("result manifest table inventory is invalid")
        frames: dict[str, pd.DataFrame] = {}
        seen_paths: set[Path] = set()
        for name in _RESULT_TABLE_NAMES:
            entry = table_entries.get(name)
            if not isinstance(entry, dict) or set(entry) != {"path", "rows", "sha256"}:
                raise ValueError(f"result manifest missing table: {name}")
            if (
                not _strict_nonnegative_int(entry["rows"])
                or not _is_sha256(entry["sha256"])
            ):
                raise ValueError(f"result manifest has invalid table inventory: {name}")
            path = (output / str(entry["path"])).resolve()
            if (
                not path.is_relative_to(output)
                or not path.is_file()
                or path in seen_paths
            ):
                raise ValueError(f"invalid or missing result table path: {name}")
            seen_paths.add(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry.get("sha256"):
                raise ValueError(f"result table hash mismatch: {name}")
            frame = pd.read_parquet(path)
            if len(frame) != entry.get("rows"):
                raise ValueError(f"result table row-count mismatch: {name}")
            frames[name] = frame
        validate_governed_result_metadata(
            metadata, frames["candidate_audit"], frames["diagnostics"]
        )
        return cls(
            daily_etf=frames["daily_etf"],
            daily_holdings=frames["daily_holdings"],
            trades=frames["trades"],
            monthly_targets=frames["monthly_targets"],
            candidate_audit=frames["candidate_audit"],
            diagnostics=frames["diagnostics"],
            metadata=metadata,
            result_manifest_sha256=observed_manifest_sha256,
        )

    def _wide(self, value: str) -> pd.DataFrame:
        wide = self.daily_etf.pivot(index="date", columns="etf_id", values=value)
        columns = [etf_id for etf_id in ETF_IDS if etf_id in wide.columns]
        result = wide.reindex(columns=columns).sort_index()
        result.columns.name = None
        return result
