from __future__ import annotations

import json
from pathlib import Path
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
}

_LOGICAL_KEYS = {
    "trading_calendar": ("date", "market"),
    "daily_price_volume": ("date", "ticker"),
    "daily_chip": ("date", "ticker"),
    "monthly_sales": ("source_row_id",),
    "financial_statement_raw": ("source_row_id",),
    "security_master": ("ticker",),
}


class DataGateway:
    def __init__(self, data_analysts_root: Path) -> None:
        self.data_analysts_root = data_analysts_root.resolve()
        self.data_store = (self.data_analysts_root / "data_store").resolve()
        self.manifest_dir = self.data_store / "manifests"

    @classmethod
    def from_data_analysts(cls, root: str | Path) -> "DataGateway":
        return cls(Path(root))

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
