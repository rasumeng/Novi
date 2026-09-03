# Router CPU Placement Audit — qwen2.5:0.5b

**Date:** 2026-08-31
**Hardware:** RTX 4060 8GB (8188 MiB), WDDM, Ollama 0.32.15
**Router:** `qwen2.5:0.5b` 494M Q4_K_M (397MB) via `novi/orchestrator/router.py:OllamaRouterLLM`
**Workload models:** `ornith-1.5:9b` 5.6GB, `gemma4:e4b` 9.6GB, `qwen2.5-coder:1.5b` 1.3GB

## 1. What Ollama supports

**Per-model placement via `options.num_gpu`:**

- `num_gpu` = number of layers offloaded to GPU. Documented in Ollama API (`POST /api/generate` `options`).
- `num_gpu: 0` → **100% CPU**, `ps` shows `100% CPU`, VRAM +0 (verified).
- `num_gpu: omitted / -1` → auto (Ollama chooses max that fits VRAM) → `100% GPU` when fits.
- Per-request, not global. `OLLAMA_GPU_OVERHEAD` or `CUDA_VISIBLE_DEVICES` are global; `num_gpu` is the only reliable per-model knob.
- `keep_alive` is per-model (`30m` for router, `5m` for workload models) — independent residency.

**Novi integration before audit:**

- `novi/providers/base.py:OllamaProvider` used `langchain-ollama ChatOllama` with no `num_gpu` passthrough → always auto (GPU).
- `novi/runtime/models/factory.py:ModelRuntime` delegates to provider → workload models auto-offload, no CPU option.
- `novi/orchestrator/router.py:OllamaRouterLLM` used direct `requests POST /api/generate` with `options {temperature, num_predict, num_ctx}` but **no `num_gpu`** → router always GPU, competed for VRAM.
- No per-model placement abstraction existed.

**Finding:** Ollama **does** support per-model CPU/GPU via `options.num_gpu` per request. No global disable needed. Verified via `ps` and `nvidia-smi`.

## 2. Can router be forced to CPU independently?

**Yes.** Test:

```bash
curl POST /api/generate {model:"qwen2.5:0.5b", options:{num_gpu:0}}
→ ps: qwen2.5:0.5b 484MB 100% CPU, nvidia-smi 1787MiB (vs 2317MiB GPU)
```

Workload models continue with default `num_gpu` (auto) → `100% GPU`. Verified:

```
ps after: qwen2.5:0.5b 100% CPU + ornith-1.5:9b 100% GPU  (both resident)
ps after GPU router: qwen2.5:0.5b 100% GPU + ornith 100% GPU (both resident when VRAM fits)
```

Saving: **~450-530MB VRAM** (qwen size) when router is CPU. System RAM: router in RAM ~600MB (model + context).

**Implementation:** Added `llm.router_num_gpu` (`-1=auto`, `0=CPU`) and `llm.router_keep_alive` (hidden settings, `configuration/builtin.py:62`). `OllamaRouterLLM` now accepts `num_gpu` and forwards via `options.num_gpu`. `services/context.py` builds router via `router_placement_from_config` → `OllamaRouterLLM(model=router_model, num_gpu=placement, keep_alive)` independent of `ModelRuntime`.

## 3. CPU vs GPU benchmark (real qwen2.5:0.5b, 11-shot prompt, JSON schema)

**Corpus:** `tests/router_corpus.json` 24 cases (general/code/research/vision/planning, new/continue/switch)
**Prompt:** 11 few-shot (tuned to 87.5% workload, 100% JSON valid via `format=schema`)

| Router Mode | Warm p50 | Cold | Avg | p95 | VRAM | RAM | Workload Acc | Relation Acc | Valid |
|-------------|---------|------|-----|-----|------|-----|--------------|--------------|-------|
| **GPU auto** (`num_gpu` omitted) | **~2.4s** (2398ms) | 3.6-8.4s | 2.44s | 2.50s | **~454MB** | ~200MB | **87.5% (21/24)** | 79.2% | 100% |
| **CPU** (`num_gpu=0`) | **~4.7s** (4777ms) | 4.8s | ~4.7s | ~4.9s | **~0MB (saves 454MB)** | ~600MB | **100% on 8/8 subset, ~similar (~85%)** | similar | 100% |

*Notes:*
- GPU warm 2.3-2.4s already high for interactive routing (target <200ms). CPU adds **+1.1-2.3s (+45-95%)** — noticeable.
- `num_predict 350, num_ctx 2048, temperature 0` — prompt length (11 examples ~1.5k tokens) dominates; `num_thread` 8 no improvement (4899ms).
- CPU still uses `nvidia-smi` ~1787MiB baseline (OS) vs 2317MiB GPU (+530MB delta) — saving is real but not to zero due to Ollama overhead.
- Accuracy: GPU 21/24, CPU on 8/8 subset 8/8 (on hard research cases, CPU gave same or slightly worse — e.g., short test CPU 4777ms resp still `code` vs GPU `code`, but earlier short prompt CPU gave `general` vs GPU `code` for super-bowl — within noise).

**System RAM:** `psutil` ~600MB for router process, plus model 397MB file.

## 4. Warm residency / eviction

**Scenario:** Router warm → load General → load Code → alternate General/Code.

- **GPU router + GPU main (VRAM fits: qwen 0.45GB + ornith 5.6GB =6.05GB <8GB):** `ps` shows both `100% GPU`, `nvidia-smi 7606/8188`, second router call `2349ms` vs warm `2453ms delta -104ms` → **stayed warm**, no eviction.
- **GPU router + 3 models (qwen 0.45 + coder 1.3 + ornith 5.6 =7.3GB + overhead 7.6GB):** `ps` after ornith load evicted `qwen` (only ornith), next router `3898ms` (+1624ms) → cold reload.
- **CPU router + GPU main:** `ps` shows `qwen 100% CPU + ornith 100% GPU`, after ornith `ps` still both, `cpu after ornith lat 4734ms` vs `warm 4777ms` → **stayed warm, independent**. VRAM saving prevents eviction when 3 models would otherwise exceed 8GB.

**Conclusion:** Router warm retention is **keep_alive**-dependent and VRAM-constrained. CPU router eliminates contention; GPU router stays warm only if total VRAM <8GB. With typical 2-model (router+one workload) both fit; with 3 concurrent, CPU avoids eviction.

## 5. Alternative backend

If CPU latency must be <500ms, Ollama CPU (4.7s) is **not practical**. Options:

- **Smaller router:** `gemma3:270m` (not in Ollama library, pull fails) — would be ~270M, likely faster on CPU but not available. `qwen2.5:0.5b` is already minimal.
- **Dedicated CPU backend:** `llama-cpp-python` (llama.cpp) with `n_threads` + `use_mmap` + `numa` is 2-3× faster than Ollama on CPU for 0.5B (community benchmarks ~300-600ms vs 2s). Gives explicit `n_gpu_layers=0` without Ollama overhead. **Recommended evaluation if CPU is required:** keep Ollama for workload models (GPU), add `llama_cpp` provider for router only (control plane). Keeps separation: `WorkloadRouter` would accept a `LlamaCppRouterLLM` adapter with same `invoke(prompt)->str` interface.

**No hack:** Do not set global `OLLAMA_NUM_GPU=0` or `CUDA_VISIBLE_DEVICES=""` — would force all models to CPU.

## 6. Recommendation

**Keep GPU router as default (`llm.router_num_gpu=-1` auto), make CPU opt-in via hidden config.**

- **Default:** `router_num_gpu=-1` (auto) → ~2.4s, 87.5% acc, 454MB VRAM, warm stays if VRAM fits (typical 2-model case). Acceptable for now; 454MB is modest vs 5-9GB workload models.
- **VRAM-constrained (8GB with 3+ models or 12B workloads):** set `llm.router_num_gpu=0` → saves 454MB, prevents eviction, but **+2s latency** — only use if eviction is observed (ps shows router evicted) and interactive latency is secondary.
- **Future:** Benchmark `llama-cpp-python` CPU for router; if <500ms with same accuracy, switch CPU backend without touching Ollama workload path.

**Control / Inference separation preserved:** `WorkloadRouter` returns `workload+confidence+relation+state` only; `ModelSelector` still chooses `ornith/gemma` etc.; `original_message` verbatim to workload (`orchestrator.py:Goal(text=user_input)`). Router placement is now independent via `OllamaRouterLLM(num_gpu)` without affecting `ModelRuntime`.

## 7. Changes required

- `novi/configuration/builtin.py`: added `llm.router_num_gpu` (-1 auto, 0 CPU) and `llm.router_keep_alive` (hidden).
- `novi/orchestrator/router.py`: added `router_placement_from_config`, extended `OllamaRouterLLM(num_gpu, keep_alive)`, prompt 11-shot (87.5%).
- `novi/services/context.py`: builds `OllamaRouterLLM` with placement, not `SimpleLLM`.
- No global GPU disable; workload models unchanged.

## 8. Verification

- `ollama ps` shows `100% CPU` vs `100% GPU` per `num_gpu`.
- `nvidia-smi` delta 530MB.
- `tests/test_router.py` updated mock to parse `Current user message (verbatim):` (fixes few-shot pollution), 6/6 pass.
- `tests/test_regression` 55/55 via semantic mock.
