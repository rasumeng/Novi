# Memory model qualification: initial preflight and harness

**Historical preflight.** Later downloads and tests supersede the pending-download
statements below. The dedicated-model exercise is retired. See
[the decision and subsequent findings](main-model-memory-direction.md) and
[cleanup preparation](tiny-memory-model-retirement.md). Preserve this report as
the initial measurement record, not the current implementation assignment.

September 11, 2026. **No deployment recommendation. No vault writes enabled.**
The six-model comparison is pending approved downloads. This report records an
initial resource screen, not completed model qualification.

## Local findings

- CPU: AMD Ryzen 5 7535HS, 6 physical cores / 12 logical processors.
- Windows reports 33,509,720,064 usable physical RAM bytes (31.21 GiB).
  Available RAM was approximately 12–13 GB during this session; it varies.
- Graphics inventory: AMD Radeon integrated graphics and NVIDIA GeForce RTX 2050.
  No GPU inference was requested; VRAM was not established by this preflight.
- Ollama 0.33.3 is installed and running. No llama.cpp executable was found on
  PATH. Other non-PATH installations were not exhaustively searched.
- The repository venv cannot launch because its referenced Python312 executable
  is missing. Bundled Python 3.12.14 with Pydantic 2.13.5 runs the harness and tests.
- Contrary to the earlier shortlist assumption, `openbmb/minicpm5:latest` already
  exists locally. Its 688,065,920-byte weight file was read and SHA-256 verified
  against the official Q4_K_M artifact. No weights were downloaded this session.
- No root AGENTS.md exists on disk; the user-supplied AGENTS instructions apply.
  Existing production edits were left untouched.

Novi's `ResolvedModel`/`ModelRuntime` boundary preserves explicit model selection.
`OllamaProvider` currently does not expose the complete schema/context/thread/
residency contract and has exception paths that drop generation controls. The
standalone harness uses the same installed Ollama backend through its local HTTP
API, with no LangChain or chat-model routing. Production curator integration must
extend the existing provider boundary and test strict control preservation.

## Measured initial resource screen

Both attempts used the exact installed MiniCPM5 Q4_K_M artifact, direct mode,
CPU-only requested, two threads requested, 8,192 context, 1,100 output-token cap,
temperature zero, seed 17, 4 GiB system reserve and a decimal 1 GB incremental
memory gate. CPU-only and thread settings were requested through the API; actual
thread scheduling and GPU residency were not independently profiled.

| Attempt | Incremental peak working set | Incremental peak private commit | Time through stop/unload | Outcome |
|---|---:|---:|---:|---|
| Initial shared-server diagnostic | 1,021,284,352 B | 1,120,333,824 B | 22.256 s | Resource gate failed; unload confirmed |
| Owned-server cancellation check | 974,512,128 B | 1,069,289,472 B | 2.101 s | Resource gate failed; owned tree terminated |

Each attempted only development case 1 of 30 and completed **zero** calls. These
times are aborted-job wall times, **not** cold-load completion times or complete
two-pass latency. No retries, accuracy, claim recall, correction accuracy or
relation precision can be scored. The remaining 29 cases were not attempted.
Neither attempt establishes MiniCPM5's performance with smaller context or other
quantization; only this configuration failed the proposed lightweight gate.

The first diagnostic revealed that a shared Ollama request could continue after
the monitor detected pressure. The current harness starts a separate owned
server on loopback port 11439 and terminates only that tree on pressure. Its
2.101-second test verifies this revised path. Normal Ollama was left running.
The first run predates the owned-server change; manifests retain code hashes,
but its earlier source is represented by this session's edit history.

Telemetry is sampled every 100 ms and can miss transient peaks. Process-tree
working sets can count shared pages twice; private commit is recorded separately.
No file-backed resident-page accounting, actual Novi foreground latency test,
OS-cache-cold test, or low-memory-device qualification was completed. Raw
CPU-time and synthetic scheduling-delay samples are available, but a synthetic
probe is not the plan's <=5% foreground regression gate.

Raw local logs (ignored by Git):

- `model_cache/qualification/minicpm5-dev30-direct-01.jsonl`
- `model_cache/qualification/minicpm5-dev30-direct-owned-02.jsonl`

## Exact files proposed for approval

Destination: `C:/Users/asume/OneDrive/Desktop/Projects/Novi/model_cache/qualification/models/`.
This directory is ignored by Git but is inside the user's OneDrive project path.
No download has been initiated. Exact revisions and SHA-256 values are in
`scripts/memory_qualification/artifacts.json`. URLs must use those revisions
rather than mutable `main`. Sizes below are API artifact metadata, not RAM.

| Repository | Selected file | Download bytes |
|---|---|---:|
| [LiquidAI/LFM2.5-230M-GGUF](https://huggingface.co/LiquidAI/LFM2.5-230M-GGUF/tree/cdf97bd8205908758f44aec508d68ac1aef98f5c) | LFM2.5-230M-Q4_K_M.gguf | 153,406,304 |
| [LiquidAI/LFM2.5-350M-GGUF](https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/tree/9969000761ce34de907bf20017cbfc3d52d6eaf9) | LFM2.5-350M-Q4_K_M.gguf | 229,312,224 |
| [LiquidAI/LFM2.5-1.2B-Instruct-GGUF](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/tree/6767265158422fb8a19c62ceb45f16f05363615b) | LFM2.5-1.2B-Instruct-Q4_K_M.gguf | 730,895,168 |
| [ibm-granite/granite-4.0-350m-GGUF](https://huggingface.co/ibm-granite/granite-4.0-350m-GGUF/tree/b8208a86a58427e1739265318028eb5895b74bf2) | granite-4.0-350m-Q4_K_M.gguf | 236,985,760 |
| [bartowski/Qwen_Qwen3.5-0.8B-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3.5-0.8B-GGUF/tree/f36b1ea49a332ede8fe5f389bbf5b3575ef71f48) | Qwen_Qwen3.5-0.8B-Q4_K_M.gguf | 579,615,840 |
| **Total new download** | | **1,930,215,296 B (1.930 GB / 1.798 GiB)** |

Reuse [official MiniCPM5 Q4_K_M](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF/tree/3d55fac80935ae6456986ad2384b5cbcc4d6c948)
at `C:/Users/asume/.ollama/models/blobs/sha256-81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa`.
All six weights together total 2,618,281,216 B. Ollama import may duplicate the
five downloaded artifacts in its blob store: allow another 1.93 GB of storage.
No runtime binary is in this approval request. A Windows CPU llama.cpp b10809 ZIP
was located (18,407,457 bytes) but is not needed for the current Ollama harness;
switching runtime would require explicit selection and fresh measurements.

## Publisher support versus local compatibility

Liquid, OpenBMB and IBM publish GGUF distributions; their cards describe llama.cpp
usage. Qwen's selected artifact is a third-party quantization, not an official
Qwen GGUF release. Metadata presence and upstream usage examples do not prove
correct templates or schema/thinking behavior in Ollama 0.33.3.
The installed MiniCPM `/api/show` exposes thinking and a template with an
`enable_thinking` branch, but the aborted run did not establish successful direct
JSON or bounded-thinking output. All other local compatibility checks await files.
[Ollama's API](https://docs.ollama.com/api/chat) documents schema format, thinking,
residency and response timings; these are supported controls to test, not assumed
behavior for every candidate.

## Harness verification and remaining work

Eleven deterministic unittest tests pass: Unicode evidence, forged sources/actors,
unknown targets, forbidden fields, operation limits, bounded repair/fresh verifier,
rejection handling, abstention, repeated revision, duplicate operations, and
dataset identities. Python compileall also passes. The real owned-server run validates
memory admission/monitoring/pressure termination and records a resource failure.
Production suites were not run because no production files changed.

The 30 synthetic development cases have expected/forbidden rubrics, marked
`human_reviewed: false`. Human review is required before using them as ground
truth. Labels are excluded from inference payloads. The runner rejects held-out
inputs. No held-out score was seen or used for tuning.

Still required for a trustworthy recommendation: download/import and template
smoke tests; completed two-pass screens for remaining configurations; human
grading; current heuristic baseline; actual foreground chat contention; strict
token admission; separate file-backed accounting; 50 reviewed development cases;
sealed 150-case held-out qualification with >=50 link and >=30 correction cases;
bounded-thinking finalist comparisons; and downstream retrieval measurements.
Do not select a model or implement automatic vault application from this screen.
