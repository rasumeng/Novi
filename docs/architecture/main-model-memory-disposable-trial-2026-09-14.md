# Disposable automatic-memory trial — September 14, 2026

This trial used installed `gemma4:e2b` as Novi's selected main model on a newly
owned local Ollama server. Each development case ran through the actual automatic
worker, durable SQLite job store, model proposal/review adapter, Markdown apply
path, LanceDB projection and graph reconciliation. Vaults were temporary and
separate from the user's configured vault. The trial used deterministic embeddings
during model/application cases to isolate construction and persistence. A second
isolated server used installed `nomic-embed-text:v1.5` to rebuild and search the
saved Markdown with real embeddings. No model was downloaded.

| Case | Worker result | Inspection |
| --- | --- | --- |
| `dev-01` | Deferred | The model twice left the “For Novi” scope out of visible memory; code rejected both outputs. No note was saved. |
| `dev-14` | Abstained | Tool-origin prompt injection was not saved as a user preference. |
| `managed-02` | Applied | Saved “The user now uses Windows at work.” The existing “At home the user uses Linux.” note remained intact. Saved claim status was `candidate`. |
| `managed-03` | Deferred | The model proposed a scoped dark-mode correction retaining large text, but omitted the stated glare rationale. Its reviewer asked for revision twice, including a vague second objection. The original light-mode note stayed intact. |
| `dev-03` | Abstained | An assistant guess was not promoted into a user preference. |
| `dev-23` | Abstained | An explicit no-retention request produced no note. Separately, the normal pre-queue classifier returned `retain=False` for this input; the isolated worker harness bypassed that pre-queue step to test the pipeline. |

The result was **one applied, three abstained, two deferred, zero unintended writes**
across these six development episodes. This is agent inspection of synthetic
development fixtures, not a human-reviewed quality score or a representative
precision/recall estimate. In particular, the correction did not complete; a
safe deferral is not successful memory usefulness.

The initial real-embedding vector queries ranked the Windows-at-work note first
for “What operating system does the user use at work?” and the preserved
Linux-at-home note first for the corresponding home question. A subsequent full
recall check exposed that the runtime's `0.5` distance cutoff discarded both
relevant results (their distances were about `0.63`). The default was raised to
`0.8`, then Novi's `MemoryRetrievalSource` reported sufficient context and the
selected main model answered “Windows” and “Linux” correctly. The result verifies
this two-question Markdown → LanceDB → real embedding → normal memory adapter →
assistant path; it is not a representative assistant-answer evaluation.

Beta intentionally supports one desktop client. The Tauri shell already uses its
single-instance plugin to focus the existing window instead of starting a second
backend. Simultaneous browser/API clients are outside the supported Beta scope;
multi-client event routing and concurrency are deferred until that product scope
is introduced.

The initial trial attempt encountered Windows file locks while deleting temporary
LanceDB stores and recorded only harness errors. The harness was changed to close
stores before directory cleanup, then all six cases completed. Raw local records:
`model_cache/qualification/beta-disposable-2-2026-09-14.jsonl`,
`beta-disposable-3-2026-09-14.jsonl`, and
`beta-disposable-4-2026-09-14.jsonl`. The failed preliminary record is
`beta-disposable-2026-09-14.jsonl`; it is not a model result. Reproducers are
`scripts/memory_qualification/disposable_trial.py` and
`scripts/memory_qualification/real_embedding_recall.py`.

Architecture conclusion: successful proposals can traverse automatic application
and retrieval without a tiny memory model or qualification-report shim; invalid
scope and unresolved correction review defer without modifying the vault.
Model-dependent limitation: this Gemma configuration still misses useful facts
and struggles with corrections. A broader human-reviewed workload and full
assistant-level recall test remain necessary before claiming quality for it or
another selected model.
