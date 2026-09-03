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

