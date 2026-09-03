# AFML Prompt Taxonomy Design

## Purpose

Separate the ETF Tricks construction subsystem from the downstream AFML research
system.  ETF Tricks produces immutable daily NAV, ETF amount, composition, and
execution-ready constituent information.  AFML consumes those artifacts; it
does not own their construction rules.

This design also adds one persistent AFML Goal Prompt, shorter than 4,000
Chinese characters, that governs bounded progress through the existing
directional, meta-labeling, allocation/execution, and DSR acceptance prompts.

## Target taxonomy

```text
docs/
  etf_tricks/
    prompts/
      00-goal-prompt.md
      01-master-prompt.md
      02-performance-optimization-prompt.md
      README.md
      etfs/
  afml/
    prompts/
      README.md
      00-goal-prompt.md
      01-dataset-goal-prompt.md
      02-dataset-master-prompt.md
      03-tiered-ml-strategy-master-prompt.md
      04-tier1-directional-label-and-model-prompt.md
      05-tier2-meta-labeling-prompt.md
      06-tier3-allocation-and-paper-execution-prompt.md
      07-strategy-governance-dsr-acceptance-prompt.md
```

`docs/etf_tricks/prompts` remains authoritative only for upstream ETF
construction. `docs/afml/prompts` becomes authoritative for Dollar bars, FFD,
features, labeling, and all later strategy research.  The numerical prefixes
restart in the AFML namespace so their order is local and unambiguous.

## Exact file mapping

| Current path | Target path | Change |
|---|---|---|
| former `03-afml-dataset-goal-prompt.md` | `docs/afml/prompts/01-dataset-goal-prompt.md` | Move and update internal references. |
| former `04-afml-dataset-master-prompt.md` | `docs/afml/prompts/02-dataset-master-prompt.md` | Move and update internal references. |
| former `05-tiered-ml-strategy-master-prompt.md` | `docs/afml/prompts/03-tiered-ml-strategy-master-prompt.md` | Move and update child references. |
| former `06-tier1-directional-label-and-model-prompt.md` | `docs/afml/prompts/04-tier1-directional-label-and-model-prompt.md` | Move and update parent reference. |
| former `07-tier2-meta-labeling-prompt.md` | `docs/afml/prompts/05-tier2-meta-labeling-prompt.md` | Move and update parent reference. |
| former `08-tier3-allocation-and-paper-execution-prompt.md` | `docs/afml/prompts/06-tier3-allocation-and-paper-execution-prompt.md` | Move and update parent reference. |
| former `09-strategy-governance-dsr-acceptance-prompt.md` | `docs/afml/prompts/07-strategy-governance-dsr-acceptance-prompt.md` | Move and update parent reference. |
| none | `docs/afml/prompts/00-goal-prompt.md` | Create the persistent AFML Goal Prompt. |

The upstream ETF Goal, master prompt, optimization prompt, ETF-specific prompt
directory, and ETF performance studies stay in place. Existing implementation
plans and all prompt links will be updated to the new AFML paths in the same
atomic change. There will be no duplicate authority or compatibility copies.

## AFML Goal contract

The new Goal Prompt will require an agent to read this AFML prompt package at
each resumption; verify source artifacts, manifests, hashes, and readiness;
work on exactly one active child stage; and persist an append-only status and
decision record. It fixes the order:

```text
AFML dataset -> Tier 1 -> Tier 2 -> Tier 3 paper execution -> governance/DSR
```

It prohibits bypassing failed PIT, label, OOF, execution, or sealed-test gates;
forbids live orders; permits only bounded 2024--2026 tests before a full-history
acceptance run; and requires results to distinguish verified evidence from
assumptions and limitations. It delegates algorithmic detail exclusively to
the numbered AFML prompts.

## Migration safety and verification

- Move tracked files with `git mv` to preserve history.
- Update every repository Markdown reference found by `rg`, including plans and
  README authority lists.
- Assert no active ETF namespace reference to a former downstream file remains.
- Assert the eight target files exist, each `Parent` reference resolves, and
  every README authority entry resolves to a file.
- Run `git diff --check`; review staged paths to ensure no source data,
  notebooks, credentials, or unrelated user changes are included.
- This is documentation-only: it cannot claim that Tier 1--3 implementation,
  paper trading, or DSR acceptance has occurred.
