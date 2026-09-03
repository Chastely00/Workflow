# Task 1 Review Package

Rebuilt after fix worker.

## FILE: plans\sdd\historical-universe\task-1-brief.md
```
# Task 1 Brief

### Task 1: Historical Universe Contracts and Config

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\OUTPUT_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\VERIFICATION_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\CONFIG_CONTRACT.md`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\configs\universe_specs.json`
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\config.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_config.py`

**Interfaces:**
- Consumes: existing `RuntimeConfig.universe_specs`.
- Produces: validated universe specs with baseline universe ids and historical-safe selector fields.

- [ ] **Step 1: Write failing config tests**

Create `tests/test_historical_universe_config.py`:

```python
import json
from pathlib import Path

import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsRoot


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        (tmp_path / "configs" / name).write_text(
            (ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_universe_specs_define_baseline_historical_universes():
    payload = json.loads((ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8"))
    universe_ids = {item["universe_id"] for item in payload["universes"]}
    assert {
        "tw_equity_all_listed",
        "tw_common_stock_all",
        "tw_common_stock_tradable",
        "tw_equity_liquid_top100",
        "tw_equity_liquid_top300",
        "tw_equity_liquid_top500",
        "twse_common_stock",
        "tpex_common_stock",
    }.issubset(universe_ids)


def test_universe_config_allows_effective_date_but_rejects_realized_return(tmp_path):
    _copy_configs(tmp_path)
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["universes"][0]["filters"].append({"field": "effective_date", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    load_runtime_config(DataAnalystsRoot.from_path(tmp_path))

    payload["universes"][0]["filters"].append({"field": "realized_return_20d", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported field"):
        load_runtime_config(DataAnalystsRoot.from_path(tmp_path))
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py -q
```

Expected: FAIL because baseline universes and `effective_date` selector field are not yet configured.

- [ ] **Step 3: Update config validator**

Modify `src/data_analysts/config.py`:

```python
SECURITY_PANEL_FIELDS = {
    "as_of_date",
    "effective_date",
    "source_max_date",
    "ticker",
    "stock_name",
    "market",
    "security_type",
    "listed",
    "tradable",
    "close",
    "adj_close",
    "traded_value",
    "market_cap",
    "adv20",
    "data_cutoff_at",
}

SUPPORTED_UNIVERSE_OPERATORS = {"eq", "gte", "not_null"}
```

Then extend `_validate_universe_fields()` so every filter has a supported `op`:

```python
def _validate_universe_fields(universe_id: str, universe: dict[str, Any]) -> None:
    for filter_rule in universe.get("filters", []):
        field = filter_rule.get("field") if isinstance(filter_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
        op = filter_rule.get("op") if isinstance(filter_rule, dict) else None
        if op not in SUPPORTED_UNIVERSE_OPERATORS:
            raise ConfigError(f"universe {universe_id} uses unsupported operator: {op}")
    for rank_rule in universe.get("rank_by", []):
        field = rank_rule.get("field") if isinstance(rank_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
```

- [ ] **Step 4: Expand universe specs**

Modify `configs/universe_specs.json` to include exactly these enabled universes:

```json
{
  "schema_version": "1.0",
  "universes": [
    {
      "universe_id": "tw_equity_all_listed",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_common_stock_all",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_common_stock_tradable",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_equity_liquid_top100",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 100
    },
    {
      "universe_id": "tw_equity_liquid_top300",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 300
    },
    {
      "universe_id": "tw_equity_liquid_top500",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 500
    },
    {
      "universe_id": "twse_common_stock",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market", "op": "eq", "value": "TWSE"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tpex_common_stock",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market", "op": "eq", "value": "TPEX"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    }
  ]
}
```

- [ ] **Step 5: Update contracts**

In `OUTPUT_CONTRACT.md`, add historical paths:

```text
runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/diagnostics.parquet
```

State that `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` is a latest-date convenience output only, not the historical canonical surface.

In `VERIFICATION_CONTRACT.md`, add hard checks:

```text
effective_date > as_of_date
duplicate (effective_date, universe_id, ticker) count == 0
duplicate (effective_date, universe_id, rank) count == 0
small_file_daily_partition_count == 0
top-N row_count <= N for every effective_date
top-N row_count == N when eligible_count >= N
```

- [ ] **Step 6: Run config tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py tests\test_pit_foundation_config.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add configs\universe_specs.json contracts\OUTPUT_CONTRACT.md contracts\VERIFICATION_CONTRACT.md contracts\CONFIG_CONTRACT.md src\data_analysts\config.py tests\test_historical_universe_config.py
git commit -m "spec: define historical universe contracts"
```


```


## FILE: plans\sdd\historical-universe\task-1-report.md
```
# Task 1 Report: Historical Universe Contracts and Config

## STATUS

DONE

## Files changed

- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_universe_config.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\config.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\configs\universe_specs.json`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\CONFIG_CONTRACT.md`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\OUTPUT_CONTRACT.md`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\contracts\VERIFICATION_CONTRACT.md`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-1-report.md`

## RED test command/result

Command:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py -q
```

Result: failed as expected.

- `test_universe_specs_define_baseline_historical_universes` failed because `configs/universe_specs.json` only contained `tw_equity_liquid_top500`.
- `test_universe_config_allows_effective_date_but_rejects_realized_return` failed because `load_runtime_config()` rejected `effective_date` as an unsupported field.

## GREEN test command/result

Command:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py tests\test_pit_foundation_config.py -q
```

Result: passed.

```text
10 passed in 0.15s
```

## Self-review notes

- Scope stayed inside `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts`.
- Followed the brief's TDD order: added the failing test first, captured RED, then implemented the minimum validator/config/contract updates needed for GREEN.
- Did not implement security panel history publishing, universe builder behavior, pipeline publish, or verify gates.
- Config validation remains fail-closed: unsupported universe fields and unsupported operators still raise `ConfigError`.
- `effective_date` was added only as an allowed selector/control field; realized-return fields remain rejected by config validation.

## Concerns

None for Task 1. Contracts now describe historical canonical paths before runtime implementation exists, which is intentional for this contract/config-only slice.

## 2026-07-07 Reviewer Fix Append

- Fixed `contracts/OUTPUT_CONTRACT.md` so historical canonical `security_panel_history` explicitly requires `effective_date` in the required-column surface.
- Fixed `contracts/OUTPUT_CONTRACT.md` so historical canonical `membership_by_year` is no longer described by the 4-column convenience schema; it now requires at least `as_of_date`, `effective_date`, `universe_id`, `ticker`, `rank`, `included`, `reason`, `market`, `security_type`, `listed`, `tradable`, `close`, `adj_close`, `market_cap`, `adv20`, and `data_cutoff_at`.
- Kept `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` explicitly scoped as a latest-date convenience output and clarified that it must not be confused with the historical canonical schema.
- Tightened `tests/test_historical_universe_config.py` so it asserts the exact enabled baseline universe set instead of a subset.
- Added a fail-closed regression proving unsupported universe filter operators still raise `ConfigError`.

Verification:

```text
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_universe_config.py tests\test_pit_foundation_config.py -q
11 passed in 0.13s
```

```


## FILE: tests\test_historical_universe_config.py
```
import json
from pathlib import Path

import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsRoot


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
    ]:
        (tmp_path / "configs" / name).write_text(
            (ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_universe_specs_define_baseline_historical_universes():
    payload = json.loads((ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8"))
    enabled_universe_ids = {
        item["universe_id"]
        for item in payload["universes"]
        if item.get("enabled", True)
    }
    assert enabled_universe_ids == {
        "tw_equity_all_listed",
        "tw_common_stock_all",
        "tw_common_stock_tradable",
        "tw_equity_liquid_top100",
        "tw_equity_liquid_top300",
        "tw_equity_liquid_top500",
        "twse_common_stock",
        "tpex_common_stock",
    }


def test_universe_config_allows_effective_date_but_rejects_realized_return(tmp_path):
    _copy_configs(tmp_path)
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["universes"][0]["filters"].append({"field": "effective_date", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    load_runtime_config(DataAnalystsRoot.from_path(tmp_path))

    payload["universes"][0]["filters"].append({"field": "realized_return_20d", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported field"):
        load_runtime_config(DataAnalystsRoot.from_path(tmp_path))


def test_universe_config_rejects_unsupported_operator(tmp_path):
    _copy_configs(tmp_path)
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["universes"][0]["filters"][0]["op"] = "lte"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported operator"):
        load_runtime_config(DataAnalystsRoot.from_path(tmp_path))

```


## FILE: configs\universe_specs.json
```
{
  "schema_version": "1.0",
  "universes": [
    {
      "universe_id": "tw_equity_all_listed",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_common_stock_all",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_common_stock_tradable",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tw_equity_liquid_top100",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 100
    },
    {
      "universe_id": "tw_equity_liquid_top300",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 300
    },
    {
      "universe_id": "tw_equity_liquid_top500",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 500
    },
    {
      "universe_id": "twse_common_stock",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market", "op": "eq", "value": "TWSE"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    },
    {
      "universe_id": "tpex_common_stock",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "security_type", "op": "eq", "value": "common_stock"},
        {"field": "market", "op": "eq", "value": "TPEX"}
      ],
      "rank_by": [
        {"field": "ticker", "direction": "asc"}
      ]
    }
  ]
}

```


## FILE: src\data_analysts\config.py
```
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from data_analysts.paths import DataAnalystsRoot
from data_analysts.source_catalog import (
    SourceCatalogError,
    forbidden_source_references,
    load_pit_registry,
    load_source_catalog,
)


SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_SOURCE_PROFILES = {"small_snapshot", "medium_pit_table", "large_daily_panel"}
SECURITY_PANEL_FIELDS = {
    "as_of_date",
    "effective_date",
    "source_max_date",
    "ticker",
    "stock_name",
    "market",
    "security_type",
    "listed",
    "tradable",
    "close",
    "adj_close",
    "traded_value",
    "market_cap",
    "adv20",
    "data_cutoff_at",
}
SUPPORTED_UNIVERSE_OPERATORS = {"eq", "gte", "not_null"}


class ConfigError(ValueError):
    """Raised when DataAnalysts config cannot be safely used."""


@dataclass(frozen=True)
class RuntimeConfig:
    mongodb_sources: dict[str, Any]
    source_family_profiles: dict[str, Any]
    universe_specs: dict[str, Any]
    source_catalog: dict[str, Any]
    pit_registry: dict[str, Any]
    family_ids: set[str]
    universe_ids: set[str]


def load_runtime_config(root: DataAnalystsRoot) -> RuntimeConfig:
    mongodb_sources = _load_required_json(root.config_path("mongodb_sources.json"))
    source_family_profiles = _load_required_json(root.config_path("source_family_profiles.json"))
    universe_specs = _load_required_json(root.config_path("universe_specs.json"))

    _require_schema(mongodb_sources, "mongodb_sources.json")
    _require_schema(source_family_profiles, "source_family_profiles.json")
    _require_schema(universe_specs, "universe_specs.json")
    _reject_plaintext_mongodb_uri(mongodb_sources)
    try:
        source_catalog = load_source_catalog(root)
        pit_registry = load_pit_registry(root)
    except SourceCatalogError as exc:
        raise ConfigError(str(exc)) from exc

    connections = mongodb_sources.get("connections")
    if not isinstance(connections, dict):
        raise ConfigError("mongodb_sources.json must define connections")

    forbidden_hits = forbidden_source_references([source_family_profiles], source_catalog, connections)
    if forbidden_hits:
        first = forbidden_hits[0]
        raise ConfigError(f"forbidden source referenced: {first['database']}.{first['collection']}")

    family_ids = _validate_families(source_family_profiles, connections)
    universe_ids = _validate_universes(universe_specs)

    return RuntimeConfig(
        mongodb_sources=mongodb_sources,
        source_family_profiles=source_family_profiles,
        universe_specs=universe_specs,
        source_catalog=source_catalog,
        pit_registry=pit_registry,
        family_ids=family_ids,
        universe_ids=universe_ids,
    )


def _load_required_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ConfigError(f"config must be a JSON object: {path.name}")
    return payload


def _require_schema(payload: dict[str, Any], filename: str) -> None:
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(f"unsupported schema_version in {filename}")


def _reject_plaintext_mongodb_uri(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "uri":
                raise ConfigError("plaintext MongoDB URI is not allowed")
            if key == "default_uri":
                _validate_localhost_default_uri(value)
            else:
                _reject_plaintext_mongodb_uri(value)
    elif isinstance(payload, list):
        for value in payload:
            _reject_plaintext_mongodb_uri(value)
    elif isinstance(payload, str) and payload.startswith(("mongodb://", "mongodb+srv://")):
        raise ConfigError("plaintext MongoDB URI is not allowed")


def _validate_localhost_default_uri(value: Any) -> None:
    if not isinstance(value, str):
        raise ConfigError("default_uri must be a string")
    parsed = urlparse(value)
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise ConfigError("default_uri must be a MongoDB URI")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError("default_uri must be localhost")
    if parsed.username or parsed.password:
        raise ConfigError("default_uri must not contain credentials")


def _validate_families(payload: dict[str, Any], connections: dict[str, Any]) -> set[str]:
    families = payload.get("families")
    if not isinstance(families, list):
        raise ConfigError("source_family_profiles.json must define families")

    seen: set[str] = set()
    for family in families:
        if not isinstance(family, dict):
            raise ConfigError("family config must be a JSON object")
        family_id = family.get("family_id")
        if not isinstance(family_id, str) or not family_id:
            raise ConfigError("family_id is required")
        if family_id in seen:
            raise ConfigError(f"duplicate family_id: {family_id}")
        seen.add(family_id)

        if family.get("enabled", True) is False:
            continue

        source_profile = family.get("source_profile")
        if source_profile not in SUPPORTED_SOURCE_PROFILES:
            raise ConfigError(f"unsupported source_profile for {family_id}: {source_profile}")

        connection = family.get("connection")
        if connection not in connections:
            raise ConfigError(f"unknown connection for {family_id}: {connection}")

        primary_key = family.get("primary_key")
        if not isinstance(primary_key, list) or not primary_key:
            raise ConfigError(f"primary_key is required for {family_id}")

    return seen


def _validate_universes(payload: dict[str, Any]) -> set[str]:
    universes = payload.get("universes")
    if not isinstance(universes, list):
        raise ConfigError("universe_specs.json must define universes")

    seen: set[str] = set()
    for universe in universes:
        if not isinstance(universe, dict):
            raise ConfigError("universe config must be a JSON object")
        universe_id = universe.get("universe_id")
        if not isinstance(universe_id, str) or not universe_id:
            raise ConfigError("universe_id is required")
        if universe_id in seen:
            raise ConfigError(f"duplicate universe_id: {universe_id}")
        seen.add(universe_id)

        if universe.get("enabled", True) is False:
            continue
        if universe.get("source") != "security_panel":
            raise ConfigError(f"universe {universe_id} must use security_panel source")
        _validate_universe_fields(universe_id, universe)

    return seen


def _validate_universe_fields(universe_id: str, universe: dict[str, Any]) -> None:
    for filter_rule in universe.get("filters", []):
        field = filter_rule.get("field") if isinstance(filter_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
        op = filter_rule.get("op") if isinstance(filter_rule, dict) else None
        if op not in SUPPORTED_UNIVERSE_OPERATORS:
            raise ConfigError(f"universe {universe_id} uses unsupported operator: {op}")
    for rank_rule in universe.get("rank_by", []):
        field = rank_rule.get("field") if isinstance(rank_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")

```


## FILE: contracts\CONFIG_CONTRACT.md
```
# Config Contract

DataAnalysts 的操作參數應主要由 `configs/*.json` 管理。CLI 只保留三種回補模式需要的最小參數，避免日常操作把資料語意變成人工選項。

## Config Layout

```text
configs/
  mongodb_sources.json
  source_family_profiles.json
  universe_specs.json
```

所有 config path 都必須是 DataAnalysts root-relative。config 不得包含密碼、token、個人帳號或可直接外流的 MongoDB URI。唯一允許寫入 config 的 URI 是無帳密的 localhost default：

```text
mongodb://localhost:27017/
```

## `mongodb_sources.json`

目的：

- 定義 MongoDB connection 的環境變數名稱。
- 定義 database 與 collection namespace。
- 不存放 credential。

範例：

```json
{
  "schema_version": "1.0",
  "connections": {
    "tej": {
      "uri_env": "DATA_ANALYSTS_MONGODB_URI",
      "default_uri": "mongodb://localhost:27017/",
      "database": "TEJ"
    },
    "futures_taifex_tx": {
      "uri_env": "DATA_ANALYSTS_MONGODB_URI",
      "default_uri": "mongodb://localhost:27017/",
      "database": "Futures_TAIFEX_TX"
    }
  }
}
```

規則：

- `uri_env` 若存在於執行環境，優先使用環境變數。
- `uri_env` 不存在時，使用 `default_uri`。
- `default_uri` 只能是無帳密 localhost，例如 `mongodb://localhost:27017/`。
- config 不得提供遠端 host、帳密、token 或個人帳號形式的 plaintext URI。
- database 名稱必須由 source family 引用，不由 CLI 直接輸入。

## `source_family_profiles.json`

目的：

- 定義每個 source family 如何抽取、PIT normalization、partition、publish。

範例：

```json
{
  "schema_version": "1.0",
  "families": [
    {
      "family_id": "daily_price_volume",
      "enabled": true,
      "connection": "tej",
      "database": "TEJ",
      "collection_pattern": "APIPRCD.{ticker}",
      "source_profile": "large_daily_panel",
      "primary_key": ["date", "ticker"],
      "date_fields": {
        "source_date": "mdate"
      },
      "availability": {
        "type": "same_day_after_close",
        "field": "mdate"
      },
      "partitioning": ["year"],
      "required_for_daily": true,
      "pit_policy": "source_date_lagged_to_decision_date"
    },
    {
      "family_id": "security_master",
      "enabled": true,
      "connection": "tej",
      "database": "TEJ",
      "collection": "APISTOCK",
      "source_profile": "small_snapshot",
      "primary_key": ["ticker"],
      "date_fields": {},
      "availability": {
        "type": "snapshot_as_of_cutoff"
      },
      "partitioning": ["single_file"],
      "required_for_daily": true,
      "pit_policy": "snapshot_cutoff"
    }
  ]
}
```

允許的 `source_profile`：

```text
small_snapshot
medium_pit_table
large_daily_panel
```

規則：

- `family_id` 必須唯一。
- `primary_key` 必須在 date normalization 後可建立 duplicate check。
- PIT table 必須定義 availability rule。
- large daily panel 必須有 bounded query rule，不可 full collection scan 全市場全歷史。
- small snapshot 不應被硬切成大量 tiny parquet files。
- unknown `source_profile` 必須 blocked。

## `universe_specs.json`

目的：

- 定義 DataAnalysts-owned universe selector。
- selector 只可使用 security panel 欄位。
- historical-safe selector 可使用 runtime-derived control fields，但不得引用任何 realized/forward outcome。

範例：

```json
{
  "schema_version": "1.0",
  "universes": [
    {
      "universe_id": "tw_equity_liquid_top500",
      "enabled": true,
      "source": "security_panel",
      "filters": [
        {"field": "listed", "op": "eq", "value": true},
        {"field": "tradable", "op": "eq", "value": true},
        {"field": "market_cap", "op": "not_null"},
        {"field": "adv20", "op": "gte", "value": 10000000}
      ],
      "rank_by": [
        {"field": "market_cap", "direction": "desc"},
        {"field": "ticker", "direction": "asc"}
      ],
      "limit": 500
    }
  ]
}
```

允許 selector 欄位：

```text
as_of_date
effective_date
source_max_date
ticker
stock_name
market
security_type
listed
tradable
close
adj_close
traded_value
market_cap
adv20
data_cutoff_at
```

允許 filter operator：

```text
eq
gte
not_null
```

Historical-safe control field semantics：

- `effective_date` 是 membership 生效日，可用於 historical universe selector。
- `source_max_date` 是 security panel 當下可觀測到的最晚 source date。
- `effective_date` 與 `source_max_date` 都屬於 runtime control fields，不可替代 realized return、strategy signal、或其他未來資訊。

禁止：

- alpha feature。
- IC/IR。
- feature importance。
- realized return。
- strategy signal。
- portfolio weight。
- external feature store 欄位。
- unsupported operator。

## Config Validation

每次 run 前必須先驗證：

- required config file exists。
- `schema_version` supported。
- no absolute output path。
- no credential value。
- family ids unique。
- enabled family has source definition。
- required daily family enabled or explicitly waived by config。
- universe selector fields all belong to security panel schema。
- universe filter operators are limited to `eq`, `gte`, `not_null`。

驗證失敗時必須 fail closed，並寫入：

```text
runtime/jobs/config_validation_result.json
```

## Source Catalog and PIT Registry

Valid configs must include:

```text
configs/source_catalog.json
configs/pit_registry.json
```

Validation fails closed when:

- either file is missing.
- schema version is unsupported.
- a family id is duplicated.
- a PIT field is missing.
- a logical key is missing.
- any config references `TEJ.AINVFQ1`.
- any config references `TEJ.APISHRACTW`.

`configs/source_catalog.json` declares approved source families and source collection names. `configs/pit_registry.json` declares the PIT field, logical key, and revision-selection contract for each PIT source family.

The catalog and PIT registry must agree on family ids. A family present in one file but missing from the other is ambiguous and must block validation when that family has PIT semantics.

```


## FILE: contracts\OUTPUT_CONTRACT.md
```
# Output Contract

DataAnalysts 的輸出是下游唯一可依賴的資料產品表面。所有 runtime 產物必須是 DataAnalysts root 的相對路徑，且位於 `runtime/` 之下。

## Runtime Layout

```text
runtime/
  data_canonical/
    raw/
      <dataset_id>/
    derived/
      events/
        dividend_events/
        capital_action_events/
      security_panel/
      universes/
        <universe_id>/
  manifests/
  diagnostics/
  jobs/
  output/
    universes/
```

## Canonical Raw Artifacts

路徑：

```text
runtime/data_canonical/raw/<dataset_id>/...
```

可接受 partition 形式：

```text
year=YYYY/part.parquet
available_year=YYYY/part.parquet
<dataset_id>.parquet
```

選擇規則：

- `small_snapshot`: 單檔或少量固定檔案。
- `medium_pit_table`: 依 source date 或 availability date partition。
- `large_daily_panel`: 依 year、ticker chunk、bounded date window 控制 I/O。

每列至少要保留：

```text
source_dataset_id
source_collection
source_row_id
data_cutoff_at
```

若 source family 有 PIT 意義，還必須有至少一個可審核日期欄位：

```text
source_date
source_available_date
source_period_date
event_date
```

## Raw Family Expansion Outputs

Raw family artifacts are registry-driven canonical parquet surfaces. They are not feature tables and they are not strategy inputs until Feature Analysts consume them.

Required raw outputs:

| artifact_id | layer | partitioning | PIT field |
|---|---|---|---|
| trading_calendar | raw | single_file | zdate |
| daily_tradability | raw | year | mdate |
| daily_chip | raw | year | mdate |
| monthly_sales | raw | available_year | annd_s |
| financial_statement_raw | raw | available_year | key3 |
| financial_statement_pit_selected | derived | decision_year | source_available_date |
| self_reported_numbers_raw | raw | available_year | annd |
| self_reported_numbers_pit_selected | derived | decision_year | source_available_date |
| taiwan_index_futures_near_month | raw | year | 日期 |

Governance and event raw families use `mdate` as `source_available_date` and publish by `available_year`.

## Adjusted Price Artifact

`daily_price_volume` 同時承載 raw price 與 DataAnalysts-owned adjusted price。

最低欄位：

```text
date
ticker
open
high
low
close
volume
traded_value
adj_factor
adj_open
adj_high
adj_low
adj_close
data_cutoff_at
```

規則：

- raw price 不得覆寫。
- adjusted price 必須可追溯到 semantic events。
- partial refresh 若缺 prior `adj_factor` seed 必須 blocked。
- 不得用 TEJ raw `adjfac` 取代 DataAnalysts-owned event-based adjusted price。

## Event Artifacts

Dividend events：

```text
runtime/data_canonical/derived/events/dividend_events/event_year=YYYY/part.parquet
```

最低欄位：

```text
event_date
ex_date
ticker
cash_dividend_per_share
stock_dividend_ratio
source_dataset_id
source_row_id
data_cutoff_at
```

Capital action events：

```text
runtime/data_canonical/derived/events/capital_action_events/event_year=YYYY/part.parquet
```

最低欄位：

```text
event_date
ex_date
ticker
action_type
share_multiplier
cash_return_per_share
price_adjustment_reference
source_dataset_id
source_row_id
data_cutoff_at
```

## Corporate Actions

路徑：

```text
runtime/data_canonical/raw/corporate_actions/year=YYYY/corporate_actions.parquet
```

規則：

- 只包含 ledger-relevant events。
- `stock_price_adjustment` 只供 adjusted price 使用，不得混入 ledger corporate_actions。
- 每列必須能回追 source event。

## Security Panel

路徑：

```text
runtime/data_canonical/derived/security_panel/as_of_date=YYYY-MM-DD/security_panel.parquet
runtime/data_canonical/derived/security_panel_history/as_of_year=YYYY/part.parquet
```

最低欄位：

```text
as_of_date
effective_date
source_max_date
ticker
stock_name
market
security_type
listed
tradable
close
adj_close
traded_value
market_cap
adv20
data_cutoff_at
```

禁止 leakage 欄位名稱：

```text
future
forward
next
realized
outcome
label_return
```

historical canonical `security_panel_history/as_of_year=YYYY/part.parquet` 必須至少包含上述 required columns，且不得省略 `effective_date`。

## Universe Membership

路徑：

```text
runtime/data_canonical/derived/universes/<universe_id>/membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet
runtime/data_canonical/derived/universes/<universe_id>/membership_by_year/as_of_year=YYYY/part.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/as_of_date=YYYY-MM-DD/diagnostics.parquet
runtime/data_canonical/derived/universes/<universe_id>/diagnostics/diagnostics.parquet
runtime/output/universes/universe_construction_result.json
```

`membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` 是 latest-date convenience output only。

latest convenience schema 固定為：

```text
as_of_date
universe_id
ticker
rank
```

historical canonical `membership_by_year/as_of_year=YYYY/part.parquet` schema 至少必須包含：

```text
as_of_date
effective_date
universe_id
ticker
rank
included
reason
market
security_type
listed
tradable
close
adj_close
market_cap
adv20
data_cutoff_at
```

規則：

- 每個 `(as_of_date, universe_id, ticker)` 必須唯一。
- `rank` 必須在同一個 `(as_of_date, universe_id)` 內可排序且不重複。
- selector 只可使用 security panel 欄位。
- 不得依賴外部 feature store。
- `membership_by_date/as_of_date=YYYY-MM-DD/membership.parquet` 不得與 historical canonical schema 混淆。
- historical canonical surface 必須使用 `membership_by_year/as_of_year=YYYY/part.parquet`，且 required columns 以 historical canonical schema 為準。

## Manifest Schema

每個 publishable artifact 必須有 manifest：

```text
runtime/manifests/<artifact_id>.json
```

最低欄位：

```text
artifact_id
schema_version
layer
source_families
source_collections
row_count
date_range
availability_date_range
columns
partitioning
artifact_paths
pit_policy
data_cutoff_at
duplicate_count
omitted_row_count
status
created_at
```

`artifact_paths` 必須都是 root-relative path，且解析後位於 DataAnalysts root 之內。

## Atomic Publish

任何 parquet 或 manifest publish 必須遵守：

- 先寫 staging。
- 驗證 row count、schema、partition、manifest path。
- 成功後 atomic rename 或替換。
- 失敗時不得留下 partial final artifact。
- job result 必須寫入 blocked reason。

## PIT Foundation Diagnostics

PIT Foundation writes diagnostics to:

```text
runs/real_all_products/diagnostics/pit_foundation/source_catalog.json
```

The diagnostic must include:

```text
forbidden_source_count
approved_source_count
pit_registry_family_count
forbidden_source_usage_count
missing_pit_field_count
missing_logical_key_count
```

Metric meanings:

- `forbidden_source_count`: count of forbidden source definitions found in the configured catalog surface.
- `approved_source_count`: count of catalog sources approved for PIT Foundation use.
- `pit_registry_family_count`: count of source families with PIT registry entries.
- `forbidden_source_usage_count`: count of forbidden source references found in configs, catalogs, manifests, diagnostics, or runtime output metadata.
- `missing_pit_field_count`: count of PIT source families without a declared availability/PIT field.
- `missing_logical_key_count`: count of PIT source families without a declared logical key.

The diagnostic is part of the publishable contract surface. Missing metrics or non-integer metric values make the diagnostic invalid.

```


## FILE: contracts\VERIFICATION_CONTRACT.md
```
# Verification Contract

Verify 的目的不是重跑資料流程，而是判斷既有 DataAnalysts runtime 是否可以交付給下游。它應只讀取 `runtime/`、`configs/` 與 contracts，不查 MongoDB，不寫 canonical parquet。

## Verify Command

```powershell
python -m data_analysts.cli verify --root .
python -m data_analysts.cli verify --root . --as-of-date 2026-07-03
```

產出：

```text
runtime/diagnostics/verification_<scope>.json
runtime/jobs/verification_result.json
```

## Scope Checks

必檢：

- DataAnalysts root 可解析。
- 所有 manifest `artifact_paths` 解析後都位於 root 內。
- `runtime/` 外沒有 DataAnalysts output。
- config 不含 plaintext credential。
- CLI contract 中被禁止的 ALF adapter 不存在於 DataAnalysts runtime path。

## Config Checks

必檢：

- `configs/mongodb_sources.json` exists。
- `configs/source_family_profiles.json` exists。
- `configs/universe_specs.json` exists。
- schema version supported。
- enabled family ids unique。
- all enabled families have supported source profile。
- source profile 為 `small_snapshot`、`medium_pit_table` 或 `large_daily_panel`。
- universe selector 欄位都屬於 security panel schema。

## Manifest Checks

每個 manifest 必檢：

- `artifact_id` exists。
- `schema_version` supported。
- `row_count` exists and non-negative。
- `columns` exists and non-empty。
- `artifact_paths` non-empty。
- `date_range` exists when artifact has date semantics。
- `availability_date_range` exists when artifact has PIT semantics。
- `duplicate_count` exists。
- `omitted_row_count` exists。
- `status` is `ready` or `blocked`。

若 `status = "blocked"`，必須包含：

```text
blocked_step
message
next_actions
```

## PIT Checks

必檢：

- date columns parse as ISO or supported source date format。
- duplicate key 在 date normalization 後計算。
- PIT family 不可只用 period date 當 availability date。
- financial/monthly sales 必須使用 availability date 或明確 cutoff policy。
- missing required source_available_date 必須 blocked 或 omitted with diagnostics。

## Raw Family Thresholds

Verification blocks unless:

- `pit_parse_failure_count_total == 0`
- `unresolved_duplicate_count_total == 0`
- `forbidden_source_usage_count_total == 0`
- every manifest artifact path stays under DataAnalysts root
- every selected PIT view has `source_available_date <= decision_date`

Every raw family diagnostic must report:

- `source_row_count`
- `published_row_count`
- `omitted_row_count`
- `pit_null_count`
- `pit_parse_failure_count`
- `duplicate_logical_key_count`
- `resolved_duplicate_count`
- `unresolved_duplicate_count`
- `date_min`
- `date_max`
- `artifact_file_count`

## Adjusted Price Checks

必檢：

- `daily_price_volume` 有 raw price 欄位。
- `daily_price_volume` 有 `adj_factor` 與 adjusted OHLC。
- partial refresh manifest 記錄 prior seed。
- 缺 prior seed 時不能 ready。
- adjusted price event sources 可追溯到 dividend/capital semantic events。

## Corporate Action Checks

必檢：

- `dividend_events` manifest exists when dividend source selected。
- `capital_action_events` manifest exists when capital source selected。
- `corporate_actions` rows can trace source event。
- `stock_price_adjustment` 不得出現在 ledger corporate_actions。

## Security Panel Checks

必檢：

- security panel artifact exists for requested as_of_date。
- required columns all exist。
- `(as_of_date, ticker)` unique。
- `source_max_date <= as_of_date` under configured decision policy。
- leakage columns 不存在。

禁止欄位名稱包含：

```text
future
forward
next
realized
outcome
label_return
```

## Universe Checks

必檢：

- universe membership exists for requested as_of_date。
- membership schema exactly includes required fields or required fields plus explicitly allowed diagnostics fields。
- `(as_of_date, universe_id, ticker)` unique。
- `rank` non-null。
- rank does not duplicate within `(as_of_date, universe_id)`。
- universe diagnostics records candidate count, included count, excluded count。
- selector uses only security panel fields。
- no dependency on external feature store。
- `effective_date > as_of_date` for historical universe membership rows。
- duplicate `(effective_date, universe_id, ticker)` count == 0。
- duplicate `(effective_date, universe_id, rank)` count == 0。
- `small_file_daily_partition_count == 0` on the historical canonical surface。
- top-N universe `row_count <= N` for every `effective_date`。
- top-N universe `row_count == N` when `eligible_count >= N`。

## Result Contract

Ready result：

```json
{
  "status": "ready",
  "checked_at": "2026-07-03T00:00:00Z",
  "scope": "2026-07-03",
  "checks": []
}
```

Blocked result：

```json
{
  "status": "blocked",
  "checked_at": "2026-07-03T00:00:00Z",
  "scope": "2026-07-03",
  "blocked_step": "security_panel",
  "message": "missing required security_panel artifact",
  "next_actions": [
    "run DataAnalysts daily refresh for the requested as_of_date",
    "inspect runtime/manifests/security_panel.json"
  ],
  "checks": []
}
```

Verify 不得把 warning 當 ready。若 downstream 需要的 artifact 不完整，結果必須 blocked。

## PIT Foundation Thresholds

Verification is blocked unless:

- `forbidden_source_usage_count == 0`
- `missing_pit_field_count == 0`
- `missing_logical_key_count == 0`
- `TEJ.AINVFQ1` references are absent
- `TEJ.APISHRACTW` references are absent

The PIT Foundation diagnostic at `runs/real_all_products/diagnostics/pit_foundation/source_catalog.json` must be readable and must contain all required metrics from `OUTPUT_CONTRACT.md`.

Verification must treat missing PIT Foundation diagnostics as blocked whenever PIT Foundation artifacts or configs are in scope. Threshold breaches are hard failures, not warnings.

```

