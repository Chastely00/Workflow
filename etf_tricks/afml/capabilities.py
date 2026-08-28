from __future__ import annotations

import hashlib
import json
from typing import Final

import numpy as np
import pandas as pd

from etf_tricks.data_gateway import DataGateway


_UNAVAILABLE_REQUIREMENTS: Final[tuple[tuple[str, tuple[str, ...], str], ...]] = (
    (
        "VPIN",
        ("tick_trade", "volume_bucket", "buy_sell_classification"),
        "VPIN requires tick trades, equal-volume buckets, and buy/sell classification.",
    ),
    (
        "KYLE_LAMBDA",
        ("price_change", "signed_order_flow", "aggressor_side"),
        "Kyle lambda requires signed order flow or a verified aggressor side.",
    ),
    (
        "ATR",
        ("synthetic_etf_open", "synthetic_etf_high", "synthetic_etf_low", "synthetic_etf_close"),
        "ATR requires true synchronized ETF Trick OHLC; constituent OHLC is not equivalent.",
    ),
    (
        "ADX",
        ("synthetic_etf_high", "synthetic_etf_low", "synthetic_etf_close"),
        "ADX requires true synchronized ETF Trick OHLC; constituent OHLC is not equivalent.",
    ),
    (
        "VIX",
        ("taiwan_implied_volatility", "source_available_at", "revision_identity"),
        "No manifest-declared PIT-safe Taiwan implied-volatility artifact is available.",
    ),
)


class SourceCapabilityAuditor:
    def __init__(self, gateway: DataGateway) -> None:
        self.gateway = gateway

    def audit(self) -> pd.DataFrame:
        evidence_at = pd.Timestamp.now(tz="UTC").isoformat()
        rows = [self._audit_ix0001(evidence_at)]
        for feature_id, required_fields, reason in _UNAVAILABLE_REQUIREMENTS:
            rows.append(
                {
                    "feature_id": feature_id,
                    "status": "UNAVAILABLE_SOURCE_GRAIN",
                    "required_fields": json.dumps(required_fields),
                    "observed_artifact": pd.NA,
                    "observed_columns": json.dumps(()),
                    "manifest_sha256": pd.NA,
                    "selected_rows_sha256": pd.NA,
                    "selected_row_count": 0,
                    "coverage_start": pd.NaT,
                    "coverage_end": pd.NaT,
                    "pit_policy": pd.NA,
                    "revision_status": "PIT_REVISION_UNVERIFIED",
                    "reason": reason,
                    "evidence_at": evidence_at,
                }
            )
        return pd.DataFrame(rows)

    def _audit_ix0001(self, evidence_at: str) -> dict[str, object]:
        manifest = self.gateway.load_manifest("daily_price_volume")
        required = ("date", "ticker", "close", "traded_value")
        frame = self.gateway.scan_artifact(
            "daily_price_volume",
            columns=required,
            filters=(("ticker", "==", "IX0001"),),
        )
        numeric_valid = (
            np.isfinite(pd.to_numeric(frame["close"], errors="coerce"))
            & np.isfinite(pd.to_numeric(frame["traded_value"], errors="coerce"))
            & frame["close"].gt(0)
            & frame["traded_value"].gt(0)
        )
        availability_field = manifest.get("availability_field")
        revision_policy = manifest.get("revision_policy")
        revision_verified = bool(availability_field) and revision_policy in {
            "append_only_vintages",
            "versioned",
            "immutable",
        }
        rows_valid = not frame.empty and bool(numeric_valid.all())
        if rows_valid and revision_verified:
            status = "AVAILABLE_VERIFIED"
            reason = (
                "IX0001 has positive close/traded_value rows plus explicit availability "
                "and revision evidence."
            )
            revision_status = "PIT_REVISION_VERIFIED"
        elif rows_valid:
            status = "PARTIAL_COVERAGE"
            reason = (
                "IX0001 observations are usable, but publication availability or revision "
                "history is not verified."
            )
            revision_status = "PIT_REVISION_UNVERIFIED"
        else:
            status = "PARTIAL_COVERAGE"
            reason = "IX0001 has no complete positive close/traded_value coverage."
            revision_status = "PIT_REVISION_UNVERIFIED"

        manifest_payload = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return {
            "feature_id": "IX0001",
            "status": status,
            "required_fields": json.dumps(required),
            "observed_artifact": "daily_price_volume",
            "observed_columns": json.dumps(required),
            "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "selected_rows_sha256": _hash_frame(frame),
            "selected_row_count": len(frame),
            "coverage_start": frame["date"].min() if not frame.empty else pd.NaT,
            "coverage_end": frame["date"].max() if not frame.empty else pd.NaT,
            "pit_policy": manifest.get("pit_policy", pd.NA),
            "revision_status": revision_status,
            "reason": reason,
            "evidence_at": evidence_at,
        }


def _hash_frame(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    if not normalized.empty:
        normalized = normalized.sort_values(list(normalized.columns), kind="mergesort")
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    payload = normalized.to_json(
        orient="records",
        date_format="iso",
        date_unit="ns",
        double_precision=15,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
