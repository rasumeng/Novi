# Memory qualification screen

September 12 direction: the user selected the main chat model for memory work,
with installed `gemma4:e2b` as the first test. The dedicated roster is retired;
existing runs remain historical diagnostics. The runner still tests a standalone
shadow contract, not Novi's selected-model integration or permission to write.
See the authoritative amendment in the curator implementation plan.

Gemma diagnostic settings: CPU-only, two threads, 4K context, direct mode,
supplied spans and bounded IDs. The first run used an 8 GB whole-process cap and
2.5 GiB reserve. Its measured peak informed a subsequent single-case diagnostic
with a 5 GB cap and 4 GiB reserve. These are explicit exploratory main-model
budgets, not results under the old 1 GB dedicated-curator gate. No weight download
or primary-model configuration change is needed.

`--explicit-task` is a separate development prompt variant: it repeats the
construction/review assignment in the user message outside the untrusted JSON.
It supplies no expected answers. Record this flag when comparing runs; never
combine prompt variants into one qualification score. Approved abstentions are
reported separately from proposals; neither establishes semantic accuracy.

Evaluation only: no Brain, storage, vault, graph or application imports. No
download endpoint, roster loop, cloud endpoint, model fallback or production
configuration changes. Uses existing Pydantic validation and Ollama's local API.
The narrower section-level contract is a development experiment, not the full
production manifest/journal contract. `accepted_shadow` is never applied memory.

## Reproduce

The repository venv launcher is broken (its base Python312 executable is missing).
Use a working Python 3.12+ with Pydantic 2, or the bundled Codex Python discovered
in this session. No new production dependency was added. Tests use unittest so
they also run without pytest in the bundled interpreter.

```powershell
$py = 'C:/Users/asume/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $py -m unittest tests.test_memory_qualification -v
& $py -m scripts.memory_qualification.build_dev
& $py -m scripts.memory_qualification.run --model openbmb/minicpm5:latest --artifact C:/Users/asume/.ollama/models/blobs/sha256-81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa --sha256 81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa --ollama-exe C:/Users/asume/AppData/Local/Programs/Ollama/ollama.exe --output model_cache/qualification/minicpm5-dev30-direct-new.jsonl
& $py -m scripts.memory_qualification.report model_cache/qualification/minicpm5-dev30-direct-new.jsonl
```

Windows metrics use native APIs: process-tree working set, private committed
bytes, cumulative CPU time, available physical RAM and scheduling-probe delay.
Shared-page double counting is possible. File-backed resident bytes and actual
foreground chat slowdown remain unmeasured; null is not zero. Sampling at 100 ms
can miss short peaks. Process-cold reload is not an OS page-cache-cold benchmark;
hashing artifacts primes file caches. Both passes and repairs share residency,
then unload. Run manifests pin weights, installed model/template, runtime,
dataset, harness, metrics, schema and prompt digests. Raw calls contain Ollama
load/prefill/generation timing and token counts when completed. Interrupted calls
have no completed token/latency response and must not be counted as two-pass runs.

The current runner starts an owned local Ollama server on 127.0.0.1:11439,
leaving the normal server alone. Only the owned process tree may be terminated.
It requests CPU-only execution, two threads, 8K context, 1,100 output tokens per
call (including any thinking), temperature 0 and a fixed seed. Strict reproducibility
still depends on backend kernels. `--mode thinking` is a separate configuration,
not automatic retry. Unsupported mode/schema flags fail visibly.

Approved artifacts can be imported with `python -m scripts.memory_qualification.prepare
--file <manifest filename> --alias novi-qual-<name> --ollama-exe <existing executable>`.
This imports into `model_cache/qualification/ollama-store`; pass that directory to
the runner with `--model-store`. The import does not load a model for inference
or change normal Ollama models. Imported blob contents are verified against the
download digest, even though their filenames differ. Logs are kept for startup
and import diagnostics. Windows CLI output is decoded as UTF-8.

Admission requires 4 GiB foreground/OS reserve plus the 1 GB experimental cap.
That cap is a first-measurement budget, not a measured model footprint. The
monitor defers further work when either incremental working set or private
commit exceeds the cap, reserve is breached, telemetry fails, deadline expires,
or `model_cache/qualification/STOP` exists. It terminates its owned server on
pressure. A 90-second exploratory deadline is not a product-approved deadline.
Current runs count the entire owned server tree against the memory cap, with a
zero memory baseline. Early `direct-01`/`direct-02` diagnostic runs subtracted idle
server memory; do not conflate those figures. Normal runner exit during unload
is distinguished from an unreadable live process. The first pressure reason is
preserved even after termination. CPU thread requests are not a hard system CPU percentage quota. Low priority and
the delay probe do not prove unobtrusive foreground behavior. No automatic
idle/foreground integration exists in this standalone manual runner.

The JSON schema is explicitly included in the system message as well as passed
to Ollama's grammar control. `--supplied-spans` is a separate development variant
that gives the model exact full-turn evidence spans to copy; code calculates
Unicode offsets, without adding labels or semantic conclusions. Preserve the
original runs when experimenting; never mix variants in a single quality score.
`--verification-only --dataset tests/fixtures/memory_curation/verifier_challenges.json
--limit 4` tests the same model against three traceable but semantically invalid
proposals and one faithful proposal. These are supplemental single-pass results,
not complete two-pass cycles. Use `compare.py` to export accounting and review
files; semantic metrics remain null until independently annotated.

Input has a conservative byte limit; the actual runtime prompt count is checked
after generation. This is not a tokenizer-based pre-admission proof of the 4K
packet target. Qualifying finalists requires model-specific token counting before
inference. Oversize input fails rather than silently dropping source segments.

## Grading and qualification boundaries

`dev30.json` has author-authored synthetic cases and review rubrics. It is NOT
human-reviewed ground truth. Expected/forbidden labels never enter model prompts.
Do not use substring matching or this same model to declare semantic precision.
For each accepted operation a human reviewer must count supported/unsupported
atomic claims, correct/incorrect semantic links and corrections, worthwhile
gold memories recovered/missed, and critical wrong-person/source/destructive
errors. Include rejected/deferred/abstained cases in recall denominators.

The screen stops on resource failure; unattempted cases are not passing cases.
One repair is allowed across the cycle, and a repair receives another fresh
verification. Exact source validation proves traceability only; semantic failures
remain possible even after both passes approve. Target identity checks do not
replace future revision guards or production evidence manifests.

Before finalists: expand to 50 human-reviewed development episodes; separately
create and seal 150 held-out episodes (at least 50 link decisions, 30 corrections).
Freeze prompts/configuration and hashes before opening held-out labels. This
screening runner intentionally rejects held-out inputs. Build the separate
qualification/review workflow before claiming release gates; no held-out data
has been generated, opened, tuned on or scored here.

The extraction-only heuristic baseline is available through `baseline.py`. It
loads the unchanged pure extractor in an isolated package without opening stores;
this does not measure production retention, deduplication or graph behavior.

Pending qualification work: human grading with counts and
uncertainty, tokenizer admission, file-backed telemetry, actual Novi chat
contention under a fixed workload, lower-memory target, bounded-thinking
comparison where supported, runtime capability checks for other artifacts,
held-out runner and dataset, downstream retrieval utility. No model is selected.
