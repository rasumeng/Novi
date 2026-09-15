# Main-model memory development test

The user changed the memory design to use Novi's selected main model and chose
installed Ollama `gemma4:e2b` for this test. There is no dedicated-model winner.
The implementation plan's September 12 amendment is authoritative. Production
selected-model routing and automatic application have not been implemented by
this experiment; the harness cannot write to the vault.

## Local artifact and conditions

- Ollama 0.33.3; installed tag `gemma4:e2b`, Q4_K_M.
- Weight file: `C:/Users/asume/.ollama/models/blobs/sha256-4e30e2665218745ef463f722c0bf86be0cab6ee676320f1cfadf91e989107448`.
- Verified SHA-256: `4e30e2665218745ef463f722c0bf86be0cab6ee676320f1cfadf91e989107448`.
- Weight bytes: 7,162,394,016. This is not measured resident memory.
- AMD Ryzen 5 7535HS; 33,509,720,064 bytes usable physical memory.
- Owned local server on port 11439; normal Ollama was empty before testing.
  CPU-only, two requested threads, 4,096 context, 1,100 maximum output tokens,
  temperature zero, seed 17, thinking disabled. No downloads or settings changes.
- Construction and fresh-context consistency review use the same artifact.
  Host-provided evidence spans and packet-bounded identity schemas are enabled.
- First run: 120-second cycle deadline, 8 GB whole-process cap, 2.5 GiB available
  reserve. These are exploratory main-model settings, not the dedicated 1 GB gate.

## First diagnostic

Raw results: `model_cache/qualification/gemma4-e2b-main-direct-01.jsonl`.
The manifest retains runtime/template, source snapshots, hashes and settings;
case rows retain raw responses and process samples. Generated results stay out
of version control.

| Case | Observed result | Complete cycle including unload | Cold load |
|---|---|---:|---:|
| dev-01, scoped preference | Abstained despite an explicit preference; reviewer approved | 59.76 s | 9.06 s |
| dev-02, filler | Abstained; reviewer approved | 56.80 s | 8.13 s |
| dev-03, actor attribution | Abstained claiming no evidence; reviewer rejected | 53.55 s | 7.68 s |
| dev-04 | Operator stop during inference; no completed call | 24.84 s | Unavailable |

Across completed cycles, peak sampled working set was 3,239,055,360 bytes and
private committed memory was 3,946,700,800 bytes for the entire owned process
tree. Minimum available system memory was 7,681,933,312 bytes. All four case rows
reported successful unload/owned-process cleanup. No repair occurred in the
three completed cycles. The fourth was interrupted to investigate repeated
task/evidence interpretation problems, not classified as a model quality error.

The first run was paused before completing the 30-case screen. It produced zero
accepted memory operations. An `accepted_shadow` status can mean approved
abstention; the report now breaks out proposal outcomes explicitly. These are
inspection findings, not human-reviewed precision/recall scores or proof that
Gemma cannot handle memory. Prompt/runtime handoff remains under investigation.

## Follow-up and remaining gates

A separate development diagnostic uses `--explicit-task`: repeat the assignment
in the user message outside the untrusted JSON. It provides no expected answer
and preserves all evidence and validation. Run name:
`gemma4-e2b-main-explicit-02.jsonl`. Based on the first measured footprint, this
uses a 5 GB cap and restores a 4 GiB reserve. Results must not be pooled with the
first prompt variant.

The follow-up completed both passes without repair in 76.14 seconds, with an
8.05-second cold load. Peak working set was 3,222,749,184 bytes and private commit
3,929,198,592 bytes; unload succeeded. It proposed a memory this time, but omitted
the original **For Novi** scope: “User prefers local models because they must
work offline. They still use hosted models at work.” The structured scope was
only `preference`. Its verifier approved the broadened claim. The exact source
quote remained valid, illustrating why deterministic traceability and self-review
do not establish semantic fidelity. This diagnostic therefore does not qualify
the current prompt/model configuration for automatic memory application.

Next development work: make project/subject scope an explicit part of the
bounded operation contract and test scope preservation through the primary-model
adapter, then freeze a development configuration for the full 30-case screen.
Do not infer a universal Gemma limitation or silently replace the selected model.

No held-out data has been consulted. The 30-case screen, separate human-reviewed
held-out qualification, actual foreground responsiveness, warm main-model
incremental overhead, and production provider/lifecycle integration remain
unverified. Process-cold loading includes warm filesystem caches from artifact
hashing. CPU thread requests are not a hard CPU quota. Sampled memory is not a
guarantee of the instantaneous peak.

Keep the main-model architecture, but do not approve automatic writes on these
results. Preserve deterministic evidence/revision checks and defer ambiguity.
The model under test is configurable; Gemma is not a production hardcoded default.
