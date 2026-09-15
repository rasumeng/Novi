# Tiny memory-model retirement preparation

Prepared September 13 and closed September 14, 2026. No installed models were
deleted. The active design is
[periodic updates with the selected main model](main-model-memory-direction.md).
The dedicated-model artifacts below are an archived experiment surface and have
no production runtime role.

## Inventory and disposition

| Work | Disposition |
|---|---|
| `docs/architecture/memory-model-shortlist.md` | Historical research; mark retired, retain citations and artifact context |
| `docs/superpowers/plans/2026-09-11-intelligent-memory-curator.md` | Historical design; replace its role as the active assignment with the new direction |
| `scripts/memory_qualification/artifacts.json` | Retain pinned artifact identities with experiment records |
| `scripts/memory_qualification/screen-approved.ps1`, `prepare.py` | Archived dedicated-roster/import tooling; no production role |
| `scripts/memory_qualification/run.py`, `contracts.py`, metrics, reports, GGUF checks | Archived experiment tooling and reusable evidence checks; no production role |
| `tests/fixtures/memory_curation/`, `tests/test_memory_qualification.py` | Keep reusable development cases and deterministic checks; adjust imports if tooling later moves |
| `model_cache/qualification/*.jsonl`, logs and reviews | Preserve raw evidence and manifests; archive before any cache cleanup |
| `model_cache/qualification/models/` | Five downloaded candidate files; removal target, 1,930,215,296 logical bytes |
| `model_cache/qualification/ollama-store/` | Isolated evaluation store; removal target, 1,930,218,138 logical bytes across 15 files |
| Normal `C:/Users/asume/.ollama/models/` | Preserve: includes preexisting MiniCPM and user-selected Gemma; not an evaluation-owned deletion target |

The two removal-target directories total 3,860,433,434 logical bytes. This is not
guaranteed reclaimed disk space: OneDrive/filesystem allocation can differ.

No production references to `memory_qualification`, `novi-qual` or a dedicated
`memory.curator` setting were found in the current `novi/` tree. The large set of
existing Brain, vault, retrieval, runtime and UI edits is not a tiny-model patch
and must not be reverted as cleanup. No production rollback is justified by this
inventory. No inference was launched during this preparation.

## Optional cache cleanup

1. Preserve raw JSONL manifests/results, pinned artifact inventory and readable
   findings. Preserve prompt variants separately and distinguish resource stops,
   validation deferrals, verifier approval and actual semantic quality.
2. Keep old dedicated-model instructions marked historical and maintain one active
   design.
3. The archived tooling may remain with its raw evidence. If it is removed later,
   retain shared validators, fixtures and required test helpers.
4. Before deleting either cache directory, resolve its absolute path and verify
   it remains under this repository's `model_cache/qualification`. Confirm no
   evaluation process uses the isolated store. Delete only the two named targets;
   preserve raw logs and the normal Ollama installation. Do not blanket-delete
   `model_cache`, `.ollama`, or unrelated untracked files.
5. Run the retained harness tests and inspect the final diff. Record actual
   removed paths and remaining artifacts. Preparation does not imply deletion.

## Evidence limits

No tiny-model winner was qualified. No human-reviewed held-out accuracy,
foreground chat slowdown or full main-model integration was established. Current
test fixtures are synthetic development material and marked not human reviewed.
The separate main-model test also failed to demonstrate safe scope handling.
Retiring the second-model architecture does not turn any of these failures into
a passing result.
