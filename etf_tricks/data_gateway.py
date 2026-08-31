from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


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
_MARKET_STATE_SOURCE_FAMILIES = (
    "security_master",
    "trading_calendar",
    "daily_price_volume",
    "daily_tradability",
)
_AVAILABILITY_PRECISION = "AFTER_CLOSE_DATE_ONLY"
_CLASSIFICATION_POLICY_VERSION = "daily_market_state_v3"
_LIFECYCLE_PIT_STATUS = "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED"
_REVISION_PIT_STATUS = "PIT_REVISION_UNVERIFIED"
_MARKET_STATE_SNAPSHOT_CHUNK_BYTES = 1 << 20


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
        manifest_bytes, manifest = self._load_manifest_snapshot("daily_market_state")
        self._validate_market_state_manifest(manifest)
        self._validate_market_state_inventory(manifest)
        self._validate_market_state_coverage(manifest, start, end)

        filters: tuple[tuple[str, str, object], ...] = ()
        if tickers is not None:
            if isinstance(tickers, (str, bytes)):
                raise DataContractError("daily_market_state tickers must be an iterable of ticker ids")
            selected_tickers = tuple(dict.fromkeys(tickers))
            if any(not isinstance(ticker, str) or not ticker for ticker in selected_tickers):
                raise DataContractError("daily_market_state tickers must contain non-empty strings")
            if not selected_tickers:
                if self._read_manifest_bytes("daily_market_state") != manifest_bytes:
                    raise DataContractError("daily_market_state manifest drifted during scan")
                return self._empty_market_state_frame()
            filters = (("ticker", "in", selected_tickers),)

        with tempfile.TemporaryDirectory(prefix="daily-market-state-") as snapshot_dir:
            snapshot_root = Path(snapshot_dir).resolve()
            self._copy_market_state_snapshot(manifest, snapshot_root)
            result = self.scan_artifact(
                "daily_market_state",
                columns=_MARKET_STATE_COLUMNS,
                filters=filters,
                start=start,
                end=end,
                date_column="date",
                manifest=manifest,
                validate_coverage=False,
                schema_validator=self._validate_market_state_physical_schema,
                table_validator=self._validate_market_state_arrow_table,
                scan_root=snapshot_root,
            )
            self._validate_market_state_inventory(manifest)
            self._validate_market_state_rows(result, manifest)
            if self._read_manifest_bytes("daily_market_state") != manifest_bytes:
                raise DataContractError("daily_market_state manifest drifted during scan")
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
        if len(declared_columns) != len(set(declared_columns)) or set(declared_columns) != set(
            _MARKET_STATE_COLUMNS
        ):
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
        if tuple(manifest.get("source_families", ())) != _MARKET_STATE_SOURCE_FAMILIES:
            raise DataContractError("daily_market_state manifest source_families are invalid")
        versions = manifest.get("dependency_versions")
        if (
            not isinstance(versions, dict)
            or set(versions) != expected_contracts
            or any(not isinstance(value, str) or not value.strip() for value in versions.values())
        ):
            raise DataContractError("daily_market_state dependency_versions are incomplete")
        fingerprint = manifest.get("dependency_certification_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise DataContractError(
                "daily_market_state dependency certification fingerprint is missing"
            )
        build_start = DataGateway._canonical_manifest_date(manifest, "build_start")
        build_end = DataGateway._canonical_manifest_date(manifest, "build_end")
        certified_source_start = DataGateway._canonical_manifest_date(
            manifest, "certified_source_start"
        )
        if certified_source_start > build_start or build_start > build_end:
            raise DataContractError("daily_market_state manifest build bounds are invalid")
        for field in (
            "classification_policy_version",
            "state_lattice_policy_version",
            "market_identity_policy_version",
        ):
            value = manifest.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DataContractError(f"daily_market_state manifest {field} is missing")
        if manifest["classification_policy_version"] != _CLASSIFICATION_POLICY_VERSION:
            raise DataContractError(
                "daily_market_state manifest classification_policy_version is invalid"
            )

    def _validate_market_state_inventory(
        self,
        manifest: dict[str, object],
        *,
        data_root: Path | None = None,
    ) -> None:
        inventory_root = self.data_store if data_root is None else data_root.resolve()
        raw_paths = manifest.get("artifact_paths")
        raw_inventory = manifest.get("partition_inventory")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise DataContractError("daily_market_state manifest has no artifact_paths")
        if not isinstance(raw_inventory, list):
            raise DataContractError("daily_market_state manifest partition_inventory is missing")
        inventory: dict[str, dict[str, object]] = {}
        for item in raw_inventory:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise DataContractError("daily_market_state partition_inventory is invalid")
            path_key = item["path"]
            if path_key in inventory:
                raise DataContractError("daily_market_state partition_inventory has duplicate paths")
            inventory[path_key] = item
        if set(raw_paths) != set(inventory):
            raise DataContractError(
                "daily_market_state partition_inventory does not match artifact_paths"
            )
        declared_schema_fingerprint = manifest.get("schema_fingerprint")
        if (
            not isinstance(declared_schema_fingerprint, str)
            or _SHA256.fullmatch(declared_schema_fingerprint) is None
        ):
            raise DataContractError("daily_market_state manifest schema_fingerprint is invalid")
        total_partition_rows = 0
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path:
                raise DataContractError("daily_market_state manifest artifact_paths are invalid")
            path = (inventory_root / raw_path).resolve()
            if not path.is_relative_to(inventory_root) or not path.is_file():
                raise DataContractError(
                    f"daily_market_state declared parquet is unavailable: {raw_path}"
                )
            item = inventory[raw_path]
            digest = item.get("content_sha256")
            if (
                not isinstance(digest, str)
                or _SHA256.fullmatch(digest) is None
                or not isinstance(item.get("size"), int)
                or isinstance(item["size"], bool)
                or item["size"] != path.stat().st_size
            ):
                raise DataContractError(
                    "daily_market_state partition_inventory content_sha256 or size is invalid"
                )
            if self._sha256_file(path) != digest:
                raise DataContractError(
                    "daily_market_state partition_inventory content_sha256 mismatch"
                )
            schema_fingerprint = item.get("schema_fingerprint")
            partition_row_count = item.get("row_count")
            if (
                not isinstance(partition_row_count, int)
                or isinstance(partition_row_count, bool)
                or partition_row_count < 0
            ):
                raise DataContractError(
                    "daily_market_state partition_inventory row_count is invalid"
                )
            try:
                physical_row_count = pq.ParquetFile(path).metadata.num_rows
            except (OSError, pa.ArrowException) as exc:
                raise DataContractError(
                    f"daily_market_state partition_inventory row_count is unreadable: {raw_path}"
                ) from exc
            if partition_row_count != physical_row_count:
                raise DataContractError(
                    "daily_market_state partition_inventory row_count mismatch"
                )
            total_partition_rows += partition_row_count
            actual_schema_fingerprint = hashlib.sha256(
                pq.read_schema(path).serialize().to_pybytes()
            ).hexdigest()
            if (
                not isinstance(schema_fingerprint, str)
                or _SHA256.fullmatch(schema_fingerprint) is None
                or schema_fingerprint != actual_schema_fingerprint
                or schema_fingerprint != declared_schema_fingerprint
            ):
                raise DataContractError(
                    "daily_market_state partition_inventory schema_fingerprint mismatch"
                )
        if total_partition_rows != manifest["row_count"]:
            raise DataContractError(
                "daily_market_state partition_inventory row_count does not match manifest row_count"
            )

    def _copy_market_state_snapshot(
        self,
        manifest: dict[str, object],
        snapshot_root: Path,
    ) -> None:
        raw_paths = manifest.get("artifact_paths")
        raw_inventory = manifest.get("partition_inventory")
        if not isinstance(raw_paths, list) or not isinstance(raw_inventory, list):
            raise DataContractError("daily_market_state manifest snapshot inventory is invalid")
        inventory = {
            item["path"]: item
            for item in raw_inventory
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if len(inventory) != len(raw_inventory) or set(raw_paths) != set(inventory):
            raise DataContractError("daily_market_state manifest snapshot inventory is invalid")
        for raw_path in raw_paths:
            if not isinstance(raw_path, str):
                raise DataContractError("daily_market_state manifest artifact_paths are invalid")
            source_path = (self.data_store / raw_path).resolve()
            destination_path = (snapshot_root / raw_path).resolve()
            if (
                not source_path.is_relative_to(self.data_store)
                or not source_path.is_file()
                or not destination_path.is_relative_to(snapshot_root)
            ):
                raise DataContractError(
                    f"daily_market_state declared parquet is unavailable: {raw_path}"
                )
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            copied_size = 0
            try:
                with source_path.open("rb") as source, destination_path.open("xb") as destination:
                    while chunk := source.read(_MARKET_STATE_SNAPSHOT_CHUNK_BYTES):
                        destination.write(chunk)
                        digest.update(chunk)
                        copied_size += len(chunk)
            except OSError as exc:
                raise DataContractError(
                    f"daily_market_state snapshot copy failed for {raw_path}: {exc}"
                ) from exc
            item = inventory[raw_path]
            if copied_size != item.get("size") or digest.hexdigest() != item.get(
                "content_sha256"
            ):
                raise DataContractError(
                    "daily_market_state snapshot copy does not match certified bytes"
                )
        self._validate_market_state_inventory(manifest, data_root=snapshot_root)

    @staticmethod
    def _validate_market_state_coverage(
        manifest: dict[str, object],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> None:
        start_value = pd.Timestamp(start)
        end_value = pd.Timestamp(end)
        if start_value > end_value:
            raise DataContractError("daily_market_state requested start must not be after end")
        build_start = pd.Timestamp(manifest["build_start"])
        build_end = pd.Timestamp(manifest["build_end"])
        if start_value < build_start or end_value > build_end:
            raise DataContractError(
                "daily_market_state requested bounds are outside governed build coverage"
            )

    @staticmethod
    def _canonical_manifest_date(manifest: dict[str, object], field: str) -> pd.Timestamp:
        value = manifest.get(field)
        if not isinstance(value, str):
            raise DataContractError(f"daily_market_state manifest {field} is invalid")
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise DataContractError(f"daily_market_state manifest {field} is invalid") from exc
        if parsed.strftime("%Y-%m-%d") != value:
            raise DataContractError(f"daily_market_state manifest {field} is invalid")
        return parsed

    def _load_manifest_snapshot(self, artifact_id: str) -> tuple[bytes, dict[str, object]]:
        raw = self._read_manifest_bytes(artifact_id)
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataContractError(f"invalid manifest for artifact {artifact_id}: {exc}") from exc
        if not isinstance(manifest, dict):
            raise DataContractError(f"invalid manifest for artifact {artifact_id}: expected object")
        self._validate_loaded_manifest(artifact_id, manifest)
        return raw, manifest

    def _read_manifest_bytes(self, artifact_id: str) -> bytes:
        path = self.manifest_dir / f"{artifact_id}.json"
        try:
            return path.read_bytes()
        except OSError as exc:
            raise DataContractError(f"missing manifest for artifact {artifact_id}: {path}") from exc

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise DataContractError(f"cannot hash declared parquet {path}: {exc}") from exc
        return digest.hexdigest()

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
            DataGateway._validate_market_state_row(row, manifest)

    @staticmethod
    def _validate_market_state_physical_schema(schema: pa.Schema) -> None:
        physical_names = tuple(schema.names)
        if (
            len(physical_names) != len(set(physical_names))
            or set(physical_names) != set(_MARKET_STATE_COLUMNS)
        ):
            raise DataContractError(
                "daily_market_state physical schema does not match the governed columns"
            )
        date_columns = {
            "date",
            "lifecycle_list_date",
            "lifecycle_delist_date",
            "lifecycle_interval_start",
            "lifecycle_interval_end_exclusive",
            "observation_date",
            "source_available_date",
            "earliest_execution_session",
        }
        boolean_columns = {
            "amount_zero_authorized",
            "price_row_present",
            "attr_row_present",
            "exchange_tradable",
            "full_delivery",
            "lifecycle_active",
            "lifecycle_conflict",
            "identity_conflict",
        }
        for name in _MARKET_STATE_COLUMNS:
            field_type = schema.field(name).type
            if name in date_columns:
                valid = (
                    pa.types.is_string(field_type)
                    or pa.types.is_large_string(field_type)
                    or pa.types.is_date(field_type)
                    or pa.types.is_timestamp(field_type)
                    or pa.types.is_null(field_type)
                )
            elif name == "authoritative_traded_value":
                valid = (
                    pa.types.is_integer(field_type)
                    or pa.types.is_floating(field_type)
                    or pa.types.is_null(field_type)
                )
            elif name in boolean_columns:
                valid = pa.types.is_boolean(field_type) or pa.types.is_null(field_type)
            else:
                valid = pa.types.is_string(field_type) or pa.types.is_large_string(field_type) or pa.types.is_null(field_type)
            if not valid:
                raise DataContractError(
                    f"daily_market_state physical schema has invalid type for {name}: {field_type}"
                )

    @staticmethod
    def _validate_market_state_arrow_table(table: pa.Table) -> None:
        amount_states = table.column("amount_state").to_pylist()
        amounts = table.column("authoritative_traded_value").to_pylist()
        for amount_state, amount in zip(amount_states, amounts, strict=True):
            if amount_state == "MISSING" and amount is not None:
                raise DataContractError(
                    "daily_market_state MISSING amount requires a true null, not NaN"
                )

    @staticmethod
    def _validate_market_state_row(row: object, manifest: dict[str, object]) -> None:
        market_state = getattr(row, "market_state")
        amount_state = getattr(row, "amount_state")
        amount = getattr(row, "authoritative_traded_value")
        zero_authorized = getattr(row, "amount_zero_authorized")
        exchange_tradable = getattr(row, "exchange_tradable")

        if type(zero_authorized) is not bool:
            raise DataContractError("daily_market_state amount_zero_authorized must be boolean")
        price_row_present = getattr(row, "price_row_present")
        attr_row_present = getattr(row, "attr_row_present")
        if type(price_row_present) is not bool or type(attr_row_present) is not bool:
            raise DataContractError(
                "daily_market_state price_row_present and attr_row_present must be boolean"
            )
        full_delivery = getattr(row, "full_delivery")
        raw_flags = (
            "atten_fg",
            "disp_fg",
            "full_fg",
            "limit_fg",
            "limo_fg",
            "sbadt_fg",
            "ssadt_fg",
            "susp_fg",
        )
        if not attr_row_present:
            if not pd.isna(full_delivery) or any(
                not pd.isna(getattr(row, flag)) for flag in raw_flags
            ):
                raise DataContractError(
                    "daily_market_state attr_row_present=False requires null raw flags and full_delivery"
                )
        elif type(full_delivery) is not bool or full_delivery != DataGateway._active_flag(
            getattr(row, "full_fg")
        ):
            raise DataContractError(
                "daily_market_state full_delivery must exactly map full_fg"
            )
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

        DataGateway._validate_market_state_lifecycle(row, manifest)
        DataGateway._validate_market_state_availability(row, manifest)

        suspended = DataGateway._active_flag(getattr(row, "susp_fg"))
        if price_row_present and market_state == "MISSING":
            expected = ("APIPRCD_INVALID_AMOUNT", "MISSING", "MISSING", False, None)
        elif price_row_present and suspended:
            expected = (
                "APISTKATTR_SUSPENSION_WITH_OBSERVED_AMOUNT",
                "HALTED",
                "OBSERVED",
                False,
                False,
            )
        elif price_row_present:
            expected = ("APIPRCD_OBSERVED_AMOUNT", "TRADING", "OBSERVED", False, True)
        elif suspended:
            expected = (
                "APISTKATTR_SUSPENSION_NO_PRICE",
                "HALTED",
                "ZERO_AUTHORIZED",
                True,
                False,
            )
        elif attr_row_present:
            expected = (
                "APISTKATTR_NON_SUSPENSION_WITHOUT_PRICE",
                "MISSING",
                "MISSING",
                False,
                None,
            )
        elif getattr(row, "instrument_kind") == "EQUITY" and getattr(row, "lifecycle_active"):
            expected = (
                "ACTIVE_LIFECYCLE_DUAL_SOURCE_ABSENCE",
                "HALTED",
                "ZERO_AUTHORIZED",
                True,
                False,
            )
        else:
            expected = ("NO_AUTHORIZED_STATE_KEY", "MISSING", "MISSING", False, None)
        actual_tuple = (
            getattr(row, "state_reason"),
            market_state,
            amount_state,
            zero_authorized,
            exchange_tradable,
        )
        expected_tuple = expected[:5]
        if actual_tuple != expected_tuple:
            raise DataContractError(
                f"daily_market_state state_reason {expected[0]} authority tuple does not match "
                "the TEJ classifier matrix"
            )
        if expected[2] == "ZERO_AUTHORIZED" and (
            not DataGateway._is_nonnegative_finite_number(amount) or float(amount) != 0.0
        ):
            raise DataContractError(
                f"daily_market_state {expected[0]} authority tuple requires amount=0.0"
            )

    @staticmethod
    def _active_flag(value: object) -> bool:
        return isinstance(value, str) and value.strip().upper() == "Y"

    @staticmethod
    def _validate_market_state_lifecycle(
        row: object,
        manifest: dict[str, object],
    ) -> None:
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
        if getattr(row, "lifecycle_pit_status") != _LIFECYCLE_PIT_STATUS:
            raise DataContractError("daily_market_state has invalid lifecycle_pit_status")
        if getattr(row, "revision_pit_status") != _REVISION_PIT_STATUS:
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
            if (
                getattr(row, "market") not in {"TWSE", "TPEX"}
                or getattr(row, "security_master_market") != getattr(row, "market")
            ):
                raise DataContractError("daily_market_state equity market identity is invalid")
            row_date = pd.Timestamp(getattr(row, "date"))
            list_date = pd.Timestamp(getattr(row, "lifecycle_list_date"))
            interval_start = pd.Timestamp(getattr(row, "lifecycle_interval_start"))
            interval_end = pd.Timestamp(getattr(row, "lifecycle_interval_end_exclusive"))
            build_start = pd.Timestamp(manifest["build_start"])
            build_end = pd.Timestamp(manifest["build_end"])
            certified_source_start = pd.Timestamp(manifest["certified_source_start"])
            build_end_exclusive = build_end + pd.Timedelta(days=1)
            if not list_date <= row_date < interval_end:
                raise DataContractError("daily_market_state equity lifecycle interval is invalid")
            delist_date = getattr(row, "lifecycle_delist_date")
            if not pd.isna(delist_date):
                delist = pd.Timestamp(delist_date)
                if delist <= list_date or row_date >= delist:
                    raise DataContractError(
                        "daily_market_state equity delist boundary is not exclusive"
                    )
                expected_end = min(delist, build_end_exclusive)
            else:
                expected_end = build_end_exclusive
            expected_start = max(list_date, build_start, certified_source_start)
            if interval_start != expected_start:
                raise DataContractError(
                    "daily_market_state equity lifecycle_interval_start is not manifest-bound"
                )
            if interval_end != expected_end:
                raise DataContractError(
                    "daily_market_state equity lifecycle_interval_end_exclusive is not manifest-bound"
                )
            if not interval_start <= row_date < interval_end:
                raise DataContractError("daily_market_state equity lifecycle interval is invalid")
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
            if not getattr(row, "price_row_present"):
                raise DataContractError("daily_market_state index requires a price-row-only key")
            if getattr(row, "market") not in {"TWSE", "TPEX", "INDEX"}:
                raise DataContractError("daily_market_state index market identity is invalid")

    @staticmethod
    def _validate_market_state_availability(
        row: object,
        manifest: dict[str, object],
    ) -> None:
        date_value = pd.Timestamp(getattr(row, "date"))
        observation_date = pd.Timestamp(getattr(row, "observation_date"))
        source_available_date = pd.Timestamp(getattr(row, "source_available_date"))
        earliest_execution_session = pd.Timestamp(
            getattr(row, "earliest_execution_session")
        )
        if observation_date != date_value:
            raise DataContractError("daily_market_state observation_date must equal date")
        if source_available_date != date_value:
            raise DataContractError(
                "daily_market_state source_available_date must equal date"
            )
        if earliest_execution_session <= source_available_date:
            raise DataContractError(
                "daily_market_state earliest_execution_session must be after source availability"
            )
        if getattr(row, "availability_precision") != _AVAILABILITY_PRECISION:
            raise DataContractError("daily_market_state has invalid availability_precision")
        data_cutoff_at = DataGateway._parse_data_cutoff(
            getattr(row, "data_cutoff_at")
        )
        if data_cutoff_at is None:
            raise DataContractError("daily_market_state has malformed data_cutoff_at")
        if data_cutoff_at.date() < source_available_date.date():
            raise DataContractError(
                "daily_market_state data_cutoff_at predates availability"
            )
        if (
            getattr(row, "classification_policy_version")
            != manifest["classification_policy_version"]
            or manifest["classification_policy_version"] != _CLASSIFICATION_POLICY_VERSION
        ):
            raise DataContractError(
                "daily_market_state has invalid classification_policy_version"
            )

    @staticmethod
    def _parse_data_cutoff(value: object) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and ("T" in value or " " in value):
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if parsed == datetime(1970, 1, 1, tzinfo=timezone.utc):
            return None
        return parsed

    @staticmethod
    def _is_nonnegative_finite_number(value: object) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(float(value)) and value >= 0

    def load_manifest(self, artifact_id: str) -> dict[str, object]:
        _, manifest = self._load_manifest_snapshot(artifact_id)
        return manifest

    @staticmethod
    def _validate_loaded_manifest(artifact_id: str, manifest: dict[str, object]) -> None:
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
        manifest: dict[str, object] | None = None,
        validate_coverage: bool = True,
        schema_validator: Callable[[pa.Schema], None] | None = None,
        table_validator: Callable[[pa.Table], None] | None = None,
        scan_root: Path | None = None,
    ) -> pd.DataFrame:
        """Read a governed artifact subset with Arrow predicate pushdown."""
        if manifest is None:
            manifest = self.load_manifest(artifact_id)
        else:
            self._validate_loaded_manifest(artifact_id, manifest)
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
        if validate_coverage:
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
        scan_data_root = self.data_store if scan_root is None else scan_root.resolve()
        scan_root_label = "data_store" if scan_root is None else "scan root"

        frames: list[pd.DataFrame] = []
        for raw_path in raw_paths:
            path = (scan_data_root / str(raw_path)).resolve()
            if not path.is_relative_to(scan_data_root):
                raise DataContractError(
                    f"artifact {artifact_id} path is outside {scan_root_label}: {raw_path}"
                )
            if not path.is_file():
                raise DataContractError(f"artifact {artifact_id} declared file is missing: {path}")
            try:
                dataset = ds.dataset(path, format="parquet")
                if schema_validator is not None:
                    schema_validator(dataset.schema)
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
                if table_validator is not None:
                    table_validator(table)
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
