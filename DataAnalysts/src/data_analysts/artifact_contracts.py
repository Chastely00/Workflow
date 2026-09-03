from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from collections.abc import Collection, Mapping
from typing import Any, Literal, TypeAlias

from data_analysts.paths import DataAnalystsContext


PublicationMode: TypeAlias = Literal[
    "full_replace", "partition_upsert", "snapshot_by_value"
]
RunScope: TypeAlias = Literal["full_history", "bounded_backfill", "daily"]

SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_LAYERS = {"raw", "derived"}
SUPPORTED_PUBLICATION_MODES = {
    "full_replace",
    "partition_upsert",
    "snapshot_by_value",
}
REQUIRED_CONTRACT_FIELDS = {
    "artifact_id",
    "layer",
    "base_path",
    "file_name",
    "required_columns",
    "logical_key",
    "publication_mode",
    "partition_name",
    "partition_field",
    "date_field",
    "availability_field",
    "pit_policy",
    "source_families",
}
REQUIRED_STATIC_ARTIFACT_IDS = {
    "security_master",
    "daily_price_volume",
    "trading_calendar",
    "daily_tradability",
    "daily_chip",
    "monthly_sales",
    "financial_statement_raw",
    "self_reported_numbers_raw",
    "taiwan_index_futures_near_month",
    "director_supervisor_holdings",
    "board_reelection_statistics",
    "executive_change_events",
    "merger_acquisition_events",
    "private_placement_relation_events",
    "insider_transfer_completed",
    "insider_transfer_declared_not_completed",
    "treasury_stock_events",
    "financial_statement_pit_selected",
    "self_reported_numbers_pit_selected",
    "dividend_events",
    "capital_action_events",
    "corporate_actions",
    "security_panel",
    "security_panel_history",
}
SOURCE_ONLY_FAMILY_IDS = {"dividend_policy", "capital_formation"}
REQUIRED_UNIVERSE_TEMPLATE_VARIANTS = {"historical", "exact_date"}


class ArtifactContractError(ValueError):
    """Raised when an artifact publication contract is incomplete or unsafe."""


def empty_contract_schema_fingerprint(contract: "ArtifactContract") -> str:
    payload = json.dumps(
        {
            "schema": "required-columns-v1",
            "required_columns": list(contract.required_columns),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def versioned_partition_value(
    contract: "ArtifactContract",
    path: str,
    *,
    active_version: str,
) -> str:
    """Return the partition value only for the contract's exact active path."""
    contract._validate_version(active_version)
    if not isinstance(path, str) or "\\" in path:
        raise ArtifactContractError(
            f"{contract.artifact_id} path is not a normalized artifact path"
        )
    parts = PurePosixPath(path).parts
    base = PurePosixPath(contract.base_path).parts
    remainder = parts[len(base):] if parts[:len(base)] == base else ()
    if contract.partition_name is None or len(remainder) != 4:
        raise ArtifactContractError(
            f"{contract.artifact_id} path does not match partition contract"
        )
    prefix = f"{contract.partition_name}="
    partition_segment = remainder[2]
    if (
        remainder[0] != "versions"
        or remainder[1] != active_version
        or not partition_segment.startswith(prefix)
        or remainder[3] != contract.file_name
    ):
        raise ArtifactContractError(
            f"{contract.artifact_id} path does not match active partition contract"
        )
    value = partition_segment[len(prefix):]
    if contract.path_for_partition(value, version=active_version) != path:
        raise ArtifactContractError(
            f"{contract.artifact_id} path is not the exact active partition path"
        )
    return value


def contract_partition_value(
    contract: "ArtifactContract",
    path: str,
    *,
    active_version: str | None,
) -> str:
    """Validate an exact legacy or active-version partition path."""
    if active_version is not None:
        return versioned_partition_value(
            contract, path, active_version=active_version
        )
    if not isinstance(path, str) or "\\" in path:
        raise ArtifactContractError(
            f"{contract.artifact_id} path is not normalized"
        )
    parts = PurePosixPath(path).parts
    base = PurePosixPath(contract.base_path).parts
    remainder = parts[len(base):] if parts[:len(base)] == base else ()
    if contract.partition_name is None or len(remainder) != 2:
        raise ArtifactContractError(
            f"{contract.artifact_id} legacy path does not match contract"
        )
    prefix = f"{contract.partition_name}="
    if not remainder[0].startswith(prefix) or remainder[1] != contract.file_name:
        raise ArtifactContractError(
            f"{contract.artifact_id} legacy path does not match contract"
        )
    value = remainder[0][len(prefix):]
    if contract.path_for_partition(value) != path:
        raise ArtifactContractError(
            f"{contract.artifact_id} legacy path is not exact"
        )
    return value


@dataclass(frozen=True)
class ArtifactContract:
    contract_key: str
    artifact_id: str
    variant: str
    layer: str
    base_path: str
    file_name: str
    required_columns: tuple[str, ...]
    logical_key: tuple[str, ...]
    publication_mode: PublicationMode
    partition_name: str | None
    partition_field: str | None
    date_field: str | None
    availability_field: str | None
    pit_policy: str
    source_families: tuple[str, ...]
    allow_empty: bool = False

    @property
    def manifest_file_name(self) -> str:
        """Return a stable identity while retaining legacy names for default artifacts."""
        if self.contract_key == self.artifact_id:
            return f"{self.artifact_id}.json"
        return f"{self.artifact_id}.{self.variant}.json"

    def path_for_partition(
        self, value: str | None = None, *, version: str | None = None
    ) -> str:
        if self.partition_name is None:
            if value is not None:
                raise ArtifactContractError(
                    f"{self.artifact_id} does not accept a partition value"
                )
            return f"{self.base_path}/{self.file_name}"
        if not isinstance(value, str) or not value.strip():
            raise ArtifactContractError(
                f"{self.artifact_id} requires a non-empty partition value"
            )
        if any(token in value for token in ("/", "\\", "..")):
            raise ArtifactContractError(
                f"{self.artifact_id} partition value must be one path segment"
            )
        prefix = self.base_path
        if version is not None:
            self._validate_version(version)
            prefix = f"{prefix}/versions/{version}"
        return f"{prefix}/{self.partition_name}={value}/{self.file_name}"

    def inventory_glob(self) -> str:
        if self.publication_mode == "full_replace":
            return f"{self.base_path}/versions/*/{self.file_name}"
        if self.publication_mode in {"partition_upsert", "snapshot_by_value"}:
            return (
                f"{self.base_path}/versions/*/"
                f"{self.partition_name}=*/{self.file_name}"
            )
        if self.partition_name is None:
            return f"{self.base_path}/{self.file_name}"
        return f"{self.base_path}/{self.partition_name}=*/{self.file_name}"

    def path_for_version(self, version: str) -> str:
        if self.publication_mode != "full_replace":
            raise ArtifactContractError(
                f"{self.artifact_id} does not support versioned full replacement"
            )
        self._validate_version(version)
        return f"{self.base_path}/versions/{version}/{self.file_name}"

    def legacy_inventory_glob(self) -> str:
        if self.partition_name is None:
            return f"{self.base_path}/{self.file_name}"
        return f"{self.base_path}/{self.partition_name}=*/{self.file_name}"

    def _validate_version(self, version: str) -> None:
        if not isinstance(version, str) or not version or version != version.strip():
            raise ArtifactContractError(
                f"{self.artifact_id} requires a non-empty version"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
            raise ArtifactContractError(
                f"{self.artifact_id} version must be a safe path slug"
            )
        stem = version.rstrip(". ").split(".", 1)[0].upper()
        reserved = {"CON", "PRN", "AUX", "NUL"} | {
            f"{prefix}{number}"
            for prefix in ("COM", "LPT")
            for number in range(1, 10)
        }
        if version.endswith((".", " ")) or stem in reserved:
            raise ArtifactContractError(
                f"{self.artifact_id} version uses a Windows-reserved path token"
            )


def load_artifact_contracts(
    context: DataAnalystsContext,
    universe_specs: dict[str, Any],
) -> dict[str, ArtifactContract]:
    path = context.config_path("artifact_contracts.json")
    if not path.exists():
        raise ArtifactContractError("missing config: artifact_contracts.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(f"cannot load artifact_contracts.json: {exc}") from exc
    profiles_path = context.config_path("source_family_profiles.json")
    try:
        source_family_profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(
            f"cannot load source_family_profiles.json: {exc}"
        ) from exc
    return parse_artifact_contracts(
        payload,
        universe_specs,
        source_family_profiles=source_family_profiles,
    )


def parse_artifact_contracts(
    payload: dict[str, Any],
    universe_specs: dict[str, Any],
    source_family_profiles: dict[str, Any] | None = None,
) -> dict[str, ArtifactContract]:
    if not isinstance(payload, dict):
        raise ArtifactContractError("artifact contract registry must be a JSON object")
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ArtifactContractError("unsupported schema_version in artifact_contracts.json")
    artifacts = payload.get("artifacts")
    templates = payload.get("universe_templates")
    if not isinstance(artifacts, list):
        raise ArtifactContractError("artifact_contracts.json must define artifacts")
    if not isinstance(templates, list):
        raise ArtifactContractError("artifact_contracts.json must define universe_templates")

    expanded: list[dict[str, Any]] = []
    expanded.extend(_require_contract_object(item) for item in artifacts)
    universe_ids = _validated_universe_ids(universe_specs)
    for template in templates:
        template_object = _require_contract_object(template)
        if "{universe_id}" not in str(template_object.get("artifact_id", "")):
            raise ArtifactContractError(
                "universe template artifact_id must contain {universe_id}"
            )
        if "{universe_id}" not in str(template_object.get("contract_key", "")):
            raise ArtifactContractError(
                "universe template contract_key must contain {universe_id}"
            )
        _parse_contract(
            _expand_universe_template(template_object, "validated_universe")
        )
        for universe_id in universe_ids:
            expanded.append(_expand_universe_template(template_object, universe_id))

    contracts: dict[str, ArtifactContract] = {}
    for item in expanded:
        contract = _parse_contract(item)
        if contract.contract_key in contracts:
            raise ArtifactContractError(
                f"duplicate artifact_id/contract_key: {contract.contract_key}"
            )
        contracts[contract.contract_key] = contract

    manifest_identities: dict[str, str] = {}
    for contract in contracts.values():
        prior = manifest_identities.get(contract.manifest_file_name)
        if prior is not None:
            raise ArtifactContractError(
                f"duplicate manifest identity {contract.manifest_file_name}: "
                f"{prior}, {contract.contract_key}"
            )
        manifest_identities[contract.manifest_file_name] = contract.contract_key

    _validate_registry_completeness(
        contracts,
        templates,
        source_family_profiles=source_family_profiles,
    )
    if source_family_profiles is not None:
        _validate_raw_family_logical_keys(contracts, source_family_profiles)
        _validate_dependency_tokens(contracts, source_family_profiles)
    return contracts


def expected_contract_outputs(
    contracts: Mapping[str, ArtifactContract],
    selected_family_ids: Collection[str],
) -> dict[str, tuple[str, ...]]:
    """Derive each selected family's transitive output obligations."""
    output: dict[str, tuple[str, ...]] = {}
    for family_id in sorted(set(selected_family_ids)):
        available = {family_id}
        expected: set[str] = set()
        while True:
            added = {
                contract.contract_key
                for contract in contracts.values()
                if contract.contract_key not in expected
                and available.intersection(contract.source_families)
            }
            if not added:
                break
            expected.update(added)
            for key in added:
                contract = contracts[key]
                available.update((contract.contract_key, contract.artifact_id))
        output[family_id] = tuple(sorted(expected))
    return output


def _validate_dependency_tokens(
    contracts: Mapping[str, ArtifactContract],
    source_family_profiles: Mapping[str, Any],
) -> None:
    source_ids = set(SOURCE_ONLY_FAMILY_IDS) | {
        str(item.get("family_id"))
        for item in source_family_profiles.get("families", [])
        if isinstance(item, Mapping) and item.get("family_id")
    }
    artifact_counts: dict[str, int] = {}
    for contract in contracts.values():
        artifact_counts[contract.artifact_id] = (
            artifact_counts.get(contract.artifact_id, 0) + 1
        )
    valid_tokens = source_ids | set(contracts) | {
        artifact_id
        for artifact_id, count in artifact_counts.items()
        if count == 1
    }
    for contract in contracts.values():
        unknown = sorted(set(contract.source_families) - valid_tokens)
        if unknown:
            raise ArtifactContractError(
                f"{contract.contract_key} has unknown or ambiguous source_families: "
                + ", ".join(unknown)
            )


def _parse_contract(item: dict[str, Any]) -> ArtifactContract:
    missing = sorted(REQUIRED_CONTRACT_FIELDS - item.keys())
    if missing:
        raise ArtifactContractError(f"contract missing fields: {', '.join(missing)}")

    artifact_id = _non_empty_string(item["artifact_id"], "artifact_id")
    contract_key = _non_empty_string(
        item.get("contract_key", artifact_id), f"{artifact_id} contract_key"
    )
    variant = _non_empty_string(
        item.get("variant", "default"), f"{artifact_id} variant"
    )
    for value, field_name in ((artifact_id, "artifact_id"), (variant, "variant")):
        if any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in value
        ):
            raise ArtifactContractError(
                f"unsafe {field_name} for manifest identity: {value}"
            )
    layer = _non_empty_string(item["layer"], f"{artifact_id} layer")
    if layer not in SUPPORTED_LAYERS:
        raise ArtifactContractError(f"unsupported layer for {artifact_id}: {layer}")
    base_path = _safe_relative_path(item["base_path"], f"{artifact_id} base_path")
    file_name = _safe_file_name(item["file_name"], f"{artifact_id} file_name")
    required_columns = _non_empty_string_tuple(
        item["required_columns"], f"{artifact_id} required_columns"
    )
    logical_key = _non_empty_string_tuple(
        item["logical_key"], f"{artifact_id} logical_key"
    )
    publication_mode = item["publication_mode"]
    if publication_mode not in SUPPORTED_PUBLICATION_MODES:
        raise ArtifactContractError(
            f"unsupported publication_mode for {artifact_id}: {publication_mode}"
        )
    partition_name = _optional_string(
        item["partition_name"], f"{artifact_id} partition_name"
    )
    partition_field = _optional_string(
        item["partition_field"], f"{artifact_id} partition_field"
    )
    if (partition_name is None) != (partition_field is None):
        raise ArtifactContractError(
            f"{artifact_id} partition_name and partition_field must both be set or null"
        )
    if publication_mode == "full_replace" and partition_name is not None:
        raise ArtifactContractError(
            f"{artifact_id} full_replace must not define partition fields"
        )
    if publication_mode != "full_replace" and partition_name is None:
        raise ArtifactContractError(
            f"{artifact_id} {publication_mode} requires partition fields"
        )
    date_field = _optional_string(item["date_field"], f"{artifact_id} date_field")
    availability_field = _optional_string(
        item["availability_field"], f"{artifact_id} availability_field"
    )
    pit_policy = _non_empty_string(item["pit_policy"], f"{artifact_id} pit_policy")
    source_families = _non_empty_string_tuple(
        item["source_families"], f"{artifact_id} source_families"
    )
    allow_empty = item.get("allow_empty", False)
    if not isinstance(allow_empty, bool):
        raise ArtifactContractError(f"{artifact_id} allow_empty must be boolean")
    missing_logical_columns = sorted(set(logical_key) - set(required_columns))
    if missing_logical_columns:
        raise ArtifactContractError(
            f"{artifact_id} logical_key must exist in required_columns: "
            f"{', '.join(missing_logical_columns)}"
        )
    for field_name, field_value in (
        ("partition_field", partition_field),
        ("date_field", date_field),
        ("availability_field", availability_field),
    ):
        if field_value is not None and field_value not in required_columns:
            raise ArtifactContractError(
                f"{artifact_id} {field_name} must exist in required_columns: "
                f"{field_value}"
            )
    _reject_unexpanded_variables(item, artifact_id)

    return ArtifactContract(
        contract_key=contract_key,
        artifact_id=artifact_id,
        variant=variant,
        layer=layer,
        base_path=base_path,
        file_name=file_name,
        required_columns=required_columns,
        logical_key=logical_key,
        publication_mode=publication_mode,
        partition_name=partition_name,
        partition_field=partition_field,
        date_field=date_field,
        availability_field=availability_field,
        pit_policy=pit_policy,
        source_families=source_families,
        allow_empty=allow_empty,
    )


def _validated_universe_ids(universe_specs: dict[str, Any]) -> tuple[str, ...]:
    if not isinstance(universe_specs, dict):
        raise ArtifactContractError("universe_specs must be a JSON object")
    universes = universe_specs.get("universes")
    if not isinstance(universes, list):
        raise ArtifactContractError("universe_specs must define universes")
    seen: set[str] = set()
    universe_ids: list[str] = []
    for universe in universes:
        if not isinstance(universe, dict):
            raise ArtifactContractError("universe spec must be a JSON object")
        universe_id = _non_empty_string(universe.get("universe_id"), "universe_id")
        if universe_id in seen:
            raise ArtifactContractError(f"duplicate universe_id: {universe_id}")
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in universe_id):
            raise ArtifactContractError(f"unsafe universe_id: {universe_id}")
        seen.add(universe_id)
        universe_ids.append(universe_id)
    return tuple(universe_ids)


def _expand_universe_template(
    template: dict[str, Any], universe_id: str
) -> dict[str, Any]:
    def expand(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("{universe_id}", universe_id)
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        return value

    return expand(template)


def _validate_raw_family_logical_keys(
    contracts: dict[str, ArtifactContract], source_family_profiles: dict[str, Any]
) -> None:
    profiles = source_family_profiles.get("families")
    if not isinstance(profiles, list):
        raise ArtifactContractError("source_family_profiles must define families")
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ArtifactContractError("source family profile must be a JSON object")
        if profile.get("enabled", True) is False:
            continue
        family_id = profile.get("family_id")
        contract = contracts.get(family_id) if isinstance(family_id, str) else None
        if family_id in SOURCE_ONLY_FAMILY_IDS:
            continue
        if contract is None or contract.layer != "raw":
            raise ArtifactContractError(
                f"missing raw artifact contract for enabled family: {family_id}"
            )
        expected = _non_empty_string_tuple(
            profile.get("primary_key"), f"{family_id} primary_key"
        )
        if contract.logical_key != expected:
            raise ArtifactContractError(
                f"raw artifact logical_key mismatch for {family_id}: "
                f"expected {expected}, got {contract.logical_key}"
            )


def _validate_registry_completeness(
    contracts: dict[str, ArtifactContract],
    universe_templates: list[Any],
    *,
    source_family_profiles: dict[str, Any] | None,
) -> None:
    artifact_ids = {contract.artifact_id for contract in contracts.values()}
    missing_artifacts = sorted(REQUIRED_STATIC_ARTIFACT_IDS - artifact_ids)
    if missing_artifacts:
        raise ArtifactContractError(
            f"missing required artifacts: {', '.join(missing_artifacts)}"
        )

    template_variants = {
        item.get("variant")
        for item in universe_templates
        if isinstance(item, dict)
    }
    missing_variants = sorted(
        REQUIRED_UNIVERSE_TEMPLATE_VARIANTS - template_variants
    )
    if missing_variants:
        raise ArtifactContractError(
            "missing required universe template variants: "
            f"{', '.join(missing_variants)}"
        )

    if source_family_profiles is None:
        return
    profiles = source_family_profiles.get("families")
    if not isinstance(profiles, list):
        raise ArtifactContractError("source_family_profiles must define families")
    enabled_family_ids = {
        profile.get("family_id")
        for profile in profiles
        if isinstance(profile, dict) and profile.get("enabled", True) is not False
    }
    covered_source_families = {
        family
        for contract in contracts.values()
        for family in contract.source_families
    }
    missing_families = sorted(enabled_family_ids - covered_source_families)
    if missing_families:
        raise ArtifactContractError(
            "missing contracts for enabled source families: "
            f"{', '.join(missing_families)}"
        )


def _require_contract_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactContractError("artifact contract must be a JSON object")
    return value


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactContractError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, field)


def _non_empty_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ArtifactContractError(f"{field} must be a non-empty list")
    values = tuple(_non_empty_string(item, field) for item in value)
    if len(values) != len(set(values)):
        raise ArtifactContractError(f"{field} must not contain duplicates")
    return values


def _safe_relative_path(value: Any, field: str) -> str:
    path = _non_empty_string(value, field).replace("\\", "/")
    pure_path = PurePosixPath(path)
    if (
        pure_path.is_absolute()
        or PureWindowsPath(path).is_absolute()
        or bool(PureWindowsPath(path).drive)
        or any(segment in {"", ".", ".."} for segment in path.split("/"))
    ):
        raise ArtifactContractError(f"{field} must be a safe relative path")
    return pure_path.as_posix()


def _safe_file_name(value: Any, field: str) -> str:
    file_name = _non_empty_string(value, field)
    if (
        file_name in {".", ".."}
        or bool(PureWindowsPath(file_name).drive)
        or PurePosixPath(file_name).name != file_name
        or PureWindowsPath(file_name).name != file_name
    ):
        raise ArtifactContractError(f"{field} must be a safe relative file name")
    return file_name


def _reject_unexpanded_variables(item: dict[str, Any], artifact_id: str) -> None:
    def has_variable(value: Any) -> bool:
        if isinstance(value, str):
            return "{" in value or "}" in value
        if isinstance(value, list):
            return any(has_variable(nested) for nested in value)
        if isinstance(value, dict):
            return any(has_variable(nested) for nested in value.values())
        return False

    if has_variable(item):
        raise ArtifactContractError(
            f"unexpanded universe template variable in {artifact_id}"
        )
