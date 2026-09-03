## Task 2: Catalog and Registry Loaders

**Files:**
- Create: `src/data_analysts/source_catalog.py`
- Modify: `src/data_analysts/config.py`
- Test: `tests/test_pit_foundation_config.py`

**Boundary:**
- This task only loads and validates static config.
- It must not implement date selection or mutate pipeline output.

**Consumes:**
- `configs/source_catalog.json`
- `configs/pit_registry.json`
- Existing `DataAnalystsRoot`.

**Produces:**
- `SourceCatalogError`
- `load_source_catalog(root)`
- `load_pit_registry(root)`
- `validate_source_catalog(payload)`
- `validate_pit_registry(payload, catalog)`
- new `RuntimeConfig.source_catalog`
- new `RuntimeConfig.pit_registry`

- [ ] **Step 1: Add failing loader tests**

Append to `tests/test_pit_foundation_config.py`:

```python
import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsRoot
from data_analysts.source_catalog import load_pit_registry, load_source_catalog


def test_load_runtime_config_includes_source_catalog_and_pit_registry():
    config = load_runtime_config(DataAnalystsRoot.from_path(ROOT))
    assert "sources" in config.source_catalog
    assert "families" in config.pit_registry


def test_source_catalog_rejects_forbidden_source_reference(tmp_path):
    root = tmp_path
    (root / "configs").mkdir()
    (root / "configs" / "mongodb_sources.json").write_text((ROOT / "configs" / "mongodb_sources.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "configs" / "universe_specs.json").write_text((ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "configs" / "source_catalog.json").write_text((ROOT / "configs" / "source_catalog.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "configs" / "pit_registry.json").write_text((ROOT / "configs" / "pit_registry.json").read_text(encoding="utf-8"), encoding="utf-8")
    bad_profiles = {
        "schema_version": "1.0",
        "families": [
            {
                "family_id": "bad_financial",
                "enabled": True,
                "connection": "tej",
                "collection": "AINVFQ1",
                "source_profile": "medium_pit_table",
                "primary_key": ["ticker", "source_date"]
            }
        ]
    }
    import json
    (root / "configs" / "source_family_profiles.json").write_text(json.dumps(bad_profiles), encoding="utf-8")

    with pytest.raises(ConfigError, match="forbidden source"):
        load_runtime_config(DataAnalystsRoot.from_path(root))
```

- [ ] **Step 2: Run tests and verify expected failure**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py -q
```

Expected: fails because `data_analysts.source_catalog` does not exist or `RuntimeConfig` has no catalog fields.

- [ ] **Step 3: Implement `src/data_analysts/source_catalog.py`**

Implement:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_analysts.paths import DataAnalystsRoot


class SourceCatalogError(ValueError):
    pass


def load_source_catalog(root: DataAnalystsRoot) -> dict[str, object]:
    return validate_source_catalog(_load_json(root.config_path("source_catalog.json")))


def load_pit_registry(root: DataAnalystsRoot) -> dict[str, object]:
    catalog = load_source_catalog(root)
    return validate_pit_registry(_load_json(root.config_path("pit_registry.json")), catalog)


def validate_source_catalog(payload: dict[str, object]) -> dict[str, object]:
    if payload.get("schema_version") != "1.0":
        raise SourceCatalogError("unsupported schema_version in source_catalog.json")
    sources = payload.get("sources")
    forbidden = payload.get("forbidden_sources")
    if not isinstance(sources, list) or not sources:
        raise SourceCatalogError("source_catalog.json must define sources")
    if not isinstance(forbidden, list) or not forbidden:
        raise SourceCatalogError("source_catalog.json must define forbidden_sources")
    seen: set[str] = set()
    forbidden_pairs = _forbidden_pairs(payload)
    for source in sources:
        if not isinstance(source, dict):
            raise SourceCatalogError("source catalog entries must be objects")
        family_id = str(source.get("family_id") or "")
        if not family_id:
            raise SourceCatalogError("source catalog entry missing family_id")
        if family_id in seen:
            raise SourceCatalogError(f"duplicate source family_id: {family_id}")
        seen.add(family_id)
        _reject_forbidden_source(source, forbidden_pairs)
        if not source.get("pit_field"):
            raise SourceCatalogError(f"source catalog entry missing pit_field: {family_id}")
        if not source.get("logical_key"):
            raise SourceCatalogError(f"source catalog entry missing logical_key: {family_id}")
    return payload


def validate_pit_registry(payload: dict[str, object], catalog: dict[str, object]) -> dict[str, object]:
    if payload.get("schema_version") != "1.0":
        raise SourceCatalogError("unsupported schema_version in pit_registry.json")
    families = payload.get("families")
    if not isinstance(families, dict) or not families:
        raise SourceCatalogError("pit_registry.json must define families")
    catalog_ids = {str(item.get("family_id")) for item in catalog.get("sources", []) if isinstance(item, dict)}
    forbidden_pairs = _forbidden_pairs(catalog)
    for family_id, rule in families.items():
        if family_id not in catalog_ids:
            raise SourceCatalogError(f"pit registry family not in source catalog: {family_id}")
        if not isinstance(rule, dict):
            raise SourceCatalogError(f"pit registry rule must be object: {family_id}")
        _reject_forbidden_source(rule, forbidden_pairs)
        if not rule.get("availability_field"):
            raise SourceCatalogError(f"pit registry missing availability_field: {family_id}")
        if not rule.get("logical_key"):
            raise SourceCatalogError(f"pit registry missing logical_key: {family_id}")
    return payload


def forbidden_source_references(config_payloads: list[dict[str, object]], catalog: dict[str, object]) -> list[dict[str, str]]:
    forbidden = _forbidden_pairs(catalog)
    hits: list[dict[str, str]] = []
    for payload in config_payloads:
        for family in payload.get("families", []) if isinstance(payload.get("families"), list) else []:
            if not isinstance(family, dict):
                continue
            database = _database_from_connection(str(family.get("connection") or ""))
            collection = str(family.get("collection") or "")
            if collection and (database, collection) in forbidden:
                hits.append({"database": database, "collection": collection, "family_id": str(family.get("family_id") or "")})
    return hits


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SourceCatalogError(f"missing config: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SourceCatalogError(f"config must be a JSON object: {path.name}")
    return payload


def _forbidden_pairs(catalog: dict[str, object]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for item in catalog.get("forbidden_sources", []):
        if isinstance(item, dict):
            pairs.add((str(item.get("database") or ""), str(item.get("collection") or "")))
    return pairs


def _reject_forbidden_source(source: dict[str, object], forbidden_pairs: set[tuple[str, str]]) -> None:
    database = str(source.get("database") or "")
    collection = str(source.get("collection") or "")
    if (database, collection) in forbidden_pairs:
        raise SourceCatalogError(f"forbidden source referenced: {database}.{collection}")


def _database_from_connection(connection: str) -> str:
    return {"tej": "TEJ", "apiprcd": "APIPRCD"}.get(connection, connection)
```

- [ ] **Step 4: Extend `RuntimeConfig` and `load_runtime_config`**

Modify `src/data_analysts/config.py`:

```python
from data_analysts.source_catalog import SourceCatalogError, forbidden_source_references, load_pit_registry, load_source_catalog
```

Add dataclass fields:

```python
source_catalog: dict[str, Any]
pit_registry: dict[str, Any]
```

In `load_runtime_config`, load catalog and registry after existing config JSON files:

```python
source_catalog = load_source_catalog(root)
pit_registry = load_pit_registry(root)
forbidden_hits = forbidden_source_references([source_family_profiles], source_catalog)
if forbidden_hits:
    first = forbidden_hits[0]
    raise ConfigError(f"forbidden source referenced: {first['database']}.{first['collection']}")
```

Return the new fields in `RuntimeConfig`.

- [ ] **Step 5: Convert catalog errors to config errors**

Add `SourceCatalogError` to the existing CLI/config exception flow by catching it where `ConfigError` is caught, or wrap it in `ConfigError` inside `load_runtime_config`.

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m pytest tests/test_pit_foundation_config.py -q
```

Expected: all tests pass.

