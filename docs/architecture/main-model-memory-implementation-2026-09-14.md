# Main-model memory: implementation and development findings

Status: automatic saving is enabled by default and can be switched off in Memory
settings. The selected main model performs both passes; only local Ollama is
supported for automatic inference. Older benchmark findings below are historical,
not a model requirement or a claim of semantic quality.

A [disposable automatic-worker trial](main-model-memory-disposable-trial-2026-09-14.md)
now records six real-model development cases and a small real-embedding recall check.

Follow-up: [prompt/contract development results](main-model-memory-prompt-development-2026-09-14.md)
record additional temperature-zero tests, including unsafe approvals and the
current conservative but low-recall behavior. They do not qualify Gemma for saving.

## Implemented behavior

The context owns one durable memory worker and one inference coordinator shared
with foreground runtime and simple-LLM calls. Permitted conversation turns stay
in the existing SQLite database. Five turns or ten minutes makes a batch eligible;
memory admission requires 60 seconds without foreground model activity, sufficient
headroom and the automatic-update toggle. There is no per-model qualification
report or tiny-model roster gate. These scheduling values are initial constants;
not all have settings controls yet.

Each job pins the selected primary model and provider configuration. The current
adapter supports local loopback Ollama only, with no pull, cloud or model fallback.
It uses the provider's residency policy and normal device selection. Structured
proposals carry supplied identities, exact source spans, actor, scope and note
revision. A separate context of the same model checks the proposal; one repair is
allowed and checked again. Self-review is a consistency check, not independent
truth verification. Exact traceability alone does not prove semantic support.

Inference runs outside the Brain lock. Foreground demand cancels memory and waits
for its transport owner to release the inference slot; a timeout cannot force an
overlapping call. Pause and shutdown also cancel through the saving stage, between
operations. Shutdown does not join inference. Leased work recovers after expiry.
Owned-server Ollama checks measured 0.031–0.047 seconds from foreground demand
to transport lease release and 15.234–23.125 seconds for the foreground reply
with installed `gemma4:e2b`. The final run exited successfully and verified the
test port was free after cleanup. This confirms those handoffs, not a universal
backend-release guarantee or a wall-clock latency bound.

Approved apply jobs journal exact before/after bytes before replacement. Managed
sections preserve user-authored text, old correction history and unknown metadata.
Changed revisions defer. Curated claims remain candidates; repetition does not
verify them. Reconciliation rebuilds LanceDB and managed provenance/conflict graph
edges. A crash after writing can replay the same delta without creating a second
note. Pre/post byte checks cannot provide filesystem compare-and-swap against an
external editor; an external write racing replacement remains a documented limit.

The WebUI has a non-modal popup, persistent stage, Pause/Resume, and readable
activity with proposed/saved text, prior content and source quotations. Memory busy
state is separate from chat input/generation state. Saved counts are emitted only
after application and reconciliation complete. Settings exposes a separate
automatic-saving toggle under the existing Memory section. Switching it off
cancels an active job before another journaled write.

The production context no longer runs legacy heuristic extraction on these same
conversation batches. Existing stored memories, explicit memory operations and
external-evidence learning remain available. With the automatic toggle off,
permitted conversation turns remain pending for later processing. Unsupported or
unavailable selected providers never fall back to another model. Evaluation uses
temporary stores or proposal-only scripts, never the user's real vault.

## Gemma development results

Local artifact: `gemma4:e2b`, Ollama 0.33.3, digest
`7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e`.
Installed metadata reports GGUF Q4_K_M, 5.1B parameters and 7,162,405,886 artifact
bytes. These are artifact metadata, not runtime RAM measurements.

The production adapter was exercised on an owned isolated Ollama server using
the installed artifact, 16,384 context, 1,800 output tokens, direct output,
normal device selection and a 180-second per-call timeout. No download or real
vault write occurred. Development prompts were adjusted after an initial case
invented an unavailable target; supplied-identity schema constraints were added.

The subsequent 30-case run completed:

| Outcome | Cases |
| --- | ---: |
| Approved proposal | 3 |
| Deferred | 13 |
| Abstained | 7 |
| Rejected | 7 |

There were 71 completed model calls; 17 cases reached a review pass. Median
case duration was 11.664 seconds; maximum was 29.265 seconds. These durations
include each case's calls and repairs, but are not a latency statistic for 30
successful two-pass constructions. Approved examples concerned an uncertain app
crash, a reading goal and historical residence. Approvals are model outcomes,
not human-certified correct memories or a supported-claim precision score.

Raw local records (ignored evaluation cache):

- `model_cache/qualification/gemma-production-smoke-2026-09-14.jsonl`
- `model_cache/qualification/gemma-production-bounded-2026-09-14.jsonl`

Reproducer: `scripts/memory_qualification/production_smoke.py --model gemma4:e2b
--ollama-exe <installed-ollama.exe> --output <new-jsonl-path> --limit 30` using
the project dependencies. The script refuses an existing output file and does
not construct Brain. Future runs embed prompt/schema/adapter source snapshots;
the September 14 records above predate that addition and do not contain a full
source snapshot. Subsequent implementation changes invalidate release qualification.

Existing-note fixtures in this screen lack managed sections, so they cannot
exercise the full production correction path. No held-out tuning or scoring was
performed. This run did not measure peak RAM, CPU contention, real Ollama
cancellation or foreground slowdown. The earlier CPU-only 3.22 GB working-set
measurement must not be assigned to this different runtime configuration.

## Remaining release work

### Architecture validation update (September 14)

Controlled regression checks now cover an in-flight primary-model/configuration
change: proposal, review and repair keep the original snapshot, while the next
job uses the new selection. An unsupported cloud selection defers locally without
constructing a fallback client. With periodic updates off, the worker does not
claim pending turns and reports that they remain pending. These checks establish
selection and scheduling behavior, not the semantic reliability of any model.

The per-model, implementation-digest-bound qualification record was removed from
production admission. Automatic application is now governed by the user's toggle,
local Ollama support, idle/headroom admission, selected-model proposal and review,
deterministic validation, revision checks and journaled application. Semantic
quality remains model-dependent; same-model review is not independent truth proof.
Permitted turns remain durable while disabled or deferred, and the retired
heuristic extractor does not silently consume them. The UI distinguishes updates
being off from a selected configuration being unavailable.

Beta is explicitly single-client: the desktop shell's existing single-instance
plugin prevents a second desktop backend, while simultaneous browser/API clients
are unsupported. Real-embedding recall now has a two-question assistant-level
check; its finding raised the default memory distance threshold from `0.5` to
`0.8`. The WebUI lifespan test now verifies context cleanup, and `cargo check`
passes for the desktop shell whose exit handler stops its owned backend. An actual
packaged-app OS quit during active generation remains unexercised. An application/
reconstruction run covering external edit races also needs end-to-end evidence.
The observed 1,465-second ReadTimeout remains
unexplained; the configured timeout is not a demonstrated wall-clock bound.

Final verification on September 14: 275 selected backend tests passed (memory,
provider/runtime, Brain, Markdown, graph, reflection, configuration, shutdown and
external learning), with 126 LanceDB deprecation warnings. The complete frontend
suite passed with 124 tests across 18 files, TypeScript plus the production Vite
build passed, and the Tauri desktop crate passed `cargo check`. A temporary
Playwright component fixture exercised typing/sending, pause and activity evidence
without a modal; this was not a native desktop or complete backend end-to-end test.
The final tracked diff check reported no whitespace errors, only Git line-ending
conversion warnings. An integration-test assertion initially used `content`
instead of the store's `text` field; it was corrected and the suites rerun.

The earlier Gemma development run produced too few useful accepted memories to
claim strong semantic recall. Automatic saving is now an explicit user-controlled
feature rather than a model-roster qualification decision. Improve construction
on development data, including real managed-note correction/link fixtures, and
evaluate separate human-reviewed episodes. Do not count conservative deferral as
successful memory recall.

Measure cold/warm incremental memory and foreground performance across broader
workloads. The focused real-embedding assistant recall check is complete. A
packaged native desktop quit during active inference still needs manual acceptance;
multiple clients are outside the single-client Beta scope. Controlled configuration
changes, application and Ollama handoff have narrower checks above; browser
component checks do not establish native desktop behavior.

The earlier release-qualification policy described here has been retired. The
automatic-update default is now true, while existing explicit off settings remain
off. There is no automatic larger-model fallback and no separate tiny model.

The retired tiny-model experiments remain historical evidence. Their tooling and
cached artifacts are retained under the existing retirement inventory; this change
does not delete normal Ollama models or unrelated work.
