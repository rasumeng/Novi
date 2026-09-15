# Unified Brain Implementation Plan

**Goal:** Make the editable Markdown vault, searchable knowledge, relationships,
and conversation learning one dependable local system.

**Architecture:** Brain owns lifecycle coordination. Markdown owns curated note
content and stable identities; LanceDB is a rebuildable projection. SQLite owns
raw experience and durable processing progress. Existing adapters and bounded
retrieval stay in place; no additional production dependency or Tencent service.

**Scope:** Implement the architecture agreed in this conversation. Preserve
unrelated workspace edits and existing public APIs where practical. Execute
inline using the superpowers executing-plans workflow and regression tests.

## Deliverables

- [x] Vault reconciliation: stable IDs across edits/renames, import at original
  path, metadata preservation, deleted-note exclusion, current wiki links,
  idempotent index refresh. Files: brain/storage/markdown_store.py,
  brain/brain.py, memory/knowledge_index.py. Tests: test_unified_brain.py.
- [x] Evidence: conservative equivalence, attributed extraction, independent
  observation sources retained through deduplication and promotion. Files:
  brain/reasoning/{extraction,verification,reflection}.py,
  brain/layers/knowledge.py, brain/storage/vector_store.py.
- [x] Durable learning: SQLite extraction watermark, bounded retryable batches,
  replay-safe identities, partial-batch processing on maintenance; serialize
  Brain maintenance. Files: brain/storage/conversation_store.py, brain/brain.py.
- [x] Runtime connection: maintenance before recall, conversation scope passed
  to recall, full trust metadata preserved, conservative graph sufficiency.
  Files: runtime/sources/memory.py, runtime/retrieval.py,
  brain/reasoning/resolver.py, services/context.py.
- [x] Rebuildability: embedding-space change preserves/re-embeds knowledge;
  paginated enumeration removes fixed-corpus correctness caps. Files:
  brain/storage/vector_store.py, brain/layers/knowledge.py.
- [x] Verify: targeted regression tests with actual SQLite/LanceDB and fake
  embeddings, existing brain/retrieval suites, review final diff, document
  remaining real-model and scale measurements without claiming benchmarks.

## Behavioral acceptance cases

1. Edit and rename an imported note: one stable identity, current body searchable,
   custom metadata preserved, old chunks absent; delete: absent from ordinary recall.
2. Repeated independent observations promote one claim; replay does not add
   evidence; opposing statements never merge merely for shared words.
3. Restart before five turns or fail extraction: subsequent maintenance processes
   durable turns; only successful work advances the watermark.
4. A conversation's scenario reaches recall; confidence/status/sources survive
   adapters; unrelated graph neighbors do not suppress evidence fallback.
5. Change embedding dimension: all knowledge remains available with new vectors.
6. Repeated maintenance with no changes performs no extraction or embedding work.

## Verification record

The combined 27-suite brain, retrieval, storage, composition, and external-evidence
run passed 428 tests. A final focused run passed 91 tests, covering the subsequent
unavailable-vault safeguard. `git diff --check` passed. Tests use actual
SQLite/LanceDB and fake embeddings; real-model and
large-vault performance is not asserted. See
`docs/architecture/unified-brain-lifecycle.md` for ownership, runtime behavior,
and measurement limits. Existing unrelated edits were preserved.
