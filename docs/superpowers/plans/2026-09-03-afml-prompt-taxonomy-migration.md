# AFML Prompt Taxonomy Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move downstream AFML authority into its own namespace and add a persistent Goal Prompt for Tier 1--3 and DSR work.

**Architecture:** ETF Tricks remains the upstream artifact-construction subsystem. AFML consumes immutable ETF artifacts through `docs/afml/prompts`; its root Goal coordinates one numbered stage at a time.

**Tech Stack:** Git, PowerShell, Markdown, ripgrep.

**Spec:** `docs/superpowers/specs/2026-09-03-afml-prompt-taxonomy-design.md`

## Global Constraints

- Do not change data, strategy code, source manifests, raw data, notebooks, PDFs, or `requirements.txt`.
- Preserve prompt history with `git mv`; do not leave duplicate authority files.
- Keep `docs/afml/prompts/00-goal-prompt.md` below 4,000 Chinese characters.
- Update every Markdown reference to former AFML prompts.

---

### Task 1: Create AFML root authority

**Files:**

- Create: `docs/afml/prompts/README.md`
- Create: `docs/afml/prompts/00-goal-prompt.md`

**Interfaces:** Consumes the approved taxonomy specification. Produces an AFML-local authority index and persistent resumption contract.

- [ ] **Step 1: Assert the root files are absent**

Run: `Test-Path docs\afml\prompts\00-goal-prompt.md; Test-Path docs\afml\prompts\README.md`

Expected: both return `False`.

- [ ] **Step 2: Create the root files**

The Goal requires manifest/hash readiness, one active stage, bounded 2024--2026 tests before full history, PIT/OOF/execution/sealed-test gates, append-only evidence, and no live orders. The README states that ETF prompts are upstream only.

- [ ] **Step 3: Validate and commit**

Run: `Test-Path docs\afml\prompts\00-goal-prompt.md; Test-Path docs\afml\prompts\README.md; git add docs/afml/prompts/README.md docs/afml/prompts/00-goal-prompt.md; git commit -m "docs: add AFML goal authority"`

Expected: paths return `True`; commit contains only the two root files.

### Task 2: Move and renumber AFML prompts

**Files:**

- Move: `docs/etf_tricks/prompts/03-afml-dataset-goal-prompt.md` -> `docs/afml/prompts/01-dataset-goal-prompt.md`
- Move: `docs/etf_tricks/prompts/04-afml-dataset-master-prompt.md` -> `docs/afml/prompts/02-dataset-master-prompt.md`
- Move: `docs/etf_tricks/prompts/05-tiered-ml-strategy-master-prompt.md` -> `docs/afml/prompts/03-tiered-ml-strategy-master-prompt.md`
- Move: `docs/etf_tricks/prompts/06-tier1-directional-label-and-model-prompt.md` -> `docs/afml/prompts/04-tier1-directional-label-and-model-prompt.md`
- Move: `docs/etf_tricks/prompts/07-tier2-meta-labeling-prompt.md` -> `docs/afml/prompts/05-tier2-meta-labeling-prompt.md`
- Move: `docs/etf_tricks/prompts/08-tier3-allocation-and-paper-execution-prompt.md` -> `docs/afml/prompts/06-tier3-allocation-and-paper-execution-prompt.md`
- Move: `docs/etf_tricks/prompts/09-strategy-governance-dsr-acceptance-prompt.md` -> `docs/afml/prompts/07-strategy-governance-dsr-acceptance-prompt.md`

**Interfaces:** Consumes seven current downstream prompts. Produces a contiguous AFML-local authority sequence with resolving parent and child references.

- [ ] **Step 1: Prove all sources exist**

Run: `Get-ChildItem docs\etf_tricks\prompts\0[3-9]-*.md | Select-Object -ExpandProperty Name`

Expected: seven source prompts.

- [ ] **Step 2: Move exactly the listed files with `git mv`**

Update each moved prompt's local predecessor, parent, and child names to their new AFML-local filenames.

- [ ] **Step 3: Validate and commit**

Run: `Get-ChildItem docs\afml\prompts\0[1-7]-*.md | Select-Object -ExpandProperty Name; git add docs/afml/prompts; git commit -m "docs: move AFML prompt authority"`

Expected: seven files numbered `01` through `07`; commit preserves moves and contains no data files.

### Task 3: Repair links and prove one authority graph

**Files:**

- Modify: `docs/etf_tricks/prompts/README.md`, `00-goal-prompt.md`, and `01-master-prompt.md`
- Modify: `docs/etf_tricks/performance/2026-08-27-run-all-performance-study.md`
- Modify: `docs/superpowers/plans/2026-08-27-etf-afml-dataset.md` and `2026-09-03-etf-afml-dataset-foundation.md`

**Interfaces:** Consumes the target paths from Tasks 1--2. Produces no stale `etf_tricks` references to downstream AFML prompts.

- [ ] **Step 1: Replace only stale downstream paths**

Run: `rg -n "docs/etf_tricks/prompts|etf_tricks/prompts" docs AGENTS.md --glob "*.md"`

Keep upstream `00`, `01`, `02`, and `etfs/` references. Convert only former downstream `03`--`09` paths.

- [ ] **Step 2: Run fail-closed checks**

Run: `rg -n "docs/etf_tricks/prompts/0[3-9]-" docs AGENTS.md --glob "*.md"; git diff --check`

Expected: no stale-path matches and no whitespace errors.

- [ ] **Step 3: Stage documentation only, commit, and push**

Run: `git status --short; git diff --cached --check; git add docs/etf_tricks/prompts docs/etf_tricks/performance docs/superpowers/plans; git commit -m "docs: link ETF prompts to AFML authority"; git push origin main`

Expected: staged files are only the documentation paths named above.
