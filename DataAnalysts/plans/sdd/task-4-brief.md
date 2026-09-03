## Task 4: Diagnostics Writer

**Files:**
- Modify: `src/data_analysts/paths.py`
- Create: `src/data_analysts/diagnostics.py`
- Test: `tests/test_pit_foundation_verify.py`

**Boundary:**
- This task writes JSON diagnostics only.
- It must enforce DataAnalysts root boundaries.

**Produces:**
- `write_diagnostic(root, name, payload)`

- [ ] **Step 1: Create failing diagnostics tests**

Create `tests/test_pit_foundation_verify.py`:

```python
import json

from data_analysts.diagnostics import write_diagnostic
from data_analysts.paths import DataAnalystsRoot


def test_write_diagnostic_stays_under_runtime_diagnostics(tmp_path):
    root = DataAnalystsRoot.from_path(tmp_path)
    path = write_diagnostic(root, "pit_foundation/source_catalog", {"status": "ready", "forbidden_source_usage_count": 0})

    assert path == tmp_path / "runs" / "real_all_products" / "diagnostics" / "pit_foundation" / "source_catalog.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["forbidden_source_usage_count"] == 0
```

- [ ] **Step 2: Run test and verify expected failure**

Run:

```powershell
python -m pytest tests/test_pit_foundation_verify.py -q
```

Expected: fails because `diagnostics.py` does not exist.

- [ ] **Step 3: Implement `src/data_analysts/diagnostics.py`**

First add this method to `DataAnalystsRoot` in `src/data_analysts/paths.py`:

```python
    def diagnostics_path(self, *parts: str) -> Path:
        return self.resolve_output(Path("runs") / "real_all_products" / "diagnostics" / Path(*parts))
```

Then implement `src/data_analysts/diagnostics.py`:

Implement:

```python
from __future__ import annotations

import json
from pathlib import Path

from data_analysts.paths import DataAnalystsRoot


def write_diagnostic(root: DataAnalystsRoot, name: str, payload: dict[str, object]) -> Path:
    safe_parts = [part for part in name.replace("\\", "/").split("/") if part and part not in {".", ".."}]
    if not safe_parts:
        raise ValueError("diagnostic name is required")
    path = root.diagnostics_path(*safe_parts).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run diagnostics test**

Run:

```powershell
python -m pytest tests/test_pit_foundation_verify.py -q
```

Expected: diagnostics test passes.

