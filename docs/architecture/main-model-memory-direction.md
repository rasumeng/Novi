# Periodic memory updates with the selected main model

Decision: September 13, 2026. This is the active direction, superseding the
dedicated tiny-model plan and candidate selection exercise. Automatic saving is
now available through the Memory settings toggle for supported local Ollama main
models; see the implementation record for current limits.

Implementation assignment:
[main-model memory plan](../superpowers/plans/2026-09-13-main-model-memory.md).
It adds visible, non-blocking update status and foreground handoff. No separate
model usage quota or tiny-curator RAM cap applies; correctness bounds and
responsiveness safeguards remain.

## Fit with the proposed conversation

The proposal matches the intended division of responsibility: the user's chosen
main model interprets experiences and proposes memory changes; the embedding
model supports retrieval; Novi's code controls lifecycle and persistence. A
bounded prompt is the model-facing part, but a prompt alone does not provide
correctness, recovery or safe editing.

Use the existing Brain and storage infrastructure. Markdown remains the
user-editable source of note content and identity. SQLite retains source turns,
processing progress and audit/journal state. LanceDB and the relationship graph
are derived retrieval projections. Distinguish personal observations from
external knowledge through provenance and metadata, not a second memory system.

The conversation needs these adjustments:

- Five new turns or elapsed pending age makes work eligible; idle time and
  resources determine when it runs. Keep the existing planned ten-minute age
  and 60-second idle thresholds as initial configurable values, not measurements.
  Chat switching may queue work. Closing persists pending work and never waits
  for model loading or memory inference.
- Retrieve relevant existing notes, including competing claims, before proposing
  changes. The conversation's later diagram reverses this ordering. An extra
  model call merely to extract search topics is not required by the design.
- Propose bounded add/update/link/ignore decisions; merge only with explicit
  targets and preserved evidence/history. Prefer supersession to destructive
  deletion. Corrections must match actor, subject, project/scope and time.
- Keep coherent Markdown notes and meaningful Obsidian wiki links. The proposed
  JSON examples omit this requirement, exact evidence spans and revision checks.
  An existing-memory identifier must be a supplied stable ID, not invented text
  such as `primary_model`.
- Numeric model confidence is not calibrated truth. The model must not grant
  verified status. Code checks evidence identity and policy; semantic support
  still needs evaluation. A separate review with the same model is a consistency
  check and can repeat the original mistake.
- Do not discard every one-time event: meaningful decisions, procedures and
  useful episodic context can deserve retention. Preserve existing opt-out and
  source-retention decisions before queuing memory work.
- Pin the configured primary provider/model for each job, with no hidden fallback
  or additional model selection. Foreground generation takes priority. Reuse its
  residency where safe and never unload a model still used by chat. A local
  memory packet must not silently move to a remote provider without an explicit
  data policy.

## Intended processing sequence

September 14 implementation: the main-model worker and visible activity UI now
exist, with automatic saving controlled by a Memory settings toggle. See
[implementation and development results](main-model-memory-implementation-2026-09-14.md).

Persist permitted turns → select an eligible bounded batch during admitted idle
time → retrieve relevant current notes → main-model proposal → deterministic
validation → fresh-context consistency review → recheck revisions → journal and
apply through existing stores → acknowledge completed source segments.

Inference stays outside the Brain lock and foreground recall. Failures and
ambiguity defer without acknowledging unprocessed evidence. Application is
idempotent and preserves concurrent user edits. The current automatic-update path
permits model-generated vault writes only after validation and review.

## Why change direction

A second instruction model adds downloads, residency, scheduling and another
quality dependency. The initial tiny-model experiments did not establish a
configuration that met both the proposed resource gate and memory contract.
The user chose to reuse the selected main model periodically instead. This is
an architecture decision, not a finding that larger models are always accurate
or that every tiny model is incapable.

The three 30-case supplied-span development runs for LFM230, LFM350 and
Granite350 all deferred. Supplemental verifier challenges contained three
traceable but semantically bad proposals and one faithful proposal; these models
approved all four. Later bounded-identity smoke tests produced some accepted
outputs, but did not establish semantic quality. LFM1.2B, Qwen0.8B and MiniCPM5
resource screens stopped before completing calls under the 1 GB experimental
gate, including 4K-context retests. Those are configuration resource failures,
not semantic scores.

Gemma4:e2b subsequently demonstrated why the main-model path still needs checks:
an explicit-task diagnostic produced a memory but omitted the “For Novi” scope,
and its review approved it. That CPU-only cycle took 76.14 seconds, with peak
working set about 3.22 GB and private commit about 3.93 GB. It is a development
diagnostic, not a completed 30-case or held-out qualification.

See [initial preflight](memory-qualification-2026-09-11.md),
[Gemma measurements](main-model-memory-test-2026-09-12.md), and
[retirement inventory](tiny-memory-model-retirement.md). Publisher claims in the
old shortlist remain historical context, separate from these local findings.
