# Unified brain lifecycle

Implemented September 10, 2026. This describes the current integration, rather
than the older redesign proposals.

## Ownership

- The configured Markdown vault owns note identity, content, and explicit
  metadata. Notes remain ordinary Obsidian-compatible Markdown. Imports receive
  an ID at their original path. Edits/renames preserve that ID.
- LanceDB holds current knowledge records and document chunks as derived
  projections. Reconciliation restores missing records from Markdown. Embedding
  model/dimension changes re-embed typed records before replacing the table,
  retaining a backup. A provider failure leaves the active table intact.
- `brain/vault.sqlite` records projection progress and earlier note revisions.
  Deleted notes leave historical knowledge marked superseded and leave ordinary
  search. Revisions are distinct from note identity.
- Conversation SQLite owns raw turns and extraction watermarks. Relationships
  SQLite owns typed edges. Brain serializes lifecycle operations within its
  process; these files are not a multi-process write coordination protocol.

## Learning

`observe` persists raw experience before attempting extraction. Five-turn batches
are processed immediately; `maintain` can process short batches, including after
a restart. Each pass visits a bounded number of pending conversations. The
watermark advances only after knowledge, provenance, Markdown, and attached
search projections succeed. Failed work remains available for retry.

Routine extraction is deterministic; it does not issue separate model calls for
a title and summary. User, assistant, and tool claims have separate attribution.
Conservative claim equivalence retains negation, values, and word order. One
canonical claim retains distinct conversation sources; repeating a batch cannot
increase its independent observation count. Three additional conversation
observations can verify a personal claim. Assistant/tool observations, summaries,
and external evidence do not use that promotion rule. Explicit corrections own
supersession; sharing a preference tag is insufficient.

Extracted claims link to their batch summary through the `related` frontmatter
property using ordinary `[[note-path]]` links. These represent episode provenance,
not proof that one claim supports another. Body and property links feed the same
bounded graph used for recall. User-authored links remain available.

## Retrieval and editing

Recall runs a bounded maintenance pass, then resolves the conversation's scenario
and searches its neighborhood before global expansion. It carries confidence,
status, sources, and external evidence through adapters. Memory context uses the
shared result merger and selection budget. An already-global query is not issued
again as a redundant expansion. Weak graph connections do not suppress fallback.

Unchanged notes avoid parsing and embedding work where cached. Note edits refresh
the current record and chunks; removed paths are purged from the chunk index.
Replacement chunks are embedded before replacing old rows. Duplicate IDs,
malformed frontmatter, and read failures abort reconciliation rather than being
treated as deletion. Delayed agent writes preserve unprocessed human body edits.

`Brain.maintain()` exposes processing counts and errors. Recall reports failed
vault reconciliation rather than serving a known-stale projection; failed
extraction retains the prior usable knowledge and queues its raw turns for retry.

## Verification and limits

September 14: production conversation curation now uses a durable main-model
worker, separate from synchronous recall/maintenance. Legacy heuristic extraction
is disabled in the production context to avoid consuming the same batches.
Automatic model-written updates now default on for supported local Ollama main
models and can be switched off in Memory settings. Unsupported work remains
pending without fallback. Existing notes and explicit/external-evidence paths
remain available. See [implemented behavior and limits](main-model-memory-implementation-2026-09-14.md).

Regression coverage uses actual SQLite and LanceDB with deterministic embeddings:
edits, renames, deletion, reconstruction, embedding changes, retries, attribution,
corroboration, graph links, scope, metadata, and unchanged maintenance.

This integration adds no production dependency and does not install TencentDB's
team-memory server. It adopts a layered memory lifecycle within Novi's existing
local stack. Real-model recall quality, large-vault latency/RAM, and simultaneous
external-editor races still require workload measurements. Maintenance scans
metadata across the corpus; it is not a claim of constant-time operation. The
typed claim store retains exact search; ANN tuning should follow recall/latency
measurement. Automatic semantic contradiction resolution and cross-conversation
scenario inference remain conservative rather than being delegated to unchecked
model guesses.
