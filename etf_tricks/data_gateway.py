from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds


class DataContractError(RuntimeError):
    pass


_DATE_COLUMNS = {
    "date",
    "source_available_date",
    "source_period_date",
    "period_start_date",
    "period_end_date",
    "revision_date",
    "list_date",
    "delist_date",
    "event_date",
    "ex_date",
    "observation_date",
    "earliest_execution_session",
    "lifecycle_list_date",
    "lifecycle_delist_date",
    "lifecycle_interval_start",
    "lifecycle_interval_end_exclusive",
}

_LOGICAL_KEYS = {
    "trading_calendar": ("date", "market"),
    "daily_price_volume": ("date", "ticker"),
    "daily_market_state": ("date", "ticker"),
    "daily_chip": ("date", "ticker"),
    "monthly_sales": ("source_row_id",),
    "financial_statement_raw": ("source_row_id",),
    "security_master": ("ticker",),
}

_MARKET_STATE_COLUMNS = (
    "date",
    "ticker",
    "market",
    "market_state",
    "state_reason",
    "amount_state",
    "authoritative_traded_value",
    "amount_zero_authorized",
    "price_row_present",
    "attr_row_present",
    "atten_fg",
    "disp_fg",
    "full_fg",
    "limit_fg",
    "limo_fg",
    "sbadt_fg",
    "ssadt_fg",
    "susp_fg",
    "exchange_tradable",
    "full_delivery",
    "instrument_kind",
    "identity_source",
    "security_master_market",
    "lifecycle_list_date",
    "lifecycle_delist_date",
    "lifecycle_interval_start",
    "lifecycle_interval_end_exclusive",
    "lifecycle_active",
    "lifecycle_conflict",
    "identity_conflict",
    "lifecycle_pit_status",
    "revision_pit_status",
    "observation_date",
    "source_available_date",
    "availability_precision",
    "earliest_execution_session",
    "security_master_manifest_sha256",
    "calendar_manifest_sha256",
    "price_manifest_sha256",
    "tradability_manifest_sha256",
    "classification_policy_version",
    "data_cutoff_at",
)
_MARKET_STATE_HASH_COLUMNS = {
    "security_master_manifest_sha256": "security_master",
    "calendar_manifest_sha256": "trading_calendar",
    "price_manifest_sha256": "daily_price_volume",
    "tradability_manifest_sha256": "daily_tradability",
}
_MARKET_STATE_REQUIRED_LIFECYCLE_COLUMNS = (
    "instrument_kind",
    "identity_source",
    "lifecycle_active",
    "lifecycle_conflict",
    "identity_conflict",
    "lifecycle_pit_status",
    "revision_pit_status",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class DataGateway:
    def __init__(self, data_analysts_root: Path) -> None:
        self.data_analysts_root = data_analysts_root.resolve()
        self.data_store = (self.data_analysts_root / "data_store").resolve()
        self.manifest_dir = self.data_store / "manifests"

    @classmethod
    def from_data_analysts(cls, root: str | Path) -> "DataGateway":
        return cls(Path(root))

    def scan_market_state(
        self,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        tickers: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Read the certified TEJ-only daily market-state artifact."""
        manifest = self.load_manifest("daily_market_state")
        self._validate_market_state_manifest(manifest)

        filters: tuple[tuple[str, str, object], ...] = ()
        if tickers is not None:
            if isinstance(tickers, (str, bytes)):
                raise DataContractError("daily_market_state tickers must be an iterable of ticker ids")
            selected_tickers = tuple(dict.fromkeys(tickers))
            if any(not isinstance(ticker, str) or not ticker for ticker in selected_tickers):
                raise DataContractError("daily_market_state tickers must contain non-empty strings")
            if not selected_tickers:
                return self._empty_market_state_frame()
            filters = (("ticker", "in", selected_tickers),)

        result = self.scan_artifact(
            "daily_market_state",
            columns=_MARKET_STATE_COLUMNS,
            filters=filters,
            start=start,
            end=end,
            date_column="date",
        )
        self._validate_market_state_rows(result, manifest)
        return result.loc[:, list(_MARKET_STATE_COLUMNS)].reset_index(drop=True)

    @staticmethod
    def _empty_market_state_frame() -> pd.DataFrame:
        frame = pd.DataFrame(columns=_MARKET_STATE_COLUMNS)
        for column in _DATE_COLUMNS.intersection(_MARKET_STATE_COLUMNS):
            frame[column] = pd.to_datetime(frame[column])
        return frame

    @staticmethod
    def _validate_market_state_manifest(manifest: dict[str, object]) -> None:
        declared_columns = tuple(str(value) for value in manifest.get("columns", ()))
        if declared_columns != _MARKET_STATE_COLUMNS:
            raise DataContractError(
                "daily_market_state manifest schema does not match the governed columns"
            )
        if manifest.get("logical_key") != ["date", "ticker"]:
            raise DataContractError("daily_market_state manifest logical_key must be ['date', 'ticker']")

        hashes = manifest.get("dependency_manifest_sha256_by_contract")
        expected_contracts = set(_MARKET_STATE_HASH_COLUMNS.values())
        if not isinstance(hashes, dict) or set(hashes) != expected_contracts:
            raise DataContractError(
                "daily_market_state dependency_manifest_sha256_by_contract is incomplete"
            )
        for contract, value in hashes.items():
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise DataContractError(
                    "daily_market_state dependency_manifest_sha256_by_contract "
                    f"has invalid hash for {contract}"
                )

    @staticmethod
    def _validate_market_state_rows(
        frame: pd.DataFrame,
        manifest: dict[str, object],
    ) -> None:
        if frame.duplicated(["date", "ticker"]).any():
            raise DataContractError("daily_market_state contains duplicate (date, ticker) rows")

        for column in _MARKET_STATE_REQUIRED_LIFECYCLE_COLUMNS:
            if frame[column].isna().any():
                raise DataContractError(
                    f"daily_market_state contains missing required lifecycle field {column}"
                )
        for column in (
            "observation_date",
            "source_available_date",
            "availability_precision",
            "earliest_execution_session",
            "classification_policy_version",
            "data_cutoff_at",
        ):
            if frame[column].isna().any():
                raise DataContractError(
                    f"daily_market_state contains missing required availability field {column}"
                )

        invalid_states = set(frame["market_state"].dropna()).difference(
            {"TRADING", "HALTED", "MISSING"}
        )
        if frame["market_state"].isna().any() or invalid_states:
            raise DataContractError(
                f"daily_market_state has invalid market_state values: {sorted(invalid_states)}"
            )
        invalid_amount_states = set(frame["amount_state"].dropna()).difference(
            {"OBSERVED", "ZERO_AUTHORIZED", "MISSING"}
        )
        if frame["amount_state"].isna().any() or invalid_amount_states:
            raise DataContractError(
                "daily_market_state has invalid amount_state values: "
                f"{sorted(invalid_amount_states)}"
            )

        hashes = manifest["dependency_manifest_sha256_by_contract"]
        if not isinstance(hashes, dict):
            raise DataContractError(
                "daily_market_state dependency_manifest_sha256_by_contract is incomplete"
            )
        for column, contract in _MARKET_STATE_HASH_COLUMNS.items():
            values = frame[column]
            if values.isna().any() or any(
                not isinstance(value, str) or _SHA256.fullmatch(value) is None
                for value in values
            ):
                raise DataContractError(
                    f"daily_market_state contains invalid required manifest hash {column}"
                )
            if not values.eq(hashes[contract]).all():
                raise DataContractError(
                    f"daily_market_state {column} does not match manifest hash for {contract}"
                )

        for row in frame.itertuples(index=False):
            DataGateway._validate_market_state_row(row)

    @staticmethod
    def _validate_market_state_row(row: object) -> None:
        market_state = getattr(row, "market_state")
        amount_state = getattr(row, "amount_state")
        amount = getattr(row, "authoritative_traded_value")
        zero_authorized = getattr(row, "amount_zero_authorized")
        exchange_tradable = getattr(row, "exchange_tradable")

        if type(zero_authorized) is not bool:
            raise DataContractError("daily_market_state amount_zero_authorized must be boolean")
        if type(getattr(row, "full_delivery")) is not bool:
            raise DataContractError("daily_market_state full_delivery must be boolean")
        if amount_state == "OBSERVED":
            if not DataGateway._is_nonnegative_finite_number(amount) or zero_authorized:
                raise DataContractError(
                    "daily_market_state OBSERVED amount requires a finite nonnegative "
                    "authoritative_traded_value and amount_zero_authorized=False"
                )
        elif amount_state == "ZERO_AUTHORIZED":
            if amount != 0 or not zero_authorized:
                raise DataContractError(
                    "daily_market_state ZERO_AUTHORIZED requires amount=0 and "
                    "amount_zero_authorized=True"
                )
        elif amount_state == "MISSING":
            if not pd.isna(amount) or zero_authorized:
                raise DataContractError(
                    "daily_market_state MISSING amount requires null authoritative_traded_value "
                    "and amount_zero_authorized=False"
                )

        expected_exchange_tradable = {
            "TRADING": True,
            "HALTED": False,
            "MISSING": None,
        }[market_state]
        if expected_exchange_tradable is None:
            if not pd.isna(exchange_tradable):
                raise DataContractError(
                    "daily_market_state MISSING market_state requires null exchange_tradable"
                )
        elif type(exchange_tradable) is not bool or exchange_tradable != expected_exchange_tradable:
            raise DataContractError(
                f"daily_market_state {market_state} requires exchange_tradable="
                f"{expected_exchange_tradable}"
            )

        if market_state == "TRADING" and amount_state != "OBSERVED":
            raise DataContractError("daily_market_state TRADING requires amount_state=OBSERVED")
        if market_state == "HALTED" and amount_state not in {"OBSERVED", "ZERO_AUTHORIZED"}:
            raise DataContractError(
                "daily_market_state HALTED requires OBSERVED or ZERO_AUTHORIZED amount_state"
            )
        if market_state == "MISSING" and amount_state != "MISSING":
            raise DataContractError("daily_market_state MISSING requires amount_state=MISSING")

        DataGateway._validate_market_state_lifecycle(row)

    @staticmethod
    def _validate_market_state_lifecycle(row: object) -> None:
        instrument_kind = getattr(row, "instrument_kind")
        identity_source = getattr(row, "identity_source")
        lifecycle_active = getattr(row, "lifecycle_active")
        if instrument_kind not in {"EQUITY", "INDEX"}:
            raise DataContractError("daily_market_state has invalid instrument_kind")
        if type(lifecycle_active) is not bool:
            raise DataContractError("daily_market_state lifecycle_active must be boolean")
        if type(getattr(row, "lifecycle_conflict")) is not bool or getattr(
            row, "lifecycle_conflict"
        ):
            raise DataContractError("daily_market_state lifecycle_conflict must be false")
        if type(getattr(row, "identity_conflict")) is not bool or getattr(
            row, "identity_conflict"
        ):
            raise DataContractError("daily_market_state identity_conflict must be false")
        if getattr(row, "lifecycle_pit_status") != "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED":
            raise DataContractError("daily_market_state has invalid lifecycle_pit_status")
        if getattr(row, "revision_pit_status") != "PIT_REVISION_UNVERIFIED":
            raise DataContractError("daily_market_state has invalid revision_pit_status")

        lifecycle_dates = (
            "lifecycle_list_date",
            "lifecycle_interval_start",
            "lifecycle_interval_end_exclusive",
        )
        if instrument_kind == "EQUITY":
            if identity_source != "SECURITY_MASTER_SNAPSHOT" or not lifecycle_active:
                raise DataContractError("daily_market_state has invalid equity lifecycle identity")
            if pd.isna(getattr(row, "security_master_market")) or any(
                pd.isna(getattr(row, column)) for column in lifecycle_dates
            ):
                raise DataContractError("daily_market_state equity lifecycle fields are incomplete")
        else:
            index_null_fields = (
                "security_master_market",
                "lifecycle_list_date",
                "lifecycle_delist_date",
                "lifecycle_interval_start",
                "lifecycle_interval_end_exclusive",
            )
            if identity_source != "APIPRCD_PRICE_ROW" or lifecycle_active or any(
                not pd.isna(getattr(row, column)) for column in index_null_fields
            ):
                raise DataContractError("daily_market_state has invalid index lifecycle identity")

    @staticmethod
    def _is_nonnegative_finite_number(value: object) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(float(value)) and value >= 0

    def load_manifest(self, artifact_id: str) -> dict[str, object]:
        path = self.manifest_dir / f"{artifact_id}.json"
        if not path.is_file():
            raise DataContractError(f"missing manifest for artifact {artifact_id}: {path}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataContractError(f"invalid manifest for artifact {artifact_id}: {exc}") from exc
        if manifest.get("artifact_id") != artifact_id:
            raise DataContractError(
                f"manifest artifact mismatch for {artifact_id}: {manifest.get('artifact_id')}"
            )
        status = manifest.get("status")
        if status != "ready":
            raise DataContractError(f"artifact {artifact_id} status is {status}, expected ready")
        row_count = manifest.get("row_count")
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            raise DataContractError(f"artifact {artifact_id} has invalid row_count: {row_count}")
        duplicate_count = manifest.get("duplicate_count")
        if duplicate_count != 0:
            raise DataContractError(
                f"artifact {artifact_id} duplicate_count is {duplicate_count}, expected 0"
            )
        return manifest

    def read_artifact(
        self,
        artifact_id: str,
        *,
        columns: Iterable[str] | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        date_column: str | None = None,
    ) -> pd.DataFrame:
        manifest = self.load_manifest(artifact_id)
        declared_columns = tuple(str(value) for value in manifest.get("columns", ()))
        selected_columns = tuple(columns) if columns is not None else declared_columns
        missing = sorted(set(selected_columns).difference(declared_columns))
        if missing:
            raise DataContractError(
                f"artifact {artifact_id} manifest missing requested columns: {missing}"
            )
        self._validate_requested_coverage(artifact_id, manifest, start, end)

        raw_logical_key = manifest.get("logical_key")
        logical_key = _LOGICAL_KEYS.get(artifact_id)
        if logical_key is None and isinstance(raw_logical_key, list):
            logical_key = tuple(str(value) for value in raw_logical_key if str(value))
        if not logical_key:
            raise DataContractError(f"artifact {artifact_id} has no governed logical key")
        missing_key_columns = sorted(set(logical_key).difference(declared_columns))
        if missing_key_columns:
            raise DataContractError(
                f"artifact {artifact_id} logical key columns are absent: {missing_key_columns}"
            )
        read_columns = tuple(dict.fromkeys((*selected_columns, *logical_key)))

        frames: list[pd.DataFrame] = []
        raw_paths = manifest.get("artifact_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise DataContractError(f"artifact {artifact_id} has no declared artifact_paths")
        for raw_path in raw_paths:
            path = (self.data_store / str(raw_path)).resolve()
            if not path.is_relative_to(self.data_store):
                raise DataContractError(
                    f"artifact {artifact_id} path is outside data_store: {raw_path}"
                )
            if not path.is_file():
                raise DataContractError(f"artifact {artifact_id} declared file is missing: {path}")
            try:
                frames.append(pd.read_parquet(path, columns=list(read_columns)))
            except Exception as exc:
                raise DataContractError(
                    f"failed reading artifact {artifact_id} file {path}: {exc}"
                ) from exc

        result = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        if len(result) != manifest["row_count"]:
            raise DataContractError(
                f"artifact {artifact_id} row_count mismatch: manifest={manifest['row_count']} physical={len(result)}"
            )
        if result.duplicated(list(logical_key)).any():
            raise DataContractError(
                f"artifact {artifact_id} contains duplicate logical key rows: {list(logical_key)}"
            )
        for column in read_columns:
            if column in _DATE_COLUMNS:
                result[column] = pd.to_datetime(result[column], errors="coerce")

        effective_date_column = date_column
        if effective_date_column is None:
            if "date" in result.columns:
                effective_date_column = "date"
            elif "source_available_date" in result.columns:
                effective_date_column = "source_available_date"
        if start is not None or end is not None:
            if effective_date_column is None or effective_date_column not in result.columns:
                raise DataContractError(
                    f"artifact {artifact_id} has no date column for requested filtering"
                )
            if start is not None:
                result = result[result[effective_date_column] >= pd.Timestamp(start)]
            if end is not None:
                result = result[result[effective_date_column] <= pd.Timestamp(end)]
        return result.loc[:, list(selected_columns)].reset_index(drop=True)

    def scan_artifact(
        self,
        artifact_id: str,
        *,
        columns: Iterable[str] | None = None,
        filters: Iterable[tuple[str, str, object]] | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        date_column: str | None = None,
    ) -> pd.DataFrame:
        """Read a governed artifact subset with Arrow predicate pushdown."""
        manifest = self.load_manifest(artifact_id)
        declared_columns = tuple(str(value) for value in manifest.get("columns", ()))
        selected_columns = tuple(columns) if columns is not None else declared_columns
        filter_specs = tuple(filters or ())
        filter_columns = tuple(spec[0] for spec in filter_specs if len(spec) == 3)
        malformed_filters = [spec for spec in filter_specs if len(spec) != 3]
        if malformed_filters:
            raise DataContractError(
                f"artifact {artifact_id} has malformed filters: {malformed_filters}"
            )

        effective_date_column = date_column
        if effective_date_column is None:
            if "date" in declared_columns:
                effective_date_column = "date"
            elif "source_available_date" in declared_columns:
                effective_date_column = "source_available_date"
        if (start is not None or end is not None) and effective_date_column is None:
            raise DataContractError(
                f"artifact {artifact_id} has no date column for requested filtering"
            )

        raw_logical_key = manifest.get("logical_key")
        logical_key = _LOGICAL_KEYS.get(artifact_id)
        if logical_key is None and isinstance(raw_logical_key, list):
            logical_key = tuple(str(value) for value in raw_logical_key if str(value))
        if not logical_key:
            raise DataContractError(f"artifact {artifact_id} has no governed logical key")

        required_columns = set(selected_columns) | set(filter_columns) | set(logical_key)
        if effective_date_column is not None and (start is not None or end is not None):
            required_columns.add(effective_date_column)
        missing = sorted(required_columns.difference(declared_columns))
        if missing:
            raise DataContractError(
                f"artifact {artifact_id} manifest missing requested columns: {missing}"
            )
        self._validate_requested_coverage(artifact_id, manifest, start, end)
        read_columns = tuple(
            dict.fromkeys(
                (
                    *selected_columns,
                    *filter_columns,
                    *logical_key,
                    *(
                        (effective_date_column,)
                        if effective_date_column is not None
                        and (start is not None or end is not None)
                        else ()
                    ),
                )
            )
        )

        raw_paths = manifest.get("artifact_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise DataContractError(f"artifact {artifact_id} has no declared artifact_paths")

        frames: list[pd.DataFrame] = []
        for raw_path in raw_paths:
            path = (self.data_store / str(raw_path)).resolve()
            if not path.is_relative_to(self.data_store):
                raise DataContractError(
                    f"artifact {artifact_id} path is outside data_store: {raw_path}"
                )
            if not path.is_file():
                raise DataContractError(f"artifact {artifact_id} declared file is missing: {path}")
            try:
                dataset = ds.dataset(path, format="parquet")
                physical_columns = set(dataset.schema.names)
                physical_missing = sorted(set(read_columns).difference(physical_columns))
                if physical_missing:
                    raise DataContractError(
                        f"artifact {artifact_id} physical file {path} is missing columns: "
                        f"{physical_missing}"
                    )
                expression = self._build_arrow_filter(
                    artifact_id=artifact_id,
                    schema=dataset.schema,
                    filters=filter_specs,
                    date_column=effective_date_column,
                    start=start,
                    end=end,
                )
                table = dataset.to_table(columns=list(read_columns), filter=expression)
                if table.num_rows:
                    frames.append(table.to_pandas())
            except DataContractError:
                raise
            except Exception as exc:
                raise DataContractError(
                    f"failed scanning artifact {artifact_id} file {path}: {exc}"
                ) from exc

        result = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=read_columns)
        )
        if not filter_specs and start is None and end is None:
            if len(result) != manifest["row_count"]:
                raise DataContractError(
                    f"artifact {artifact_id} row_count mismatch: "
                    f"manifest={manifest['row_count']} physical={len(result)}"
                )

        for column in read_columns:
            if column in _DATE_COLUMNS:
                raw_notna = result[column].notna()
                result[column] = pd.to_datetime(result[column], errors="coerce")
                if (raw_notna & result[column].isna()).any():
                    raise DataContractError(
                        f"artifact {artifact_id} contains invalid date values in {column}"
                    )
        if start is not None:
            result = result[result[effective_date_column] >= pd.Timestamp(start)]
        if end is not None:
            result = result[result[effective_date_column] <= pd.Timestamp(end)]
        if result.duplicated(list(logical_key)).any():
            raise DataContractError(
                f"artifact {artifact_id} filtered result contains duplicate logical key rows: "
                f"{list(logical_key)}"
            )
        if not result.empty:
            result = result.sort_values(list(logical_key), kind="mergesort")
        return result.loc[:, list(selected_columns)].reset_index(drop=True)

    @staticmethod
    def _build_arrow_filter(
        *,
        artifact_id: str,
        schema: pa.Schema,
        filters: tuple[tuple[str, str, object], ...],
        date_column: str | None,
        start: str | pd.Timestamp | None,
        end: str | pd.Timestamp | None,
    ) -> ds.Expression | None:
        expressions: list[ds.Expression] = []
        supported = {"==", "!=", "<", "<=", ">", ">=", "in", "not in"}
        for column, operator, raw_value in filters:
            if operator not in supported:
                raise DataContractError(
                    f"artifact {artifact_id} has unsupported filter operator: {operator}"
                )
            field_type = schema.field(column).type
            field = ds.field(column)
            if operator in {"in", "not in"}:
                if isinstance(raw_value, (str, bytes)) or not isinstance(raw_value, Iterable):
                    raise DataContractError(
                        f"artifact {artifact_id} filter {operator} requires a value collection"
                    )
                values = [
                    DataGateway._coerce_arrow_scalar(field_type, value, column)
                    for value in raw_value
                ]
                expression = field.isin(values)
                if operator == "not in":
                    expression = ~expression
            else:
                value = DataGateway._coerce_arrow_scalar(field_type, raw_value, column)
                expression = {
                    "==": lambda: field == value,
                    "!=": lambda: field != value,
                    "<": lambda: field < value,
                    "<=": lambda: field <= value,
                    ">": lambda: field > value,
                    ">=": lambda: field >= value,
                }[operator]()
            expressions.append(expression)

        if start is not None or end is not None:
            if date_column is None:
                raise DataContractError(
                    f"artifact {artifact_id} has no date column for requested filtering"
                )
            date_type = schema.field(date_column).type
            field = ds.field(date_column)
            if start is not None:
                expressions.append(
                    field
                    >= DataGateway._coerce_arrow_scalar(date_type, start, date_column)
                )
            if end is not None:
                expressions.append(
                    field <= DataGateway._coerce_arrow_scalar(date_type, end, date_column)
                )

        combined: ds.Expression | None = None
        for expression in expressions:
            combined = expression if combined is None else combined & expression
        return combined

    @staticmethod
    def _coerce_arrow_scalar(
        field_type: pa.DataType,
        value: object,
        column: str,
    ) -> object:
        if column in _DATE_COLUMNS:
            timestamp = pd.Timestamp(value)
            if pa.types.is_string(field_type) or pa.types.is_large_string(field_type):
                return timestamp.date().isoformat()
            if pa.types.is_date(field_type):
                return timestamp.date()
            if pa.types.is_timestamp(field_type):
                if field_type.tz:
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.tz_localize(field_type.tz)
                    else:
                        timestamp = timestamp.tz_convert(field_type.tz)
                elif timestamp.tzinfo is not None:
                    timestamp = timestamp.tz_convert("UTC").tz_localize(None)
                return timestamp.to_pydatetime()
            raise DataContractError(
                f"date column {column} has unsupported Arrow type {field_type}"
            )
        return value

    @staticmethod
    def _validate_requested_coverage(
        artifact_id: str,
        manifest: dict[str, object],
        start: str | pd.Timestamp | None,
        end: str | pd.Timestamp | None,
    ) -> None:
        if start is None and end is None:
            return
        raw_range = manifest.get("date_range") or manifest.get("availability_date_range")
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            raise DataContractError(
                f"artifact {artifact_id} lacks coverage metadata for requested bounds"
            )
        lower, upper = pd.Timestamp(raw_range[0]), pd.Timestamp(raw_range[1])
        if start is not None and pd.Timestamp(start) < lower:
            raise DataContractError(
                f"artifact {artifact_id} coverage starts at {lower.date()}, requested {start}"
            )
        if end is not None and pd.Timestamp(end) > upper:
            raise DataContractError(
                f"artifact {artifact_id} coverage ends at {upper.date()}, requested {end}"
            )
